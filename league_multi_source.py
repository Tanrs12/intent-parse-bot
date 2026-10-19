"""Explicitly attributed statistical sources for the Flexers draft engine.

Only inputs with documented permitted reuse are downloaded automatically.
League of Graphs / LoLalytics / LOL.PS remain reference-only until a supported
feed or permission is supplied. Public pro stats are NEVER mislabelled SoloQ.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from league_source_stats import source_mtime_ns

OPEN_PRO_PATH = Path(__file__).with_name("data") / "league_open_pro_pairs.json"

STAT_SOURCES = {
    "LoLalytics": {
        "url": "https://lolalytics.com/",
        "use": "Solo Queue role win rates and counters",
        "mode": "reference_pending_permission",
    },
    "League of Graphs": {
        "url": "https://www.leagueofgraphs.com/",
        "use": "high-volume counters, synergies and specialist performance",
        "mode": "reference_pending_permission",
    },
    "LOL.PS": {
        "url": "https://lol.ps/en/datastudio/introduction",
        "use": "GM+ counters, duos and item-value analysis",
        "mode": "reference_pending_permission",
    },
    "ChainCC": {
        "url": "https://chaincc.lol/free/data",
        "use": "licensed professional team drafts and role pairings",
        "mode": "scheduled_open_dataset",
        "license": "CC BY 4.0; attribution required",
    },
}

@lru_cache(maxsize=4)
def _read_pairs(path: str, mtime_ns: int) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("source", {}).get("name") != "ChainCC" or not isinstance(d.get("pairs"), list):
            return {}
        return d
    except (OSError, ValueError):
        return {}

def load_open_pairs() -> dict:
    try:
        stamp = source_mtime_ns(OPEN_PRO_PATH)
    except OSError:
        return {}
    return _read_pairs(str(OPEN_PRO_PATH), stamp)

@lru_cache(maxsize=4)
def _pair_index(path: str, stamp: int) -> dict:
    data = _read_pairs(path, stamp)
    return {
        frozenset(((r["role_a"], r["champion_a"]),
                   (r["role_b"], r["champion_b"]))): r
        for r in data.get("pairs", ())
        if isinstance(r, dict) and r.get("role_a") and r.get("role_b")
    }

def open_pro_pair_signal(role_a: str, champ_a: str, role_b: str, champ_b: str) -> dict:
    """Shrink estimated pair lift; missing/undersampled rows return zero.

    The resulting adjustment is small on purpose: pro match samples are
    strongly correlated with team quality and drafting context.
    """
    try:
        stamp = source_mtime_ns(OPEN_PRO_PATH)
    except OSError:
        return {"adjustment": 0.0, "games": 0}
    lookup = frozenset(((role_a, champ_a), (role_b, champ_b)))
    row = _pair_index(str(OPEN_PRO_PATH), stamp).get(lookup)
    if row is None or int(row.get("games") or 0) < 5:
        return {"adjustment": 0.0, "games": 0}
    lift = max(-0.08, min(0.08, float(row.get("shrunken_lift") or 0.0)))
    return {
        "adjustment": round(lift * 9.0, 3),
        "games": int(row["games"]),
        "source": "ChainCC (CC BY 4.0)",
        "patch": load_open_pairs().get("latest_patch"),
    }
