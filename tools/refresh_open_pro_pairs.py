"""Refresh CC BY 4.0 professional role-pairing evidence from ChainCC.

Public, reusable open dataset. 2026 player rows contain champions and positions;
team rows contain the same game and side with full picks/bans. This collector
uses the former for correct role assignment, the latter to verify full matches.
No private API, bypassed protections or undocumented web endpoints.
"""
from __future__ import annotations
import csv
import gzip
import io
import itertools
import json
import os
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL_BASE = "https://chaincc.lol/data"
ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
POSITION_MAP = {
    "top": "Top", "jng": "Jungle", "jungle": "Jungle", "mid": "Mid",
    "bot": "ADC", "adc": "ADC", "sup": "Support", "support": "Support",
}

def download_rows(name: str, max_compressed_bytes: int = 18_000_000) -> list[dict]:
    request = urllib.request.Request(
        f"{URL_BASE}/{name}", headers={"User-Agent": "INTent-Flexers-Draft-Research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        compressed = response.read(max_compressed_bytes + 1)
    if len(compressed) > max_compressed_bytes:
        raise ValueError(f"{name} exceeded size limit")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as decompressed:
        content = decompressed.read(50_000_000 + 1)
    if len(content) > 50_000_000:
        raise ValueError(f"{name} exceeded decoded size limit")
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))

def patch_parts(text: str) -> tuple[int, int]:
    try:
        a, b = str(text).split(".")[:2]
        return int(a), int(b)
    except (ValueError, TypeError):
        return (0, 0)

def _truth(raw: str) -> bool:
    return str(raw).strip().casefold() in ("true", "1", "win", "yes")

def build_open_pro_pairs(teams: list[dict], players: list[dict]) -> dict:
    good_teams = {}
    for row in teams:
        game_id, side = row.get("game_id"), row.get("side")
        if not game_id or side not in ("Blue", "Red") or not row.get("patch"):
            continue
        if not all(row.get(f"pick{i}") for i in range(1, 6)):
            continue
        good_teams[(game_id, side)] = row
    patches = [patch_parts(r["patch"]) for r in good_teams.values()]
    current = max(patches or [(0, 0)])
    # Current and previous two patches. Long-term champion proficiency evidence
    # is stored elsewhere; stale professional comps should not dominate the draft.
    min_patch = (current[0], max(0, current[1] - 2))
    seats = defaultdict(dict)
    for row in players:
        key = row.get("game_id"), row.get("side")
        if key not in good_teams:
            continue
        role = POSITION_MAP.get(str(row.get("position") or "").lower())
        champion = str(row.get("champion") or "").strip()
        if role and champion and role not in seats[key]:
            seats[key][role] = champion

    games = []
    for key, team in good_teams.items():
        patch = patch_parts(team["patch"])
        if patch < min_patch or patch > current:
            continue
        roster = seats.get(key, {})
        if len(roster) != 5 or len(set(roster.values())) != 5:
            continue
        games.append((roster, _truth(team.get("result")), patch))
    if not games:
        raise ValueError("no valid complete pro games for the latest three patches")

    champion_results = defaultdict(lambda: [0, 0])
    pair_results = defaultdict(lambda: [0, 0])
    for picks, win, patch in games:
        for role, champ in picks.items():
            bucket = champion_results[(role, champ)]
            bucket[0] += 1
            bucket[1] += int(win)
        for role_a, role_b in itertools.combinations(ROLES, 2):
            key = (role_a, picks[role_a], role_b, picks[role_b])
            bucket = pair_results[key]
            bucket[0] += 1
            bucket[1] += int(win)

    output_pairs = []
    for (role_a, champ_a, role_b, champ_b), (count, wins) in pair_results.items():
        if count < 5:
            continue
        # Team-strength confounder remains; an average of two individual
        # champion-role win rates is only a rough baseline, never causal proof.
        n1, w1 = champion_results[(role_a, champ_a)]
        n2, w2 = champion_results[(role_b, champ_b)]
        # Bayesian smoothing on both champion baselines.
        expected = ((w1 + 10) / (n1 + 20) + (w2 + 10) / (n2 + 20)) / 2
        wr = (wins + 10) / (count + 20)
        lift = (wr - expected) * count / (count + 35)
        output_pairs.append({
            "role_a": role_a, "champion_a": champ_a,
            "role_b": role_b, "champion_b": champ_b,
            "games": count, "wins": wins,
            "shrunken_lift": round(max(-0.08, min(0.08, lift)), 4),
        })
    output_pairs.sort(key=lambda r: (
        ROLES.index(r["role_a"]), ROLES.index(r["role_b"]),
        -r["games"], r["champion_a"], r["champion_b"],
    ))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_patch": f"{current[0]}.{current[1]}",
        "game_count": len(games) // 2,
        "team_side_count": len(games),
        "pairs": output_pairs,
        "source": {
            "name": "ChainCC",
            "url": "https://chaincc.lol/free/data",
            "license": "CC BY 4.0",
            "season": 2026,
            "patches_included": [f"{current[0]}.{i}" for i in range(max(0,current[1]-2),current[1]+1)],
            "scope": "Professional matches, NOT Solo Queue",
        },
    }

def main() -> None:
    team_rows = download_rows("chaincc-teams-2026.csv.gz")
    player_rows = download_rows("chaincc-players-2026.csv.gz")
    data = build_open_pro_pairs(team_rows, player_rows)
    path = ROOT / "data" / "league_open_pro_pairs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    if old:
        # Ignore refresh timestamps when judging whether the evidence actually changed.
        old["generated_at"] = data["generated_at"]
    changed = old != data
    if changed:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output,"a",encoding="utf-8") as f:
            f.write(f"updated={'true' if changed else 'false'}\n")
    print(json.dumps({
        "updated": changed,
        "latest_patch": data["latest_patch"],
        "team_sides": data["team_side_count"],
        "distinct_games": data["game_count"],
        "covered_pairs": len(data["pairs"]),
    }))

if __name__ == "__main__":
    main()
