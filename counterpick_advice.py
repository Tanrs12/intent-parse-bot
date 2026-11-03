"""Evidence-gated same-role counter suggestions for a selected five-player plan.

Not a second drafting engine. This reads the already-ranked leading composition and
the SAME calibrated matchups / team score as league_comp_engine. An off-pool pick
is always a scouting/learning IDEA, never legal for Hover / Lock or auto-selection.
"""
from __future__ import annotations

from typing import Mapping
from league_matchup_universe import role_universe, scout_note
from league_opgg_mcp_reference import cached_for_enemy
from league_opgg_full_reference import full_for_enemy
from league_chaincc_pro_synergy import pro_duo
from league_pocketpick_history import historical_for_enemy
from league_pro_order_context import selected_after as pro_selected_after

from league_comp_engine import (
    ROLES, LIVE_PATCH, load_meta, load_lane_matchups, load_solo_queue_winrates,
    live_lane_matchup_adjustment, score_comp,
)

def _pro_duo_references(champions: dict[str, str]) -> dict:
    """At most two factual team-pair references; not ranking or legality."""
    results = {}
    for package, (first_role, second_role) in {
        "Mid+Jungle": ("Mid", "Jungle"),
        "ADC+Support": ("ADC", "Support"),
    }.items():
        first, second = champions.get(first_role), champions.get(second_role)
        if not first or not second:
            continue
        row = pro_duo(package, first, second, patch=LIVE_PATCH)
        if row:
            results[package] = row
    return results


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
    # Optional local professional reference is presentation-only; injected
    # test fixtures must not depend on private filesystem research caches.
    pro_refs = _pro_duo_references(champions) if matchup_data is None else {}
    if not champions or not enemy_roles:
        return {"lanes": [], "status": "waiting_for_confirmed_enemy_lane",
                "pro_duos": pro_refs}
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
        # Earlier-patch EUW Silver-Platinum history is a broad, attributed
        # scouting fallback for positions without current verified samples.
        # It is NEVER a reason to force a counter or alter the legal pool.
        historical = (historical_for_enemy(role, enemy)
                      if matchup_data is None else {"rows": []})
        historical_base = None
        historical_alternatives = []
        for observation in historical.get("rows") or []:
            champion_name = observation["champion"]
            if champion_name == original:
                historical_base = observation
            elif (champion_name not in unavailable
                  and champion_name not in champions.values()
                  and observation["games"] >= 20):
                info = dict(observation)
                info["type"] = ("playable" if champion_name in pool_set
                                else "learn")
                info["confidence"] = ("low" if observation["games"] < 40
                                       else "historical")
                # Sorting only. This is NOT a candidate ranking bonus.
                info["shrunk_reference_lift_pp"] = round(
                    info["adjusted_matchup_lift_pp"] *
                    info["games"] / (info["games"] + 100.0), 2)
                historical_alternatives.append(info)
        historical_alternatives.sort(key=lambda row: (
            row["type"] == "playable",
            row["games"] >= 40,
            row["shrunk_reference_lift_pp"],
            row["games"],
        ), reverse=True)
        # Ordered-pick archive: a later pro pick is not necessarily a lane
        # counter, and professional Worlds 26.20 is NOT in this source.
        pro_response_planned = (pro_selected_after(original, enemy)
                                if matchup_data is None else {})
        pro_response_alternatives = []
        if matchup_data is None:
            for champion_name in pool:
                if (champion_name == original or champion_name in unavailable
                    or champion_name in champions.values()):
                    continue
                pattern = pro_selected_after(champion_name, enemy)
                if pattern:
                    pro_response_alternatives.append(pattern)
            # n is evidence density, NOT an implied winrate-based counter
            pro_response_alternatives.sort(
                key=lambda e:e["games"], reverse=True)
        # Local-only, rate-limited OP.GG MCP references do not have validated
        # patch, rank or queue. Show as evidence links, never score auto-picks.
        opgg_cache = cached_for_enemy(role, enemy) if matchup_data is None else {}
        base_opgg = opgg_cache.get("".join(c for c in original.casefold() if c.isalnum()))
        # Unlike the MCP top-three summaries, the public OP.GG web page
        # exposes the full visible opponent list. Keep filtered same-patch
        # Emerald+ observations separate from the legal draft's score.
        full_page = full_for_enemy(role, enemy, patch=LIVE_PATCH) if matchup_data is None else {"rows": []}
        full_base = None
        full_alternatives = []
        for observed in full_page.get("rows") or []:
            champion_name = observed.get("ours")
            if not champion_name:
                continue
            info = {
                "champion": champion_name,
                "enemy": enemy,
                "games": observed["games"],
                "head_to_head_win_rate": observed["head_to_head_win_rate"],
                "source_patch": observed["source_patch"],
                "rank_filter": observed["rank_filter"],
                "retrieved_at": observed.get("retrieved_at"),
                "source_url": observed["source_url"],
                "evidence_limit": "patch_rank_attested_observation_not_autopick",
                "source_direction": observed.get("source_direction", "own_champion_page"),
                "role_baseline_win_rate": observed.get("role_baseline_win_rate"),
                "role_baseline_games": observed.get("role_baseline_games"),
            }
            try:
                if observed.get("role_baseline_win_rate") is not None:
                    info["adjusted_matchup_lift_pp"] = round(
                        float(observed["head_to_head_win_rate"]) -
                        float(observed["role_baseline_win_rate"]), 2)
            except (TypeError, ValueError):
                pass
            if champion_name == original:
                full_base = info
            elif champion_name not in unavailable and champion_name not in champions.values():
                info["type"] = "playable" if champion_name in pool_set else "learn"
                if info["type"] == "playable":
                    # The assigned owner's submitted role pool makes this a
                    # real option, but single-seat changes may remove crucial
                    # frontline. The shared adaptive engine can separately
                    # test compensated two-seat completions.
                    fit = score_comp({**champions, role: champion_name}, meta)
                    fit_delta = float(fit.get("score", 0)) - scored_baseline
                    info["team_score_delta"] = round(fit_delta, 2)
                    info["team_fit"] = ("needs_compensation" if fit_delta < -3
                                        else "fits_comp")
                full_alternatives.append(info)
        # Prioritise actual player pool, a coherent five, then baseline-
        # adjusted opponent lift rather than sorting raw win rates alone.
        full_alternatives.sort(key=lambda e: (
            e["type"] == "playable",
            e.get("team_fit") != "needs_compensation",
            e.get("adjusted_matchup_lift_pp", -100),
            e["games"],
        ), reverse=True)
        opgg_alternatives = []
        for _key, row in opgg_cache.items():
            name = row.get("ours")
            if (not name or name == original or name in unavailable or
                name in champions.values()):
                continue
            opgg_alternatives.append({
                "champion": name,
                "enemy": enemy,
                "type": "playable" if name in pool_set else "learn",
                "games": row["games"],
                "head_to_head_win_rate": row["head_to_head_win_rate"],
                "role_baseline_win_rate": row.get("role_baseline_win_rate"),
                "residual_pp": row.get("residual_pp"),
                "source_url": row["source_url"],
                "retrieved_at": row.get("retrieved_at"),
                "source_patch": None,
                "evidence_limit": "rolling_unversioned_reference_only",
            })
        opgg_alternatives.sort(
            key=lambda row: (row["type"] == "playable",
                             row["residual_pp"] if row["residual_pp"] is not None else -100,
                             row["games"]), reverse=True
        )
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
            "pro_order_reference": {
                "planned": pro_response_planned,
                "alternatives": pro_response_alternatives[:3],
                "label": "Selected later in a historical pro draft (lane unknown), not a proven counter",
                "source_url": "https://github.com/DeusExMachina1993/LoL-Draft",
                "licence": "CC BY-SA 4.0",
                "last_patch": "26.17",
            },
            "pocket_reference": {
                "planned": historical_base,
                "alternatives": historical_alternatives[:4],
                "patch": historical.get("patch"),
                "rank": historical.get("rank"),
                "region": historical.get("region"),
                "observations": historical.get("observations", 0),
                "source_url": historical.get("source_url"),
                "label": "Historical lower-rank research, not a 26.21 counter or an automatic pick",
            },
            "opgg_full_reference": {
                "planned": full_base,
                "alternatives": full_alternatives[:4],
                "source": "OP.GG public counters page",
                "patch": full_page.get("source_patch"),
                "rank": full_page.get("rank_filter"),
                "label": "patch/rank-labelled statistical reference; no automatic pick changes",
            },
            "opgg_reference": {
                "original": ({
                    "champion": original,
                    "games": base_opgg["games"],
                    "head_to_head_win_rate": base_opgg["head_to_head_win_rate"],
                    "role_baseline_win_rate": base_opgg.get("role_baseline_win_rate"),
                    "residual_pp": base_opgg.get("residual_pp"),
                    "source_url": base_opgg["source_url"],
                } if base_opgg else None),
                "alternatives": opgg_alternatives[:3],
                "source": "OP.GG official MCP",
                "label": "rolling reference; rank, patch and queue not attested",
            },
            "candidates_searched": len(candidates),
            "base_evidence_games": int(original_edge.get("games") or 0),
            "coverage": ("verified" if playable or learn else
                         "scouting_only" if scouting else "no_qualified_counter_data"),
        })
    return {"lanes": rows, "status": "evaluated", "pro_duos": pro_refs}
