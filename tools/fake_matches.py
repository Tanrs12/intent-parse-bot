"""Synthetic League match history in the client's own format, for the fake client and the stats tests.

Deterministic per player name, so a test can assert on it. Shape follows
``/lol-match-history/v1/games/{gameId}`` (match-v4 layout: participants + participantIdentities).
"""
from __future__ import annotations

import hashlib
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from draftcoach import champions as champ_map  # noqa: E402

LANES = [("TOP", "SOLO"), ("JUNGLE", "NONE"), ("MIDDLE", "SOLO"), ("BOTTOM", "DUO_CARRY"), ("BOTTOM", "DUO_SUPPORT")]
POOLS = {"TOP": ["Camille", "Ornn", "Gnar", "Jax", "Ambessa", "Garen"], "JUNGLE": ["Amumu", "Nocturne", "Lee Sin", "Vi", "Wukong", "Zac"],
         "MIDDLE": ["Ahri", "Syndra", "Galio", "Yasuo", "Lux", "Orianna"], "BOTTOM": ["Kai'Sa", "Jinx", "Ezreal", "Ashe", "Aphelios", "Caitlyn"],
         "UTILITY": ["Lulu", "Thresh", "Leona", "Nami", "Karma", "Nautilus"]}
QUEUES = [440, 440, 440, 420, 400]


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))


def match_history(riot_id: str, puuid: str, main_lane: int, count: int = 30, *, now_ms: int) -> list[dict]:
    """``count`` games, newest first, for one player; nine other synthetic players fill each game."""
    rng = _rng(riot_id)
    name, _, tag = riot_id.partition("#")
    games = []
    skill = 0.45 + rng.random() * 0.15                       # this player's underlying win chance
    for n in range(count):
        game_id = 7_000_000_000 + int(hashlib.sha256(f"{riot_id}:{n}".encode()).hexdigest()[:8], 16)
        start = now_ms - (n * 3 + rng.randint(0, 2)) * 3600 * 1000 - rng.randint(0, 1800) * 1000
        duration = rng.randint(17, 38) * 60 + rng.randint(0, 59)
        queue = rng.choice(QUEUES)
        lane_index = main_lane if rng.random() < 0.75 else rng.randrange(5)
        own_team = rng.choice([100, 200])
        win = rng.random() < (skill + (0.08 if lane_index == main_lane else -0.1))
        participants, identities = [], []
        used: set[str] = set()
        for team in (100, 200):
            for lane_i, (lane, role) in enumerate(LANES):
                pid = len(participants) + 1
                position = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"][lane_i]
                is_me = team == own_team and lane_i == lane_index
                champion = rng.choice([c for c in POOLS[position] if c not in used]) if is_me or rng.random() < 2 else None
                used.add(champion)
                team_win = (team == own_team) == win
                minutes = duration / 60
                kills = max(0, int(rng.gauss(6 if team_win else 3, 3)))
                deaths = max(0, int(rng.gauss(3 if team_win else 6, 2)))
                assists = max(0, int(rng.gauss(8, 4)))
                cs_rate = {"TOP": 7.2, "JUNGLE": 5.8, "MIDDLE": 7.6, "BOTTOM": 8.2, "UTILITY": 1.4}[position] + rng.gauss(0, 0.9)
                if is_me:
                    cs_rate += 0.6 if lane_index == main_lane else -0.8
                stats = {"win": team_win, "kills": kills, "deaths": deaths, "assists": assists,
                         "goldEarned": int(minutes * (380 + rng.gauss(0, 40)) + kills * 300),
                         "totalMinionsKilled": max(0, int(minutes * cs_rate * 0.85)), "neutralMinionsKilled": max(0, int(minutes * cs_rate * 0.15)),
                         "totalDamageDealtToChampions": int(minutes * (650 + rng.gauss(0, 150)) + kills * 400),
                         "visionScore": int(minutes * ({"UTILITY": 2.4}.get(position, 0.9) + rng.gauss(0, 0.3))),
                         "wardsPlaced": rng.randint(4, 30), "wardsKilled": rng.randint(0, 9), "visionWardsBoughtInGame": rng.randint(0, 6),
                         "champLevel": rng.randint(13, 18), "doubleKills": int(kills >= 5 and rng.random() < 0.5), "tripleKills": int(kills >= 8 and rng.random() < 0.3),
                         "quadraKills": 0, "pentaKills": 0, "firstBloodKill": rng.random() < 0.1, "gameEndedInSurrender": (not team_win) and rng.random() < 0.3,
                         "gameEndedInEarlySurrender": False, "totalDamageTaken": int(minutes * 700), "totalHeal": int(minutes * 120),
                         "damageDealtToTurrets": int(minutes * 150), "damageDealtToObjectives": int(minutes * 300), "teamPosition": position}
                participants.append({"participantId": pid, "teamId": team, "championId": champ_map.id_for(champion) or 1, "spell1Id": 4, "spell2Id": 12,
                                     "stats": stats, "timeline": {"lane": lane, "role": role, "participantId": pid}})
                player = {"puuid": puuid, "gameName": name, "tagLine": tag or "EUW", "summonerName": name} if is_me else \
                    {"puuid": f"fake-other-{game_id}-{pid}", "gameName": f"Player{pid}", "tagLine": "EUW", "summonerName": f"Player{pid}"}
                identities.append({"participantId": pid, "player": player})
        teams = [{"teamId": t, "win": "Win" if (t == own_team) == win else "Fail", "firstBlood": rng.random() < 0.5, "firstTower": rng.random() < 0.5,
                  "firstDragon": rng.random() < 0.5, "firstRiftHerald": rng.random() < 0.5, "firstBaron": rng.random() < 0.5, "firstInhibitor": rng.random() < 0.5}
                 for t in (100, 200)]
        games.append({"gameId": game_id, "platformId": "EUW1", "gameCreation": start, "gameDuration": duration, "queueId": queue, "mapId": 11,
                      "gameMode": "CLASSIC", "gameType": "MATCHED_GAME", "gameVersion": "15.21.1", "seasonId": 15,
                      "participants": participants, "participantIdentities": identities, "teams": teams})
    return games


def ranked_stats(riot_id: str) -> dict:
    rng = _rng(riot_id + ":ranked")
    tiers = ["SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]
    out = []
    for qtype in ("RANKED_SOLO_5x5", "RANKED_FLEX_SR"):
        wins, losses = rng.randint(20, 90), rng.randint(20, 90)
        out.append({"queueType": qtype, "tier": rng.choice(tiers), "division": rng.choice(["I", "II", "III", "IV"]),
                    "leaguePoints": rng.randint(0, 99), "wins": wins, "losses": losses, "isProvisional": False})
    return {"queues": out, "highestRankedEntry": out[0]}
