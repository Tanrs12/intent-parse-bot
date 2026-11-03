"""Adapt a captain-selected five using the *shared* legal draft candidate pool.

The shared scorer owns player pools, mastery/comfort, matchup evidence and comp
quality. We select between its finished candidates, never inventing champions.

A counter in any role can prompt a supporting champion change in any
other role (Top Vayne + Mid Galio, or Mid Zed + Bot Ziggs). These are
champion changes, not player role swaps; the scorer still owns legality.
"""
from __future__ import annotations

from typing import Mapping

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
FRONTLINE_TAGS = {"TANK", "VANGUARD", "WARDEN", "JUGGERNAUT"}
DAMAGE_TYPES = {"MAGIC_DAMAGE", "PHYSICAL_DAMAGE"}


def _damage_roles(champs: Mapping[str, str], traits: Mapping[str, dict],
                  kind: str) -> set[str]:
    return {role for role in ROLES
            if str(traits.get(champs.get(role, ""), {}).get("adaptive_type") or "") == kind}


def _signature(row: dict) -> tuple[str, ...]:
    return tuple(str((row.get("champions") or {}).get(role) or "") for role in ROLES)


def _frontline_roles(champs: Mapping[str, str], traits: Mapping[str, dict]) -> set[str]:
    roles = set()
    for role in ROLES:
        entry = traits.get(champs.get(role, ""), {})
        tags = {str(x).upper() for x in entry.get("roles", ())}
        toughness = int((entry.get("ratings") or {}).get("toughness", 0) or 0)
        if tags & FRONTLINE_TAGS or toughness >= 3:
            roles.add(role)
    return roles


def _same_role_owners(a: dict, b: dict) -> bool:
    owners_a = a.get("player_ids") or {}
    owners_b = b.get("player_ids") or {}
    return all(not owners_a.get(role) or not owners_b.get(role)
               or str(owners_a[role]) == str(owners_b[role])
               for role in ROLES)


def select_adaptive_comp(
    candidates: list[dict],
    chosen: Mapping[str, str],
    *,
    enemy_roles: Mapping[str, str] | None = None,
    unavailable: set[str] | None = None,
    actual_locks: Mapping[str, str] | None = None,
    identity: str | None = None,
    limit: int = 10,
    champion_traits: Mapping[str, dict] | None = None,
    global_counter_rules: Mapping[str, list[dict]] | None = None,
    statistical_evidence: Mapping[str, list[dict]] | None = None,
) -> list[dict]:
    """Choose stable single- or two-seat counter completions from scored legal drafts.

    Only two coordinated swaps allowed, with at least one proven counter
    against a confirmed enemy. The other swap must preserve our composition's
    role functions. All five role owners stay the same and live locks win.
    """
    if not candidates:
        return []
    original = {role: str(chosen.get(role) or "") for role in ROLES}
    if not all(original.values()):
        return candidates[:limit]
    known = {role: champ for role, champ in (enemy_roles or {}).items()
             if role in ROLES and champ}
    gone = set(unavailable or ())
    locked = {r: c for r, c in (actual_locks or {}).items() if r in ROLES}
    forced = {r for r in ROLES if original[r] in gone and locked.get(r) != original[r]}
    traits = champion_traits or {}
    global_rules = global_counter_rules or {}
    # Optional *local-only*, exact-patch OP.GG Emerald+ observations. The
    # shared backend supplies this evidence; the selector makes no requests.
    # Both the planned pick and alternative must have their OWN champion
    # page to estimate a same-population champion baseline. No raw WR swaps.
    statistic_by_role = {
        role: {str(row.get("ours") or ""): row
               for row in ((statistical_evidence or {}).get(role) or [])}
        for role in ROLES
    }

    def changed(rec):
        values = rec.get("champions") or {}
        return {role for role in ROLES if values.get(role) != original[role]}

    eligible = [
        row for row in candidates
        if all(row.get("champions", {}).get(role) == champ for role, champ in locked.items())
        and not any(row.get("champions", {}).get(role) in gone
                    for role in ROLES if role not in locked)
    ]
    if not eligible:
        return candidates[:limit]
    closest = min(len(changed(row)) for row in eligible)
    baselines = [row for row in eligible if len(changed(row)) == closest]
    if identity:
        matching = [row for row in baselines if row.get("identity") == identity]
        if matching:
            baselines = matching
    base = max(baselines, key=lambda row: (
        float(row.get("score", 0)), float(row.get("rank_score", 0))))
    current = base
    explanation = {}
    if known:
        base_score = float(base.get("score", 0))
        base_front = _frontline_roles(base.get("champions") or original, traits)
        base_edges = {e.get("role"): e for e in (base.get("live_lane_edges") or [])
                      if isinstance(e, dict)}
        comparable = []
        for row in eligible:
            swaps = changed(row) - forced
            if not (1 <= len(swaps) <= 2) or len(changed(row)) > len(forced) + 2:
                continue
            if swaps & set(locked) or not swaps & set(known):
                continue
            if not _same_role_owners(base, row):
                continue
            two_seat = len(swaps) == 2
            if not two_seat and not swaps.issubset(set(known) - set(locked)):
                continue
            identity_break = (bool(identity) and row.get("identity") != identity
                              and base.get("identity") == identity)
            # A markedly better five may change the plan's label, but never
            # let a routine lane WR swing discard a captain's selected plan.
            # The candidate still must pass real evidence and all fit gates.
            if identity_break and (not two_seat or
                                   float(row.get("score", 0)) < base_score + 2.0):
                continue
            if float(row.get("score", 0)) < base_score - (1.0 if two_seat else 4.0):
                continue
            plan_score = float(row.get("team_plan_score", 0))
            bot_score = float(row.get("bot_lane_score", 0))
            mid_jg_score = float(row.get("mid_jungle_score", 0))
            # A coordinated composition repair may sacrifice SOME lane
            # pairing strength (e.g. Jinx -> Ziggs when Mid picks Zed),
            # but must still be a playable team and improve global fit.
            # The shared score already prices the trade-off.
            if plan_score < max(6.5, float(base.get("team_plan_score", 0)) - (1.5 if two_seat else 0.8)):
                continue
            if bot_score < max(6.8, float(base.get("bot_lane_score", 0)) - (2.0 if two_seat else 1.0)):
                continue
            if mid_jg_score < max(5.0, float(base.get("mid_jungle_score", 0)) - (1.8 if two_seat else 1.0)):
                continue

            # Preserve critical composition jobs independent of lane. A top
            # tank can be replaced by Galio mid, and lost AP from a mid
            # assassin change can be restored by a mage bot or any other
            # qualified teammate. The shared five-player score then judges
            # finer structure, agency, synergy and skill.
            new_front = _frontline_roles(row.get("champions") or {}, traits)
            base_damage = {kind: _damage_roles(base.get("champions") or original,
                                              traits, kind) for kind in DAMAGE_TYPES}
            new_damage = {kind: _damage_roles(row.get("champions") or {},
                                             traits, kind) for kind in DAMAGE_TYPES}
            if two_seat:
                if len(new_front) < len(base_front):
                    continue
                if any(base_damage[kind] and not new_damage[kind]
                       for kind in DAMAGE_TYPES):
                    continue

            evidence = []
            for role in swaps & set(known):
                new_champion = (row.get("champions") or {}).get(role)
                edge = next((e for e in (row.get("live_lane_edges") or [])
                             if isinstance(e, dict)
                             and e.get("role") == role
                             and e.get("enemy") == known[role]), None)
                old = base_edges.get(role) or {}
                if edge and int(edge.get("games") or 0) >= 100 and edge.get("usable_for_ranking") is not False:
                    gain = float(edge.get("points", 0)) - float(old.get("points", 0))
                    if gain >= (1.25 if two_seat else 0.75):
                        evidence.append((gain, role,
                                         f"counter into {known[role]} (+{gain:.1f} calibrated matchup points)"))
                # Baseline-adjusted observational strength. Raw head-to-head
                # WR is not a counter advantage: compare each champion's
                # performance into this enemy against that champion's normal
                # WR on the SAME patch/rank/role. Shrink small samples.
                # Only a large meaningful improvement may modify the five.
                observed = statistic_by_role.get(role) or {}
                current_obs = observed.get(new_champion) or {}
                planned_obs = observed.get(original[role]) or {}
                try:
                    comparable_population = (
                        current_obs.get("source_patch") and
                        current_obs.get("source_patch") == planned_obs.get("source_patch") and
                        current_obs.get("rank_filter") == "Emerald+" and
                        planned_obs.get("rank_filter") == "Emerald+" and
                        current_obs.get("queue_filter") == "Ranked Solo/Duo" and
                        planned_obs.get("queue_filter") == "Ranked Solo/Duo"
                    )
                    n_cur = int(current_obs.get("games") or 0)
                    n_old = int(planned_obs.get("games") or 0)
                    n_global_cur = int(current_obs.get("role_baseline_games") or 0)
                    n_global_old = int(planned_obs.get("role_baseline_games") or 0)
                    if (comparable_population and n_cur >= 500 and n_old >= 500
                        and n_global_cur >= 10000 and n_global_old >= 10000):
                        cur_lift = (float(current_obs["head_to_head_win_rate"]) -
                                    float(current_obs["role_baseline_win_rate"]))
                        old_lift = (float(planned_obs["head_to_head_win_rate"]) -
                                    float(planned_obs["role_baseline_win_rate"]))
                        confidence = min(n_cur / (n_cur + 700.0),
                                         n_old / (n_old + 700.0))
                        gain = max(-3.0, min(3.0, (cur_lift - old_lift)
                                             * confidence * 0.26))
                        if gain >= (1.20 if two_seat else 0.80):
                            evidence.append((
                                gain, role,
                                f"OP.GG current-patch adjusted counter into "
                                f"{known[role]} (+{gain:.1f} evidence points; "
                                f"{n_cur}/{n_old} games, Emerald+)",
                            ))
                except (TypeError, ValueError, KeyError):
                    pass
                # Rare cross-lane champion-specific interactions, e.g.
                # Sylas stealing Malphite R, are defined ONCE in the shared
                # engine's rule catalogue and passed to this selector.
                for revealed_enemy in known.values():
                    for rule in global_rules.get(revealed_enemy, []):
                        if (rule.get("champion") == new_champion
                            and float(rule.get("score") or 0) >= 4):
                            evidence.append((float(rule["score"]), role,
                                             str(rule.get("reason") or "exceptional counter")))
            if not evidence:
                continue
            gain, role, reason = max(evidence)
            if two_seat:
                companion = next(iter(swaps - {role}))
                replaced_jobs = []
                if role in base_front and role not in new_front:
                    if companion not in new_front or companion in base_front:
                        continue
                    replaced_jobs.append("frontline/engage")
                missing_job = False
                for kind, label in (("MAGIC_DAMAGE", "magic damage"),
                                    ("PHYSICAL_DAMAGE", "physical damage")):
                    if role in base_damage[kind] and role not in new_damage[kind]:
                        if (companion in new_damage[kind]
                            and companion not in base_damage[kind]):
                            replaced_jobs.append(label)
                        elif len(new_damage[kind]) < len(base_damage[kind]):
                            missing_job = True
                            break
                if missing_job:
                    continue
                # When both lanes are countered, a second swap still needs
                # a concrete structural explanation, not two unrelated picks.
                if companion in known and not replaced_jobs:
                    continue
                job = ", ".join(replaced_jobs) if replaced_jobs else "team composition"
                why = (f"{reason}; switch {companion} to "
                       f"{row['champions'][companion]} to restore {job}")
            else:
                why = reason
            # Counter must be worth the combined complexity; a minuscule
            # improvement cannot demand two player changes.
            team_delta = float(row.get("score", 0)) - base_score
            merit = gain + min(0.0, team_delta) * (0.6 if two_seat else 0.4)
            if two_seat and merit < 1.0:
                continue
            comparable.append((merit, float(row.get("score", 0)), row, role, why,
                               swaps))
        if comparable:
            _, _, current, role, why, swaps = max(
                comparable, key=lambda x: (x[0], x[1]))
            explanation[role] = why
            if len(swaps) == 2:
                companion = next(iter(swaps - {role}))
                explanation[companion] = (
                    "compensating team-structure change for the verified counter")

    selected = dict(current)
    selected["adaptive_selected"] = True
    selected["adaptive_reasons"] = explanation
    others = [row for row in candidates if _signature(row) != _signature(selected)]
    return [selected, *others[:max(0, limit - 1)]]
