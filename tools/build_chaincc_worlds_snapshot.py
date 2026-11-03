"""Aggregate ChainCC Worlds 2026 drafts under CC BY 4.0.

No claim that pick1..pick5 are chronological. This is a frozen snapshot of
available professional picks, bans and actual lane-identified duo appearances.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_chaincc_matchup_index import _folder

ROLES = {"jng":"Jungle", "mid":"Mid", "bot":"ADC", "sup":"Support", "top":"Top"}
PACKAGES = (("Mid","Jungle"),("ADC","Support"))


def _csv_rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def build_snapshot(folder: Path, *, league: str = "WLDs", year: int = 2026) -> dict:
    teams_file = folder / f"chaincc-teams-{year}.csv.gz"
    players_file = folder / f"chaincc-players-{year}.csv.gz"
    if not teams_file.is_file() or not players_file.is_file():
        raise FileNotFoundError("The approved ChainCC team/player archive is missing")
    teams = {}
    picks, bans, winning_picks = Counter(), Counter(), Counter()
    appearances, champion_games = defaultdict(set), defaultdict(set)
    patches = Counter()
    last_date = ""
    for row in _csv_rows(teams_file):
        if row.get("league") != league:
            continue
        key = (row["game_id"], row["side"].casefold())
        if key in teams:
            raise ValueError("duplicate game-side in ChainCC Worlds source")
        teams[key] = row
        patches[row["patch"]] += 1
        last_date = max(last_date, row["date"])
        selections = {row.get(f"pick{i}", "").strip() for i in range(1,6)}
        prohibitions = {row.get(f"ban{i}", "").strip() for i in range(1,6)}
        for champ in selections - {""}:
            picks[champ] += 1
            appearances[champ].add(row["game_id"])
            if row["result"].strip().upper() in {"TRUE","1"}:
                winning_picks[champ] += 1
        for champ in prohibitions - {""}:
            bans[champ] += 1
            appearances[champ].add(row["game_id"])
    if not teams:
        raise ValueError("No matching Worlds games at this league code")
    slots = defaultdict(dict)
    for row in _csv_rows(players_file):
        key = (row.get("game_id"), row.get("side", "").casefold())
        if key not in teams:
            continue
        role = ROLES.get(row.get("position","").casefold())
        if role and row.get("champion"):
            if role in slots[key]:
                raise ValueError("Duplicate professional lane role per game-side")
            slots[key][role] = row
    duos = defaultdict(lambda: [0,0])
    for key, champs in slots.items():
        for a,b in PACKAGES:
            if a not in champs or b not in champs:
                continue
            one, two = champs[a], champs[b]
            if (one["patch"] != two["patch"]
                or one["result"] != two["result"]):
                continue
            pairkey = f"{a}+{b}|{one['champion']}|{two['champion']}"
            duos[pairkey][0] += 1
            duos[pairkey][1] += int(one["result"].strip().upper() in {"TRUE","1"})
    return {
        "schema_version": 1, "provider": "ChainCC",
        "source_url": "https://chaincc.lol/free/data/",
        "licence": "CC BY 4.0",
        "attribution": "ChainCC. League of Legends esports match dataset. chaincc.lol/free/data",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_code": league, "season": year,
        "last_source_game_date": last_date,
        "games": len({game_id for game_id, side in teams}),
        "team_sides": len(teams),
        "patch_team_sides": dict(patches),
        "champions": [
            {"champion": champ, "picked": picks[champ], "banned": bans[champ],
             "picked_wins": winning_picks[champ],
             "games_picked_or_banned": len(appearances[champ])}
            for champ in sorted(set(picks) | set(bans))],
        "duos": [
            {"package": package, "first": first, "second": second,
             "games": n, "wins": w}
            for key, (n,w) in sorted(duos.items())
            for package, first, second in [key.split("|",2)]],
        "limitations": "Frozen tournament sample; picks/bans not chronological, no inferred counterpicks, no automatic scoring.",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", type=Path, default=_folder())
    p.add_argument("--league", default="WLDs")
    p.add_argument("--output", type=Path, default=ROOT / "data" / "league_worlds_2026_chaincc_snapshot.json")
    args = p.parse_args()
    result = build_snapshot(args.dir, league=args.league)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({
        "output":str(args.output),"games":result["games"],
        "team_sides":result["team_sides"],"last_game":result["last_source_game_date"],
        "patches":result["patch_team_sides"],
        "champions":len(result["champions"]),"duo_pairs":len(result["duos"]),
        "most_picked": sorted(result["champions"],key=lambda x:-x["picked"])[:10],
        "most_banned": sorted(result["champions"],key=lambda x:-x["banned"])[:10],
    }, indent=2))


if __name__=="__main__":
    raise SystemExit(main())
