"""Audit the locally collected full OP.GG page database without network use.

Counts are directed matchup observations, not uniquely scored counters.
Separate from official-MCP top-three counter cache and older data sources.
"""
from __future__ import annotations
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_matchup_universe import ROLES, role_universe
from league_opgg_full_reference import folder as default_folder


def _canon(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def audit(folder: Path) -> dict:
    filename = folder / "index.json"
    data = json.loads(filename.read_text(encoding="utf8"))
    if data.get("source") != "OP.GG public counters page" or data.get("schema_version") != 1:
        raise ValueError("not an OP.GG full counters index")
    roles = {}
    total = total_catalogue = total_100 = 0
    for role in ROLES:
        roster = {_canon(x) for x in role_universe(role)}
        matched = set()
        all_pairs = set()
        samples = 0
        for enemy, candidates in ((data.get("matchups") or {}).get(role) or {}).items():
            for row in candidates:
                our = _canon(row.get("ours"))
                if our == _canon(enemy):
                    continue
                key = (our, _canon(enemy))
                all_pairs.add(key)
                if our in roster and _canon(enemy) in roster:
                    matched.add(key)
                if int(row.get("games", 0)) >= 100:
                    samples += 1
        total += len(all_pairs)
        total_catalogue += len(matched)
        total_100 += samples
        possible = len(roster) * (len(roster) - 1)
        roles[role] = {
            "source_rows": len(all_pairs),
            "catalogue_pairs": len(matched),
            "min_100_game_rows": samples,
            "eligible_catalogue_pairs": possible,
            "catalogue_coverage_pct": round(100 * len(matched) / possible, 1) if possible else 0,
        }
    return {
        "page_snapshots": data.get("pages", 0),
        "source_observations": data.get("observations", 0),
        "distinct_source_pair_observations": total,
        "catalogue_pair_observations": total_catalogue,
        "min_100_game_rows": total_100,
        "patch_pages": data.get("by_patch", {}),
        "roles": roles,
        "caution": "Patch/tier-labelled page statistics; overlaps historical and MCP references; not independent samples to add.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--folder",type=Path,default=default_folder())
    options = p.parse_args()
    print(json.dumps(audit(options.folder),indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
