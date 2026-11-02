"""Sync check: the relay tells every app who has the app open and the role each player wants."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import bot

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
bridge = importlib.import_module("intent_live_draft_bridge")


def test_relay_heartbeat_lists_everyone_with_their_wanted_role():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        app = web.Application()
        app.router.add_post("/draft-sync/{key}/heartbeat", bot._relay_heartbeat)
        key = "s" * 24
        async with TestClient(TestServer(app)) as client:
            await client.post(f"/draft-sync/{key}/heartbeat", json={"client_id": "client-cian-0001", "player_id": 1, "player_name": "Cian",
                                                                      "priority": 0, "in_champ_select": True, "role": "Jungle"})
            r = await client.post(f"/draft-sync/{key}/heartbeat", json={"client_id": "client-jack-0002", "player_id": 2, "player_name": "Jack",
                                                                          "priority": 1, "in_champ_select": False, "role": "ADC"})
            body = await r.json()
            players = body["participants"]
            assert [p["player_name"] for p in players] == ["Cian", "Jack"]
            assert players[0]["role"] == "Jungle" and players[0]["in_champ_select"] is True and players[0]["captain"] is True
            assert players[1]["role"] == "ADC" and players[1]["captain"] is False and players[1]["seen_seconds_ago"] < 5
            assert "client_id" not in players[0]
            # a role is optional and bounded
            r = await client.post(f"/draft-sync/{key}/heartbeat", json={"client_id": "client-jack-0002", "player_id": 2, "player_name": "Jack",
                                                                          "priority": 1, "in_champ_select": False, "role": "x" * 50})
            body = await r.json()
            assert len([p for p in body["participants"] if p["player_name"] == "Jack"][0]["role"]) == 12
    asyncio.run(go())


def test_wanted_role_is_remembered_and_travels_with_the_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_role_file", lambda: tmp_path / "var" / "my-role.json")
    assert bridge._set_wanted_role("Jungle") == "Jungle"
    assert bridge._load_wanted_role() == "Jungle"
    assert bridge._set_wanted_role("Bogus") is None and bridge._load_wanted_role() is None
    bridge._set_wanted_role("Mid")
    monkeypatch.setattr(bridge, "_json_get", lambda *a, **k: {"status": {}, "state": {"our_team": []}})
    identity = bridge._auto_identity()
    assert identity["role"] == "Mid"
    bridge._set_wanted_role(None)
    monkeypatch.setattr(bridge, "_json_get", lambda *a, **k: {"status": {}, "state": {"local_cell_id": 3, "our_team": [{"cell_id": 3, "role": "ADC", "display_name": "Jack"}]}})
    assert bridge._auto_identity()["role"] == "ADC"


def test_sync_meta_carries_the_room_roster():
    with bridge._auto_lock:
        bridge._auto_participants = [{"player_name": "Cian", "role": "Jungle", "in_champ_select": True, "captain": True}]
        bridge._auto_leader_name = "Cian"
    meta = bridge._auto_sync_meta("auto-follower")
    assert meta["players"][0]["player_name"] == "Cian" and meta["leader"] == "Cian" and "my_role" in meta
