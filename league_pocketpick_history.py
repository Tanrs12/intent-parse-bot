"""Permissioned PocketPick aggregate history — never label this current-patch D2+.

Steffen explicitly approved use in the user's non-commercial private draft assistant
on 20 Oct 2026. Original methodology/data: https://pocketpick.gg/data/
Only local var/pocketpick aggregate files are read; no HTTP during champ select.
No raw match records or proprietary PocketPick scoring logic.
"""
from __future__ import annotations

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from league_source_stats import source_mtime_ns

SOURCE_URL = "https://pocketpick.gg/data/"
SOURCE_PATCH = "16.16"
SOURCE_RANK = "Silver-Platinum"
SOURCE_REGION = "EUW"
ROLES = {"Top": "TOP", "Jungle": "JUNGLE", "Mid": "MID", "ADC": "ADC", "Support": "SUPPORT"}
# This sample is five patches old and from below our team's rank. Keep it
# historically informative, not a substitute for current high-elo evidence.
MIN_GAMES_TO_SCORE = 40
KNOWN_ALIASES = {"monkeyking": "wukong", "nunu": "nunuandwillump", "nunuwillump": "nunuandwillump", "renata": "renataglasc"}


def storage_dir() -> Path:
    override = os.environ.get("DRAFTCOACH_POCKETPICK_DIR", "").strip()
    if override:
        return Path(override)
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "var" / "pocketpick"


def _norm(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", str(value).lower())
    return KNOWN_ALIASES.get(cleaned, cleaned)


@lru_cache(maxsize=4)
def _read(path: str, mtime_ns: int) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _data() -> tuple[dict, dict]:
    folder = storage_dir()
    a, b = folder / "matchv5-metrics.json", folder / "gd15-matchups.json"
    if not a.is_file() or not b.is_file():
        return {}, {}
    metrics = _read(str(a), source_mtime_ns(a))
    gold = _read(str(b), source_mtime_ns(b))
    if (
        metrics.get("patches") != {SOURCE_PATCH: 30000}
        or gold.get("patch") != {SOURCE_PATCH: 30000}
        or metrics.get("matches") != 30000
        or gold.get("matches") != 30000
        or metrics.get("queue") != 420
        or metrics.get("region") != "euw"
    ):
        return {}, {}
    return metrics, gold


@lru_cache(maxsize=4)
def _index(path: str, mtime_ns: int) -> tuple[dict, dict]:
    data = _read(path, mtime_ns)
    lane = {}
    base = {}
    for champion, by_role in data.get("metrics", {}).items():
        for role, row in by_role.items():
            if isinstance(row, dict) and row.get("games"):
                base[_norm(champion), role] = row
    for key, row in data.get("matchups", {}).items():
        parts = key.split("|")
        if len(parts) != 3 or not isinstance(row, dict):
            continue
        champion, role, enemy = parts
        lane[_norm(champion), role, _norm(enemy)] = row
    return lane, base


def historical_matchup(role: str, our_champion: str, enemy_champion: str) -> dict:
    """All matches remain searchable; thin rows return NO draft-score effect."""
    if role not in ROLES:
        return {}
    metrics, gold = _data()
    if not metrics:
        return {}
    path = storage_dir() / "matchv5-metrics.json"
    rows, baseline = _index(str(path), source_mtime_ns(path))
    key = (_norm(our_champion), ROLES[role], _norm(enemy_champion))
    row = rows.get(key)
    own_base = baseline.get(key[:2])
    if not row or not own_base:
        return {}
    n = int(row.get("n") or 0)
    own_games = int(own_base.get("games") or 0)
    wr = row.get("wr")
    base_wr = own_base.get("wr")
    if wr is None or base_wr is None:
        return {}
    delta = max(-8.0, min(8.0, (float(wr) - float(base_wr)) * 100.0))
    gd15 = row.get("gd15")
    # These tiny retrospective observations are NOT extrapolated to today's
    # high-elo patch. Strict sample floor; shrink and clamp every contribution.
    credible = n >= MIN_GAMES_TO_SCORE and own_games >= 100
    confidence = n / (n + 250.0) if credible else 0.0
    wr_points = delta * confidence * 0.15
    gold_points = max(-900.0, min(900.0, float(gd15 or 0))) / 900.0 * confidence * 0.20
    points = round(max(-0.65, min(0.65, wr_points + gold_points)), 3) if credible else 0.0
    return {
        "source": "PocketPick historical EUW SoloQ aggregate",
        "url": SOURCE_URL, "patch": SOURCE_PATCH, "rank": SOURCE_RANK,
        "queue": 420, "games": n, "overall_games": own_games,
        "match_win_rate": round(float(wr) * 100.0, 2),
        "overall_win_rate": round(float(base_wr) * 100.0, 2),
        "win_rate_residual_pp": round(delta, 2),
        "gold_diff_15": round(float(gd15), 1) if gd15 is not None else None,
        "draft_points": points, "usable_for_ranking": credible,
        "confidence": "low / historical" if credible else "insufficient matchup sample",
    }


def archive_summary() -> dict:
    metrics, gold = _data()
    if not metrics:
        return {"installed": False, "location": str(storage_dir())}
    rows = metrics.get("matchups", {})
    return {
        "installed": True,
        "location": str(storage_dir()),
        "matches": metrics["matches"],
        "matchup_rows": len(rows),
        "champions": len(metrics.get("metrics", {})),
        "scorable_matchups": sum(int(v.get("n") or 0) >= MIN_GAMES_TO_SCORE for v in rows.values()),
        "pair_residual_champions": len(gold.get("pairRes", {})),
        "kit_champions": len(gold.get("kitM", {})),
        "patch": SOURCE_PATCH,
        "rank": SOURCE_RANK,
        "source_url": SOURCE_URL,
    }



@lru_cache(maxsize=4)
def _enemy_catalog(path: str, stamp: int) -> dict:
    """Complete *historical* opponent reference catalogue, including thin rows."""
    from league_matchup_universe import role_universe
    lanes, baselines = _index(path, stamp)
    names_by_role = {
        role: {_norm(name): name for name in role_universe(role)}
        for role in ROLES
    }
    catalog = {}
    for (ours, old_role, enemy), row in lanes.items():
        role = next((role for role, code in ROLES.items()
                     if code == old_role), None)
        if not role:
            continue
        baseline = baselines.get((ours, old_role))
        if not baseline or not isinstance(row, dict):
            continue
        n = int(row.get("n") or 0)
        if not n or row.get("wr") is None or baseline.get("wr") is None:
            continue
        # Include only champion-role pairs in the supported lane catalogue.
        # This is a reference index, not a permission to put an off-pool
        # champion into an active player's draft.
        champ = names_by_role[role].get(ours)
        opp = names_by_role[role].get(enemy)
        if not champ or not opp or ours == enemy:
            continue
        observed_wr = float(row["wr"]) * 100.0
        baseline_wr = float(baseline["wr"]) * 100.0
        if not (0 <= observed_wr <= 100 and 0 <= baseline_wr <= 100):
            continue
        item = {
            "champion": champ, "enemy": opp, "role": role,
            "games": n,
            "head_to_head_win_rate": round(observed_wr, 2),
            "role_baseline_win_rate": round(baseline_wr, 2),
            "adjusted_matchup_lift_pp": round(observed_wr - baseline_wr, 2),
            "overall_games": int(baseline.get("games") or 0),
            "gold_diff_15": (round(float(row["gd15"]), 1)
                             if row.get("gd15") is not None else None),
            "source": "PocketPick", "source_url": SOURCE_URL,
            "source_patch": SOURCE_PATCH, "rank_filter": SOURCE_RANK,
            "region_filter": SOURCE_REGION, "queue_filter": "Ranked Solo/Duo",
            "source_kind": "historical_reference_only",
            "usable_for_current_patch_autopick": False,
            "sample_grade": ("thin" if n < 40 else
                             "moderate-historical" if n < 100 else
                             "observed-historical"),
        }
        catalog.setdefault((role, enemy), []).append(item)
    for values in catalog.values():
        values.sort(key=lambda row: row["games"], reverse=True)
    return catalog


def historical_for_enemy(role: str, enemy: str) -> dict:
    """Read all sampled lanes for an enemy, with age/rank/sample labels.

    This fills *scouting* gaps for 172 currently supported champions, but
    must not be interpreted as Emerald+ patch 26.21 statistics or used to
    pick a champion automatically. Does not access any external service.
    """
    if role not in ROLES or not enemy:
        return {"status": "unsupported_role", "rows": []}
    metrics, _gold = _data()
    if not metrics:
        return {"status": "historical_archive_missing", "rows": []}
    path = storage_dir() / "matchv5-metrics.json"
    catalog = _enemy_catalog(str(path), source_mtime_ns(path))
    rows = catalog.get((role, _norm(enemy)), [])
    return {
        "status": "historical_only" if rows else "no_historical_rows",
        "rows": rows,
        "patch": SOURCE_PATCH, "rank": SOURCE_RANK, "region": SOURCE_REGION,
        "source_url": SOURCE_URL, "observations": len(rows),
        "warning": "One older-patch, lower-rank sample. Observations are not current counters.",
    }
