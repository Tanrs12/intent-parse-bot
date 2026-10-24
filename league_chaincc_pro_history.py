"""Indexed ChainCC CC BY 4.0 professional matchup history.

Source: ChainCC. League of Legends esports match dataset.
https://chaincc.lol/free/data/ — CC BY 4.0 attribution required.

Always observational professional-game outcomes, never inferred draft chronology
or SoloQ counter win rates. Exact patch first, same-season fallback second.
"""
from __future__ import annotations
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from league_source_stats import source_mtime_ns

SOURCE = "https://chaincc.lol/free/data/"
ROLES = {"Top", "Jungle", "Mid", "ADC", "Support"}


def _folder() -> Path:
    override = os.environ.get("DRAFTCOACH_CHAINCC_DIR", "").strip()
    if override:
        return Path(override)
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "var" / "chaincc"


@lru_cache(maxsize=2)
def _load(path: str, stamp: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (data.get("schema_version") == 1
            and data.get("provider") == "ChainCC"
            and data.get("licence") == "CC BY 4.0"):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _index() -> dict:
    file = _folder() / "matchup-index.json"
    if not file.is_file():
        return {}
    return _load(str(file), source_mtime_ns(file))


def _canon_patch(patch: str | None) -> str:
    if not patch:
        return ""
    parts = str(patch).split(".")
    if len(parts) < 2:
        return ""
    minor = parts[-1]
    if not minor.isdigit():
        return ""
    # Official 2026 client patch 26.x; esports historical records use 16.x
    major = parts[0]
    return ("16" if major == "26" else major) + "." + str(int(minor))


def pro_matchup(role: str, ours: str, enemy: str, *, patch: str = "26.21", year: int = 2026) -> dict:
    """Return factual pro matchup evidence, not a lane-counter point bonus."""
    if role not in ROLES or not ours or not enemy:
        return {}
    index = _index()
    if not index:
        return {}
    keys = [f"{year}|{_canon_patch(patch)}|{role}|{ours}|{enemy}",
            f"{year}|{role}|{ours}|{enemy}"]
    row, chosen = None, ""
    for key in keys:
        value = index.get("matchups", {}).get(key)
        if value is not None:
            row, chosen = value, key
            break
    if row is None:
        return {}
    exact = chosen == keys[0]
    n = int(row.get("games", 0))
    return {
        "source": "ChainCC professional match archive",
        "source_url": SOURCE, "licence": "CC BY 4.0",
        "attribution": "ChainCC. League of Legends esports match dataset. chaincc.lol/free/data",
        "year": year, "patch": _canon_patch(patch) if exact else f"{year} season combined",
        "exact_patch": exact, "games": n, "wins": int(row.get("wins", 0)),
        "match_win_rate": row.get("win_rate"),
        "avg_lane_gold_diff_15": row.get("avg_gd15"),
        "gold_diff_samples": int(row.get("gd15_games", 0)),
        "confidence": "limited" if n < 20 else "observational professional evidence",
        "draft_points": 0,
        "note": "Observed pro lane matchup; not independent lane strength or proven counterpick.",
    }


def pro_data_summary() -> dict:
    data = _index()
    return {
        "installed": bool(data),
        "years": data.get("years", []),
        "player_rows": data.get("player_rows", {}),
        "team_rows": data.get("team_rows", {}),
        "matched_lane_pairs": data.get("matched_lane_pairs", 0),
        "matchup_records": data.get("matchup_records", 0),
        "source": SOURCE, "licence": "CC BY 4.0",
    }
