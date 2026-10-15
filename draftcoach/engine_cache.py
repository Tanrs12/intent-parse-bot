"""Memoisation of the backend's pure scoring calls for the live loop.

``recommend_comps`` scores every candidate composition with ``score_comp`` and
``_player_fit_adjustment``. During a draft each event re-scores mostly the same
compositions (a ban or lock only shrinks the candidate set), so caching by
(champions, players) turns the per-event cost from ~1 s into tens of
milliseconds after the first pass. The engine module itself stays untouched:
the wrappers are installed on the imported module object.

``clear()`` must be called whenever engine data is replaced (a loadfile that
swaps the matchup or meta snapshot) because cached scores depend on it.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import wraps
from typing import Callable

import league_comp_engine as engine  # type: ignore

MAX_ENTRIES = 60_000
_score_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_fit_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_installed = False
_original_score_comp: Callable = engine.score_comp
_original_player_fit: Callable = engine._player_fit_adjustment
stats = {"score_hits": 0, "score_misses": 0, "fit_hits": 0, "fit_misses": 0, "generation": 0}


def _remember(cache: OrderedDict, key: tuple, value: dict) -> dict:
    cache[key] = value
    if len(cache) > MAX_ENTRIES:
        cache.popitem(last=False)
    return value


def _champion_key(champions: dict) -> tuple:
    return tuple((role, champions[role]) for role in engine.ROLES if role in champions)


@wraps(_original_score_comp)
def cached_score_comp(champions: dict, meta: dict | None = None) -> dict:
    key = (_champion_key(champions), id(meta) if meta is not None else 0, stats["generation"])
    hit = _score_cache.get(key)
    if hit is not None:
        stats["score_hits"] += 1
        _score_cache.move_to_end(key)
        return dict(hit)
    stats["score_misses"] += 1
    return dict(_remember(_score_cache, key, _original_score_comp(champions, meta)))


@wraps(_original_player_fit)
def cached_player_fit(assignment: dict, champions: dict, archetypes: list, meta: dict) -> dict:
    key = (
        tuple((role, int(assignment[role]["user_id"])) for role in engine.ROLES),
        _champion_key(champions),
        tuple(archetypes),
        id(meta),
        stats["generation"],
    )
    hit = _fit_cache.get(key)
    if hit is not None:
        stats["fit_hits"] += 1
        _fit_cache.move_to_end(key)
        return dict(hit)
    stats["fit_misses"] += 1
    return dict(_remember(_fit_cache, key, _original_player_fit(assignment, champions, archetypes, meta)))


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
    """Drop every cached score (call after engine data changes)."""
    _score_cache.clear()
    _fit_cache.clear()
    for key in ("score_hits", "score_misses", "fit_hits", "fit_misses"):
        stats[key] = 0
    stats["generation"] += 1
