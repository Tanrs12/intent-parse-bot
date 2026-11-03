"""Audit distinct matchup evidence by source and sample threshold.

This tool never copies or publishes the private PocketPick/OP.GG provider rows.
A recorded historical matchup is not proof of a current-patch counter.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Mapping

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_comp_engine import load_lane_matchups
from league_matchup_universe import ROLES, role_universe
from league_pocketpick_history import storage_dir

SOURCE_ROLES = {"TOP": "Top", "JUNGLE": "Jungle", "MID": "Mid", "ADC": "ADC", "SUPPORT": "Support"}
ALIASES = {"monkeyking": "wukong", "nunu": "nunuandwillump"}


def norm(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", str(name).lower())
    return ALIASES.get(cleaned, cleaned)


def inventory(
    bundled: dict,
    pocket: dict | None,
    *,
    available: Mapping[str, set[str]] | None = None,
) -> dict:
    """Compute independent and union sets; thresholds are per-source, no merging counts."""
    roles = available or {role: set(role_universe(role)) for role in ROLES}
    legitimate = {role: {norm(n) for n in names} for role, names in roles.items()}
    current = {role: set() for role in ROLES}
    historical = {role: {1: set(), 40: set(), 100: set()} for role in ROLES}
    for role in ROLES:
        for enemy, info in ((bundled.get("matchups") or {}).get(role) or {}).items():
            for ours, sample in (info.get("responses") or {}).items():
                x, y = norm(ours), norm(enemy)
                if x != y and x in legitimate[role] and y in legitimate[role]:
                    if int((sample or {}).get("games", 0) or 0) >= 100:
                        current[role].add((x, y))
    pocket_valid = bool(
        pocket and pocket.get("patches") == {"16.16": 30000}
        and pocket.get("matches") == 30000 and pocket.get("queue") == 420
        and pocket.get("region") == "euw"
    )
    raw_count = len((pocket or {}).get("matchups") or {}) if pocket_valid else 0
    if pocket_valid:
        for key, sample in pocket["matchups"].items():
            parts = key.split("|")
            if len(parts) != 3:
                continue
            ours, old_role, enemy = parts
            role = SOURCE_ROLES.get(old_role)
            if role is None:
                continue
            x, y = norm(ours), norm(enemy)
            if x == y or x not in legitimate[role] or y not in legitimate[role]:
                continue
            n = int((sample or {}).get("n", 0) or 0)
            for minimum in (1, 40, 100):
                if n >= minimum:
                    historical[role][minimum].add((x, y))

    summary = {}
    for role in ROLES:
        old = historical[role]
        new = current[role]
        eligible = len(legitimate[role]) * (len(legitimate[role]) - 1)
        summary[role] = {
            "eligible": eligible,
            "bundled_100_plus": len(new),
            "pocket_observed_1_plus": len(old[1]),
            "pocket_40_plus": len(old[40]),
            "pocket_100_plus": len(old[100]),
            "union_observed_or_bundled": len(new | old[1]),
            "union_40_plus_or_bundled": len(new | old[40]),
            "union_100_plus": len(new | old[100]),
            "historical_additions_40_plus": len(old[40] - new),
        }
    return {
        "pocket_provenance_valid": pocket_valid,
        "pocket_raw_pair_rows": raw_count,
        "sources": {"bundled": "LoLalytics historical 16.20",
                    "private": "PocketPick historical 16.16 EUW Silver-Platinum"},
        "roles": summary,
        "totals": {key: sum(v[key] for v in summary.values())
                   for key in next(iter(summary.values()))},
        "warning": "Historic source sample sizes never imply same-patch high-rank counter estimates.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pocketpick-dir", type=Path, default=storage_dir())
    parser.add_argument("--json", action="store_true", help="Counts-only JSON output")
    args = parser.parse_args()
    source = args.pocketpick_dir / "matchv5-metrics.json"
    try:
        pocket = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pocket = None
    data = inventory(load_lane_matchups(), pocket)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print("Source-aware *historical* pair coverage; read-only, no provider uploads")
        print("Role     Old 100+  Pocket 1+  Pocket 40+  Union 40+  Union 100+")
        for role, v in data["roles"].items():
            print(f"{role:<8} {v['bundled_100_plus']:>8} {v['pocket_observed_1_plus']:>10} "
                  f"{v['pocket_40_plus']:>11} {v['union_40_plus_or_bundled']:>10} "
                  f"{v['union_100_plus']:>11}")
        v = data["totals"]
        print(f"TOTAL    {v['bundled_100_plus']:>8} {v['pocket_observed_1_plus']:>10} "
              f"{v['pocket_40_plus']:>11} {v['union_40_plus_or_bundled']:>10} "
              f"{v['union_100_plus']:>11}")
        print("Local private source validated:", data["pocket_provenance_valid"])
        print(data["warning"])
    return 0 if data["pocket_provenance_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
