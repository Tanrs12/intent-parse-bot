"""Approved current-patch Solo Queue aggregate evidence for Discord + Draft Coach.

Never request restricted/private statistics endpoints here. Consume only
explicitly licensed JSON exports validated by tools/import_licensed_soloq.py.
The source is a single shared snapshot, with source provenance per row.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from league_source_stats import source_mtime_ns

SOLOQ_PATH = Path(os.environ.get("DRAFTCOACH_LICENSED_SOLOQ_PATH") or
                  (Path(__file__).with_name("data") / "league_licensed_soloq.json"))
ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
MIN_MATCHUP_GAMES = 120
MIN_PAIR_GAMES = 150

@lru_cache(maxsize=4)
def _read(path: str, mtime_ns: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not validate_snapshot(data):
            return {}
        return data
    except (OSError, ValueError):
        return {}

def validate_snapshot(data: object) -> bool:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return False
    if data.get("queue") != "RANKED_SOLO_5x5":
        return False
    if not isinstance(data.get("sources"), list):
        return False
    if any(not isinstance(s, dict) or not s.get("name") or not s.get("permission_reference")
           for s in data["sources"]):
        return False
    return all(isinstance(data.get(key), list) for key in ("champions", "matchups", "pairs"))

def load_licensed_soloq() -> dict:
    try:
        stamp = source_mtime_ns(SOLOQ_PATH)
    except OSError:
        return {}
    return _read(str(SOLOQ_PATH), stamp)

def _current_patch(data: dict, patch: str | None) -> bool:
    if not patch:
        return False
    return data.get("patch") == patch

@lru_cache(maxsize=128)
def _indices(path: str, stamp: int) -> dict:
    data = _read(path, stamp)
    matchup = {}
    champion = {}
    pairs = {}
    for row in data.get("matchups", []):
        if not isinstance(row, dict):
            continue
        try:
            role, enemy, own = row["role"], row["enemy"], row["response"]
            games = int(row["games"])
            edge = float(row["response_advantage"])
        except (KeyError, TypeError, ValueError):
            continue
        if role not in ROLES or games < MIN_MATCHUP_GAMES or not math.isfinite(edge):
            continue
        k = role, enemy, own
        if games > int(matchup.get(k, {}).get("games", 0)):
            matchup[k] = row
    for row in data.get("champions", []):
        if not isinstance(row, dict):
            continue
        try:
            key = (row["role"], row["champion"])
            games = int(row["games"])
            wr = float(row["win_rate"])
        except (KeyError, ValueError, TypeError):
            continue
        if key[0] not in ROLES or games < 300 or not 0 <= wr <= 100:
            continue
        if games > int(champion.get(key, {}).get("games", 0)):
            champion[key] = row
    for row in data.get("pairs", []):
        if not isinstance(row, dict):
            continue
        try:
            a = row["role_a"], row["champion_a"]
            b = row["role_b"], row["champion_b"]
            games = int(row["games"])
            lift = float(row["lift_pp"])
        except (KeyError, TypeError, ValueError):
            continue
        if a[0] not in ROLES or b[0] not in ROLES or a[0] == b[0] or games < MIN_PAIR_GAMES or not math.isfinite(lift):
            continue
        key = frozenset((a, b))
        if games > int(pairs.get(key, {}).get("games", 0)):
            pairs[key] = row
    return {"matchups": matchup, "champions": champion, "pairs": pairs}

def _active(patch: str | None) -> dict:
    data = load_licensed_soloq()
    if not _current_patch(data, patch):
        return {}
    try:
        stamp = source_mtime_ns(SOLOQ_PATH)
    except OSError:
        return {}
    return _indices(str(SOLOQ_PATH), stamp)

def approved_matchups(patch: str | None) -> list[dict]:
    return list(_active(patch).get("matchups", {}).values())

def approved_champions(patch: str | None) -> list[dict]:
    return list(_active(patch).get("champions", {}).values())

def approved_pair_signal(role_a: str, champ_a: str, role_b: str, champ_b: str, patch: str | None) -> dict:
    row = _active(patch).get("pairs", {}).get(frozenset(((role_a, champ_a), (role_b, champ_b))))
    if not row:
        return {"adjustment": 0.0, "games": 0}
    n = int(row["games"])
    # Double-safety against implausibly large raw percentages. This is a
    # secondary signal, smaller than composition and player comfort.
    lift = max(-4.0, min(4.0, float(row["lift_pp"])))
    shrink = n / (n + 800.0)
    return {
        "adjustment": round(lift * shrink * 0.18, 3),
        "games": n,
        "source": row.get("source"),
    }
