"""Memoisation of the backend's pure scoring calls for the live loop.

``recommend_comps`` scores every candidate composition with ``score_comp`` and
``_player_fit_adjustment``. During a draft each event re-scores mostly the same
compositions (a ban or lock only shrinks the candidate set), so caching by
(champions, players) turns the per-event cost from ~1 s into tens of
milliseconds after the first pass. The engine module itself stays untouched:
the wrappers are installed on the imported module object.

Cache keys (FLEXERS correctness fixes after the Codex review)
-------------------------------------------------------------
* Champions are keyed by ``tuple(champions.items())`` -- insertion order is
  part of the key because ``score_comp`` output depends on it (for example the
  ``damage_profile`` source lists follow traversal order).
* ``meta`` is resolved with the same fallback as ``score_comp`` (``meta or
  load_meta()``) *before* keying and is keyed by an ``_IdentityKey`` that
  retains the object, so a replaced default meta is a different key and a
  recycled ``id()`` can never alias a live entry.
* The player-fit key fingerprints every assignment input the current
  ``_player_fit_adjustment`` reads: per role the user id, the display name
  (copied into ``carry_options``/``carry_plan``) and whether the chosen
  champion is in that player's submitted pool for the role
  (``_declared_pool_competence_floor`` / ``_player_jungle_agency_adjustment``),
  plus whether the ADC's effective priority is 2 or 3 (the shared-lane
  penalty). Re-audit ``_fit_input_key`` whenever the scorer reads another
  player field.
* Hits and misses both return a ``deepcopy``: callers may mutate what they get
  back without contaminating the cache or later callers.

Invalidation
------------
Every key carries a generation token: ``stats["generation"]`` (bumped by
``clear()``), today's date (``live_patch_active()`` is date-gated and the
26.21 bridge expires on 2026-11-04; the date term makes entries scored under
the old gate miss once the day changes) and the mtimes of the data files the
scorers read (meta, lane matchups, player profiles, recent pro, open pro
pairs, licensed SoloQ). Inside a ``source_stat_scope`` the mtimes are the
scope's frozen snapshot -- the same one the engine's loaders see -- so an
entry can never be computed from one snapshot and keyed by another.

``clear()`` only drops THIS module's two caches. It does NOT invalidate the
engine's own memoised loaders, which are plain ``lru_cache``s with no file
dependency: ``load_meta``, ``load_lane_matchups``, ``load_solo_queue_winrates``,
``load_player_profiles``, ``load_recent_match_index``,
``load_player_score_index``, ``role_experience_score``, ``_mastery_points_for``,
``_champion_ownership``, ``_champion_comfort``, ``_adc_field_strength_adjustment``
and ``_read_explicit_champion_confirmations``. Those must be ``cache_clear()``ed
by the caller (``draftcoach.loadfiles.flush_engine_caches`` does so and then
calls ``clear()``; the Discord cog and the live bridge call
``engine.clear_player_profile_caches`` / ``load_lane_matchups.cache_clear``
directly without touching this cache -- the mtime term covers the file
replacement in that case). ``load_recent_pro`` and the ``league_multi_source``
/ ``league_aggregate_sources`` readers are keyed by file mtime and refresh on
their own; the mtime term keeps this cache consistent with them.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Callable

import league_aggregate_sources  # type: ignore
import league_comp_engine as engine  # type: ignore
import league_multi_source  # type: ignore
import league_source_stats  # type: ignore
from league_source_stats import source_mtime_ns  # type: ignore

MAX_ENTRIES = 60_000
_score_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_fit_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_installed = False
_original_score_comp: Callable = engine.score_comp
_original_player_fit: Callable = engine._player_fit_adjustment
stats = {"score_hits": 0, "score_misses": 0, "fit_hits": 0, "fit_misses": 0, "generation": 0}

# FLEXERS: per-scope memo of the data-file mtime tuple (see _data_mtimes).
_MTIME_MEMO: tuple[object, tuple] = (None, ())


class _IdentityKey:
    """Hashable identity of an object that keeps the object alive.

    FLEXERS: a bare ``id()`` can be recycled by a later object once the original
    is garbage collected; retaining the reference makes that impossible while
    the key is in the cache.
    """

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __hash__(self) -> int:
        return id(self.obj)

    def __eq__(self, other) -> bool:
        return isinstance(other, _IdentityKey) and self.obj is other.obj

    def __repr__(self) -> str:
        return f"_IdentityKey({type(self.obj).__name__}@{id(self.obj):#x})"


def _remember(cache: OrderedDict, key: tuple, value: dict) -> dict:
    cache[key] = value
    if len(cache) > MAX_ENTRIES:
        cache.popitem(last=False)
    return value


def _champion_key(champions: dict) -> tuple:
    # FLEXERS: retain traversal order used by scoring sums and output lists.
    return tuple(champions.items())


def _data_sources() -> tuple[Path, ...]:
    """Data files whose contents feed score_comp / _player_fit_adjustment.

    Resolved on every call so monkeypatched module paths are honoured.
    """
    return (
        engine.DATA_PATH,
        engine.MATCHUP_PATH,
        engine.PLAYER_PROFILE_PATH,
        engine.RECENT_PRO_PATH,
        league_multi_source.OPEN_PRO_PATH,
        league_aggregate_sources.SOLOQ_PATH,
    )


def _data_mtimes() -> tuple:
    """Current mtimes of the scorer's data files (None for an absent file).

    Inside a ``source_stat_scope`` the engine sees one frozen mtime per file for
    the whole calculation, so the tuple is memoised per scope object and costs
    one stat per file per calculation. Outside a scope every call stats the
    files, exactly as the engine's own mtime-keyed loaders do.
    """
    global _MTIME_MEMO
    scope = getattr(league_source_stats._SOURCE_CACHE, "mtimes", None)
    if scope is not None and _MTIME_MEMO[0] is scope:
        return _MTIME_MEMO[1]
    mtimes = []
    for path in _data_sources():
        try:
            mtimes.append(source_mtime_ns(path))
        except OSError:
            mtimes.append(None)
    token = tuple(mtimes)
    if scope is not None:
        _MTIME_MEMO = (scope, token)
    return token


def _generation() -> tuple:
    # FLEXERS: a clear(), a new day (live_patch_active is date-gated) or a
    # replaced data file each start a fresh generation.
    return (stats["generation"], date.today().toordinal(), _data_mtimes())


def _fit_input_key(assignment: dict, champions: dict) -> tuple:
    """Every assignment field the current _player_fit_adjustment reads."""
    # FLEXERS: mirrors the effective-ADC-priority resolution in the scorer.
    adc = assignment["ADC"]
    priority = adc.get("_role_priority_level")
    if priority not in {1, 2, 3}:
        try:
            priority = int((adc.get("role_priority") or {}).get("ADC"))
        except (TypeError, ValueError):
            priority = None
    rows = []
    for role in engine.ROLES:
        player = assignment[role]
        pool = (player.get("champions") or {}).get(role, ())
        rows.append((
            role,
            int(player["user_id"]),
            player.get("display_name"),
            champions[role] in pool,
        ))
    return (tuple(rows), priority in {2, 3})


@wraps(_original_score_comp)
def cached_score_comp(champions: dict, meta: dict | None = None) -> dict:
    # FLEXERS: same fallback semantics as score_comp, resolved before keying.
    meta = meta or engine.load_meta()
    key = (_champion_key(champions), _IdentityKey(meta), _generation())
    hit = _score_cache.get(key)
    if hit is not None:
        stats["score_hits"] += 1
        _score_cache.move_to_end(key)
        return deepcopy(hit)
    stats["score_misses"] += 1
    return deepcopy(_remember(_score_cache, key, _original_score_comp(champions, meta)))


@wraps(_original_player_fit)
def cached_player_fit(assignment: dict, champions: dict, archetypes: list, meta: dict) -> dict:
    key = (
        _fit_input_key(assignment, champions),
        _champion_key(champions),
        tuple(archetypes),
        _IdentityKey(meta),
        _generation(),
    )
    hit = _fit_cache.get(key)
    if hit is not None:
        stats["fit_hits"] += 1
        _fit_cache.move_to_end(key)
        return deepcopy(hit)
    stats["fit_misses"] += 1
    return deepcopy(_remember(_fit_cache, key, _original_player_fit(assignment, champions, archetypes, meta)))


def install() -> None:
    """Route the engine's internal calls through the caches (idempotent)."""
    global _installed
    if _installed:
        return
    engine.score_comp = cached_score_comp
    engine._player_fit_adjustment = cached_player_fit
    _installed = True


def uninstall() -> None:
    global _installed
    engine.score_comp = _original_score_comp
    engine._player_fit_adjustment = _original_player_fit
    _installed = False
    clear()


def clear() -> None:
    """Drop every cached score (call after engine data changes).

    See the module docstring: this does not clear the engine's own lru_cache
    loaders; ``draftcoach.loadfiles.flush_engine_caches`` does both.
    """
    global _MTIME_MEMO
    _score_cache.clear()
    _fit_cache.clear()
    _MTIME_MEMO = (None, ())
    for key in ("score_hits", "score_misses", "fit_hits", "fit_misses"):
        stats[key] = 0
    stats["generation"] += 1
