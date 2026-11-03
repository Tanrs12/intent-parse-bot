"""Audit matchup search breadth and empirical coverage without making up samples.

Usage:
  python tools/matchup_coverage.py
  python tools/matchup_coverage.py --pocketpick-dir "C:/path/to/private/var/pocketpick"

No data leaves the local machine. Historical counts do not imply current win
rates, especially for low-sample / older-patch observations.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from league_comp_engine import load_lane_matchups
from league_matchup_universe import matrix_coverage, role_universe


def _norm(value: str) -> str:
    return re.sub("[^a-z0-9]", "", str(value).lower())


def historical_coverage(folder: Path) -> dict | None:
    source = folder / "matchv5-metrics.json"
    if not source.is_file():
        return None
    try:
        metrics = json.loads(source.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    # Private PocketPick export contract: ignore unrelated/invalid files.
    if metrics.get("matches") != 30000 or metrics.get("patches") != {"16.16": 30000}:
        return None
    source_roles = {"TOP": "Top", "JUNGLE": "Jungle", "MID": "Mid",
                    "ADC": "ADC", "SUPPORT": "Support"}
    available = {role: {_norm(name) for name in role_universe(role)}
                 for role in source_roles.values()}
    counts = {role: {1: set(), 40: set(), 100: set()} for role in available}
    for key, data in (metrics.get("matchups") or {}).items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        own, name, enemy = parts
        role = source_roles.get(name)
        if (not role or _norm(own) == _norm(enemy)
            or _norm(own) not in available[role]
            or _norm(enemy) not in available[role]):
            continue
        n = int((data or {}).get("n", 0) or 0)
        for threshold in (1, 40, 100):
            if n >= threshold:
                counts[role][threshold].add((_norm(own), _norm(enemy)))
    return {role: {threshold: len(group) for threshold, group in levels.items()}
            for role, levels in counts.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pocketpick-dir", type=Path, default=None,
                        help="optional folder containing the user's private permitted PocketPick export")
    args = parser.parse_args()
    sample = load_lane_matchups()
    coverage = matrix_coverage(sample)
    count_possible = count_sampled = 0
    print("Bundled/approved sampled role-pair coverage (100+ recorded games)")
    print("Role      Champions  Eligible pairs  Sampled pairs  Missing  Coverage")
    for role, stat in coverage.items():
        print(f"{role:<9} {stat['role_champions']:>9}  {stat['possible']:>14}"
              f" {stat['sampled_100_plus']:>14} {stat['missing']:>8}"
              f" {stat['coverage_percent']:>8.1f}%")
        count_possible += stat["possible"]
        count_sampled += stat["sampled_100_plus"]
    print(f"TOTAL     {'':>9} {count_possible:>14} {count_sampled:>14}"
          f" {count_possible - count_sampled:>8}"
          f" {count_sampled / count_possible * 100 if count_possible else 0:>8.1f}%")
    source = sample.get("source") or {}
    print("Bundled sample:", source.get("site"), source.get("patches_detected"))

    if args.pocketpick_dir is not None:
        older = historical_coverage(args.pocketpick_dir)
        if older is None:
            print("Private historical file not present or invalid; no historical coverage inferred.")
        else:
            print("\nPrivate PocketPick historical 16.16: observed / 40+ / 100+ pair rows")
            for role, values in older.items():
                print(f"{role:<9} {values[1]:>5} / {values[40]:>5} / {values[100]:>5}")
            print("This was a separate EUW Silver-Platinum sample, NOT current-patch confirmation.")
    print("\nMissing evidence never means a matchup is even; old data is always labelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
