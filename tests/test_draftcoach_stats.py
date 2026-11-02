"""Player review: client match history -> match-v5 rows -> Matchup Master aggregates -> the review."""
from __future__ import annotations

import sys
import time
from pathlib import Path

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
