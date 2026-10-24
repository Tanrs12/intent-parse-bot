"""Small, citable qualitative match-up observations.

Unlike numerical SoloQ rows these are hypotheses / kit interactions. They
never contribute invented win-rate or draft-score points. Future community,
VOD and Discord observations should be reviewed and inserted with provenance.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path
from league_source_stats import source_mtime_ns

PATH = Path(__file__).with_name("data") / "league_community_matchup_notes.json"


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@lru_cache(maxsize=4)
def _read(path: str, stamp: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if data.get("schema_version") == 1 else {}
    except (OSError, ValueError):
        return {}


def matchup_notes(role: str, ours: str, enemy: str) -> list[dict]:
    try:
        data = _read(str(PATH), source_mtime_ns(PATH))
    except OSError:
        return []
    return [
        {k: v for k, v in row.items() if k not in {"role", "ours", "enemy"}}
        for row in data.get("entries", [])
        if row.get("role") == role
        and _key(str(row.get("ours", ""))) == _key(ours)
        and _key(str(row.get("enemy", ""))) == _key(enemy)
    ]
