"""Discord-free drafting brain shared by the `/league game` command and Flexers Draft Coach.

Everything here is pure data in / data out: role assignment, composition diversity, role
alternatives, roster-real pick ordering and conditional ban branches. `cogs/league_flex.py`
imports these names, so the Discord command behaves exactly as before; `league_draft_backend`
and the desktop app import this module without needing discord.py, asyncpg or Pillow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from datetime import datetime, time, timezone

from league_comp_engine import (
    ALL_IN_ADCS,
    BLOCKED_ROLE_PICKS,
    ENCHANTER_SUPPORTS,
    ENGAGE_SUPPORTS,
    POKE_SUPPORTS,
    counter_ban_recommendations,
    load_lane_matchups,
    load_player_score_index,
    load_solo_queue_winrates,
    role_experience_score,
    _bot_lane_duo_score,
    _coordinated_duo_score,
    EXPLICIT_BOT_PAIR_COVERAGE,
    load_meta,
    score_comp,
)

log = logging.getLogger(__name__)

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")

_CURATED_ROLE_CHAMPIONS = {
    "Top": (
        "Aatrox", "Akali", "Ambessa", "Aurora", "Camille", "Cho'Gath", "Darius", "Dr. Mundo",
        "Fiora", "Gangplank", "Garen", "Gwen", "Illaoi", "Irelia", "Jax", "Jayce", "K'Sante",
        "Kayle", "Kennen", "Kled", "Malphite", "Mordekaiser", "Nasus", "Olaf", "Ornn",
        "Pantheon", "Poppy", "Quinn", "Renekton", "Riven", "Sett", "Shen", "Singed", "Sion",
        "Tahm Kench", "Teemo", "Trundle", "Tryndamere", "Urgot", "Vayne", "Volibear",
        "Warwick", "Yone", "Yorick", "Zac",
    ),
    "Jungle": (
        "Amumu", "Bel'Veth", "Briar", "Diana", "Ekko", "Elise", "Evelynn", "Fiddlesticks",
        "Graves", "Hecarim", "Ivern", "Jarvan IV", "Jax", "Karthus", "Kayn", "Kha'Zix",
        "Kindred", "Lee Sin", "Lillia", "Maokai", "Master Yi", "Nidalee", "Nocturne",
        "Nunu & Willump", "Olaf", "Poppy", "Rammus", "Rek'Sai", "Rengar", "Sejuani",
        "Shaco", "Shyvana", "Skarner", "Taliyah", "Talon", "Trundle", "Udyr", "Vi",
        "Viego", "Volibear", "Warwick", "Wukong", "Xin Zhao", "Zac", "Zyra",
    ),
    "Mid": (
        "Ahri", "Akali", "Anivia", "Annie", "Aurelion Sol", "Aurora", "Azir", "Cassiopeia",
        "Corki", "Diana", "Ekko", "Fizz", "Galio", "Hwei", "Irelia", "Jayce", "Kassadin",
        "Katarina", "LeBlanc", "Lissandra", "Lux", "Malzahar", "Naafiri", "Neeko",
        "Orianna", "Qiyana", "Ryze", "Swain", "Syndra", "Taliyah", "Talon", "Tristana",
        "Twisted Fate", "Veigar", "Vel'Koz", "Vex", "Viktor", "Vladimir", "Xerath",
        "Yasuo", "Yone", "Zed", "Ziggs", "Zoe",
    ),
    "ADC": (
        "Aphelios", "Ashe", "Caitlyn", "Corki", "Draven", "Ezreal", "Jhin", "Jinx",
        "Kai'Sa", "Kalista", "Kog'Maw", "Lucian", "Miss Fortune", "Nilah", "Samira",
        "Senna", "Seraphine", "Sivir", "Smolder", "Tristana", "Twitch", "Varus", "Vayne",
        "Xayah", "Zeri", "Ziggs",
    ),
    "Support": (
        "Alistar", "Bard", "Blitzcrank", "Brand", "Braum", "Janna", "Karma", "Leona",
        "Lulu", "Lux", "Maokai", "Milio", "Morgana", "Nami", "Nautilus", "Neeko",
        "Pantheon", "Pyke", "Rakan", "Rell", "Renata Glasc", "Senna", "Seraphine",
        "Sona", "Soraka", "Swain", "Tahm Kench", "Taric", "Thresh", "Vel'Koz", "Xerath",
        "Yuumi", "Zilean", "Zyra",
    ),
}

CURRENT_ROLE_SAMPLE_MIN = 2000


def _build_role_champions() -> dict[str, tuple[str, ...]]:
    pools = {role: set(champions) for role, champions in _CURATED_ROLE_CHAMPIONS.items()}

    role_data_path = Path(__file__).resolve().parent / "data" / "league_role_champions.json"
    try:
        role_data = json.loads(role_data_path.read_text(encoding="utf-8"))
        for role, champions in (role_data.get("roles") or {}).items():
            if role in pools:
                pools[role].update(str(name) for name in champions if str(name).strip())
    except Exception:
        log.exception("Could not expand League signup champion roles from bundled role data")

    try:
        current_roles = load_solo_queue_winrates()
        for role in ROLES:
            for champion, row in current_roles.get(role, {}).items():
                if (role, str(champion)) in BLOCKED_ROLE_PICKS:
                    continue
                if int(row.get("weight", 0) or 0) >= CURRENT_ROLE_SAMPLE_MIN:
                    pools[role].add(str(champion))
    except Exception:
        log.exception("Could not expand League signup champion roles from current role data")

    return {
        role: tuple(sorted(pools[role], key=lambda name: name.casefold()))
        for role in ROLES
    }


def _role_priority_level(player: dict, role: str) -> int | None:
    """Return a role's 1/2/3 preference, including the legacy primary/secondary/fill shape."""
    if role not in (player.get("roles") or []):
        return None
    preference = player.get("role_priority") or {}

    # New format: {"Top": 2, "Jungle": 1, ...}
    raw = preference.get(role)
    try:
        level = int(raw)
    except (TypeError, ValueError):
        level = None
    if level in {1, 2, 3}:
        return level

    # Backward compatibility for the old Primary / Secondary / Fill format.
    if role == preference.get("primary"):
        return 1
    if role == preference.get("secondary"):
        return 2
    if role in set(preference.get("fill", [])):
        return 3

    # Existing signups created before priorities existed stay legal but neutral until edited.
    return 2


def _normalised_role_priorities(player: dict) -> dict[str, int]:
    return {
        role: level
        for role in (player.get("roles") or [])
        if (level := _role_priority_level(player, role)) is not None
    }


def _role_preference_penalty(player: dict, role: str) -> float:
    level = _role_priority_level(player, role)
    if level == 1:
        return 0.0
    if level == 2:
        # Secondary is genuinely available, but should normally lose to a good
        # Priority-1 layout rather than behaving almost like an equal choice.
        return 1.4
    if level == 3:
        # Priority-3 remains legal for drafts that materially improve the comp,
        # but it is a real fallback rather than routine role rotation.
        return 3.75
    return 50.0


def _assignment_preference_cost(result: dict[str, dict]) -> float:
    return sum(_role_preference_penalty(result[role], role) for role in ROLES)


def _assignment_priority3_users(result: dict[str, dict]) -> tuple[int, ...]:
    return tuple(sorted(
        int(result[role]["user_id"])
        for role in ROLES
        if _role_priority_level(result[role], role) == 3
    ))


def generate_team_options(signups: list[dict], limit: int = 5) -> list[dict[str, dict]]:
    """Return legal five-player assignments while respecting, but not hard-locking, 1/2/3 priorities."""
    eligible = [s for s in signups if s.get("roles")]
    if len(eligible) < 5:
        return []
    ordered = sorted(eligible, key=lambda s: (len(s["roles"]), s["display_name"].lower()))
    results: list[dict[str, dict]] = []

    def walk(role_index: int, used: set[int], current: dict[str, dict]):
        # A wider assignment search is intentional: Priority-3 roles must remain available
        # when they unlock a materially better champion composition.
        if len(results) >= max(limit * 24, 80):
            return
        if role_index == len(ROLES):
            results.append(dict(current))
            return
        role = ROLES[role_index]
        candidates = [p for p in ordered if role in p["roles"] and p["user_id"] not in used]
        candidates.sort(
            key=lambda p: (
                _role_preference_penalty(p, role),
                -role_experience_score(int(p["user_id"]), role),
                len(p["roles"]),
                p["display_name"].lower(),
            )
        )
        for player in candidates:
            used.add(player["user_id"])
            assigned = dict(player)
            assigned["_role_priority_level"] = _role_priority_level(player, role)
            assigned["_role_preference_penalty"] = _role_preference_penalty(player, role)
            current[role] = assigned
            walk(role_index + 1, used, current)
            current.pop(role, None)
            used.remove(player["user_id"])

    walk(0, set(), {})
    if not results:
        return []

    results.sort(
        key=lambda result: (
            _assignment_preference_cost(result),
            -sum(role_experience_score(int(result[role]["user_id"]), role) for role in ROLES),
        )
    )

    varied: list[dict[str, dict]] = []
    seen_signatures: set[tuple[int, ...]] = set()
    best_cost = _assignment_preference_cost(results[0])

    def add(result: dict[str, dict]) -> bool:
        signature = tuple(int(result[role]["user_id"]) for role in ROLES)
        if signature in seen_signatures:
            return False
        seen_signatures.add(signature)
        varied.append(result)
        return True

    # First preserve different five-player lineups where more than five people are signed.
    seen_team_sets: set[tuple[int, ...]] = set()
    for result in results:
        team_key = tuple(sorted(int(result[role]["user_id"]) for role in ROLES))
        if team_key in seen_team_sets:
            continue
        seen_team_sets.add(team_key)
        add(result)
        if len(varied) >= limit:
            return varied

    # ADC is often the bottleneck role for this flex group. Preserve the best legal
    # assignment for each player who explicitly selected ADC so the composition solver
    # can compare them instead of silently funnelling every prepared draft to one person.
    adc_players = sorted(
        [player for player in eligible if "ADC" in (player.get("roles") or [])],
        key=lambda player: (
            _role_preference_penalty(player, "ADC"),
            -role_experience_score(int(player["user_id"]), "ADC"),
        ),
    )
    for player in adc_players:
        uid = int(player["user_id"])
        candidates = [
            result for result in results
            if int(result["ADC"]["user_id"]) == uid
            and tuple(int(result[role]["user_id"]) for role in ROLES) not in seen_signatures
            and _assignment_preference_cost(result) <= best_cost + 8.0
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda result: (
                _assignment_preference_cost(result),
                -sum(role_experience_score(int(result[role]["user_id"]), role) for role in ROLES),
            )
        )
        add(candidates[0])
        if len(varied) >= limit:
            return varied

    # Preserve role variety for the strongest flexible carry profiles before the
    # composition solver runs. Otherwise an excellent Mid/Jungle option can disappear
    # simply because only a handful of role layouts survive this pre-search stage.
    player_scores = load_player_score_index()
    carry_ranked_users = sorted(
        {int(player["user_id"]) for player in eligible},
        key=lambda uid: float(player_scores.get(uid, {}).get("carry_index", 50.0) or 50.0),
        reverse=True,
    )[:2]
    by_user = {int(player["user_id"]): player for player in eligible}
    for uid in carry_ranked_users:
        player = by_user.get(uid)
        if not player or len(player.get("roles") or []) < 2:
            continue
        role_targets = sorted(
            player.get("roles") or [],
            key=lambda role: (
                _role_preference_penalty(player, role),
                -role_experience_score(uid, role),
            ),
        )
        for target_role in role_targets:
            candidates = [
                result for result in results
                if int(result[target_role]["user_id"]) == uid
                and tuple(int(result[role]["user_id"]) for role in ROLES) not in seen_signatures
                and _assignment_preference_cost(result) <= best_cost + 5.0
            ]
            if not candidates:
                continue
            candidates.sort(
                key=lambda result: (
                    _assignment_preference_cost(result),
                    -sum(role_experience_score(int(result[role]["user_id"]), role) for role in ROLES),
                )
            )
            add(candidates[0])
            if len(varied) >= limit:
                return varied

    # Then deliberately keep a small number of viable Priority-3 assignments in the
    # candidate set. The composition engine still charges their preference penalty, so
    # they only win when the comp/player-fit gain is genuinely worth it. Rotate who
    # takes the Priority-3 hit where possible rather than shafting the same person.
    used_p3: set[int] = set()
    flex_slots = min(max(1, limit // 4), max(0, limit - len(varied)))
    for _ in range(flex_slots):
        candidates = [
            result for result in results
            if tuple(int(result[role]["user_id"]) for role in ROLES) not in seen_signatures
            and _assignment_priority3_users(result)
            and _assignment_preference_cost(result) <= best_cost + 8.0
        ]
        if not candidates:
            break
        candidates.sort(
            key=lambda result: (
                len(set(_assignment_priority3_users(result)) & used_p3),
                _assignment_preference_cost(result),
                -sum(role_experience_score(int(result[role]["user_id"]), role) for role in ROLES),
            )
        )
        chosen = candidates[0]
        add(chosen)
        used_p3.update(_assignment_priority3_users(chosen))

    for result in results:
        if len(varied) >= limit:
            break
        add(result)
    return varied


def _filter_unavailable(signups: list[dict], unavailable: set[str]) -> list[dict]:
    if not unavailable:
        return signups
    blocked = {name.casefold().strip() for name in unavailable if name.strip()}
    filtered = []
    for signup in signups:
        copy = dict(signup)
        copy["champions"] = {
            role: [champ for champ in picks if champ.casefold() not in blocked]
            for role, picks in signup.get("champions", {}).items()
        }
        filtered.append(copy)
    return filtered


def _conditional_ban_annotations(base_bans: list[dict], branches: list[dict]) -> dict[str, list[dict]]:
    """Map each primary ban to compact 'if pick X -> ban Y' alternatives for the card."""
    base_names = [str(row.get("champion") or "") for row in base_bans]
    notes: dict[str, list[dict]] = {name: [] for name in base_names if name}
    for branch in branches:
        condition = str(branch.get("champion") or "")
        added = [str(name) for name in branch.get("added", []) if str(name)]
        removed = [str(name) for name in branch.get("removed", []) if str(name) in notes]
        if not condition or not added:
            continue
        targets = removed or (base_names[-1:] if base_names else [])
        for idx, alt_ban in enumerate(added[:2]):
            if not targets:
                break
            target = targets[min(idx, len(targets) - 1)]
            row = {"condition": condition, "ban": alt_ban}
            if row not in notes.setdefault(target, []):
                notes[target].append(row)
    return notes


def _roster_real_flex_value(rec: dict, champion: str) -> float:
    """Return flex value only when this exact five can credibly move the champion.

    Global meta flexes are not useful draft concealment if only one assigned player has
    the champion in a submitted pool. That is a bluff, not a real flex.
    """
    submitted = rec.get("submitted_pools", {}) or {}
    credible_roles = sum(
        champion in set(submitted.get(role, []) or [])
        for role in ROLES
    )
    return min(1.0, max(0, credible_roles - 1) / 2.0)


def _pro_pick_order(rec: dict, meta: dict, side: str = "blue") -> list[tuple[str, str]]:
    """Plan our five slots for flex queue, prioritising real blindability over pro fashion.

    B1 -> R1/R2 -> B2/B3 -> R3/R4 -> B4/B5 -> R5.
    Red deliberately saves a solo-lane R5 counter where possible.
    """
    role_stats = meta.get("role_stats", {})
    solo_index = load_solo_queue_winrates()
    matchup_data = load_lane_matchups()
    role_counter_cost = {"Jungle": 0.10, "ADC": 0.14, "Support": 0.26, "Mid": 0.34, "Top": 0.46}
    rows = []
    for role in ROLES:
        champion = rec["champions"][role]
        entry = role_stats.get(role, {}).get(champion, {})
        games = float(entry.get("games", 0) or 0)
        pro_priority = min(1.0, math.log1p(games) / math.log1p(30))
        flex_value = _roster_real_flex_value(rec, champion)

        # How punishable is revealing this exact role/champion before the opponent answers?
        counter_edges = []
        for enemy_data in matchup_data.get("matchups", {}).get(role, {}).values():
            response = enemy_data.get("responses", {}).get(champion)
            if response:
                edge = -float(response.get("response_advantage", 0.0) or 0.0)
                if edge > 0:
                    counter_edges.append(edge)
        matchup_risk = (sum(sorted(counter_edges, reverse=True)[:3]) / min(3, len(counter_edges))) if counter_edges else 0.0
        counter_cost = role_counter_cost[role] + min(0.35, matchup_risk / 25.0)

        # LoLalytics SoloQ strength is relevant to our games; current pro presence is only
        # a tiny tie-breaker. It should never turn a counter-sensitive Top carry into an
        # early blind merely because pros happened to draft it often.
        solo_entry = solo_index.get(role, {}).get(champion, {})
        solo_wr = float(solo_entry.get("win_rate", 50.0) or 50.0)
        solo_conf = min(1.0, float(solo_entry.get("weight", 0) or 0) / 5000.0)
        solo_strength = ((solo_wr - 50.0) / 10.0) * 0.08 * solo_conf
        early_score = flex_value * 0.30 + solo_strength + pro_priority * 0.05 - counter_cost
        late_value = counter_cost - flex_value * 0.20 - pro_priority * 0.03
        rows.append({"early": early_score, "late": late_value, "role": role, "champion": champion})

    rows.sort(key=lambda row: row["early"], reverse=True)
    if str(side).casefold() == "red":
        # R5 is the only true unanswered pick. Spend it on the solo lane that benefits most,
        # rather than wasting it on Jungle/ADC because they happen to have low pro priority.
        solo_rows = [row for row in rows if row["role"] in {"Top", "Mid"}]
        if solo_rows:
            r5 = max(solo_rows, key=lambda row: row["late"])
            rows.remove(r5)
            rows.append(r5)
    return [(row["role"], row["champion"]) for row in rows]


def _conditional_ban_branches(
    rec: dict,
    meta: dict,
    side: str,
    unavailable: set[str],
    base_bans: list[dict] | None = None,
) -> list[dict]:
    """Recalculate protection bans for every champion option actually shown to the user."""
    base_order = _pro_pick_order(rec, meta, side)
    if base_bans is None:
        base_bans = counter_ban_recommendations(
            rec["champions"], base_order, side, unavailable=unavailable, limit=5
        )
    base_names = [row["champion"] for row in base_bans]
    branches: list[dict] = []
    for role in ROLES:
        options = []
        options.extend(rec.get("role_alternatives", {}).get(role, []))
        options.extend(rec.get("learn_suggestions", {}).get(role, []))
        for champion in dict.fromkeys(options):
            if champion == rec["champions"][role]:
                continue
            swapped = dict(rec["champions"])
            swapped[role] = champion
            alt_rec = dict(rec)
            alt_rec["champions"] = swapped
            alt_order = _pro_pick_order(alt_rec, meta, side)
            alt_bans = counter_ban_recommendations(
                swapped, alt_order, side, unavailable=unavailable, limit=5
            )
            alt_names = [row["champion"] for row in alt_bans]
            branches.append({
                "role": role,
                "champion": champion,
                "bans": alt_names,
                "added": [name for name in alt_names if name not in base_names],
                "removed": [name for name in base_names if name not in alt_names],
            })
    return branches


# User-requested variety: five visible options when Ste plays mid; the other
# roles keep three. This does not boost a champion's composition score.
_PLAYER_ROLE_OPTION_LIMITS = {286592835850076162: {"Mid": 5}}


def role_option_limit(role: str, user_id: int | None) -> int:
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        uid = 0
    return int(_PLAYER_ROLE_OPTION_LIMITS.get(uid, {}).get(role, 3))


def _attach_role_alternatives(selected: list[dict], candidates: list[dict], max_per_role: int = 2) -> list[dict]:
    """Attach same-plan one-role variants, including deliberate tank-vs-carry agency shifts."""
    for rec in selected:
        base_players = tuple(int(rec["players"][role]["user_id"]) for role in ROLES)
        base_plan = rec.get("team_plan")
        base_score = float(rec.get("score", 0))
        alternatives: dict[str, list[str]] = {}
        for role in ROLES:
            player_id = (rec.get("players", {}).get(role) or {}).get("user_id")
            alternative_limit = max(max_per_role, role_option_limit(role, player_id) - 1)
            viable: list[tuple[float, str]] = []
            for candidate in candidates:
                if tuple(int(candidate["players"][r]["user_id"]) for r in ROLES) != base_players:
                    continue
                if candidate.get("team_plan") != base_plan:
                    continue
                changed = [r for r in ROLES if candidate["champions"][r] != rec["champions"][r]]
                if changed != [role]:
                    continue
                if base_score - float(candidate.get("score", 0)) > 3.0:
                    continue
                if float(candidate.get("team_plan_score", 5.0)) < float(rec.get("team_plan_score", 5.0)) - 0.8:
                    continue
                if rec.get("credible_mixed_damage") and not candidate.get("credible_mixed_damage"):
                    continue
                base_top_structure = float(rec.get("top_structure_adjustment", 0.0) or 0.0)
                cand_top_structure = float(candidate.get("top_structure_adjustment", 0.0) or 0.0)
                if base_top_structure > 0 and cand_top_structure < 0:
                    continue
                if cand_top_structure < base_top_structure - 1.0:
                    continue
                if role in {"ADC", "Support"} and float(candidate.get("bot_lane_score", 5.0)) < float(rec.get("bot_lane_score", 5.0)) - 0.8:
                    continue
                if role in {"Jungle", "Mid"} and float(candidate.get("mid_jungle_score", 5.0)) < float(rec.get("mid_jungle_score", 5.0)) - 0.8:
                    continue
                alt = candidate["champions"][role]
                viable.append((float(candidate.get("score", 0)), alt))
            if viable:
                ordered = []
                for _, champ in sorted(viable, reverse=True):
                    if champ not in ordered and champ != rec["champions"][role]:
                        ordered.append(champ)
                    if len(ordered) >= alternative_limit:
                        break
                if ordered:
                    alternatives[role] = ordered
        rec["role_alternatives"] = alternatives
        rec["bot_lane_packages"] = _linked_bot_lane_packages(rec)
        rec["mid_jungle_packages"] = _linked_mid_jungle_packages(rec)
    return selected


def _linked_bot_lane_packages(rec: dict, limit: int = 3) -> list[dict]:
    """Return explicit ADC+Support substitutions; never imply independent swaps.

    Evaluate the entire five-player composition after swapping BOTH bot picks.
    Recent individual ADC performance is only a tie-breaker elsewhere: it
    cannot prevent a stronger submitted ADC+Support combination from appearing.
    Team plans may change; we label that honestly rather than misrepresent a
    Kalista/Renata all-in as Aphelios front-to-back scaling.
    """
    submitted = rec.get("submitted_pools") or {}
    adcs = list(dict.fromkeys(submitted.get("ADC") or []))
    supports = list(dict.fromkeys(submitted.get("Support") or []))
    # Full bot-lane substitutions must check the WHOLE submitted support
    # pool. Renata may be a poor one-role swap with Aphelios but the right
    # partner for Kalista, so filtering to current one-role alternates is wrong.
    if not adcs or not supports or not rec.get("champions"):
        return []
    current = rec["champions"]
    base_plan = str(rec.get("team_plan") or "")
    base_raw = float(rec.get("composition_score", rec.get("score", 0)) or 0.0)
    base_rank = float(rec.get("rank_score", base_raw) or base_raw)
    meta = load_meta()
    candidates = []
    for adc in adcs:
        for support in supports:
            if adc == support or (adc, support) == (current.get("ADC"), current.get("Support")):
                continue
            if adc in (current.get("Top"), current.get("Jungle"), current.get("Mid")):
                continue
            if support in (current.get("Top"), current.get("Jungle"), current.get("Mid")):
                continue
            duo = _bot_lane_duo_score(adc, support, meta)
            if float(duo["score"]) < 7.4:
                continue
            candidates.append((float(duo["score"]), adc, support))
    # Bound full five-man rescoring. Preserve one path per distinct Support
    # and top-pair ADC, rather than all 40 evaluations being enchanter clones.
    candidates.sort(reverse=True)
    shortlist = candidates[:24]
    for _score, adc, support in candidates[24:]:
        if len(shortlist) >= 40:
            break
        if (not any(row[2] == support for row in shortlist)
            or not any(row[1] == adc for row in shortlist)):
            shortlist.append((_score, adc, support))
    # Preserve expressly requested duo candidates even if more popular pro
    # pairs occupied the first 40 heuristic slots. These still must pass all
    # complete-team quality checks below.
    shortlisted_pairs = {(adc, support) for _score, adc, support in shortlist}
    for duo_score, adc, support in candidates:
        if (adc, support) in EXPLICIT_BOT_PAIR_COVERAGE and (adc, support) not in shortlisted_pairs:
            shortlist.append((duo_score, adc, support))
            shortlisted_pairs.add((adc, support))
    viable = []
    for duo_score, adc, support in shortlist:
        swapped = dict(current, ADC=adc, Support=support)
        scored = score_comp(swapped, meta)
        comp_rank = float(scored.get("rank_score", scored["score"]))
        if comp_rank < base_raw - 9.0:
            continue
        if float(scored.get("team_plan_score", 0)) < 5.4:
            continue
        if rec.get("credible_mixed_damage") and not scored.get("credible_mixed_damage"):
            continue
        viable.append({
            "adc": adc, "support": support,
            "duo_score": float(duo_score),
            "composition_score": round(comp_rank, 1),
            "team_plan": scored.get("team_plan"),
            "team_plan_score": scored.get("team_plan_score"),
            "plan_change": scored.get("team_plan") != base_plan,
        })
    viable.sort(key=lambda row: (row["composition_score"], row["duo_score"]), reverse=True)
    chosen, seen_supports, seen_adcs = [], set(), set()
    # First retain meaningful explicitly discussed package variants if their
    # FULL team score is respectable; never award an invisible scoring bonus.
    # Then fill gaps with whichever other submitted pairs are actually best.
    requested = [row for row in viable
                 if (row["adc"], row["support"]) in EXPLICIT_BOT_PAIR_COVERAGE]
    for row in requested:
        if row["support"] in seen_supports or row["adc"] in seen_adcs:
            continue
        chosen.append(row)
        seen_supports.add(row["support"])
        seen_adcs.add(row["adc"])
        if len(chosen) >= limit:
            return chosen
    for row in viable:
        if row["support"] in seen_supports or row["adc"] in seen_adcs:
            continue
        chosen.append(row)
        seen_supports.add(row["support"])
        seen_adcs.add(row["adc"])
        if len(chosen) >= limit:
            return chosen
    for row in viable:
        if row not in chosen:
            chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def _linked_mid_jungle_packages(rec: dict, limit: int = 2) -> list[dict]:
    """Rebuild the whole five around a linked Jungle+Mid switch.

    One-role alternatives can misrepresent a pair whose value comes from
    knock-up follow-through, setup or dive timing. Keep role submissions and
    full five-man team-plan constraints intact.
    """
    submitted = rec.get("submitted_pools") or {}
    jungles = list(dict.fromkeys(submitted.get("Jungle") or []))
    mids = list(dict.fromkeys(submitted.get("Mid") or []))
    current = rec.get("champions") or {}
    if not jungles or not mids or not current:
        return []
    meta = load_meta()
    base_raw = float(rec.get("composition_score", rec.get("score", 0)) or 0)
    base_plan = str(rec.get("team_plan") or "")
    rows = []
    for jungle in jungles:
        for mid in mids:
            if jungle == mid or (jungle, mid) == (current.get("Jungle"), current.get("Mid")):
                continue
            if jungle in (current.get("Top"), current.get("ADC"), current.get("Support")):
                continue
            if mid in (current.get("Top"), current.get("ADC"), current.get("Support")):
                continue
            duo = float(_coordinated_duo_score("Jungle", jungle, "Mid", mid, meta)["score"])
            if duo < 7.25:
                continue
            rows.append((duo, jungle, mid))
    rows.sort(reverse=True)
    viable = []
    for duo_score, jungle, mid in rows[:32]:
        scored = score_comp(dict(current, Jungle=jungle, Mid=mid), meta)
        if float(scored["rank_score"]) < base_raw - 8.5:
            continue
        if float(scored.get("team_plan_score", 0)) < 5.4:
            continue
        if rec.get("credible_mixed_damage") and not scored.get("credible_mixed_damage"):
            continue
        viable.append({
            "jungle": jungle, "mid": mid, "duo_score": round(duo_score, 1),
            "composition_score": round(float(scored["rank_score"]), 1),
            "team_plan": scored.get("team_plan"),
            "plan_change": scored.get("team_plan") != base_plan,
        })
    viable.sort(key=lambda row: (row["composition_score"], row["duo_score"]), reverse=True)
    selected, used_jg, used_mid = [], set(), set()
    for row in viable:
        if row["jungle"] in used_jg or row["mid"] in used_mid:
            continue
        selected.append(row)
        used_jg.add(row["jungle"])
        used_mid.add(row["mid"])
        if len(selected) >= limit:
            break
    return selected


def _recommendation_respects_selected_roles(rec: dict) -> bool:
    """Defence-in-depth: never render a player in a role they did not select."""
    for role in ROLES:
        player = rec.get("players", {}).get(role, {})
        selected = set(player.get("roles") or [])
        if role not in selected:
            return False
    return True


def _select_diverse_recommendations(
    recommendations: list[dict],
    limit: int = 6,
    core_limit: int = 4,
    role_mix_slots: int = 2,
    rotation_token: str | None = None,
) -> list[dict]:
    """Choose 4-6 genuinely different high-quality composition shells.

    Small one-role swaps belong inside a draft as role alternatives; they should not
    consume a separate prepared-draft slot. ``core_limit``/``role_mix_slots`` remain
    accepted for compatibility, but all output slots now compete on composition diversity.
    """
    recommendations = [rec for rec in recommendations if _recommendation_respects_selected_roles(rec)]
    if not recommendations:
        return []

    limit = max(1, int(limit))
    mixed = [rec for rec in recommendations if rec.get("credible_mixed_damage", True)]
    if len(mixed) >= min(4, limit):
        recommendations = mixed
    if len(recommendations) == 1:
        return recommendations

    top_score = float(recommendations[0].get("score", 0))

    def champion_signature(rec: dict):
        return [rec["champions"][role] for role in ROLES]

    def player_signature(rec: dict):
        return tuple(int(rec["players"][role]["user_id"]) for role in ROLES)

    def bot_package(rec: dict):
        return (rec["champions"]["ADC"], rec["champions"]["Support"])

    def mid_jungle_package(rec: dict):
        return (rec["champions"]["Mid"], rec["champions"]["Jungle"])

    def bot_style(rec: dict) -> str:
        adc = rec["champions"]["ADC"]
        support = rec["champions"]["Support"]
        if support in ENGAGE_SUPPORTS and adc in ALL_IN_ADCS:
            return "all-in engage"
        if support in ENGAGE_SUPPORTS:
            return "engage"
        if support in ENCHANTER_SUPPORTS:
            return "enchanter"
        if support in POKE_SUPPORTS:
            return "poke"
        return "other"

    def rotation_value(rec: dict) -> int:
        if not rotation_token:
            return 0
        payload = "|".join([
            str(rotation_token),
            *(champion_signature(rec)),
            *(str(uid) for uid in player_signature(rec)),
        ])
        return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")

    # A fresh /league draft may start from any near-top shell rather than pinning the
    # exact same #1 every time. The small score window preserves quality while allowing
    # different comps and legal role assignments across sessions.
    initial_pool = [
        rec for rec in recommendations
        if float(rec.get("score", 0)) >= top_score - 3.0
        and float(rec.get("team_plan_score", 5.0) or 5.0) >= 6.0
    ] or recommendations[:1]
    if rotation_token and len(initial_pool) > 1:
        first = max(initial_pool, key=rotation_value)
    else:
        first = recommendations[0]
    chosen = [first]
    remaining = [rec for rec in recommendations if rec is not first]

    def min_champion_difference(rec: dict) -> int:
        sig = champion_signature(rec)
        return min(
            sum(a != b for a, b in zip(sig, champion_signature(prev)))
            for prev in chosen
        )

    while remaining and len(chosen) < limit:
        score_pool = [
            rec for rec in remaining
            if float(rec.get("score", 0)) >= top_score - 12.0
        ] or remaining
        best_plan = max(float(rec.get("team_plan_score", 5.0) or 5.0) for rec in score_pool)
        plan_floor = max(5.5, best_plan - 1.5)
        quality_pool = [
            rec for rec in score_pool
            if float(rec.get("team_plan_score", 5.0) or 5.0) >= plan_floor
        ] or score_pool

        # Only rotate ADC duty deliberately when this five has no true P1 ADC.
        # In that fallback-only case, spreading viable ADC assignments stops a P2
        # flex carry from being funnelled bot every draft just because P3 teammates
        # are slightly less preferred there. If a genuine P1 ADC is available (e.g.
        # Jack), role priority and comp/player-fit scoring take over instead: the P1
        # ADC may appear in most drafts, while swaps remain legal when they improve
        # the actual composition.
        adc_priorities = {
            int(rec["players"]["ADC"]["user_id"]):
            (_role_priority_level(rec["players"]["ADC"], "ADC") or 3)
            for rec in quality_pool
        }
        if adc_priorities and min(adc_priorities.values()) > 1:
            adc_usage = {}
            for prev in chosen:
                uid = int(prev["players"]["ADC"]["user_id"])
                adc_usage[uid] = adc_usage.get(uid, 0) + 1
            candidate_adc_users = set(adc_priorities)
            least_used = min(adc_usage.get(uid, 0) for uid in candidate_adc_users)
            balanced_adc = [
                rec for rec in quality_pool
                if adc_usage.get(int(rec["players"]["ADC"]["user_id"]), 0) == least_used
            ]
            if balanced_adc:
                quality_pool = balanced_adc

        # One-pick changes are already shown as alternatives inside a role slot.
        different_shells = [rec for rec in quality_pool if min_champion_difference(rec) >= 2]
        if different_shells:
            quality_pool = different_shells

        used_plans = {str(rec.get("team_plan", "balanced")) for rec in chosen}
        used_bot = {bot_package(rec) for rec in chosen}
        used_bot_styles = {bot_style(rec) for rec in chosen}
        used_mj = {mid_jungle_package(rec) for rec in chosen}
        used_carry_roles = {(rec.get("carry_plan") or {}).get("role") for rec in chosen}
        used_champions = {champ for prev in chosen for champ in champion_signature(prev)}

        def diversity_key(rec: dict):
            champ_sig = champion_signature(rec)
            player_sig = player_signature(rec)
            min_champ_diff = min_champion_difference(rec)
            min_player_diff = min(
                sum(a != b for a, b in zip(player_sig, player_signature(prev)))
                for prev in chosen
            )
            plan = str(rec.get("team_plan", "balanced"))
            carry_role = (rec.get("carry_plan") or {}).get("role")
            archetypes = set(rec.get("archetypes", []))
            min_new_archetypes = min(
                len(archetypes - set(prev.get("archetypes", [])))
                for prev in chosen
            )
            package_novelty = int(bot_package(rec) not in used_bot) + int(mid_jungle_package(rec) not in used_mj)
            bot_style_novelty = int(bot_style(rec) not in used_bot_styles)
            champ_band = 2 if min_champ_diff >= 3 else 1 if min_champ_diff >= 2 else 0
            role_band = 2 if min_player_diff >= 2 else 1 if min_player_diff >= 1 else 0
            new_champion_count = sum(champ not in used_champions for champ in champ_sig)
            return (
                # Entire bot and mid+jungle units are worth more than repeatedly
                # rotating one recently successful champion into every shell.
                package_novelty,
                int(bot_package(rec) not in used_bot),
                int(mid_jungle_package(rec) not in used_mj),
                bot_style_novelty,
                champ_band,
                int(plan not in used_plans),
                role_band,
                new_champion_count,
                int(carry_role not in used_carry_roles),
                min_new_archetypes,
                min_champ_diff,
                min_player_diff,
                rotation_value(rec),
                float(rec.get("score", 0)),
            )

        best = max(quality_pool, key=diversity_key)
        chosen.append(best)
        remaining.remove(best)

    return chosen
