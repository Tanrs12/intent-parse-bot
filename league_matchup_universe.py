"""Role-wide counter scouting without manufacturing matchup win rates.

The entire Meraki champion-position catalogue is eligible for scouting; only
qualified numerical observations enter auto-drafting. Human-reviewed directional
notes can suggest manual exploration, never apply score points or client actions.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from league_source_stats import source_mtime_ns

DATA = Path(__file__).with_name("data")
ROLE_FILE = DATA / "league_role_champions.json"
SCOUT_FILE = DATA / "league_curated_counter_scout.json"
OTP_FILE = DATA / "league_otp_matchup_notes.json"
HIGH_ELO_GUIDE_FILE = DATA / "league_onetricks_matchup_notes.json"
ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")


def _normalise(champion: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(champion).casefold())


@lru_cache(maxsize=4)
def _role_catalog(path: str, stamp: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        roles = data.get("roles") or {}
        return {r: tuple(dict.fromkeys(str(c) for c in roles.get(r, []) if c))
                for r in ROLES}
    except (OSError, ValueError, TypeError):
        return {}


def role_universe(role: str) -> tuple[str, ...]:
    """All valid published positions, not the eight opponents in the old sample."""
    if role not in ROLES:
        return ()
    try:
        return _role_catalog(str(ROLE_FILE), source_mtime_ns(ROLE_FILE)).get(role, ())
    except OSError:
        return ()


@lru_cache(maxsize=4)
def _scout_catalog(path: str, stamp: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            return {}
        entries = {}
        for row in data.get("entries") or []:
            if row.get("role") not in ROLES or row.get("verdict") not in (
                "favourable", "unfavourable", "mixed"
            ):
                continue
            sources = row.get("sources") or []
            if not sources or not all(s.get("url", "").startswith("https://")
                                      for s in sources):
                continue
            key = (row["role"], _normalise(row.get("our")), _normalise(row.get("enemy")))
            entries[key] = row
        return entries
    except (OSError, ValueError, TypeError):
        return {}


def scout_note(role: str, ours: str, enemy: str) -> dict:
    """Read reviewed signals; multi-source curated > ranked single-guide > anecdote."""
    key = (role, _normalise(ours), _normalise(enemy))
    for path in (SCOUT_FILE, HIGH_ELO_GUIDE_FILE, OTP_FILE):
        try:
            rows = _scout_catalog(str(path), source_mtime_ns(path))
        except OSError:
            continue
        result = rows.get(key)
        if result:
            return result
    return {}


def matrix_coverage(matchups: dict) -> dict:
    """Honest denominator of all non-mirror role matchups, including missing data."""
    results = {}
    for role in ROLES:
        roster = set(role_universe(role))
        eligible = len(roster) * (len(roster) - 1)
        pairs = set()
        enemies = (matchups.get("matchups") or {}).get(role, {})
        for enemy, data in enemies.items():
            if enemy not in roster:
                continue
            for ours, observation in (data.get("responses") or {}).items():
                if ours == enemy or ours not in roster:
                    continue
                if int(observation.get("games", 0) or 0) >= 100:
                    pairs.add((ours, enemy))
        results[role] = {
            "role_champions": len(roster),
            "possible": eligible,
            "sampled_100_plus": len(pairs),
            "missing": eligible - len(pairs),
            "coverage_percent": round(len(pairs) / eligible * 100, 1) if eligible else 0.0,
        }
    return results
