"""The game plan: five players -> drafts built in the app itself, no Discord in the loop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draftcoach import gameplan
from draftcoach.server import create_app

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "live_flex_signups.json"


@pytest.fixture
def client(tmp_path):
    app = create_app({"auto_profiles": False, "debounce_ms": 0}, enable_lcu=False, roster_path=tmp_path / "roster.json")
    with TestClient(app) as test_client:
        yield test_client


def test_signups_prefer_the_discord_cache_and_fall_back_to_roster_pools(tmp_path):
    cache = tmp_path / "signups.json"
    cache.write_text(json.dumps([
        {"user_id": 1, "display_name": "Cian", "roles": ["Top"], "role_priority": {"Top": 1}, "champions": {"Top": ["Camille"]}},
        {"user_id": 9, "display_name": "No pools", "roles": ["Mid"], "role_priority": {}, "champions": {}},
    ]), encoding="utf-8")

    class Entry:
        def __init__(self, champion):
            self.champion = champion

    class Player:
        def __init__(self, name, user_id, roles, pools):
            self.name, self.user_id, self.roles, self.pools = name, user_id, roles, pools

    class Roster:
        players = {
            "cian": Player("Cian (roster)", 1, ["Top", "Mid"], {"Top": [Entry("Gnar")]}),
            "jack": Player("Jack", 2, ["ADC"], {"ADC": [Entry("Jinx"), Entry("Ashe")]}),
            "nobody": Player("No id", None, ["Mid"], {"Mid": [Entry("Ahri")]}),
        }

    rows = gameplan.load_signups(Roster(), cache)
    # the cache wins for Cian (his own submitted pool); a player without a Discord id cannot be drafted for
    assert [r["display_name"] for r in rows] == ["Cian", "Jack"]
    assert rows[0]["champions"] == {"Top": ["Camille"]}
    assert gameplan.players_summary(rows)[1] == {"user_id": "2", "display_name": "Jack", "roles": ["ADC"], "pools": {"ADC": 2}}
    assert gameplan.load_signups(None, tmp_path / "missing.json") == []


def test_unavailable_names_are_cleaned_and_bounded():
    assert gameplan.clean_unavailable(" Morgana, Jarvan IV ,, Morgana\nAhri") == ["Morgana", "Jarvan IV", "Ahri"]
    assert gameplan.clean_unavailable(None) == []
    assert len(gameplan.clean_unavailable([str(i) for i in range(40)])) == gameplan.MAX_UNAVAILABLE


@pytest.mark.skipif(not CACHE.exists() or not gameplan.available(), reason="needs the signup cache and the engine")
def test_game_plan_endpoint_builds_drafts_for_five_and_rejects_bad_requests(client):
    players = client.get("/api/gameplan").json()["players"]
    assert len(players) >= 5 and all(p["pools"] for p in players)
    # Discord ids are beyond JavaScript's exact-integer range: they travel as strings, both ways
    assert all(isinstance(p["user_id"], str) and int(p["user_id"]) > 2 ** 53 for p in players)
    ids = [p["user_id"] for p in players][:5]

    bad = client.post("/api/gameplan", json={"players": ids[:4]})
    assert bad.status_code == 400 and "five" in bad.json()["detail"]
    unknown = client.post("/api/gameplan", json={"players": ids[:4] + [424242]})
    assert unknown.status_code == 400 and "424242" in unknown.json()["detail"]

    response = client.post("/api/gameplan", json={"players": ids, "unavailable": "Jinx, Orianna"})
    assert response.status_code == 200, response.text
    plan = response.json()
    recs = plan["recommendations"]
    assert recs and plan["elapsed_ms"] >= 0 and [p["user_id"] for p in plan["players"]] == ids
    assert plan["unavailable"] == ["Jinx", "Orianna"]
    for rec in recs:
        assert len(rec["champions"]) == 5
        assert not {"Jinx", "Orianna"} & set(rec["champions"].values())
        assert len(rec["sides"]["blue"]["pick_order"]) == 5 and len(rec["sides"]["red"]["pick_order"]) == 5
        chosen = rec["control"]["control"]["chosen"]
        assert chosen["champions"] == rec["champions"] and chosen["source"] == "app" and chosen["label"] == rec["label"]
        assert all(isinstance(uid, str) for uid in rec["control"]["control"]["role_locks"].values())
    # the plan is remembered, so a reopened page (or the panel) can show it again
    assert client.get("/api/gameplan").json()["result"]["built_at"] == plan["built_at"]
