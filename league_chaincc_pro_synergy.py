"""Read-only ChainCC professional duo history, separate from draft scoring.

Mid+Jungle and ADC+Support combinations come from observed rosters on the
same team, never inferred as chronological picks or causal synergy.
ChainCC esports dataset: https://chaincc.lol/free/data/ (CC BY 4.0).
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

from league_source_stats import source_mtime_ns

SOURCE = "https://chaincc.lol/free/data/"
PACKAGES = {"Mid+Jungle", "ADC+Support"}


def _folder() -> Path:
    override = os.environ.get("DRAFTCOACH_CHAINCC_DIR", "").strip()
    if override:
        return Path(override)
    base = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    return base / "var" / "chaincc"


@lru_cache(maxsize=2)
def _read(path: str, stamp: int) -> dict:
    try:
        row = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (row.get("schema_version") == 1
        and row.get("provider") == "ChainCC"
        and row.get("licence") == "CC BY 4.0"):
        return row
    return {}


def _index() -> dict:
    file = _folder() / "duo-index.json"
    if not file.is_file():
        return {}
    return _read(str(file), source_mtime_ns(file))


def _canon_patch(patch: str) -> str:
    parts = str(patch or "").split(".")
    if len(parts) < 2 or not parts[-1].isdigit():
        return ""
    major = "16" if parts[0] == "26" else parts[0]
    return major + "." + str(int(parts[-1]))


def pro_duo(package: str, first: str, second: str, *,
            patch: str = "26.21", year: int = 2026) -> dict:
    """Return attributed pro observations; NEVER a numerical draft bonus."""
    if package not in PACKAGES or not first or not second:
        return {}
    data = _index()
    if not data:
        return {}
    current = _canon_patch(patch)
    keys = ([f"{year}|{current}|{package}|{first}|{second}"] if current else [])
    keys.append(f"{year}|{package}|{first}|{second}")
    for index, key in enumerate(keys):
        row = (data.get("duos") or {}).get(key)
        if not row:
            continue
        games = int(row.get("games", 0))
        if games <= 0:
            continue
        exact = index == 0 and bool(current)
        return {
            "provider": "ChainCC", "source_url": SOURCE,
            "licence": "CC BY 4.0",
            "attribution": "ChainCC. League of Legends esports match dataset. chaincc.lol/free/data",
            "package": package, "first": first, "second": second,
            "year": year, "patch": current if exact else f"{year} season aggregate",
            "exact_patch": exact, "games": games,
            "wins": int(row.get("wins", 0)),
            "win_rate": row.get("win_rate"),
            "confidence": "low sample" if games < 20 else "observed professional co-occurrence",
            "draft_points": 0,
            "warning": "Observed professional duo outcomes, not controlled synergy or a counterpick statistic.",
        }
    return {}


def pro_duo_summary() -> dict:
    data = _index()
    return {
        "installed": bool(data),
        "years": data.get("years", []),
        "team_sides": data.get("team_sides", 0),
        "duo_observations": data.get("duo_observations", 0),
        "duo_records": data.get("duo_records", 0),
        "source": SOURCE, "licence": "CC BY 4.0",
    }
