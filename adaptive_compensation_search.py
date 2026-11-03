"""Bounded all-lane compensation search using the existing legal draft engine.

No separate scorer, invented champion, player assignment, or automatic lock.
Only a confirmed enemy counter can anchor a coordinated two-champion change.
For each other open lane we hold the remaining three planned champions fixed
and ask the SAME shared backend to return playable candidates. It retains
the owner's submitted role pool, champ select locks, synergy and ranker.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from adaptive_draft import (
    ROLES, select_adaptive_comp, _frontline_roles, _damage_roles,
    _same_role_owners, _signature,
)


def search_all_lane_companions(
    initial: dict,
    *,
    chosen: Mapping[str, str],
    known_enemy_lanes: Mapping[str, str],
    actual_locks: Mapping[str, str],
    hard_required: Mapping[str, str],
    unavailable: set[str],
    backend_kwargs: Mapping[str, Any],
    backend: Callable[..., dict],
    champion_traits: Mapping[str, dict],
    global_counter_rules: Mapping[str, list[dict]],
    statistical_evidence: Mapping[str, list[dict]] | None = None,
    max_branches: int = 4,
    limit: int = 10,
) -> dict:
    """Try all *other* unlocked lanes, never change more than two champions.

    Returns initial unchanged if no valid improvement exists. Callers should
    cache by champ-select state; optional exhaustive exploration is not a
    network request and is never used for hover/lock directly.
    """
    existing = list(initial.get("recommendations") or [])
    planned = {r: str(chosen.get(r) or "") for r in ROLES}
    forced_locked = {
        role for role, champ in actual_locks.items()
        if role in ROLES and champ and champ != planned.get(role)
    }
    if (not existing or not all(planned.values())
        or not (known_enemy_lanes or forced_locked)
        or not 1 <= max_branches <= len(ROLES) - 1):
        return initial

    hard = {r: c for r, c in hard_required.items() if r in ROLES and c}
    locked = {r: c for r, c in actual_locks.items() if r in ROLES and c}
    anchors = {
        role for role in known_enemy_lanes
        if role in ROLES and role not in locked and role not in hard
    } | forced_locked
    if not anchors:
        return initial

    all_candidates = list(existing)
    seen = {
        tuple(str(row.get("champions", {}).get(r) or "") for r in ROLES)
        for row in existing
    }
    searched = []
    for companion in ROLES:
        if companion in anchors or companion in locked or companion in hard:
            continue
        if len(searched) >= max_branches:
            break
        # Explicitly freeze all other unchallenged roles; only the confirmed
        # counter lane(s) and THIS companion may change in this branch.
        required = dict(hard)
        required.update({
            role: champ for role, champ in planned.items()
            if role not in anchors and role != companion and role not in locked
            and champ not in unavailable and role not in hard
        })
        branch_kwargs = dict(backend_kwargs)
        branch_kwargs["required_champions"] = required
        branch_kwargs["chosen_champions"] = planned
        # Do not invoke a fallback which relaxes the preserved seats: an
        # invalid role combination must simply be discarded.
        try:
            branch = backend(**branch_kwargs)
        except Exception:
            # An optional counter search must not break the ordinary draft.
            continue
        searched.append(companion)
        if branch.get("locks_relaxed"):
            continue
        for row in branch.get("recommendations") or []:
            signature = tuple(str((row.get("champions") or {}).get(r) or "")
                              for r in ROLES)
            if not all(signature) or signature in seen:
                continue
            seen.add(signature)
            all_candidates.append(row)
    if len(all_candidates) == len(existing):
        return initial

    ranked = select_adaptive_comp(
        all_candidates, planned,
        enemy_roles=known_enemy_lanes,
        unavailable=unavailable,
        actual_locks={**hard, **locked},
        identity=backend_kwargs.get("identity"),
        limit=limit,
        champion_traits=champion_traits,
        global_counter_rules=global_counter_rules,
        statistical_evidence=statistical_evidence,
    )
    winner = ranked[0] if ranked else {}
    if not winner.get("adaptive_reasons") and forced_locked:
        ranked = select_locked_pick_repair(
            all_candidates, planned, actual_locks=locked,
            champion_traits=champion_traits, limit=limit,
        )
        winner = ranked[0] if ranked else {}
    if not winner.get("adaptive_reasons"):
        return initial
    if len([r for r in ROLES if winner.get("champions", {}).get(r) != planned[r]]) > 2:
        return initial
    # Candidate scores already include comfort, whole-team, duo and tempo.
    # Preserve base recommendation metadata apart from its new chosen five.
    result = dict(initial)
    result["recommendations"] = ranked
    result["compensation_search"] = {
        "considered_roles": searched,
        "selected_changes": [
            r for r in ROLES if winner["champions"].get(r) != planned[r]
        ],
    }
    return result



# The normal champ-select response must not wait for up to four additional
# legal team re-scores. Cache one bounded asynchronous task, publish its
# result on the next routine poll, and discard results for superseded states.
import threading
from collections import OrderedDict


class CompanionSearchService:
    def __init__(self, *, runner=search_all_lane_companions):
        self._runner = runner
        self._lock = threading.Lock()
        self._pending_key: str | None = None
        self._results: OrderedDict[str, dict] = OrderedDict()

    def request(self, key: str, *, on_update=None, **kwargs) -> tuple[dict | None, str]:
        """Return cached completion or schedule at most one background search.

        Never runs the shared draft engine on the caller's UI/scoring thread.
        """
        with self._lock:
            if key in self._results:
                self._results.move_to_end(key)
                return self._results[key], "ready"
            if self._pending_key is not None:
                return None, "evaluating" if self._pending_key == key else "queued_next_state"
            self._pending_key = key
            threading.Thread(
                target=self._work, args=(key, kwargs, on_update),
                name="DraftCoach-CompensationSearch", daemon=True,
            ).start()
            return None, "evaluating"

    def _work(self, key: str, kwargs: dict, on_update) -> None:
        result = None
        try:
            result = self._runner(**kwargs)
        except Exception:
            # Exploratory changes must never replace or interrupt the normal
            # role-legal draft. The next game state can retry separately.
            pass
        finally:
            with self._lock:
                if result is not None:
                    self._results[key] = result
                    while len(self._results) > 8:
                        self._results.popitem(last=False)
                self._pending_key = None
        if on_update:
            try:
                on_update()
            except Exception:
                pass


_service = CompanionSearchService()


def request_compensation_search(key: str, *, on_update=None, **kwargs):
    return _service.request(key, on_update=on_update, **kwargs)



def select_locked_pick_repair(
    candidates: list[dict],
    chosen: Mapping[str, str],
    *,
    actual_locks: Mapping[str, str],
    champion_traits: Mapping[str, dict],
    limit: int = 10,
) -> list[dict]:
    """Keep a real Zed/Yasuo lock and restore lost magic/frontline elsewhere.

    Works without confirmed enemy lane information. Exactly one changed lock
    and at most one other unlocked champion are considered; all candidates
    already passed the *shared* pool legality and scoring engine.
    """
    forced = {
        role: champ for role, champ in actual_locks.items()
        if role in ROLES and champ and champ != chosen.get(role)
    }
    if len(forced) != 1 or not candidates:
        return candidates[:limit]
    fixed_role = next(iter(forced))
    applicable = [
        row for row in candidates
        if all((row.get("champions") or {}).get(role) == champ
               for role, champ in actual_locks.items() if role in ROLES and champ)
    ]
    if not applicable:
        return candidates[:limit]
    def changed(row):
        return {role for role in ROLES
                if (row.get("champions") or {}).get(role) != chosen.get(role)}
    baselines = [r for r in applicable if changed(r) == {fixed_role}]
    if not baselines:
        return candidates[:limit]
    base = max(baselines, key=lambda row: float(row.get("score") or 0))
    original_front = _frontline_roles(chosen, champion_traits)
    base_front = _frontline_roles(base["champions"], champion_traits)
    damage_kinds = ("MAGIC_DAMAGE", "PHYSICAL_DAMAGE")
    original_damage = {k: _damage_roles(chosen, champion_traits, k)
                       for k in damage_kinds}
    base_damage = {k: _damage_roles(base["champions"], champion_traits, k)
                   for k in damage_kinds}
    lost_front = len(original_front) > len(base_front)
    lost_damage = [
        k for k in damage_kinds
        if len(original_damage[k]) > len(base_damage[k])
    ]
    if not lost_front and not lost_damage:
        return candidates[:limit]
    viable = []
    for row in applicable:
        moved = changed(row)
        if len(moved) != 2 or fixed_role not in moved:
            continue
        companion = next(iter(moved - {fixed_role}))
        if companion in actual_locks:
            continue
        if not _same_role_owners(base, row):
            continue
        if float(row.get("score") or 0) < float(base.get("score") or 0) + 0.5:
            continue
        if float(row.get("team_plan_score") or 0) < max(
            6.5, float(base.get("team_plan_score") or 0) - 1.5):
            continue
        if float(row.get("bot_lane_score") or 0) < max(
            6.8, float(base.get("bot_lane_score") or 0) - 2.0):
            continue
        if float(row.get("mid_jungle_score") or 0) < max(
            5.0, float(base.get("mid_jungle_score") or 0) - 1.8):
            continue
        roles_front = _frontline_roles(row["champions"], champion_traits)
        if lost_front and (
            len(roles_front) < len(original_front)
            or companion not in roles_front or companion in base_front
        ):
            continue
        if any(
            len(_damage_roles(row["champions"], champion_traits, kind))
                < len(original_damage[kind])
            or companion not in _damage_roles(row["champions"],
                                               champion_traits, kind)
            or companion in base_damage[kind]
            for kind in lost_damage
        ):
            continue
        labels = (["frontline"] if lost_front else []) + [
            "magic damage" if k == "MAGIC_DAMAGE" else "physical damage"
            for k in lost_damage
        ]
        viable.append((
            float(row.get("score") or 0), row, companion, ", ".join(labels)
        ))
    if not viable:
        return candidates[:limit]
    _, winner, companion, job = max(viable, key=lambda x: x[0])
    winner = dict(winner)
    winner["adaptive_selected"] = True
    winner["adaptive_reasons"] = {
        fixed_role: (f"{actual_locks[fixed_role]} is already locked in {fixed_role}; "
                     f"preserve that pick and restore {job} in {companion}"),
        companion: (f"Swap {companion} to {winner['champions'][companion]} "
                    f"to restore {job} without undoing the locked pick"),
    }
    others = [r for r in candidates if _signature(r) != _signature(winner)]
    return [winner, *others[:max(0, limit - 1)]]
