"""Summarise licensed ChainCC pro evidence without conflating seasons/patches.

All reported counts are observed pro matches/co-occurrences, not calibrated
lane strength or cross-season draft scoring.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_chaincc_matchup_index import _folder


def audit(folder: Path) -> dict:
    match = json.loads((folder / "matchup-index.json").read_text(encoding="utf8"))
    duo = json.loads((folder / "duo-index.json").read_text(encoding="utf8"))
    for row in (match, duo):
        if row.get("provider") != "ChainCC" or row.get("licence") != "CC BY 4.0":
            raise ValueError("Unexpected source attribution/license")
    year_match = Counter()
    patch_match = Counter()
    patch_pair_min5 = Counter()
    patch_pair_min20 = Counter()
    for key, info in match["matchups"].items():
        parts = key.split("|")
        n = int(info["games"])
        if len(parts) == 4:
            year_match[parts[0]] += 1
        elif len(parts) == 5:
            label = parts[0] + "|" + parts[1]
            patch_match[label] += 1
            if n >= 5:
                patch_pair_min5[label] += 1
            if n >= 20:
                patch_pair_min20[label] += 1
    year_duo = Counter()
    patch_duo = Counter()
    patch_duo_min5 = Counter()
    patch_duo_min20 = Counter()
    for key, info in duo["duos"].items():
        parts = key.split("|")
        n = int(info["games"])
        if len(parts) == 4:
            year_duo[parts[0]] += 1
        elif len(parts) == 5:
            label = parts[0] + "|" + parts[1]
            patch_duo[label] += 1
            if n >= 5:
                patch_duo_min5[label] += 1
            if n >= 20:
                patch_duo_min20[label] += 1
    recent_patches = sorted(
        (p for p in patch_match if p.startswith("2026|")),
        key=lambda key: tuple(int(x) for x in key.split("|")[1].split(".")),
    )
    recent = recent_patches[-1] if recent_patches else None
    return {
        "years": match.get("years", []),
        "professional_lane_pairings": match.get("matched_lane_pairs", 0),
        "directional_year_and_patch_records": match.get("matchup_records", 0),
        "professional_duo_observations": duo.get("duo_observations", 0),
        "yearly_2026_directional_pairs": year_match["2026"],
        "yearly_2026_duo_pairs": year_duo["2026"],
        "most_recent_2026_patch_label": recent,
        "recent_patch_directional_pairs": patch_match[recent] if recent else 0,
        "recent_patch_directional_pairs_5plus": patch_pair_min5[recent] if recent else 0,
        "recent_patch_directional_pairs_20plus": patch_pair_min20[recent] if recent else 0,
        "recent_patch_duos": patch_duo[recent] if recent else 0,
        "recent_patch_duos_5plus": patch_duo_min5[recent] if recent else 0,
        "recent_patch_duos_20plus": patch_duo_min20[recent] if recent else 0,
        "warning": "Pro observations are selected competitions and teams, not solo-queue counter estimates. Recent-patch small samples require caution.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=_folder())
    args = parser.parse_args()
    print(json.dumps(audit(args.dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
