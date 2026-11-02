"""Keep a Discord-selected five as the starting plan; adapt only for proven lane counters.

The draft engine still owns player pools, champion legality, identity and full
composition scoring. This selector never invents a pick or changes a role.
"""
from __future__ import annotations

from typing import Mapping

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")


def _signature(row: dict) -> tuple[str, ...]:
    return tuple(str((row.get("champions") or {}).get(role) or "") for role in ROLES)


def select_adaptive_comp(
    candidates: list[dict],
    chosen: Mapping[str, str],
    *,
    enemy_roles: Mapping[str, str] | None = None,
    unavailable: set[str] | None = None,
    actual_locks: Mapping[str, str] | None = None,
    identity: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Select a stable, credible counter completion among *scored* pool-legal comps.

    Starting comp wins until an enemy lane reveal provides an evidenced matchup
    improvement. One optional counter swap at a time; bans/taken picks can
    force more. No speculative lane inference. The actual League picks always win.
    """
    if not candidates:
        return []
    original = {role: str(chosen.get(role) or "") for role in ROLES}
    if not all(original.values()):
        return candidates[:limit]
    known = {role: champ for role, champ in (enemy_roles or {}).items() if role in ROLES and champ}
    gone = set(unavailable or ())
    locked = {r: c for r, c in (actual_locks or {}).items() if r in ROLES}
    forced = {r for r in ROLES if original[r] in gone and locked.get(r) != original[r]}

    def changed(rec):
        values = rec.get("champions") or {}
        return {role for role in ROLES if values.get(role) != original[role]}

    eligible = [
        row for row in candidates
        if all(row.get("champions", {}).get(role) == champ for role, champ in locked.items())
        and not any(row.get("champions", {}).get(role) in gone for role in ROLES if role not in locked)
    ]
    if not eligible:
        return candidates[:limit]
    # The closest legal completion anchors the selected plan; not whatever the
    # global meta model currently places at #1.
    closest = min(len(changed(row)) for row in eligible)
    baselines = [row for row in eligible if len(changed(row)) == closest]
    if identity:
        matching = [row for row in baselines if row.get("identity") == identity]
        if matching:
            baselines = matching
    base = max(baselines, key=lambda row: (float(row.get("score", 0)), float(row.get("rank_score", 0))))
    current = base
    explanatory = {}
    # Never counter a lane without an actual role identification. Only switch
    # an unpicked slot if matchup evidence for *that same role* improves.
    if known:
        comparable = []
        base_edges = {
            e.get("role"): e for e in (base.get("live_lane_edges") or [])
            if isinstance(e, dict)
        }
        base_score = float(base.get("score", 0))
        for row in eligible:
            swaps = changed(row) - forced
            if len(swaps) != 1 or not swaps.issubset(set(known) - set(locked)):
                continue
            if len(changed(row)) > max(1, len(forced) + 1):
                continue
            if identity and row.get("identity") != identity and base.get("identity") == identity:
                continue
            if float(row.get("score", 0)) < base_score - 4.0:
                continue
            if float(row.get("team_plan_score", 0)) < float(base.get("team_plan_score", 0)) - 0.8:
                continue
            if float(row.get("bot_lane_score", 0)) < float(base.get("bot_lane_score", 0)) - 1.0:
                continue
            if float(row.get("mid_jungle_score", 0)) < float(base.get("mid_jungle_score", 0)) - 1.0:
                continue
            role = next(iter(swaps))
            edge = next((e for e in (row.get("live_lane_edges") or [])
                         if isinstance(e, dict) and e.get("role") == role
                         and e.get("enemy") == known[role]), None)
            if not edge:
                continue
            old = base_edges.get(role) or {}
            gain = float(edge.get("points", 0)) - float(old.get("points", 0))
            games = int(edge.get("games") or 0)
            # A difference of roughly three quarters of a calibrated matchup
            # point with 100+ games is worth considering, but never overrides
            # the player pool or the five-man quality guardrails above.
            if games < 100 or gain < 0.75:
                continue
            comparable.append((gain, float(row.get("score", 0)), row, role))
        if comparable:
            gain, _, current, role = max(comparable, key=lambda x: (x[0], x[1]))
            explanatory[role] = f"counter into {known[role]} (+{gain:.1f} matchup points)"

    # Forced ban/taken substitutions are explained separately in chosen_status.
    selected = dict(current)
    selected["adaptive_selected"] = True
    selected["adaptive_reasons"] = explanatory
    others = [row for row in candidates if _signature(row) != _signature(selected)]
    return [selected, *others[:max(0, limit - 1)]]
