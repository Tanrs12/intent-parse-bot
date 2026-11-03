"""Evidence-gated same-role counter suggestions for a selected five-player plan.

Not a second drafting engine. This reads the already-ranked leading composition and
the SAME calibrated matchups / team score as league_comp_engine. An off-pool pick
is always a scouting/learning IDEA, never legal for Hover / Lock or auto-selection.
"""
from __future__ import annotations

from typing import Mapping

from league_comp_engine import (
    ROLES, load_meta, load_lane_matchups, load_solo_queue_winrates,
    live_lane_matchup_adjustment, score_comp,
)

MIN_GAMES = 100
MIN_GAIN = 0.75
MAX_COMP_LOSS = 4.0


def counter_advice(
    lead: dict | None,
    enemy_roles: Mapping[str, str] | None,
    *,
    unavailable: set[str] | None = None,
    ally_locks: Mapping[str, str] | None = None,
    matchup_data: dict | None = None,
    meta: dict | None = None,
) -> dict:
    """Return per-lane pool-safe swaps and separate, never-autopicked learn ideas.

    The trusted caller supplies *confirmed* enemy lane assignments, not hovers or
    a guess based only on the enemy player's champ-select seat.
    """
    champions = dict((lead or {}).get("champions") or {})
    if not champions or not enemy_roles:
        return {"lanes": [], "status": "waiting_for_confirmed_enemy_lane"}
    meta = meta if meta is not None else load_meta()
    samples = matchup_data if matchup_data is not None else load_lane_matchups()
    unavailable = set(unavailable or ())
    locks = dict(ally_locks or {})
    original_scored = score_comp(champions, meta)
    scored_baseline = float(original_scored.get("score", 0))
    base_plan = str((lead or {}).get("team_plan") or original_scored.get("team_plan") or "")
    submitted = (lead or {}).get("submitted_pools") or {}
    solo = load_solo_queue_winrates()
    rows = []

    for role in ROLES:
        enemy = (enemy_roles or {}).get(role)
        if not enemy or role in locks or not champions.get(role):
            continue
        original = champions[role]
        baseline_edges = live_lane_matchup_adjustment(
            {role: original}, {role: enemy}, matchup_data=matchup_data
        )["edges"]
        original_edge = baseline_edges[0] if baseline_edges else {}
        original_points = float(original_edge.get("points") or 0)
        pool = [str(c) for c in submitted.get(role, []) if c and c not in unavailable]
        pool_set = set(pool) | {original}
        # Only candidates with actual evidence or a submitted champion are evaluated.
        # Role stats contribute possible learning candidates, never automatic picks.
        recorded = (samples.get("matchups") or {}).get(role, {}).get(enemy, {}).get("responses") or {}
        candidates = set(pool) | set(recorded) | set((meta.get("role_stats") or {}).get(role, {}))
        candidates |= set(solo.get(role, {}))
        # Avoid evaluating a full champion database on every poll; most candidates
        # without a recent authorised observation will fall out before composition scoring.
        eligible = []
        for champion in sorted(candidates):
            if (not champion or champion == original or champion in unavailable
                or champion in champions.values()):
                continue
            edge_rows = live_lane_matchup_adjustment(
                {role: champion}, {role: enemy}, matchup_data=matchup_data
            )["edges"]
            edge = edge_rows[0] if edge_rows else {}
            n = int(edge.get("games") or 0)
            if (n < MIN_GAMES or edge.get("qualitative_only")
                or edge.get("usable_for_ranking") is False):
                continue
            gain = round(float(edge.get("points") or 0) - original_points, 2)
            if gain < MIN_GAIN:
                continue
            replaced = {**champions, role: champion}
            fitting = score_comp(replaced, meta)
            if base_plan and fitting.get("team_plan") != base_plan:
                continue
            if scored_baseline - float(fitting.get("score", 0)) > MAX_COMP_LOSS:
                continue
            if (float(fitting.get("team_plan_score", 0)) <
                float(original_scored.get("team_plan_score", 0)) - 0.9):
                continue
            if role in ("ADC", "Support") and (float(fitting.get("bot_lane_score", 0)) <
                float(original_scored.get("bot_lane_score", 0)) - 0.9):
                continue
            if role in ("Jungle", "Mid") and (float(fitting.get("mid_jungle_score", 0)) <
                float(original_scored.get("mid_jungle_score", 0)) - 0.9):
                continue
            source = edge.get("source") or (
                "LoLalytics D2+ historical snapshot" if edge.get("advantage") is not None
                else "verified historical matchup"
            )
            patch = edge.get("source_patch") or (
                ", ".join((samples.get("source") or {}).get("patches_detected") or [])
            )
            eligible.append({
                "champion": champion,
                "type": "playable" if champion in pool_set else "learn",
                "enemy": enemy,
                "role": role,
                "games": n,
                "gain_points": gain,
                "team_score_delta": round(float(fitting.get("score", 0)) - scored_baseline, 2),
                "source": source,
                "evidence_freshness": "current-patch" if edge.get("sampled_ranked") else "historical",
                "patch": patch,
                "source_url": edge.get("source_url"),
            })
        eligible.sort(
            key=lambda entry: (entry["gain_points"] + min(0, entry["team_score_delta"]) * 0.3,
                               entry["games"]), reverse=True
        )
        playable = [x for x in eligible if x["type"] == "playable"][:2]
        learn = [x for x in eligible if x["type"] == "learn"][:2]
        rows.append({
            "role": role,
            "player": str(((lead or {}).get("players") or {}).get(role, {}).get("display_name") or role),
            "enemy": enemy,
            "planned": original,
            "playable": playable,
            "learn": learn,
            "base_evidence_games": int(original_edge.get("games") or 0),
            "coverage": "verified" if playable or learn else "no_qualified_counter_data",
        })
    return {"lanes": rows, "status": "evaluated"}
