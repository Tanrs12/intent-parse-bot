"""Rate-limited official Riot API collector for private ranked lane-matchup samples.

No undocumented endpoints, no bypass, no unauthorised third-party scraping.
Uses RIOT_API_KEY from the environment (never written to disk or logs).
Collects *rank-anchor* EUW1 Gold/Platinum/Emerald/Diamond Solo Queue samples:
a Gold player's game is not proof all nine other players are Gold. All source
tiers, patches and counts stay separate. Data contain champion outcomes, not
player identifiers. Match IDs are stored solely for deduplication.

Usage:
  set RIOT_API_KEY=...  (in a PRIVATE terminal, never commit it)
  python tools/collect_riot_ranked_matchups.py --tiers GOLD PLATINUM EMERALD
  python tools/collect_riot_ranked_matchups.py --export-only
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = "euw1"
ROUTING = "europe"
TIERS = ("GOLD", "PLATINUM", "EMERALD", "DIAMOND")
ROLE_KEYS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support"}
MIN_GAMES = 40
DEFAULT_PATCH = "16.21"


def data_dir() -> Path:
    path = os.getenv("DRAFTCOACH_RIOT_SAMPLE_DIR")
    return Path(path) if path else ROOT / "var" / "riot-rank-samples"


class Api:
    def __init__(self, key: str, minimum_interval: float = 1.6):
        if not key:
            raise ValueError("RIOT_API_KEY environment variable is required")
        self.key = key
        self.minimum_interval = max(1.3, minimum_interval)
        self.next_call = 0.0

    def request(self, host: str, path: str):
        url = f"https://{host}.api.riotgames.com{path}"
        for attempt in range(6):
            delay = self.next_call - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_call = time.monotonic() + self.minimum_interval
            req = urllib.request.Request(url, headers={
                "X-Riot-Token": self.key,
                "Accept": "application/json",
                "User-Agent": "INTentDraftCoach-PrivateRankSample/1",
            })
            try:
                with urllib.request.urlopen(req, timeout=25) as response:
                    return json.loads(response.read(5_000_000))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise RuntimeError("Riot key invalid, expired or insufficiently authorised") from None
                if exc.code == 404:
                    return None
                if exc.code == 429:
                    retry = exc.headers.get("Retry-After", "10")
                    try:
                        wait = min(180.0, max(1.0, float(retry)))
                    except ValueError:
                        wait = 10.0
                    self.next_call = max(self.next_call, time.monotonic() + wait)
                elif exc.code >= 500:
                    self.next_call = max(self.next_call, time.monotonic() + (attempt + 1) * 5)
                else:
                    raise
        raise RuntimeError("Riot API did not recover after rate-limit/server errors")


def database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS matches(
        match_id TEXT PRIMARY KEY, anchor_tier TEXT NOT NULL, patch TEXT NOT NULL,
        observed_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lanes(
        match_id TEXT NOT NULL, role TEXT NOT NULL, blue TEXT NOT NULL,
        red TEXT NOT NULL, blue_win INTEGER NOT NULL,
        PRIMARY KEY(match_id,role)
    )""")
    return conn


def _patch(version: str) -> str:
    parts = str(version).split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return ""
    return f"{int(parts[0])}.{int(parts[1])}"


def extract_lanes(match: dict):
    """Only matches with unambiguous one-per-side roles are admissible."""
    info = match.get("info") or {}
    if int(info.get("queueId") or 0) != 420:
        return {}
    sides = {"blue": {}, "red": {}}
    for row in info.get("participants") or []:
        role = ROLE_KEYS.get(str(row.get("teamPosition") or "").upper())
        team_id = int(row.get("teamId") or 0)
        side = "blue" if team_id == 100 else "red" if team_id == 200 else None
        if not role or not side or role in sides[side] or not row.get("championName"):
            return {}
        sides[side][role] = (row["championName"], int(bool(row.get("win"))))
    if any(set(group) != set(ROLE_KEYS.values()) for group in sides.values()):
        return {}
    return {
        role: (
            sides["blue"][role][0], sides["red"][role][0],
            sides["blue"][role][1],
        )
        for role in ROLE_KEYS.values()
    }


def record_match(conn: sqlite3.Connection, game: dict, anchor_tier: str,
                 required_patch: str) -> bool:
    metadata, info = game.get("metadata") or {}, game.get("info") or {}
    match_id = str(metadata.get("matchId") or "")
    patch = _patch(info.get("gameVersion") or "")
    if not match_id or patch != required_patch or anchor_tier not in TIERS:
        return False
    lanes = extract_lanes(game)
    if len(lanes) != 5:
        return False
    with conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO matches VALUES(?,?,?,?)",
            (match_id, anchor_tier, patch, datetime.now(timezone.utc).isoformat()),
        ).rowcount
        if not inserted:
            return False
        conn.executemany(
            "INSERT INTO lanes VALUES(?,?,?,?,?)",
            [(match_id, role, blue, red, blue_win)
             for role, (blue, red, blue_win) in lanes.items()],
        )
    return True


def build_export(conn: sqlite3.Connection, path: Path) -> dict:
    base = defaultdict(lambda: [0, 0])
    pairs = defaultdict(lambda: [0, 0])
    games_by_tier = defaultdict(int)
    for tier, patch, game_count in conn.execute(
        "SELECT anchor_tier, patch, COUNT(*) FROM matches GROUP BY anchor_tier,patch"
    ):
        games_by_tier[tier + "|" + patch] += game_count
    rows = conn.execute("""SELECT m.anchor_tier,m.patch,l.role,l.blue,l.red,l.blue_win
        FROM lanes l JOIN matches m ON l.match_id=m.match_id""")
    for tier, patch, role, blue, red, blue_win in rows:
        for our, opponent, win in ((blue, red, blue_win), (red, blue, 1-blue_win)):
            base[(tier, patch, role, our)][0] += 1
            base[(tier, patch, role, our)][1] += win
            pairs[(tier, patch, role, our, opponent)][0] += 1
            pairs[(tier, patch, role, our, opponent)][1] += win
    matchups = {}
    for (tier, patch, role, own, opponent), (n, wins) in pairs.items():
        baseline = base[tier, patch, role, own]
        residual = 100 * (wins / n - baseline[1] / baseline[0])
        matchups["|".join((tier, patch, role, own, opponent))] = {
            "games": n, "wins": wins, "overall_games": baseline[0],
            "match_win_rate": round(100*wins/n, 2),
            "baseline_win_rate": round(100*baseline[1]/baseline[0], 2),
            "residual_pp": round(residual, 3),
        }
    output = {
        "schema_version": 1, "provider": "Riot Match-V5",
        "source": "https://developer.riotgames.com/apis#match-v5",
        "region": "EUW", "queue": 420,
        "tier_semantics": "Rank of sampled anchor account; NOT verified rank of all ten participants",
        "patch_semantics": "Actual match gameVersion; not converted into another season label",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sampled_matches": dict(games_by_tier),
        "matchup_rows": len(matchups), "matchups": matchups,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".incoming")
    tmp.write_text(json.dumps(output, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return {k: v for k, v in output.items() if k != "matchups"}


def collect(api: Api, conn: sqlite3.Connection, *, tiers: tuple[str, ...],
            division: str, pages: int, seeds_per_tier: int, matches_per_seed: int,
            required_patch: str) -> dict:
    outcome = {}
    for tier in tiers:
        sampled = added = 0
        for page in range(1, pages + 1):
            path = f"/lol/league-exp/v4/entries/RANKED_SOLO_5x5/{tier}/{division}?page={page}"
            entries = api.request(PLATFORM, path) or []
            for entry in entries:
                if sampled >= seeds_per_tier:
                    break
                summoner_id = entry.get("summonerId")
                if not summoner_id:
                    continue
                user = api.request(PLATFORM, "/lol/summoner/v4/summoners/" +
                                   urllib.parse.quote(str(summoner_id), safe=""))
                puuid = (user or {}).get("puuid")
                if not puuid:
                    continue
                sampled += 1
                # Fetch IDs without a queue filter because historic match-v5
                # filtering regressions exist. Validate queue ID in match body.
                ids = api.request(ROUTING,
                    "/lol/match/v5/matches/by-puuid/" + urllib.parse.quote(puuid, safe="") +
                    "/ids?start=0&count=" + str(min(30, matches_per_seed))) or []
                for match_id in ids[:matches_per_seed]:
                    if conn.execute("SELECT 1 FROM matches WHERE match_id=?", (match_id,)).fetchone():
                        continue
                    game = api.request(ROUTING,
                        "/lol/match/v5/matches/" + urllib.parse.quote(str(match_id), safe=""))
                    if game and record_match(conn, game, tier, required_patch):
                        added += 1
            if sampled >= seeds_per_tier:
                break
        outcome[tier] = {"anchor_accounts": sampled, "new_matches": added}
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--tiers", nargs="+", choices=TIERS, default=list(TIERS[:3]))
    parser.add_argument("--division", choices=("I", "II", "III", "IV"), default="II")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--seeds-per-tier", type=int, default=4)
    parser.add_argument("--matches-per-seed", type=int, default=8)
    parser.add_argument("--patch", default=DEFAULT_PATCH)
    options = parser.parse_args()
    folder = data_dir()
    conn = database(folder / "samples.sqlite3")
    if not options.export_only:
        api = Api(os.getenv("RIOT_API_KEY", ""))
        print("Sampling approved Riot Match-V5 EUW queue 420, Gold/Platinum/Emerald/optional Diamond")
        print(json.dumps(collect(api, conn,
            tiers=tuple(options.tiers), division=options.division,
            pages=max(1, min(10, options.pages)),
            seeds_per_tier=max(1, min(100, options.seeds_per_tier)),
            matches_per_seed=max(1, min(30, options.matches_per_seed)),
            required_patch=options.patch), indent=2))
    print(json.dumps(build_export(conn, folder / "ranked-matchups.json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
