"""Evidence-gated same-role counter suggestions for a selected five-player plan.

Not a second drafting engine. This reads the already-ranked leading composition and
the SAME calibrated matchups / team score as league_comp_engine. An off-pool pick
is always a scouting/learning IDEA, never legal for Hover / Lock or auto-selection.
"""
from __future__ import annotations

from typing import Mapping
from league_matchup_universe import role_universe, scout_note

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
        # The prototype bundled data covers eight opponents, but this search
        # visits EVERY officially supported champion in the assigned lane.
        candidates |= set(role_universe(role))
        # Avoid evaluating a full champion database on every poll; most candidates
        # without a recent authorised observation will fall out before composition scoring.
        eligible = []
        scouting = []
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
                # A manually verified directional observation isn't a win rate.
                # It may guide a human's exploratory pick but never earns
                # numerical points or unlocks an automated counter substitution.
                note = scout_note(role, champion, enemy) if matchup_data is None else {}
                if note and note["verdict"] in ("favourable", "mixed"):
                    swapped = {**champions, role: champion}
                    fit = score_comp(swapped, meta)
                    team_delta = float(fit.get("score", 0)) - scored_baseline
                    weak_fit = (team_delta < -MAX_COMP_LOSS or
                                (base_plan and fit.get("team_plan") != base_plan and
                                 float(fit.get("team_plan_score", 0)) <
                                 float(original_scored.get("team_plan_score", 0)) - 0.8))
                    # Don't hide an owned matchup option just because it weakens
                    # the selected shell. Let the user SEE that trade-off; only
                    # the hard-scored live engine may choose an automatic swap.
                    scouting.append({
                        "champion": champion,
                        "role": role,
                        "enemy": enemy,
                        "type": "playable" if champion in pool_set else "learn",
                        "verdict": note["verdict"],
                        "summary": note["summary"],
                        "patch": note["patch"],
                        "sources": note["sources"],
                        "source_weight": min(3, len(note["sources"])),
                        "source_quality": note.get("source_quality", "qualitative"),
                        "qualitative_only": True,
                        "historical_hint": (
                            {"games": n, "patch": edge.get("source_patch"),
                             "confidence": edge.get("confidence", "low sample")}
                            if n and edge.get("historical") else None
                        ),
                        "team_score_delta": round(team_delta, 2),
                        "team_fit": "weakens_comp" if weak_fit else "fits_comp",
                    })
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
        # An owned champion is a real option; an unsubmitted champion is an
        # exploration idea. More independent sources break qualitative ties.
        # This is presentation order, NOT a hidden bonus to the draft scorer.
        scouting.sort(key=lambda x: (
            x["type"] == "playable",
            x["verdict"] == "favourable",
            x["source_weight"],
            x["team_score_delta"],
        ), reverse=True)
        risk_note = scout_note(role, original, enemy) if matchup_data is None else {}
        risk = ({"summary": risk_note["summary"], "sources": risk_note["sources"],
                 "patch": risk_note.get("patch")}
                if risk_note.get("verdict") == "unfavourable" else None)
        rows.append({
            "role": role,
            "planned_risk": risk,
            "player": str(((lead or {}).get("players") or {}).get(role, {}).get("display_name") or role),
            "enemy": enemy,
            "planned": original,
            "playable": playable,
            "learn": learn,
            "scout": scouting[:4],
            "candidates_searched": len(candidates),
            "base_evidence_games": int(original_edge.get("games") or 0),
            "coverage": ("verified" if playable or learn else
                         "scouting_only" if scouting else "no_qualified_counter_data"),
        })
    return {"lanes": rows, "status": "evaluated"}
