from __future__ import annotations

import copy
from typing import Iterable, Mapping

from cogs.league_flex import (
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
)


def _constrain_assignments(
    assignments: list[dict[str, dict]],
    *,
    locked_champions: Mapping[str, str] | None = None,
    role_players: Mapping[str, int] | None = None,
) -> list[dict[str, dict]]:
    """Apply live-draft locks without changing the core /league game scorer.

    ``locked_champions`` maps role -> champion already locked by our team.
    ``role_players`` optionally maps role -> Discord user id when the live client
    already knows which player is occupying that role.
    """
    locked_champions = dict(locked_champions or {})
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

            champion = locked_champions.get(role)
            if champion:
                selected = list(player.get("champions", {}).get(role, []))
                if champion not in selected:
                    valid = False
                    break
                pools = dict(player.get("champions", {}))
                pools[role] = [champion]
                player["champions"] = pools
            row[role] = player

        if valid:
            constrained.append(row)

    return constrained


def recommend_live_drafts(
    signups: list[dict],
    *,
    unavailable: Iterable[str] | None = None,
    locked_champions: Mapping[str, str] | None = None,
    role_players: Mapping[str, int] | None = None,
    side: str = "blue",
    limit: int = 6,
    rotation_token: str | None = None,
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
    unavailable_set = {str(champion) for champion in (unavailable or set())}
    # Our own locked champions must remain available to the constrained scorer.
    unavailable_for_search = unavailable_set - set(locked_champions.values())

    filtered = _filter_unavailable(signups, unavailable_for_search)
    assignments = generate_team_options(filtered, limit=8)
    assignments = _constrain_assignments(
        assignments,
        locked_champions=locked_champions,
        role_players=role_players,
    )
    if not assignments:
        return {"recommendations": [], "missing": [], "side": side_key}

    candidates, missing = recommend_comps(assignments, limit=300, pool_limit=10)
    selected = _select_diverse_recommendations(
        candidates,
        limit=limit,
        core_limit=min(4, limit),
        role_mix_slots=min(2, max(0, limit - min(4, limit))),
        rotation_token=rotation_token,
    )
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
            {"slot": index + 1, "role": role, "champion": champion}
            for index, (role, champion) in enumerate(order)
        ]
        row["counter_bans"] = bans
        recommendations.append(row)

    return {
        "recommendations": recommendations,
        "missing": missing,
        "side": side_key,
    }
