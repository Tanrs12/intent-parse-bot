"""Turn a draft state into guidance.

``advise(state, roster)`` ranks the legal immediate choices by simulating the
remaining draft with the engine (``recommend_comps`` / ``score_comp`` /
``counter_ban_recommendations`` / ``matchup_response``) and layers the
coaching context on top:

* player **priorities** from loadfiles move comfort picks up or down,
* **avoid** / **never pick** lists are removed outright,
* **scouting** (known enemy pools) sharpens ban targets and counter risk and
  predicts the enemy's likely next picks,
* **team ban policy** (``ban_priority``) is blended into the ban list,
* **flex picks** still open in our pools are listed so the draft can hide intent,
* a one-line **headline** and **alerts** say what to do right now.

Nothing in ``engine/`` is modified.
"""

from __future__ import annotations

from league_source_stats import source_stat_scope

import math
import time
from typing import Any

from . import backend
from . import champions as champ_map
from . import engine_cache
from .roster import Roster
from .state import ActiveAction, DraftState, PlayerSlot, resolve_locked_swaps

import league_comp_engine as engine  # type: ignore  # noqa: E402

ROLES = list(engine.ROLES)
_SYNTHETIC_USER_BASE = 900_000_000_000
ROLE_SHORT = {"Top": "Top", "Jungle": "Jungle", "Mid": "Mid", "ADC": "Bot", "Support": "Support"}


# --------------------------------------------------------------------------- #
# Pools and assignments
# --------------------------------------------------------------------------- #


def _pool_for(slot: PlayerSlot, role: str, unavailable: set[str], meta: dict, limit: int,
              locked: str | None, never_pick: list[str]) -> tuple[list[str], str]:
    """Legal champions for this seat in this role, plus where they came from."""
    if locked:
        return [locked], "locked"
    blocked = set(unavailable) | set(slot.avoid) | set(never_pick) | engine.DISPLAY_EXCLUDED_CHAMPIONS
    submitted = [c for c in dict.fromkeys(slot.pool.get(role, [])) if c and c not in blocked]
    if submitted:
        priorities = slot.priorities.get(role, {})
        submitted.sort(key=lambda c: (priorities.get(c, 0) == 0, priorities.get(c, 0)))
        return submitted[:limit], slot.pool_source if slot.pool_source in ("player", "default", "payload", "history") else "submitted"
    exhausted = bool(slot.pool.get(role)) and slot.pool_source == "player"
    history: list[str] = []
    if slot.user_id:
        try:
            history = engine._historical_pool(int(slot.user_id), role, meta, limit=limit + len(unavailable))
        except Exception:  # noqa: BLE001
            history = []
    history = [c for c in history if c not in blocked]
    if history:
        return history[:limit], "exhausted" if exhausted else "history-fallback"
    role_rows = meta.get("role_stats", {}).get(role, {})
    ranked = sorted(role_rows.items(), key=lambda kv: (float(kv[1].get("bayes_win_rate", 0.5)), int(kv[1].get("games", 0))), reverse=True)
    fallback = [c for c, row in ranked if int(row.get("games", 0)) >= 8 and c not in blocked]
    return fallback[:limit], "exhausted" if exhausted else "meta"


def _assignments(state: DraftState) -> list[dict[str, PlayerSlot]]:
    """Role -> seat mappings. Usually one; permutations when roles are unknown."""
    known = {p.role: p for p in state.our_team if p.role in ROLES}
    if len(known) == 5 and len(state.our_team) == 5:
        return [known]
    unassigned = [p for p in state.our_team if p.role not in ROLES]
    open_roles = [r for r in ROLES if r not in known]
    if not unassigned or len(unassigned) != len(open_roles):
        base = dict(known)
        for role in open_roles:
            base[role] = PlayerSlot(cell_id=-1, role=role, display_name=role, role_source="placeholder")
        return [base]
    import itertools

    results = []
    for perm in itertools.permutations(open_roles):
        candidate = dict(known)
        ok = True
        for slot, role in zip(unassigned, perm):
            if slot.allowed_roles and role not in slot.allowed_roles:
                ok = False
                break
            candidate[role] = slot
        if ok:
            results.append(candidate)
    return results or [dict(known, **{r: s for r, s in zip(open_roles, unassigned)})]


# --------------------------------------------------------------------------- #
# Matchup helpers
# --------------------------------------------------------------------------- #


def _scouted_for_role(state: DraftState, role: str) -> tuple[set[str], bool]:
    """Champions the enemy's known players play in ``role`` (and whether we know anything)."""
    champions: set[str] = set()
    known = False
    for enemy in state.enemy_team:
        if not enemy.scouted:
            continue
        pools = enemy.scouted.get("pools", {})
        if enemy.role == role or not enemy.role or enemy.role not in ROLES:
            known = known or bool(pools.get(role)) or bool(pools.get("Any"))
            champions |= set(pools.get(role, [])) | set(pools.get("Any", []))
        elif enemy.locked is None:
            champions |= set(pools.get("Any", []))
    return champions, known


def _counter_risk(role: str, champion: str, unavailable: set[str], matchups: dict, scouted: set[str],
                  scouting_known: bool, weight: float, quality: float = 0.7) -> tuple[float, list[dict], bool]:
    """Expected calibrated loss if the enemy answers this pick: 80% expectation over the enemy's
    likely choices + 20% upper tail. Returns (risk points, worst answers, coverage_known)."""
    answers = []
    for enemy, data in matchups.get("matchups", {}).get(role, {}).items():
        if enemy in unavailable:
            continue
        response = data.get("responses", {}).get(champion)
        if not response:
            continue
        residual, points = engine.calibrated_matchup(role, float(response.get("response_advantage", 0.0) or 0.0),
                                                     int(response.get("games", 0) or 0), quality=quality)
        loss = max(0.0, -points)
        # Choice probability: scouted comfort picks are far likelier than the population.
        prob = 1.0
        if scouting_known:
            prob = (1.0 + 1.5 * weight) if enemy in scouted else max(0.2, 1.0 - 0.6 * weight)
        answers.append({"enemy": enemy, "edge": round(-residual, 2), "games": int(response.get("games", 0) or 0),
                        "loss": loss, "prob": prob, "scouted": enemy in scouted})
    if not answers:
        return 0.0, [], False
    total_prob = sum(a["prob"] for a in answers)
    expected = sum(a["loss"] * a["prob"] for a in answers) / total_prob
    answers.sort(key=lambda a: a["loss"] * a["prob"], reverse=True)
    tail = answers[: max(1, len(answers) // 5)]
    tail_loss = sum(a["loss"] * a["prob"] for a in tail) / sum(a["prob"] for a in tail)
    risk = 0.8 * expected + 0.2 * tail_loss
    worst = [a for a in answers if a["loss"] > 0][:3]
    return round(risk, 2), [{"enemy": a["enemy"], "edge": a["edge"], "games": a["games"], "scouted": a["scouted"]} for a in worst], True


def _lane_edges(role: str, champion: str, enemy_team: list[PlayerSlot], matchups: dict, quality: float = 0.7) -> list[dict]:
    out = []
    for enemy in enemy_team:
        name = enemy.locked or enemy.hover
        if not name or enemy.role != role:
            continue
        advantage, games = engine.matchup_response(role, name, champion, matchups)
        if advantage is None:
            continue
        residual, points = engine.calibrated_matchup(role, advantage, games, quality=quality)
        out.append({"enemy": name, "advantage": round(advantage, 2), "adjusted": residual, "points": points, "games": games,
                    "locked": bool(enemy.locked)})
    return out


def _priority_adjustment(slot: PlayerSlot | None, role: str, champion: str, weight: float) -> tuple[float, int]:
    """Preference among ready options: small (±2 tiers × weight), never a mastery claim."""
    if slot is None:
        return 0.0, 0
    priority = slot.priorities.get(role, {}).get(champion, 0)
    if not priority:
        return 0.0, 0
    return round(weight * max(-2, min(2, 3 - priority)), 2), priority


FAMILIARITY_POINTS = {"competitive": {"comfort": 2.0, "refresh": -0.5, "developing": -3.0},
                      "practice": {"comfort": 1.0, "refresh": 0.0, "developing": -1.0}}


def _familiarity_adjustment(slot: PlayerSlot | None, role: str, champion: str, practice: bool) -> tuple[float, str]:
    """Confirmed comfort / refresh / developing from the loadfile; unknown is neutral."""
    if slot is None:
        return 0.0, ""
    familiarity = (slot.familiarity.get(role, {}) if hasattr(slot, "familiarity") else {}).get(champion, "")
    table = FAMILIARITY_POINTS["practice" if practice else "competitive"]
    return table.get(familiarity, 0.0), familiarity


# --------------------------------------------------------------------------- #
# Playbook: target compositions from the team's own plans
# --------------------------------------------------------------------------- #

FAMILIARITY_WEIGHT = {"comfort": 1.0, "refresh": 0.8, "developing": 0.6}


def evaluate_plans(playbook: list[dict], state: DraftState, our_locked: dict[str, str], unavailable: set[str],
                   slot_by_role: dict[str, PlayerSlot], pools_by_role_all: dict[str, list[str]] | None = None,
                   player_pools: dict[str, dict[str, list[str]]] | None = None) -> list[dict]:
    """How close the draft is to each target composition, and what would complete it.

    A plan is viable while one of its approved variants (the base lineup, or one single
    substitution) is still legal: every locked role matches and every open role's champion is
    available and in the prepared pool of the player the plan assigns to it. Tournament draft has
    no client-assigned roles, so a plan may reseat players: that is reported (``reseat``) and
    costs score, but does not kill the plan. Score is bounded (max +3): 2.25 x progress,
    +0.75 on completion, -0.5 per substitution, -0.5 per reseat.
    """
    pools_by_role_all = pools_by_role_all or {}
    player_pools = player_pools or {}
    out = []
    for plan in playbook or []:
        picks = plan.get("picks") or {}
        if len(picks) != 5:
            continue
        variants = plan.get("variants") or [{"id": "base", "champions": {r: picks[r]["champion"] for r in ROLES}, "cost": 0.0}]
        mismatches = []
        for role in ROLES:
            entry = picks.get(role) or {}
            seat = slot_by_role.get(role)
            if entry.get("player") and seat is not None and seat.player_key and seat.player_key != entry["player"]:
                mismatches.append({"role": role, "wants": entry.get("player_name") or entry["player"], "seated": seat.display_name})
        feasible = []
        blocked_all: list[dict] = []
        for variant in variants:
            champs = variant.get("champions") or {}
            if set(champs) != set(ROLES) or len(set(champs.values())) != 5:
                continue
            blocked = []
            for role in ROLES:
                if role in our_locked and our_locked[role] != champs[role]:
                    blocked.append({"role": role, "reason": f"locked {our_locked[role]} instead of {champs[role]}"})
                elif role not in our_locked and champs[role] in unavailable:
                    blocked.append({"role": role, "reason": f"{champs[role]} is gone"})
                elif role not in our_locked:
                    owner = (picks.get(role) or {}).get("player")
                    sub = next((x for x in plan.get("substitutions", []) if x.get("role") == role and x.get("champion") == champs[role]), None)
                    if sub and sub.get("player"):
                        owner = sub["player"]
                    prepared = player_pools.get(owner, {}).get(role) if owner in player_pools else pools_by_role_all.get(role, [])
                    if prepared is not None and champs[role] not in prepared:
                        who = (picks.get(role) or {}).get("player_name") or owner or role
                        blocked.append({"role": role, "reason": f"{champs[role]} is not in {who}'s prepared {role} pool"})
            if blocked:
                blocked_all.extend(blocked)
                continue
            matched = [{"role": r, "champion": our_locked[r]} for r in ROLES if r in our_locked]
            next_picks = []
            for r in ROLES:
                if r in our_locked:
                    continue
                seat = slot_by_role.get(r)
                fam = seat.familiarity.get(r, {}).get(champs[r], "") if seat is not None and hasattr(seat, "familiarity") else ""
                next_picks.append({"role": r, "champion": champs[r], "substitute": champs[r] != picks[r]["champion"],
                                   "player": picks[r].get("player_name") or picks[r].get("player"), "familiarity": fam or picks[r].get("familiarity")})
            progress = len(matched)
            cost = float(variant.get("cost", 0.0))
            score = 2.25 * progress / 5.0 * 5.0 / 5.0 * 5.0 / 5.0  # placeholder, replaced below
            score = 2.25 * (progress / 5.0) + (0.75 if progress == 5 else 0.0) - 0.5 * cost
            feasible.append({"variant": variant.get("id"), "score": score, "matched": matched, "next": next_picks, "cost": cost})
        viable = bool(feasible)
        if feasible:
            best = max(feasible, key=lambda f: (f["score"], f["variant"]))
        else:
            best = {"variant": None, "score": 0.0, "matched": [], "next": [], "cost": 0.0}
        reasons = blocked_all[:3] if not feasible else []
        reseat = [{"role": m["role"], "reason": f"needs {m['wants']} {ROLE_SHORT.get(m['role'], m['role']).lower()} (seated: {m['seated']})"} for m in mismatches]
        out.append({"id": plan.get("id"), "name": plan.get("name"), "style": plan.get("style"), "viable": viable,
                    "progress": len(best["matched"]), "matched": best["matched"], "next": best["next"], "blocked": reasons,
                    "mismatches": mismatches, "reseat": reseat, "variant": best["variant"], "variants_open": len(feasible),
                    "score": round(best["score"] - 0.5 * len(mismatches), 2), "win": plan.get("win", "")})
    out.sort(key=lambda p: (p["viable"], p["score"]), reverse=True)
    return out


def plan_bonus(plans: list[dict], role: str, champion: str, weight: float) -> tuple[float, dict | None]:
    """Bounded playbook preference for a pick: the best viable plan's score after this pick, scaled by
    ``weight / 3`` so the default weight keeps the term within about +/-4 points. Max over plans, not a sum."""
    best = None
    for plan in plans:
        if not plan["viable"]:
            continue
        hit = next((n for n in plan["next"] if n["role"] == role and n["champion"] == champion), None)
        if hit is None:
            continue
        progress_after = plan["progress"] + 1
        after = 2.25 * (progress_after / 5.0) + (0.75 if progress_after == 5 else 0.0) - 0.5 * (0.5 if hit["substitute"] else 0.0)
        # Reseating players is a cost, never a reason to steer away from an otherwise-live plan.
        value = max(0.0, after - 0.25 * len(plan.get("mismatches", []))) * (weight / 3.0)
        if plan.get("committed"):
            # The team chose this draft: its next picks lead unless something is badly wrong with them.
            value = max(value, 5.0 + after)
        if best is None or value > best[0]:
            best = (value, plan)
    if best is None:
        return 0.0, None
    return round(best[0], 2), best[1]


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


def _conditioned_completions(assignment: dict, forced: dict[str, str], meta: dict, *, limit: int = 4) -> list[dict]:
    """Best full compositions with ``forced`` champions fixed, scored by the engine's full objective."""
    import copy

    trial = copy.deepcopy(assignment)
    for role, champion in forced.items():
        if champion not in trial[role]["champions"].get(role, []):
            return []
        trial[role]["champions"][role] = [champion]
    if len(set(forced.values())) != len(forced):
        return []
    pool_limit = max(len(trial[r]["champions"].get(r, [])) for r in ROLES)
    try:
        rows, _ = engine.recommend_comps([trial], limit=limit, pool_limit=max(1, pool_limit), meta=meta)
    except Exception:  # noqa: BLE001
        return []
    return rows


# How punishable it is to reveal a role early, before matchup evidence (bot's _pro_pick_order).
ROLE_COUNTER_COST = {"Jungle": 0.10, "ADC": 0.14, "Support": 0.26, "Mid": 0.34, "Top": 0.46}


def real_flex_value(champion: str, pools_by_role_all: dict[str, list[str]]) -> float:
    """Flex value only when this exact five can credibly move the champion between roles.

    A global meta flex is not draft concealment if only one assigned player has the champion in a
    submitted pool: that is a bluff, not a flex.
    """
    credible = sum(1 for role in ROLES if champion in set(pools_by_role_all.get(role, []) or []))
    return min(1.0, max(0, credible - 1) / 2.0)


def plan_pick_order(champions: dict[str, str], side: str, meta: dict, matchups: dict, pools_by_role_all: dict[str, list[str]],
               locked: dict[str, str] | None = None, quality: float = 0.7) -> list[dict]:
    """Plan the order in which we reveal a target five: real blindability first, counter-prone picks held back.

    Locked roles come first in the order they were revealed. Among the rest, the early score rewards a
    credible flex, solo-queue strength and (lightly) pro presence, and charges the role's counter cost
    plus how hard the champion is answered in the matchup data. On red side the last pick (R5) is the
    only unanswered one, so it is spent on the solo lane that benefits most.
    """
    locked = locked or {}
    role_stats = meta.get("role_stats", {})
    solo_index = engine.load_solo_queue_winrates()
    rows = []
    for role in ROLES:
        champion = champions.get(role)
        if not champion:
            continue
        entry = role_stats.get(role, {}).get(champion, {})
        games = float(entry.get("games", 0) or 0)
        pro_priority = min(1.0, math.log1p(games) / math.log1p(30))
        flex_value = real_flex_value(champion, pools_by_role_all)
        losses = []
        for enemy_data in matchups.get("matchups", {}).get(role, {}).values():
            response = enemy_data.get("responses", {}).get(champion)
            if response:
                residual, _ = engine.calibrated_matchup(role, float(response.get("response_advantage", 0.0) or 0.0),
                                                        int(response.get("games", 0) or 0), quality=quality)
                if residual < 0:
                    losses.append(-residual)
        top = sorted(losses, reverse=True)[:3]
        matchup_risk = sum(top) / len(top) if top else 0.0           # 0 .. 2.5 calibrated pp
        counter_cost = ROLE_COUNTER_COST[role] + min(0.35, matchup_risk / 7.0)
        solo_entry = solo_index.get(role, {}).get(champion, {})
        solo_wr = float(solo_entry.get("win_rate", 50.0) or 50.0)
        solo_conf = min(1.0, float(solo_entry.get("weight", 0) or 0) / 5000.0)
        solo_strength = ((solo_wr - 50.0) / 10.0) * 0.08 * solo_conf
        early = flex_value * 0.30 + solo_strength + pro_priority * 0.05 - counter_cost
        late = counter_cost - flex_value * 0.20 - pro_priority * 0.03
        tags = []
        if flex_value > 0:
            tags.append("flex")
        if counter_cost < 0.25:
            tags.append("safe blind")
        elif counter_cost >= 0.45:
            tags.append("counter-prone: hold")
        rows.append({"role": role, "champion": champion, "champion_id": champ_map.id_for(champion), "early": round(early, 3),
                     "late": round(late, 3), "flex_value": flex_value, "counter_cost": round(counter_cost, 2), "tags": tags,
                     "locked": role in locked and locked[role] == champion})
    revealed = [r for r in rows if r["locked"]]
    open_rows = sorted([r for r in rows if not r["locked"]], key=lambda r: r["early"], reverse=True)
    if str(side).casefold() == "red" and open_rows:
        solo = [r for r in open_rows if r["role"] in ("Top", "Mid")]
        if solo:
            r5 = max(solo, key=lambda r: r["late"])
            open_rows.remove(r5)
            open_rows.append(r5)
            if "R5 counter slot" not in r5["tags"]:
                r5["tags"].append("R5 counter slot")
    ordered = revealed + open_rows
    if not ({"ADC", "Support"} & set(locked)):
        from league_flex_core import _protect_linked_bot_pick_order
        ordered = _protect_linked_bot_pick_order(ordered, side, meta)
    letter = "R" if str(side).casefold() == "red" else "B"
    for i, row in enumerate(ordered):
        row["slot"] = f"{letter}{i + 1}"
        row["status"] = "locked" if row["locked"] else ("next" if i == len(revealed) else "later")
    return ordered


@source_stat_scope
def advise(state: DraftState, roster: Roster | None = None, *, meta: dict | None = None,
           matchups: dict | None = None) -> dict:
    """Rank the legal immediate choices for the current draft state."""
    started = time.perf_counter()
    settings = dict(roster.settings) if roster is not None else {}
    limit = int(settings.get("picks_shown", 6))
    bans_limit = int(settings.get("bans_shown", 5))
    comps_limit = int(settings.get("comps_limit", 14))
    pool_limit = int(settings.get("pool_limit", 8))
    priority_weight = float(settings.get("priority_weight", 1.5))
    scouting_weight = float(settings.get("scouting_weight", 1.0))
    plan_weight = float(settings.get("plan_weight", 4.0))
    practice = str(settings.get("mode", "competitive")).casefold() == "practice"
    matchup_quality = 0.7   # D2+ evidence transferred to a mixed amateur team

    meta = meta or engine.load_meta()
    matchups = matchups or engine.load_lane_matchups()
    engine_cache.install()
    unavailable = state.unavailable()
    notes: list[str] = list(state.notes)
    alerts: list[dict] = []
    effective_by_cell, swaps = resolve_locked_swaps(state.our_team, meta)
    for swap in swaps:
        alerts.append({"level": "info", "text": f"Swap expected: {swap['champion']} goes to {swap['to_role']} ({swap['to_player']})"})
    our_locked = {p.role: effective_by_cell[p.cell_id] for p in state.our_team if p.cell_id in effective_by_cell and p.role}

    assignments = _assignments(state)
    engine_assignments = []
    pool_sources: dict[str, str] = {}
    pools_by_role: dict[str, list[str]] = {}
    slot_by_role: dict[str, PlayerSlot] = {}
    synthetic = 0
    for assignment in assignments:
        engine_assignment = {}
        for role in ROLES:
            slot = assignment[role]
            pool, source = _pool_for(slot, role, unavailable, meta, pool_limit, effective_by_cell.get(slot.cell_id), state.never_pick)
            pools_by_role.setdefault(role, pool)
            pool_sources.setdefault(role, source)
            slot_by_role.setdefault(role, slot)
            if slot.user_id:
                user_id = int(slot.user_id)
            else:
                synthetic += 1
                user_id = _SYNTHETIC_USER_BASE + slot.cell_id if slot.cell_id >= 0 else _SYNTHETIC_USER_BASE + 100 + synthetic
            engine_assignment[role] = {"user_id": user_id, "display_name": slot.display_name or role,
                                       "roles": [role], "champions": {role: pool}}
        engine_assignments.append(engine_assignment)

    for role, source in pool_sources.items():
        if source == "exhausted":
            alerts.append({"level": "warn", "text": f"No prepared {role} pick remains: review a role swap or choose an emergency pick"})
        elif source == "history-fallback":
            alerts.append({"level": "warn", "text": f"{role}: pool exhausted, using account history"})
        elif source == "meta":
            alerts.append({"level": "warn", "text": f"{role}: no pool or history, using current role meta"})

    # The ranking brain is the repository's /league game backend. The engine search is only the
    # fallback when the backend is unavailable or cannot place these five (e.g. pinned roles it
    # never generates).
    use_backend = str(settings.get("ranking", "backend")).casefold() == "backend" and backend.available()
    comps: list[dict] = []
    missing: list = []
    backend_result = None
    backend_pick_order: list[dict] = []
    if use_backend and engine_assignments:
        signups = []
        role_players: dict[str, int] = {}
        for role in ROLES:
            seat = engine_assignments[0][role]
            slot = slot_by_role[role]
            signups.append(backend.signup_for_seat(slot, role, pools_by_role.get(role, []), roster, seat["user_id"]))
            if slot.user_id and slot.role_source in ("client", "config"):
                role_players[role] = int(slot.user_id)
        backend_result = backend.recommend(signups, unavailable=unavailable, locked=our_locked, role_players=role_players,
                                           side=state.side, limit=comps_limit)
        if backend_result and not backend_result.get("recommendations") and role_players:
            # Pinning every seat can leave the search empty: let the backend choose roles itself.
            backend_result = backend.recommend(signups, unavailable=unavailable, locked=our_locked, role_players={},
                                               side=state.side, limit=comps_limit)
        comps = backend.as_comps(backend_result)
        if comps:
            notes.append("ranking: shared /league game backend")
            backend_pick_order = list(comps[0].get("pick_order") or [])
    if not comps:
        comps, missing = engine.recommend_comps(engine_assignments, limit=comps_limit, pool_limit=pool_limit, meta=meta)
        if use_backend:
            notes.append("ranking: engine search (backend returned no composition for these seats)")
    comps.sort(key=lambda c: float(c.get("rank_score", c["score"])), reverse=True)
    if missing:
        notes.append("missing pools: " + ", ".join(missing))

    # ---- which roles are up -------------------------------------------------
    cell_to_role = {p.cell_id: p.role for p in state.our_team}
    picking_now = state.active.kind == "pick" and state.active.ours and bool(state.active.cell_ids)
    if picking_now:
        roles_now = [cell_to_role.get(c) for c in state.active.cell_ids if cell_to_role.get(c)]
    else:
        upcoming = [a for a in state.actions_remaining if a["kind"] == "pick" and a["ours"]]
        roles_now = []
        for a in upcoming[:2]:
            role = cell_to_role.get(a["cell_id"]) if a.get("cell_id") is not None else None
            if role and role not in roles_now:
                roles_now.append(role)
    vacated = [p.role for p in state.our_team if p.locked and p.cell_id not in effective_by_cell and p.role]
    resolved: list[str] = []
    for role in roles_now:
        if role in our_locked and vacated:
            role = vacated.pop(0)
        if role not in resolved:
            resolved.append(role)
    roles_now = [r for r in resolved if r in ROLES and r not in our_locked]
    if not roles_now:
        roles_now = [r for r in ROLES if r not in our_locked]

    enemy_locked = state.enemy_locked()
    enemy_open_roles = set(ROLES) - set(enemy_locked)
    enemy_picks_before_our_next = 0
    for a in state.actions_remaining:
        if a["kind"] == "pick" and a["ours"]:
            break
        if a["kind"] == "pick" and not a["ours"]:
            enemy_picks_before_our_next += 1
    our_picks_remaining = sum(1 for a in state.actions_remaining if a["kind"] == "pick" and a["ours"])
    enemy_picks_remaining = sum(1 for a in state.actions_remaining if a["kind"] == "pick" and not a["ours"])
    # Answers the enemy still has *after* the pick we are ranking: the whole exposure of that pick.
    # Blue B1 faces five, red R5 faces none and is the true counter-pick slot.
    remaining_picks = [a for a in state.actions_remaining if a["kind"] == "pick"]
    first_ours = next((i for i, a in enumerate(remaining_picks) if a["ours"]), None)
    enemy_answers_after = sum(1 for a in remaining_picks[first_ours + 1:] if not a["ours"]) if first_ours is not None else enemy_picks_remaining
    exposure = min(1.0, enemy_answers_after / 3.0)          # 0 = last pick, 1 = three or more enemy answers follow
    counter_slot = enemy_answers_after == 0 and len(enemy_locked) >= 3

    plans = evaluate_plans(getattr(roster, "playbook", []) if roster is not None else [], state, our_locked, unavailable, slot_by_role,
                           {r: list(dict.fromkeys(slot_by_role[r].pool.get(r, []))) for r in ROLES if r in slot_by_role},
                           {key: {role: [e.champion for e in entries] for role, entries in pl.pools.items()}
                            for key, pl in (roster.players.items() if roster is not None else [])})
    committed_id = str(settings.get("committed_plan") or "")
    committed = next((p for p in plans if committed_id and p.get("id") == committed_id), None)
    for p in plans:
        p["committed"] = p is committed
    if committed is not None and committed["viable"]:
        # The team has chosen its draft: only that plan pulls on the picks, and it pulls harder.
        live_plans = [committed]
    else:
        live_plans = [p for p in plans if p["viable"]]
        if committed is not None:
            why_blocked = "; ".join(b["reason"] for b in committed.get("blocked", [])[:2]) or "not viable from here"
            alerts.append({"level": "warn", "text": f"Chosen plan {committed['name']} is blocked: {why_blocked}. Falling back to all plans"})

    # ---- rank immediate picks -----------------------------------------------------
    picks = []
    for role in roles_now:
        scouted, scouting_known = _scouted_for_role(state, role)
        for champion in pools_by_role.get(role, []):
            containing = [c for c in comps if c["champions"].get(role) == champion]
            if containing:
                best = containing[0]
                top3 = containing[:3]
                base = 0.7 * float(best["score"]) + 0.3 * (sum(float(c["score"]) for c in top3) / len(top3))
            else:
                if not comps:
                    continue
                # Conditioned completion: re-run the engine with this champion forced, so the candidate is
                # scored by the same objective (structure + player fit) as the returned compositions.
                conditioned = _conditioned_completions(engine_assignments[0], {role: champion}, meta, limit=4)
                if not conditioned:
                    continue
                best = conditioned[0]
                base = 0.7 * float(best["score"]) + 0.3 * (sum(float(c["score"]) for c in conditioned[:3]) / len(conditioned[:3]))
            flex_roles = [r for r in ROLES if r != role and r not in our_locked and champion in pools_by_role.get(r, [])]
            lane = _lane_edges(role, champion, state.enemy_team, matchups, matchup_quality)
            lane_adj = sum(e["points"] for e in lane if e["locked"]) * (1.2 if counter_slot else 1.0)
            lane_adj = max(-4.0, min(4.0, lane_adj))
            risk, risk_rows, risk_known = 0.0, [], True
            if role in enemy_open_roles and enemy_answers_after:
                risk, risk_rows, risk_known = _counter_risk(role, champion, unavailable, matchups, scouted, scouting_known, scouting_weight, matchup_quality)
            risk_adj = -risk * (0.35 + 0.65 * exposure)
            prio_adj, priority = _priority_adjustment(slot_by_role.get(role), role, champion, priority_weight)
            fam_adj, familiarity = _familiarity_adjustment(slot_by_role.get(role), role, champion, practice)
            plan_adj, plan_hit = plan_bonus(live_plans, role, champion, plan_weight)
            flex_adj = round(0.75 * exposure, 2) if flex_roles else 0.0      # a flex pick hides our role plan while answers remain
            if fam_adj < 0 and lane_adj > 0:
                lane_adj = lane_adj * 0.5                                     # a favourable unfamiliar matchup cannot cancel a developing penalty
            rank_value = float(best.get("rank_score", best["score"])) if containing else base
            score = max(0.0, min(100.0, base + lane_adj + risk_adj + prio_adj + fam_adj + plan_adj + flex_adj))
            rank = rank_value + (base - float(best["score"]) if containing else 0.0) + lane_adj + risk_adj + prio_adj + fam_adj + plan_adj + flex_adj
            fit = (best.get("player_fit_details") or {}).get(role, {})
            picks.append({
                "role": role, "champion": champion, "champion_id": champ_map.id_for(champion),
                "score": round(score, 1), "rank": round(rank, 3), "comp_score": round(float(best["score"]), 1),
                "familiarity": familiarity, "familiarity_adjustment": fam_adj, "risk_known": risk_known,
                "flex": len(containing), "flex_roles": flex_roles,
                "lane": lane, "lane_adjustment": round(lane_adj, 1),
                "counter_risk": risk, "counter_risk_by": risk_rows, "blind_safe": risk_known and risk < 0.5,
                "exposure": round(exposure, 2), "counter_slot": counter_slot, "flex_adjustment": flex_adj,
                "priority": priority, "priority_adjustment": prio_adj,
                "plan": {"id": plan_hit["id"], "name": plan_hit["name"], "progress": plan_hit["progress"], "bonus": plan_adj} if plan_hit else None,
                "best_comp": best["champions"], "team_plan": best.get("team_plan"),
                "strengths": best.get("strengths", []), "gaps": best.get("gaps", []),
                "source": pool_sources.get(role, "submitted"),
                "player": slot_by_role[role].display_name if role in slot_by_role else role,
                "player_fit": fit if isinstance(fit, dict) else {},
                "why": _why(champion, best, lane, risk, risk_rows, pool_sources.get(role, "submitted"), priority, flex_roles,
                            plan_hit["name"] if plan_hit else None, familiarity, risk_known),
            })
    # Emergency (exhausted-pool) picks rank after every prepared pick for a role that is up.
    picks.sort(key=lambda p: (p["source"] != "exhausted", p["rank"], p["flex"]), reverse=True)

    # ---- bans ------------------------------------------------------------------------
    planned = dict(comps[0]["champions"]) if comps else dict(our_locked)
    pick_order = []
    for cell in state.our_pick_cells:
        role = cell_to_role.get(cell)
        if role and role not in {r for r, _ in pick_order}:
            pick_order.append((role, planned.get(role, "")))
    if not pick_order:
        pick_order = [(r, planned.get(r, "")) for r in ROLES]
    try:
        ban_rows = engine.counter_ban_recommendations(planned, pick_order, state.side, unavailable=unavailable,
                                                      matchup_data=matchups, meta=meta, limit=max(bans_limit * 2, 8))
    except Exception:  # noqa: BLE001
        ban_rows = []
    bans: dict[str, dict] = {}
    # Our prepared next picks: banning one of them costs us a completion.
    our_next: set[str] = set()
    for plan in live_plans:
        our_next.update(n["champion"] for n in plan["next"])
    for role in [r for r in ROLES if r not in our_locked]:
        our_next.update(pools_by_role.get(role, [])[:2])
    for row in ban_rows:
        reasons = []
        for r in row.get("reasons", []):
            if r.get("reason"):
                reasons.append(f"{r['reason']} (vs our {r['our_champion']})")
            else:
                reasons.append(f"beats our {r['our_champion']} {ROLE_SHORT.get(r['role'], r['role'])} by {r['counter_edge']:+.1f} over {r['games']} games")
        bans[row["champion"]] = {"champion": row["champion"], "champion_id": champ_map.id_for(row["champion"]),
                                 "score": float(row["score"]), "engine_score": float(row["score"]), "reasons": reasons,
                                 "tags": []}

    def ban_row(champion: str) -> dict:
        return bans.setdefault(champion, {"champion": champion, "champion_id": champ_map.id_for(champion), "score": 0.0,
                                          "engine_score": 0.0, "reasons": [], "tags": []})

    # Scouting: enemy players' known champions in roles they still have open. One scenario per
    # (player, champion): a champion listed for several roles counts once, at its best strength.
    likely_enemy: list[dict] = []
    scouted_strength: dict[str, float] = {}
    for enemy in state.enemy_team:
        if not enemy.scouted or enemy.locked:
            continue
        pools = enemy.scouted.get("pools", {})
        priorities = enemy.scouted.get("priorities", {})
        roles = [enemy.role] if enemy.role in ROLES and enemy.role in enemy_open_roles else sorted(enemy_open_roles)
        pool_size = max(1, len({c for r in roles for c in pools.get(r, [])} | set(pools.get("Any", []))))
        seen_here: set[str] = set()
        for role in roles:
            for champion in list(pools.get(role, [])) + list(pools.get("Any", [])):
                if champion in unavailable or champion in seen_here:
                    continue
                seen_here.add(champion)
                priority = priorities.get(role, {}).get(champion) or priorities.get("Any", {}).get(champion) or 0
                strength = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.45, 5: 0.3}.get(priority, 0.5)
                # Narrow enemy pools make each champion likelier and its loss more damaging.
                strength = min(1.0, strength * (1.0 + 0.5 / pool_size))
                likely_enemy.append({"champion": champion, "champion_id": champ_map.id_for(champion), "role": role,
                                     "player": enemy.scouted.get("name") or enemy.display_name, "priority": priority,
                                     "strength": round(strength, 2)})
                scouted_strength[champion] = max(scouted_strength.get(champion, 0.0), strength)
                row = ban_row(champion)
                label = f"{enemy.scouted.get('name') or enemy.display_name}'s {ROLE_SHORT.get(role, role)} pick"
                if priority == 1:
                    label += " (signature)"
                if label not in row["reasons"]:
                    row["reasons"].append(label)
                if "scouted" not in row["tags"]:
                    row["tags"].append("scouted")
    seen_likely: set[tuple[str, str]] = set()
    likely_unique = []
    for row in sorted(likely_enemy, key=lambda r: (-r["strength"], r["champion"])):
        key = (row["champion"], row["role"])
        if key in seen_likely:
            continue
        seen_likely.add(key)
        likely_unique.append(row)

    ban_roles_by_name: dict[str, set[str]] = {}
    for row in ban_rows:
        ban_roles_by_name.setdefault(row["champion"], set()).update(
            str(x.get("role")) for x in row.get("reasons", []) if isinstance(x, dict) and x.get("role"))
    # Scouting coverage: when we know what the enemy's players play in a role, a counter-ban that
    # none of them plays is unlikely to be picked. It is marked, and its probability drops below.
    scouted_roles: dict[str, set[str]] = {}
    for enemy in state.enemy_team:
        if enemy.scouted:
            pools = enemy.scouted.get("pools", {})
            everything = set().union(*[set(v) for v in pools.values()]) if pools else set()
            for r in ROLES:
                if pools.get(r) or pools.get("Any"):
                    scouted_roles.setdefault(r, set()).update(pools.get(r, []), pools.get("Any", []))
            if enemy.role in ROLES and everything:
                scouted_roles.setdefault(enemy.role, set()).update(everything)
    if scouted_roles:
        for row in ban_rows:
            name = row["champion"]
            roles_hit = {r["role"] for r in row.get("reasons", []) if r.get("role")}
            known = any(name in scouted_roles.get(r, set()) for r in roles_hit) if roles_hit else any(name in pool for pool in scouted_roles.values())
            covered = all(r in scouted_roles for r in roles_hit) if roles_hit else bool(scouted_roles)
            if covered and not known and name in bans:
                bans[name]["reasons"].append("not seen in their known pools")
                bans[name]["tags"].append("unlikely")

    # Team policy bans and enemy hovers (a hover raises the chance they pick it; no separate additive bonus).
    policy_weight: dict[str, float] = {}
    for policy in (roster.ban_priority if roster is not None else []):
        champion = policy["champion"]
        if champion in unavailable:
            continue
        row = ban_row(champion)
        policy_weight[champion] = max(policy_weight.get(champion, 0.0), float(policy.get("weight", 1.0)))
        row["reasons"].append("team ban policy" + (f": {policy['note']}" if policy.get("note") else ""))
        row["tags"].append("policy")
    hovered: set[str] = set()
    for enemy in state.enemy_team:
        if enemy.hover and enemy.hover not in unavailable:
            hovered.add(enemy.hover)
            row = ban_row(enemy.hover)
            row["reasons"].append(f"enemy {ROLE_SHORT.get(enemy.role, 'player') if enemy.role else 'player'} is hovering it")
            row["tags"].append("hover")
            alerts.append({"level": "info", "text": f"Enemy hovering {enemy.hover}"})

    # Expected marginal damage prevented: B = p * (2.0*C + 1.2*H + 2.0*N) - 2.5*O + 0.3*P, every term 0..1.
    max_engine = max([b["engine_score"] for b in bans.values()] + [1.0])
    narrow_roles = {r for r in ROLES if r not in our_locked and 0 < len(pools_by_role.get(r, [])) <= 2}
    for name, row in bans.items():
        strength = scouted_strength.get(name, 0.0)
        if name in hovered:
            prob = 0.9
        elif strength:
            prob = 0.35 + 0.6 * strength
        elif "unlikely" in row["tags"]:
            prob = 0.15                               # the roles it fits are scouted and nobody there plays it
        else:
            prob = 0.3                                # population prior without scouting
        if name in policy_weight:
            prob = max(prob, 0.5)                     # the team has seen this one coming before
        comfort_drop = strength
        harm = min(1.0, row["engine_score"] / max_engine) if row["engine_score"] > 0 else 0.0
        narrow = 1.0 if ban_roles_by_name.get(name, set()) & narrow_roles else 0.0
        opportunity = 1.0 if name in our_next else 0.0
        pref = min(1.0, policy_weight.get(name, 0.0) / 3.0)
        row["score"] = round(prob * (2.0 * comfort_drop + 1.2 * harm + 2.0 * narrow) - 2.5 * opportunity + 1.0 * pref, 2)
        row["probability"] = round(prob, 2)
        if narrow:
            row["reasons"].append("protects a narrow prepared pool of ours")
        if opportunity:
            row["reasons"].append("we may want it ourselves")
            row["tags"].append("ours")
        if "unlikely" in row["tags"] and strength:
            row["tags"].remove("unlikely")
    ban_list = sorted(bans.values(), key=lambda b: (b["score"], b["engine_score"]), reverse=True)
    for b in ban_list:
        b["reasons"] = b["reasons"][:3]
    ban_list = [b for b in ban_list if b["score"] > 0 or not any(b2["score"] > 0 for b2 in ban_list)][:bans_limit]

    # ---- counters to enemy locks ---------------------------------------------------------
    counters = []
    for enemy in state.enemy_team:
        if not enemy.locked or not enemy.role or enemy.role in our_locked:
            continue
        role = enemy.role
        responses = matchups.get("matchups", {}).get(role, {}).get(enemy.locked, {}).get("responses", {})
        ranked = []
        for champion in pools_by_role.get(role, []):
            entry = responses.get(champion)
            if not entry:
                continue
            ranked.append({"champion": champion, "champion_id": champ_map.id_for(champion),
                           "advantage": round(float(entry.get("response_advantage", 0.0)), 2), "games": int(entry.get("games", 0))})
        ranked.sort(key=lambda r: (r["advantage"], r["games"]), reverse=True)
        counters.append({"enemy": enemy.locked, "enemy_id": champ_map.id_for(enemy.locked), "role": role,
                         "role_source": enemy.role_source, "responses": ranked[:4], "has_data": bool(responses)})

    # ---- flex picks still open ----------------------------------------------------------------
    flex: dict[str, set[str]] = {}
    open_roles = [r for r in ROLES if r not in our_locked]
    for role in open_roles:
        for champion in pools_by_role.get(role, []):
            flex.setdefault(champion, set()).add(role)
    flex_rows = [{"champion": c, "champion_id": champ_map.id_for(c), "roles": [r for r in ROLES if r in rs]}
                 for c, rs in flex.items() if len(rs) >= 2]
    flex_rows.sort(key=lambda r: (-len(r["roles"]), r["champion"]))

    # ---- team score so far -------------------------------------------------------------------------
    team = engine.score_comp(our_locked, meta) if len(our_locked) == 5 else None
    enemy_team = engine.score_comp(enemy_locked, meta) if len(enemy_locked) == 5 else None
    draft_complete = len(our_locked) == 5

    # ---- pick order for the target five -----------------------------------------------------------------
    pools_all = {r: list(dict.fromkeys(slot_by_role[r].pool.get(r, []))) for r in ROLES if r in slot_by_role}
    target: dict[str, str] = {}
    order_source = ""
    # The chosen plan when there is one; otherwise the leading composition the ranked picks come from,
    # so the order and the headline never describe different fives.
    source_plan = committed if committed is not None and committed["viable"] else None
    if source_plan is not None:
        target = {m["role"]: m["champion"] for m in source_plan["matched"]}
        target.update({n["role"]: n["champion"] for n in source_plan["next"]})
        order_source = f"plan:{source_plan['name']}"
    elif comps:
        target = dict(comps[0]["champions"])
        order_source = "comp"
    target.update(our_locked)
    order_rows = plan_pick_order(target, state.side, meta, matchups, pools_all, our_locked) if len(target) == 5 else []
    if order_rows and source_plan is None and backend_pick_order and {r["champion"] for r in backend_pick_order} == set(target.values()):
        # The backend's roster-real _pro_pick_order decides the sequence; our rows only add the tags.
        by_champion = {r["champion"]: r for r in order_rows}
        revealed = [r for r in order_rows if r["locked"]]
        ordered = revealed + [by_champion[b["champion"]] for b in backend_pick_order if not by_champion[b["champion"]]["locked"]]
        letter = "R" if str(state.side).casefold() == "red" else "B"
        for i, row in enumerate(ordered):
            row["slot"] = f"{letter}{i + 1}"
            row["status"] = "locked" if row["locked"] else ("next" if i == len(revealed) else "later")
        order_rows = ordered
        order_source = "backend"
    if order_rows:
        # The current slot should reveal what the order schedules now, not what it holds for later.
        scheduled_now = {(r["role"], r["champion"]) for r in order_rows if r["status"] == "next"}
        held = {(r["role"], r["champion"]) for r in order_rows if r["status"] == "later"}
        for pick in picks:
            key = (pick["role"], pick["champion"])
            if key in scheduled_now:
                pick["order_adjustment"] = 1.0
                pick["why"].append("pick order: reveal now")
            elif key in held:
                pick["order_adjustment"] = -1.0
                pick["why"].append("pick order: better held for a later slot")
            else:
                pick["order_adjustment"] = 0.0
            pick["score"] = round(max(0.0, min(100.0, pick["score"] + pick["order_adjustment"])), 1)
            pick["rank"] = round(pick["rank"] + pick["order_adjustment"], 3)
        picks.sort(key=lambda p: (p["source"] != "exhausted", p["rank"], p["flex"]), reverse=True)

    # ---- what to do now -------------------------------------------------------------------------------
    headline, phase_kind = _headline(state, picks, ban_list, roles_now, picking_now, draft_complete, team, enemy_team)
    if picking_now and picks and picks[0]["counter_risk"] >= 2.0 and enemy_picks_before_our_next:
        alerts.append({"level": "warn", "text": f"{picks[0]['champion']} can still be answered by " +
                       ", ".join(r["enemy"] for r in picks[0]["counter_risk_by"][:2])})
    for slot in state.our_team:
        if slot.hover and not slot.locked and slot.role in roles_now:
            hovered = next((p for p in picks if p["champion"] == slot.hover and p["role"] == slot.role), None)
            if hovered is None:
                alerts.append({"level": "warn", "text": f"{slot.display_name} is hovering {slot.hover}, which is not in the ranked pool"})
            elif picks and picks[0]["champion"] != slot.hover and picks[0]["score"] - hovered["score"] >= 6:
                alerts.append({"level": "info", "text": f"{slot.hover} is {picks[0]['score'] - hovered['score']:.0f} below {picks[0]['champion']} for {ROLE_SHORT.get(slot.role, slot.role)}"})

    return {
        "headline": headline, "phase_kind": phase_kind,
        "roles_now": roles_now, "picking_now": picking_now, "draft_complete": draft_complete,
        "our_locked": our_locked, "enemy_locked": enemy_locked, "swaps": swaps,
        "picks": picks[: max(limit, 1) * 2], "bans": ban_list, "counters": counters,
        "plans": plans[:6],
        "committed_plan": {"id": committed["id"], "name": committed["name"], "viable": committed["viable"]} if committed else None,
        "pick_order": order_rows, "pick_order_source": order_source,
        "likely_enemy": likely_unique[:8], "flex": flex_rows[:6],
        "comps": [
            {"champions": c["champions"], "score": c["score"], "team_plan": c.get("team_plan"),
             "strengths": c.get("strengths", []), "gaps": c.get("gaps", []),
             "players": {r: c["players"][r]["display_name"] for r in ROLES}}
            for c in comps[:5]
        ],
        "team": team, "enemy_team_score": enemy_team,
        "pool_sources": pool_sources, "notes": notes, "alerts": alerts,
        "our_picks_remaining": our_picks_remaining, "enemy_picks_remaining": enemy_picks_remaining,
        "compute_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _headline(state: DraftState, picks: list[dict], bans: list[dict], roles_now: list[str], picking_now: bool,
              complete: bool, team: dict | None, enemy_team: dict | None) -> tuple[str, str]:
    if state.phase in ("IDLE",):
        return "Waiting for champion select", "idle"
    if complete:
        ours = f"{team['score']:.0f}" if team else "?"
        theirs = f"{enemy_team['score']:.0f}" if enemy_team else "?"
        return f"Draft locked. Comp {ours} vs {theirs}", "complete"
    top = picks[0] if picks else None
    lead = f"{top['champion']} ({ROLE_SHORT.get(top['role'], top['role'])})" if top else ""
    roles_text = " / ".join(ROLE_SHORT.get(r, r) for r in roles_now) if len(roles_now) <= 2 else "open roles"
    if state.active.kind == "ban" and state.active.ours:
        if bans:
            return f"Ban now: {bans[0]['champion']}", "ban"
        return "Ban now", "ban"
    if state.active.kind == "ban" and not state.active.ours:
        if top:
            return f"Enemy banning. Next pick leads: {lead}", "wait"
        return "Enemy banning", "wait"
    if picking_now:
        if top:
            return f"Pick now: {lead}", "pick"
        return f"Pick now ({roles_text})", "pick"
    if state.active.kind == "pick" and not state.active.ours:
        if top:
            return f"Enemy picking. Prepare {lead}", "wait"
        return "Enemy picking", "wait"
    if state.phase == "PLANNING":
        if bans:
            return f"Planning. First ban: {bans[0]['champion']}", "plan"
        return "Planning phase", "plan"
    if top:
        return f"Next: {lead}", "wait"
    return "Draft in progress", "wait"


def _why(champion: str, best: dict, lane: list[dict], risk: float, risk_rows: list[dict], source: str,
         priority: int, flex_roles: list[str], plan_name: str | None = None, familiarity: str = "",
         risk_known: bool = True) -> list[str]:
    why = []
    if source == "exhausted":
        why.append("emergency pick: no prepared champion remains")
    if plan_name:
        why.append(f"{'completes' if best.get('plan_complete') else 'advances'} plan {plan_name}")
    if familiarity == "comfort":
        why.append("confirmed comfort")
    elif familiarity == "developing":
        why.append("developing: practice assignment")
    elif familiarity == "refresh":
        why.append("refresh pick")
    if priority == 1:
        why.append("preferred pick")
    elif priority >= 4:
        why.append(f"low preference (p{priority})")
    for edge in lane:
        sign = "favourable" if edge["adjusted"] > 0.2 else "unfavourable" if edge["adjusted"] < -0.2 else "even"
        who = "locked" if edge["locked"] else "hovered"
        why.append(f"{sign} matchup signal vs {who} {edge['enemy']}: {edge['adjusted']:+.1f} adjusted pp, {edge['games']} games")
    if risk >= 0.5 and risk_rows:
        who = ", ".join(r["enemy"] + ("*" if r.get("scouted") else "") for r in risk_rows[:2])
        why.append("answerable by " + who)
    elif not lane and not risk_known:
        why.append("blind-pick safety unknown: limited matchup coverage")
    elif not lane:
        why.append("blind-safe")
    if flex_roles:
        why.append("flex " + "/".join(ROLE_SHORT.get(r, r) for r in flex_roles))
    strengths = best.get("strengths") or []
    if strengths:
        why.append(strengths[0])
    gaps = best.get("gaps") or []
    if gaps:
        why.append("gap: " + gaps[0])
    if source in ("history", "history-fallback", "meta"):
        why.append("from account history" if source.startswith("history") else "from role meta")
    return why[:5]


def hypothetical_lock(state: DraftState, role: str, champion: str) -> DraftState:
    """Copy of ``state`` with ``champion`` locked in ``role`` (used for cache pre-warming)."""
    import copy

    hypothetical = copy.deepcopy(state)
    slot = next((p for p in hypothetical.our_team if p.role == role and not p.locked), None)
    if slot is not None:
        slot.locked, slot.hover = champion, None
    for i, a in enumerate(hypothetical.actions_remaining):
        if a["kind"] == "pick" and a["ours"]:
            del hypothetical.actions_remaining[i]
            break
    hypothetical.active = ActiveAction("none", False, [])
    return hypothetical
