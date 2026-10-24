"""Citable matchup observations and public-reference links (never win rates)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from league_source_stats import source_mtime_ns
from league_matchup_catalog import lookup_matchup_references

DATA_DIR = Path(__file__).with_name("data")
PATHS = (
    DATA_DIR / "league_community_matchup_notes.json",
    DATA_DIR / "league_high_elo_matchup_notes.json",
)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


@lru_cache(maxsize=8)
def _read(path: str, stamp: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if data.get("schema_version") == 1 else {}
    except (OSError, ValueError):
        return {}


def matchup_notes(role: str, ours: str, enemy: str) -> list[dict]:
    found: list[dict] = []
    for path in PATHS:
        try:
            data = _read(str(path), source_mtime_ns(path))
        except OSError:
            continue
        found.extend(
            {k: v for k, v in row.items() if k not in {"role", "ours", "enemy"}}
            for row in data.get("entries", [])
            if row.get("role") == role
            and _key(str(row.get("ours", ""))) == _key(ours)
            and _key(str(row.get("enemy", ""))) == _key(enemy)
        )
    # Reference links, not extra matchup score or claims of tier certainty.
    found.extend(lookup_matchup_references(role, ours, enemy, max_results=3))
    return found
