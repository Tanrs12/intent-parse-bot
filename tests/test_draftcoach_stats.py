"""Player review: client match history -> match-v5 rows -> Matchup Master aggregates -> the review."""
from __future__ import annotations

import sys
import time
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from fake_matches import match_history, ranked_stats  # noqa: E402

from draftcoach import stats  # noqa: E402


def _payload(name="RISE OF PETE#PING", puuid="fake-rise-of-pete", lane=2, count=30):
    now = int(time.time() * 1000)
    return {"fetched_at": time.time(), "account": {"puuid": puuid, "gameName": name.split("#")[0], "tagLine": name.split("#")[1], "summonerLevel": 300},
            "ranked": ranked_stats(name), "games": match_history(name, puuid, lane, count, now_ms=now)}


def test_client_game_converts_to_match_v5_with_positions():
    game = _payload(count=1)["games"][0]
    match = stats.to_match_v5(game)
    assert match["metadata"]["matchId"].startswith("EUW1_")
    info = match["info"]
    assert len(info["participants"]) == 10
    assert {p["teamPosition"] for p in info["participants"]} == {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
    me = [p for p in info["participants"] if p["puuid"] == "fake-rise-of-pete"]
    assert len(me) == 1 and me[0]["championName"] and isinstance(me[0]["win"], bool)
    assert all(isinstance(t["win"], bool) for t in info["teams"])


def test_review_has_every_section_and_is_deterministic():
    payload = _payload()
    review = stats.build_review(payload)
    again = stats.build_review(payload)
    for key in ("account", "ranked", "overview", "champions", "roles", "queues", "recent", "matchups", "compare", "tilt", "records", "insights"):
        assert key in review, key
    assert review["account"]["name"] == "RISE OF PETE#PING"
    assert review["overview"]["games"] == 30
    assert review["overview"]["wins"] + review["overview"]["losses"] == 30
    assert len(review["overview"]["form"]) == 10 and set(review["overview"]["form"]) <= {"W", "L"}
    assert review["overview"]["main_role"] == "Mid"
    assert review["champions"] and all(c["games"] >= 1 for c in review["champions"])
    assert sum(c["games"] for c in review["roles"]) == 30
    assert len(review["recent"]) == 20 and review["recent"][0]["date"] >= review["recent"][-1]["date"]
    assert {q["queue"] for q in review["ranked"]} == {"Solo/Duo", "Flex"}
    assert any(c["key"] == "cs_per_min" and c["percentile"] is not None for c in review["compare"])
    assert review["insights"] and all(i["level"] in ("good", "warn", "info") for i in review["insights"])
    assert review == again


def test_review_copes_with_a_thin_account():
    review = stats.build_review(_payload(count=2))
    assert review["overview"]["games"] == 2
    assert review["insights"][0]["level"] == "info"


def test_cache_round_trip(tmp_path):
    class Rest:
        def __init__(self):
            self.payload = _payload(count=5)
            self.calls = []

        def get(self, path, **_):
            self.calls.append(path)
            if path == "/lol-summoner/v1/current-summoner":
                return {"puuid": "fake-rise-of-pete", "gameName": "RISE OF PETE", "tagLine": "PING", "summonerLevel": 300}
            if path == "/lol-ranked/v1/current-ranked-stats":
                return self.payload["ranked"]
            if path.startswith("/lol-match-history/v1/products/lol/"):
                rows = [{**g, "participants": g["participants"][:1]} for g in self.payload["games"]]
                return {"games": {"games": rows}}
            if path.startswith("/lol-match-history/v1/games/"):
                gid = int(path.rsplit("/", 1)[1])
                return next(g for g in self.payload["games"] if g["gameId"] == gid)
            return None

    rest = Rest()
    payload = stats.fetch_from_client(rest, count=5, cache_dir=tmp_path)
    assert len(payload["games"]) == 5 and all(len(g["participants"]) == 10 for g in payload["games"])
    first_calls = len(rest.calls)
    payload2 = stats.fetch_from_client(rest, count=5, cache_dir=tmp_path)
    assert len(rest.calls) - first_calls == 3            # summoner, ranked, listing: games came from disk
    assert stats.load_cached(cache_dir=tmp_path)["account"]["puuid"] == "fake-rise-of-pete"
    assert stats.build_review(payload2)["overview"]["games"] == 5


def test_legacy_identity_matches_numeric_account_after_rename():
    payload = _payload(count=1)
    payload["account"].update(accountId=2468, summonerId=1357)
    game = payload["games"][0]
    for identity in game["participantIdentities"]:
        player = identity["player"]
        if player.pop("puuid") == payload["account"]["puuid"]:
            player.update(currentAccountId=2468, summonerId=1357, summonerName="Old name")
    assert stats.build_review(payload)["overview"]["games"] == 1


def test_ranked_queue_map_without_queues():
    review = stats.ranked_summary({"queueMap": {"RANKED_FLEX_SR": {
        "tier": "GOLD", "division": "II", "leaguePoints": 42, "wins": 6, "losses": 4}}})
    assert len(review) == 1
    assert (review[0]["queue"], review[0]["lp"], review[0]["winrate"]) == ("Flex", 42, 60.0)


@pytest.mark.parametrize("lane,role,expected", [
    ("TOP", "SOLO", "TOP"), ("JUNGLE", "NONE", "JUNGLE"), ("MID", "SOLO", "MIDDLE"),
    ("BOT", "DUO_CARRY", "BOTTOM"), ("BOT", "DUO_SUPPORT", "UTILITY"), ("BOTTOM", "NONE", None),
])
def test_legacy_lane_roles_without_v5_positions(lane, role, expected):
    assert stats._position({"timeline": {"lane": lane, "role": role}}) == expected


def test_short_early_surrender_excluded_but_fifteen_minute_surrender_counted():
    payload = _payload(count=2)
    payload["games"][0]["gameDuration"] = 180
    payload["games"][1]["gameDuration"] = 900
    for game in payload["games"]:
        for p in game["participants"]:
            p["stats"]["gameEndedInEarlySurrender"] = True
    review = stats.build_review(payload)
    assert review["overview"]["games"] == 1
    assert len(review["recent"]) == 1
    assert review["recent"][0]["duration"] == 900


def test_partial_history_never_fabricates_team_kill_participation():
    payload = _payload(count=1)
    game = payload["games"][0]
    mine = next(i["participantId"] for i in game["participantIdentities"] if i["player"]["puuid"] == payload["account"]["puuid"])
    game["participants"] = [p for p in game["participants"] if p["participantId"] == mine]
    assert stats.build_review(payload)["overview"]["kp"] is None


def test_inconsistent_team_totals_are_missing_not_over_one_hundred_percent():
    payload = _payload(count=1)
    for p in payload["games"][0]["participants"]:
        p["stats"].update(kills=1, assists=10)
    assert stats.build_review(payload)["overview"]["kp"] is None


def test_lcu_duration_is_seconds_even_before_match_v5_unit_change():
    payload = _payload(count=1)
    payload["games"][0].update(gameVersion="11.19.1", gameDuration=1800)
    assert stats.build_review(payload)["recent"][0]["duration"] == 1800
