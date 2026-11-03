"""Index licensed ChainCC pro duo co-occurrences, preserving patch/year context.

Two packages: Mid+Jungle and ADC+Support. Does not infer game phase, champion
pick order, counterpick status, causal synergy, or numerical draft bonuses.
CC BY 4.0: ChainCC League of Legends esports match dataset, chaincc.lol/free/data
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_chaincc_matchup_index import _ensure_source, _folder

POSITIONS = {"top": "Top", "jng": "Jungle", "mid": "Mid", "bot": "ADC", "sup": "Support"}
PACKAGES = (("Mid", "Jungle"), ("ADC", "Support"))
EXPECTED = {"game_id", "side", "position", "champion", "result", "patch"}


def build_duo_index(folder: Path, *, years: tuple[int, ...], download: bool = False) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    counts = defaultdict(lambda: [0, 0])
    games_per_year = {}
    team_sides = 0
    for year in years:
        archive = _ensure_source(folder, "players", year, download)
        team_by_side = {}
        with gzip.open(archive, "rt", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not EXPECTED.issubset(reader.fieldnames or []):
                raise ValueError(f"Unexpected ChainCC columns: {archive.name}")
            for row in reader:
                side = row["side"].casefold()
                role = POSITIONS.get(row["position"].casefold())
                champion = row["champion"].strip()
                if side not in ("red", "blue") or not role or not champion:
                    continue
                key = (row["game_id"], side)
                slot = team_by_side.setdefault(key, {})
                # A duplicated position invalidates that side to avoid fake duos.
                if role in slot:
                    slot["_invalid"] = True
                slot[role] = (champion, row["patch"], row["result"].strip().upper() in {"TRUE", "1"})
        year_team_sides = 0
        for slots in team_by_side.values():
            if slots.get("_invalid"):
                continue
            year_team_sides += 1
            for first, second in PACKAGES:
                if first not in slots or second not in slots:
                    continue
                one, two = slots[first], slots[second]
                if one[1] != two[1] or one[2] != two[2] or not one[1]:
                    continue
                package = f"{first}+{second}"
                for period in (str(year), f"{year}|{one[1]}"):
                    key = "|".join((period, package, one[0], two[0]))
                    values = counts[key]
                    values[0] += 1
                    values[1] += int(one[2])
        games_per_year[str(year)] = year_team_sides // 2
        team_sides += year_team_sides
    duos = {key: {"games": n, "wins": w, "win_rate": round(100 * w / n, 2)}
            for key, (n, w) in counts.items()}
    index = {
        "schema_version": 1,
        "provider": "ChainCC", "licence": "CC BY 4.0",
        "source": "https://chaincc.lol/free/data/",
        "attribution": "ChainCC. League of Legends esports match dataset. chaincc.lol/free/data",
        "years": list(years), "games_per_year": games_per_year,
        "team_sides": team_sides,
        "duo_observations": sum(n for key, (n, _) in counts.items()
                                if key.count("|") == 3),
        "duo_records": len(duos),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "packages": ["Mid+Jungle", "ADC+Support"],
        "warning": "Historical pro co-occurrence only; win rate is not causal champion synergy or a live draft point.",
        "duos": duos,
    }
    target = folder / "duo-index.json"
    temp = target.with_suffix(".json.incoming")
    temp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(target)
    return {k: v for k, v in index.items() if k != "duos"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", type=Path, default=_folder())
    p.add_argument("--years", nargs="+", type=int, default=[2025, 2026])
    p.add_argument("--all-years", action="store_true")
    p.add_argument("--download", action="store_true")
    a = p.parse_args()
    years = tuple(range(2014, 2027)) if a.all_years else tuple(sorted(set(a.years)))
    if not years or any(y < 2014 or y > 2026 for y in years):
        p.error("Supported years are 2014-2026")
    print(json.dumps(build_duo_index(a.dir, years=years, download=a.download), indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
