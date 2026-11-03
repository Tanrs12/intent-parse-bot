from __future__ import annotations

from adaptive_draft import select_adaptive_comp
from league_opgg_full_reference import full_for_enemy

import copy
from typing import Iterable, Mapping

import team_sync
from league_flex_core import (
    _attach_role_alternatives,
    _conditional_ban_branches,
    _filter_unavailable,
    _pro_pick_order,
    _select_diverse_recommendations,
    generate_team_options,
)
from league_comp_engine import (
    ROLES,
    LIVE_PATCH,
    attach_learn_suggestions,
    counter_ban_recommendations,
    load_meta,
    DRAFT_SPECIFIC_COUNTERS,
    recommend_comps,
    _mastery_points_for,
    _champion_history_for,
    load_recent_match_index,
)
from league_source_stats import source_stat_scope


def _verified_champion_experience(uid: int, champion: str, role: str) -> bool:
    """A mere hover is not proof that a player can pilot the champion."""
    history = _champion_history_for(uid, champion) or {}
    recent = (load_recent_match_index().get(uid, {})
              .get("by_role_champion", {}).get(role, {}).get(champion, {}))
    return (
        _mastery_points_for(uid, champion) >= 30000
        or int(history.get("games", 0) or 0) >= 3
        or float(recent.get("effective_games", 0) or 0) >= 2.0
    )


def _constrain_assignments(
    assignments: list[dict[str, dict]],
    *,
    locked_champions: Mapping[str, str] | None = None,
    hovered_champions: Mapping[str, str] | None = None,
    role_players: Mapping[str, int] | None = None,
) -> list[dict[str, dict]]:
    """Apply live-draft locks without changing the core /league game scorer.

    ``locked_champions`` maps role -> champion already locked by our team.
    ``hovered_champions`` maps role -> current ally hover. Hovers are soft
    candidate signals, never constraints: League players can change their mind.
    A hovered champion may be evaluated, but alternatives must remain available.
    ``role_players`` optionally maps role -> Discord user id when the live client
    already knows which player is occupying that role.
    """
    locked_champions = dict(locked_champions or {})
    hovered_champions = dict(hovered_champions or {})
    role_players = {role: int(uid) for role, uid in (role_players or {}).items()}
    constrained: list[dict[str, dict]] = []

    for assignment in assignments:
        row: dict[str, dict] = {}
        valid = True
        for role in ROLES:
            player = copy.deepcopy(assignment[role])
            wanted_uid = role_players.get(role)
            if wanted_uid is not None and int(player["user_id"]) != wanted_uid:
                valid = False
                break

            locked = locked_champions.get(role)
            hovered = None if locked else hovered_champions.get(role)
            if locked:
                # Only an actual lock-in can constrain a role to one champion.
                selected = list(player.get("champions", {}).get(role, []))
                pools = dict(player.get("champions", {}))
                pools[role] = [locked]
                player["champions"] = pools
                player["_forced_live_lock"] = locked not in selected
                player["_forced_live_hover"] = False
                player["_forced_live_champion"] = locked
            elif hovered:
                # Keep the full comfort pool. A real hover outside that pool can be
                # considered, but must compete with alternatives rather than erase them.
                pools = dict(player.get("champions", {}))
                selected = list(pools.get(role, []))
                if hovered not in selected:
                    if _verified_champion_experience(int(player["user_id"]), hovered, role):
                        pools[role] = [hovered, *selected]
                        player["champions"] = pools
                        player["_hover_experience_only"] = hovered
                    else:
                        player["_unverified_live_hover"] = hovered
                player["_forced_live_lock"] = False
                player["_forced_live_hover"] = True
                player["_live_hover"] = hovered
            row[role] = player

        if valid:
            constrained.append(row)

    return constrained


def _pick_group_meta(side: str, position: int) -> dict:
    """Return the strategic draft phase for our Nth planned pick.

    Pick order inside simultaneous pairs is intentionally interchangeable: blue B2/B3 and
    B4/B5, red R1/R2 and R3/R4.  The linear position is still retained for display/ties.
    """
    side_key = str(side or "blue").casefold()
    if side_key == "red":
        groups = [
            (1, "R1/R2", [1, 2]), (1, "R1/R2", [1, 2]),
            (2, "R3/R4", [3, 4]), (2, "R3/R4", [3, 4]),
            (3, "R5", [5]),
        ]
    else:
        groups = [
            (1, "B1", [1]),
            (2, "B2/B3", [2, 3]), (2, "B2/B3", [2, 3]),
            (3, "B4/B5", [4, 5]), (3, "B4/B5", [4, 5]),
        ]
    group, label, slots = groups[max(0, min(4, int(position) - 1))]
    return {"pick_group": group, "pick_group_label": label, "pick_group_slots": slots}


IDENTITY_MAX_GAP = 6.0   # how far an identity-matching draft may trail the best before the engine overrides


def _counterpick_window(side: str, ally_locks: Mapping[str, str],
                        enemy_roles: Mapping[str, str]) -> dict[str, float]:
    """Increase lane weight only during a genuine late solo-lane response.

    Red R5: four ally locks, no enemy answer remaining.
    Blue B4/B5: three ally locks, red R4 already revealed, but R5 remains.
    Unknown enemy roles never create fictional matchup advantages.
    """
    remaining = {r for r in ("Top", "Mid") if r not in ally_locks and enemy_roles.get(r)}
    if not remaining:
        return {}
    if side == "red" and len(ally_locks) == 4:
        return {r: 2.8 if r == "Top" else 2.35 for r in remaining}
    if side == "blue" and len(ally_locks) == 3:
        return {r: 1.85 if r == "Top" else 1.6 for r in remaining}
    return {}


# Team identities a captain can commit the draft to. Each maps the engine's team_plan labels and
# archetype tags onto one family, so "keep our identity" is a filter the whole party shares.
IDENTITIES: dict[str, dict] = {
    "dive": {"label": "Dive / all-in", "plans": ("dive / all-in",), "archetypes": ("dive",),
             "description": "Mobile engage that collapses on a target; wins by committing first."},
    "poke": {"label": "Poke / siege", "plans": ("poke / siege", "poke + side-lane"), "archetypes": ("poke",),
             "description": "Range and zone control; whittle, then take objectives safely."},
    "front-to-back": {"label": "Front-to-back", "plans": ("scaling front-to-back",), "archetypes": ("front-to-back engage",),
                      "description": "Frontline plus a scaling carry; fight in formation."},
    "protect": {"label": "Protect the carry", "plans": (), "archetypes": ("protect-the-carry",),
                "description": "Peel and shields around one hypercarry."},
    "pick": {"label": "Pick / catch", "plans": (), "archetypes": ("pick",),
             "description": "Control and burst; win by catching one target."},
    "tempo": {"label": "Tempo skirmish", "plans": ("tempo skirmish",), "archetypes": (),
              "description": "Early-game skirmishing with a strong mid-jungle."},
    "split": {"label": "Side-lane / 1-3-1", "plans": (), "archetypes": ("1-3-1 / split-pressure option",),
              "description": "A split-push threat with a team that can hold four-versus-four."},
}


def draft_identity(rec: Mapping) -> str:
    """The identity family a scored composition belongs to (``balanced`` when none fits)."""
    plan = str(rec.get("team_plan") or "")
    archetypes = set(rec.get("archetypes") or [])
    for key, spec in IDENTITIES.items():
        if plan in spec["plans"]:
            return key
    for key, spec in IDENTITIES.items():
        if archetypes & set(spec["archetypes"]):
            return key
    return "balanced"


def _signature(champions: Mapping[str, str] | None) -> str:
    champions = champions or {}
    return "|".join(str(champions.get(role) or "?") for role in ROLES)


@source_stat_scope  # FLEXERS: one live calculation stats each source file once, not per pairing
def recommend_live_drafts(
    signups: list[dict],
    *,
    unavailable: Iterable[str] | None = None,
    locked_champions: Mapping[str, str] | None = None,
    hovered_champions: Mapping[str, str] | None = None,
    role_players: Mapping[str, int] | None = None,
    enemy_champions: Iterable[str] | None = None,
    enemy_roles: Mapping[str, str] | None = None,
    side: str = "blue",
    limit: int = 10,
    rotation_token: str | None = None,
    preferred_champions: Mapping[str, str] | None = None,
    chosen_champions: Mapping[str, str] | None = None,
    required_champions: Mapping[str, str] | None = None,
    identity: str | None = None,
    exclude_signatures: Iterable[str] | None = None,
) -> dict:
    """Return live draft recommendations using the same brain as ``/league game``.

    This is the integration entry point for Flexers Draft Coach.  Feed it the
    current five players in the same signup shape used by ``cogs.league_flex``.
    Re-call it after each pick or ban with the updated unavailable set and any
    champions/roles already locked by our team.

    The function deliberately reuses the Discord command's assignment search,
    composition scorer, diversity selection, role alternatives, pick-order logic
    and counter-ban logic instead of maintaining a second ranking model.
    """
    side_key = str(side or "blue").casefold()
    if side_key not in {"blue", "red"}:
        raise ValueError("side must be 'blue' or 'red'")

    locked_champions = dict(locked_champions or {})
    hovered_champions = dict(hovered_champions or {})
    # A captain's hard champion lock (role -> champion) behaves like a lock we have not revealed yet:
    # every composition must contain it, and it stays available to the search.
    required_champions = {role: champ for role, champ in dict(required_champions or {}).items()
                          if role in ROLES and champ and role not in locked_champions}
    identity = str(identity or "").strip().casefold() or None
    if identity not in IDENTITIES:
        identity = None
    excluded = {str(sig) for sig in (exclude_signatures or ()) if sig}
    unavailable_set = {str(champion) for champion in (unavailable or set())}
    unavailable_set -= set(required_champions.values())
    # A stale/illegal hover must never override a real ban or lock. Our own hard locks remain
    # available to the constrained scorer because they are already part of our team.
    hovered_champions = {role: champion for role, champion in hovered_champions.items() if champion and champion not in unavailable_set}
    unavailable_for_search = unavailable_set - set(locked_champions.values())

    filtered = _filter_unavailable(signups, unavailable_for_search)
    # A live League role assignment is authoritative.  The normal eight
    # "best" pre-game layouts can omit the real five even though every player
    # has a legal pool for their assigned role. Do not return zero suggestions
    # (or silently swap teammates) just because the layout ranks ninth.
    pinned = {role: int(uid) for role, uid in (role_players or {}).items()
              if role in ROLES and uid is not None}
    if len(pinned) == len(ROLES) and len(set(pinned.values())) == len(ROLES):
        signup_by_id = {int(p["user_id"]): p for p in filtered}
        if all(uid in signup_by_id for uid in pinned.values()):
            assignments = [{role: copy.deepcopy(signup_by_id[pinned[role]]) for role in ROLES}]
        else:
            assignments = []
    else:
        # A few known live roles can still push the legal layout past eighth.
        # Generating the cheap role permutations is fine; only score a bounded
        # shortlist of layouts that actually match the known live seats.
        options = generate_team_options(filtered, limit=120 if pinned else 8)
        assignments = [row for row in options if all(
            int(row[role]["user_id"]) == uid for role, uid in pinned.items()
        )][:8]
    assignments = _constrain_assignments(
        assignments,
        locked_champions={**required_champions, **locked_champions},
        hovered_champions=hovered_champions,
        role_players=role_players,
    )
    if not assignments:
        return {"recommendations": [], "missing": [], "side": side_key, "identity": identity}

    counter_leverage = _counterpick_window(side_key, locked_champions, dict(enemy_roles or {}))
    candidates, missing = recommend_comps(
        assignments, limit=300, pool_limit=10,
        live_enemy_champions=enemy_champions,
        live_enemy_roles=dict(enemy_roles or {}),
        rotation_token=rotation_token,
        preferred_champions=dict(preferred_champions or {}),
        full_counter_roles=frozenset(counter_leverage),
        lane_leverage=counter_leverage,
    )
    for rec in candidates:
        rec["identity"] = draft_identity(rec)
    if identity:
        # Keep the team's identity: compositions of that family lead while any of them is within a
        # bounded gap of the objectively best draft; a much better off-identity draft still wins.
        best = float(candidates[0].get("score", 0.0) or 0.0) if candidates else 0.0
        matching = [rec for rec in candidates if rec["identity"] == identity]
        if matching and float(matching[0].get("score", 0.0) or 0.0) >= best - IDENTITY_MAX_GAP:
            others = [rec for rec in candidates if rec["identity"] != identity]
            candidates = matching + others
    if excluded:
        # Shake it up: drafts the party has already seen this game fall behind everything unseen.
        fresh = [rec for rec in candidates if _signature(rec.get("champions")) not in excluded]
        stale = [rec for rec in candidates if _signature(rec.get("champions")) in excluded]
        if fresh:
            candidates = fresh + stale
    if side_key == "red" and len(locked_champions) == 4 and counter_leverage:
        # R5 is a single decisive pick, not a new /league-game variety gallery.
        # Show the objectively highest-scoring answers first, including lane
        # counter evidence. Same-role full-pool scoring has already happened.
        selected = candidates[:limit]
    else:
        selected = _select_diverse_recommendations(
            candidates,
            limit=limit,
            core_limit=min(4, limit),
            role_mix_slots=min(2, max(0, limit - min(4, limit))),
            rotation_token=rotation_token,
            # Enemy counterpicks, real locks and a captain-selected identity
            # outrank pregame champion-variety preferences.
            prefer_captain_jungle=not bool(
                identity or enemy_champions or enemy_roles
                or locked_champions or required_champions
            ),
        )
    # A chosen Discord comp is a stable starting plan, not five unearned
    # champion locks. A proven enemy-lane counter can update one unpicked seat
    # if the player actually submitted the counter and team fit remains sound.
    if chosen_champions and len(chosen_champions) == len(ROLES):
        selected = select_adaptive_comp(
            candidates, chosen_champions, enemy_roles=dict(enemy_roles or {}),
            unavailable=unavailable_set, actual_locks=locked_champions,
            identity=identity, limit=limit,
            champion_traits=load_meta().get("champion_traits") or {},
            global_counter_rules=DRAFT_SPECIFIC_COUNTERS,
            statistical_evidence={
                role: full_for_enemy(role, enemy, patch=LIVE_PATCH).get("rows") or []
                for role, enemy in (enemy_roles or {}).items()
                if role in ROLES and enemy
            },
        )
        candidates = selected + [r for r in candidates if r not in selected]
    # Preserve the captain's planned five if still legal and objectively close
    # to the strongest current option; never force it when the enemy draft makes
    # the plan significantly worse. It may naturally appear in selected already.
    if not chosen_champions and preferred_champions and len(preferred_champions) == 5 and candidates:
        exact = next(
            (rec for rec in candidates if all(rec["champions"].get(role) == champ
                                              for role, champ in preferred_champions.items())),
            None,
        )
        if exact and float(exact.get("score", 0)) >= float(candidates[0].get("score", 0)) - 2.5:
            if (float(exact.get("team_plan_score", 0)) >= 6.0
                and float(exact.get("bot_lane_score", 0)) >= 7.0
                and float(exact.get("mid_jungle_score", 0)) >= 7.0
                and not any(rec["champions"] == exact["champions"] for rec in selected)):
                selected = [*selected[:max(0, limit - 1)], exact]
    selected = _attach_role_alternatives(selected, candidates)
    selected = attach_learn_suggestions(selected)

    meta = load_meta()
    recommendations: list[dict] = []
    for rec in selected:
        row = copy.deepcopy(rec)
        order = _pro_pick_order(row, meta, side_key)
        bans = counter_ban_recommendations(
            row["champions"],
            order,
            side_key,
            unavailable=unavailable_set,
            meta=meta,
        )
        row["pick_order"] = [
            {
                "slot": index + 1,
                "role": role,
                "champion": champion,
                **_pick_group_meta(side_key, index + 1),
            }
            for index, (role, champion) in enumerate(order)
        ]
        row["counter_bans"] = bans
        recommendations.append(row)

    return {
        "recommendations": recommendations,
        "missing": missing,
        "side": side_key,
        "hovered_champions": hovered_champions,
        "identity": identity,
        "identities": {key: sum(1 for rec in recommendations if rec.get("identity") == key) for key in IDENTITIES},
    }


def _jsonable(value):
    """Engine rows carry tuples/sets (e.g. pick orders); the app serialises them over HTTP."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=repr)
    return value


def _playable_roles(signup: Mapping) -> list[str]:
    return [role for role in signup.get("roles", []) if signup.get("champions", {}).get(role)]


@source_stat_scope  # FLEXERS: the whole pre-game board stats each source file once
def plan_game(
    signups: list[dict],
    *,
    unavailable: Iterable[str] | None = None,
    rotation_token: str | None = None,
    limit: int = 10,
) -> dict:
    """Build the pre-game draft board for five players, exactly as ``/league game`` does.

    This is the Discord cog's ``send_draft_comps`` + ``_build_draft_visuals`` pipeline
    (same functions, same arguments) so the desktop app can plan a game without the
    bot: layouts, scored pool, diverse selection, role alternatives, learn
    suggestions, then both sides' pick orders, bans and pick-conditional bans.
    Every draft also carries the relay ``control`` body that makes every app follow it.
    """
    if len(signups) != 5:
        raise ValueError("plan_game needs exactly five signed-up players")
    unavailable_set = {str(champion) for champion in (unavailable or set()) if str(champion).strip()}

    signups = _filter_unavailable(signups, unavailable_set)
    # Ten visible drafts need enough legal role layouts before scoring.
    assignments = generate_team_options(signups, limit=24, require_pools=True)
    coverage = {
        "missing_pools": [
            f"{s['display_name']} {role}"
            for s in signups
            for role in s.get("roles", [])
            if not s.get("champions", {}).get(role)
        ],
        "single_role": [
            s["display_name"] + " (" + ", ".join(_playable_roles(s)) + ")"
            for s in signups
            if len(_playable_roles(s)) == 1
        ],
    }
    if not assignments:
        counts = {role: sum(role in s.get("roles", []) for s in signups) for role in ROLES}
        return {
            "recommendations": [],
            "missing": [],
            "layouts": 0,
            "coverage": {**coverage, "role_counts": counts},
            "side_notes": None,
            "reason": "I can't build a legal five-player role assignment yet. Coverage: "
                      + " • ".join(f"{role}: {counts[role]}" for role in ROLES),
        }

    candidate_pool, missing = recommend_comps(
        assignments,
        limit=300,
        pool_limit=10,
        # The pre-game FLEX gallery can trial deliberately submitted off-role champions.
        prepared_flex_gallery=True,
        rotation_token=rotation_token,
    )
    recommendations = _select_diverse_recommendations(
        candidate_pool,
        limit=limit,
        core_limit=4,
        role_mix_slots=6,
        rotation_token=rotation_token,
    )
    recommendations = _attach_role_alternatives(recommendations, candidate_pool)
    recommendations = attach_learn_suggestions(recommendations)
    if not recommendations:
        return {
            "recommendations": [],
            "missing": list(missing),
            "layouts": 0,
            "coverage": coverage,
            "side_notes": None,
            "reason": "I have legal role assignments, but not enough role-specific champion "
                      "picks to build a five-champion draft yet.",
        }

    meta = load_meta()
    drafts: list[dict] = []
    for index, source in enumerate(recommendations, start=1):
        rec = copy.deepcopy(source)
        rec["index"] = index
        rec["label"] = f"Draft {index}"
        rec["identity"] = draft_identity(rec)
        sides: dict[str, dict] = {}
        for side in ("blue", "red"):
            order = _pro_pick_order(rec, meta, side)
            bans = counter_ban_recommendations(
                rec["champions"], order, side, unavailable=unavailable_set, meta=meta, limit=5
            )
            sides[side] = {
                "pick_order": [
                    {
                        "slot": slot + 1,
                        "role": role,
                        "champion": champion,
                        **_pick_group_meta(side, slot + 1),
                    }
                    for slot, (role, champion) in enumerate(order)
                ],
                "bans": bans,
                "conditional_bans": _conditional_ban_branches(rec, meta, side, unavailable_set, bans),
            }
        rec["sides"] = sides
        rec["control"] = team_sync.draft_control_payload(rec, label=rec["label"], source="app")
        drafts.append(rec)

    # A player may select an off-role without submitting champions for it; the
    # scorer cannot invent that pool, so the distinct layout count is worth showing.
    layouts = {
        tuple(int(rec["players"][role]["user_id"]) for role in ROLES)
        for rec in drafts
    }
    return _jsonable({
        "recommendations": drafts,
        "missing": list(missing),
        "layouts": len(layouts),
        "coverage": coverage,
        "side_notes": None,
    })
