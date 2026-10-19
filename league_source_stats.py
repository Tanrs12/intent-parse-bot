"""Thread-local, per-evaluation source-stat caching for large draft searches.

A draft evaluates thousands of compositions against identical pro and
authorised-source snapshots. Re-statting the same JSON file on Windows for
every champion pairing is expensive. A scope snapshots file mtimes once for
one logical calculation, then discards the cache. Outside a scope file-change
checks are immediate; across evaluations newly refreshed datasets are seen.
"""
from __future__ import annotations

from functools import wraps
from pathlib import Path
from threading import local

_SOURCE_CACHE = local()


def source_mtime_ns(path: Path) -> int:
    """Return one mtime per source per active evaluation; preserve OSError."""
    cache = getattr(_SOURCE_CACHE, "mtimes", None)
    if cache is None:
        return path.stat().st_mtime_ns
    key = str(path)
    if key not in cache:
        cache[key] = path.stat().st_mtime_ns
    return cache[key]


def source_stat_scope(func):
    """Reuse source snapshots inside one calculation, never between drafts."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        outer = getattr(_SOURCE_CACHE, "mtimes", None)
        if outer is None:
            _SOURCE_CACHE.mtimes = {}
        try:
            return func(*args, **kwargs)
        finally:
            if outer is None:
                del _SOURCE_CACHE.mtimes
    return wrapped
