"""Import ChainCC CC BY 4.0 professional-game CSVs into a compact local index.

Source: https://chaincc.lol/free/data/ — attribution required.
Usage: python tools/build_chaincc_matchup_index.py
Can be rerun when new season files appear. Raw CSVs and derived summaries
live in var/chaincc, not in our public repository.

This does not infer chronological pick order from pick1..pick5, does not
pretend a pro matchup rate is a SoloQ win rate, and does not use match
results in the current draft as if they were pre-draft knowledge.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSITIONS = {"top": "Top", "jng": "Jungle", "mid": "Mid", "bot": "ADC", "sup": "Support"}
BASE_URL = "https://chaincc.lol/data/"
MAX_GZ_BYTES = 30_000_000


def _folder() -> Path:
    explicit = os.getenv("DRAFTCOACH_CHAINCC_DIR", "").strip()
    if explicit:
        return Path(explicit)
    return ROOT / "var" / "chaincc"


def _ensure_source(folder: Path, kind: str, year: int, download: bool) -> Path:
    name = f"chaincc-{kind}-{year}.csv.gz"
    path = folder / name
    if not path.is_file() and download:
        req = urllib.request.Request(BASE_URL + name, headers={"User-Agent": "INTentDraftCoach-Research/1"})
        with urllib.request.urlopen(req, timeout=90) as response:
            data = response.read(MAX_GZ_BYTES + 1)
        if not 0 < len(data) <= MAX_GZ_BYTES:
            raise ValueError(f"Unexpected file size: {name}")
        tmp = path.with_suffix(path.suffix + ".incoming")
        tmp.write_bytes(data)
        tmp.replace(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > MAX_GZ_BYTES:
        raise ValueError("Source archive unexpectedly large")
    return path


def _rows(path: Path, expected: set[str]):
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not expected.issubset(set(reader.fieldnames or [])):
            raise ValueError("Unexpected ChainCC format in " + path.name)
        yield from reader


def build_index(folder: Path, years: tuple[int, ...] = (2025, 2026), download: bool = False) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    paired = {}
    file_metadata = {}
    team_counts = {}
    player_counts = {}
    for year in years:
        players = _ensure_source(folder, "players", year, download)
        teams = _ensure_source(folder, "teams", year, download)
        file_metadata[players.name] = {"sha256": hashlib.sha256(players.read_bytes()).hexdigest(), "bytes": players.stat().st_size}
        file_metadata[teams.name] = {"sha256": hashlib.sha256(teams.read_bytes()).hexdigest(), "bytes": teams.stat().st_size}
        seen_teams = set()
        team_total = 0
        for row in _rows(teams, {"game_id", "side", "patch", "pick1", "ban1"}):
            team_total += 1
            seen_teams.add((year, row["game_id"]))
        team_counts[str(year)] = {"rows": team_total, "games": len(seen_teams)}
        count = 0
        for row in _rows(players, {"game_id", "side", "position", "champion", "result", "patch", "golddiffat15"}):
            count += 1
            role = POSITIONS.get(row["position"].casefold())
            side = row["side"].casefold()
            if not role or side not in {"blue", "red"} or not row["champion"]:
                continue
            key = (year, row["game_id"], role)
            if key not in paired:
                paired[key] = {}
            paired[key][side] = {
                "champion": row["champion"], "result": row["result"].strip().upper() in {"TRUE", "1"},
                "patch": row["patch"], "gold": row["golddiffat15"],
            }
        player_counts[str(year)] = count

    # Per-year + per-patch aggregates, each ordered as our champion vs lane opponent.
    grouped = defaultdict(lambda: [0, 0, 0.0, 0])
    matched_lane_pairs = 0
    for (year, _game, role), sides in paired.items():
        if set(sides) != {"blue", "red"}:
            continue
        matched_lane_pairs += 1
        for side in ("blue", "red"):
            ours, enemy = sides[side], sides["red" if side == "blue" else "blue"]
            if ours["patch"] != enemy["patch"]:
                continue
            for period in (str(year), str(year) + "|" + ours["patch"]):
                key = (period, role, ours["champion"], enemy["champion"])
                values = grouped[key]
                values[0] += 1
                values[1] += int(ours["result"])
                if ours["gold"]:
                    try:
                        values[2] += float(ours["gold"])
                        values[3] += 1
                    except ValueError:
                        pass

    matchups = {
        "|".join(key): {
            "games": val[0],
            "wins": val[1],
            "win_rate": round(100 * val[1] / val[0], 2),
            "avg_gd15": round(val[2] / val[3], 1) if val[3] else None,
            "gd15_games": val[3],
        }
        for key, val in grouped.items() if val[0]
    }
    payload = {
        "schema_version": 1, "provider": "ChainCC",
        "source": "https://chaincc.lol/free/data/", "licence": "CC BY 4.0",
        "attribution": "ChainCC. League of Legends esports match dataset. chaincc.lol/free/data",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "years": list(years), "player_rows": player_counts, "team_rows": team_counts,
        "matched_lane_pairs": matched_lane_pairs, "matchup_records": len(matchups),
        "sources": file_metadata, "matchups": matchups,
        "limitations": "Pro games only; team and player selection biases. Row position is observed lane, not draft-pick order. 2026 contains games on 15.24 and later.",
    }
    tmp = folder / "matchup-index.json.incoming"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(folder / "matchup-index.json")
    return {k: v for k, v in payload.items() if k not in {"matchups", "sources"}}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--download", action="store_true", help="Retrieve CC BY 4.0 source files from ChainCC")
    p.add_argument("--dir", type=Path, default=_folder())
    options = p.parse_args()
    print(json.dumps(build_index(options.dir, download=options.download), indent=2))
