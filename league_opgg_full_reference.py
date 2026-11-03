"""Read patch-labelled, private OP.GG full-page matchup references.

Never makes network requests, never reuses old-patch rows for new-patch
scoring, and never redistributes source data. Only observation display.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

SOURCE = "https://op.gg/lol/champions"
ROLES = {"Top", "Jungle", "Mid", "ADC", "Support"}
MAX_PRESENTATION_AGE = 7 * 86400
MIN_RELIABLE_GAMES = 100


def folder() -> Path:
    override = os.environ.get("DRAFTCOACH_OPGG_FULL_DIR", "").strip()
    if override:
        return Path(override)
    base = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    return base / "var" / "opgg_full"


def _canon(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _source_patch(live_patch: str) -> str:
    parts = str(live_patch or "").split(".")
    if len(parts) < 2 or not parts[-1].isdigit():
        return ""
    major = "16" if parts[0] == "26" else parts[0]
    return f"{major}.{int(parts[-1])}"


@lru_cache(maxsize=3)
def _load_index(path: str, stamp: int) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf8"))
        if d.get("schema_version") == 1 and d.get("source") == "OP.GG public counters page":
            return d
    except (OSError, ValueError, TypeError):
        pass
    return {}


def full_for_enemy(role: str, enemy: str, *, patch: str,
                   now: float | None = None, index_path: Path | None = None) -> dict:
    """Return filtered facts, not a pick score or a forced counter recommendation."""
    if role not in ROLES or not enemy:
        return {"rows": [], "status": "invalid_role_or_enemy"}
    expected = _source_patch(patch)
    if not expected:
        return {"rows": [], "status": "unknown_patch"}
    index_file = index_path or folder() / "index.json"
    if not index_file.is_file():
        return {"rows": [], "status": "no_local_index"}
    try:
        d = _load_index(str(index_file), index_file.stat().st_mtime_ns)
    except OSError:
        return {"rows": [], "status": "index_unavailable"}
    candidates = ((d.get("matchups") or {}).get(role) or {}).get(_canon(enemy)) or []
    t = time.time() if now is None else now
    unique = {}
    for row in candidates:
        try:
            age = t - float(row["retrieved_epoch"])
            games = int(row["games"])
            wr = float(row["head_to_head_win_rate"])
        except (TypeError, ValueError, KeyError):
            continue
        if (row.get("source_patch") != expected or
            row.get("rank_filter") != "Emerald+" or
            row.get("queue_filter") != "Ranked Solo/Duo" or
            row.get("region_filter") != "All Servers" or
            not 0 <= age <= MAX_PRESENTATION_AGE or games < MIN_RELIABLE_GAMES or
            not 0 <= wr <= 100):
            continue
        champion = row.get("ours")
        if not champion or _canon(champion) == _canon(enemy):
            continue
        key = _canon(champion)
        if key not in unique or int(unique[key]["games"]) < games:
            unique[key] = {**row, "games": games, "head_to_head_win_rate": wr,
                           "type": "statistical_reference_only"}
    return {
        "status": "matching_patch" if unique else "no_matching_patch_rows",
        "rows": list(unique.values()), "source_patch": expected,
        "rank_filter": "Emerald+", "source": "OP.GG public counters page",
        "note": "Patch/tier-labelled observational win rates, not automatically calibrated counter bonuses.",
    }
