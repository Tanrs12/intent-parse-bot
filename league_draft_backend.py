from __future__ import annotations

import copy
from typing import Iterable, Mapping

from league_flex_core import (
    _attach_role_alternatives,
    _filter_unavailable,
    _pro_pick_order,
    _select_diverse_recommendations,
    generate_team_options,
)
from league_comp_engine import (
    ROLES,
    attach_learn_suggestions,
    counter_ban_recommendations,
    load_meta,
    recommend_comps,
    _mastery_points_for,
    _champion_history_for,
    load_recent_match_index,
)


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
    unavailable_set = {str(champion) for champion in (unavailable or set())}
    # A stale/illegal hover must never override a real ban or lock. Our own hard locks remain
    # available to the constrained scorer because they are already part of our team.
    hovered_champions = {role: champion for role, champion in hovered_champions.items() if champion and champion not in unavailable_set}
    unavailable_for_search = unavailable_set - set(locked_champions.values())

    filtered = _filter_unavailable(signups, unavailable_for_search)
    assignments = generate_team_options(filtered, limit=8)
    assignments = _constrain_assignments(
        assignments,
        locked_champions=locked_champions,
        hovered_champions=hovered_champions,
        role_players=role_players,
    )
    if not assignments:
        return {"recommendations": [], "missing": [], "side": side_key}

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
    selected = _select_diverse_recommendations(
        candidates,
        limit=limit,
        core_limit=min(4, limit),
        role_mix_slots=min(2, max(0, limit - min(4, limit))),
        rotation_token=rotation_token,
    )
    # Preserve the captain's planned five if still legal and objectively close
    # to the strongest current option; never force it when the enemy draft makes
    # the plan significantly worse. It may naturally appear in selected already.
    if preferred_champions and len(preferred_champions) == 5 and candidates:
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
    }
