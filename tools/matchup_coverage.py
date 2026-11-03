"""Show matchups with and without qualified source evidence.

Run: python tools/matchup_coverage.py
The denominator is every non-mirror positional champion pair in the current
Meraki role catalogue. It does not claim missing pairs are neutral matchups.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from league_comp_engine import load_lane_matchups
from league_matchup_universe import matrix_coverage


def main() -> int:
    sample = load_lane_matchups()
    coverage = matrix_coverage(sample)
    count_possible = count_sampled = 0
    print("Verified sampled role-pair coverage (100+ recorded games)")
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
    print("Historical bundled sample:", source.get("site"), source.get("patches_detected"))
    print("No numeric evidence for a pair never implies an even matchup.")
    print("Qualified current-patch Riot samples are separate from this table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
