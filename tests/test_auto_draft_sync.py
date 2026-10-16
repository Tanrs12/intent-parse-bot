from __future__ import annotations

import time

import bot
from tools import intent_live_draft_bridge as bridge


def test_relay_elects_lowest_priority_active_flexer():
    now = time.time()
    row = {
        "participants": {
            "jack-client": {
                "client_id": "jack-client",
                "player_id": 332490596004659201,
                "player_name": "Jack",
                "priority": 1,
                "in_champ_select": True,
                "updated": now,
            },
            "cian-client": {
                "client_id": "cian-client",
                "player_id": 139837324438536193,
                "player_name": "Cian",
                "priority": 0,
                "in_champ_select": True,
                "updated": now,
            },
        }
    }
    leader = bot._elect_leader(row, now)
    assert leader["client_id"] == "cian-client"
    assert leader["player_name"] == "Cian"


def test_relay_fails_over_when_previous_host_is_stale():
    now = time.time()
    row = {
        "participants": {
            "cian-client": {
                "client_id": "cian-client",
                "player_id": 139837324438536193,
                "player_name": "Cian",
                "priority": 0,
                "in_champ_select": True,
                "updated": now - bot._SYNC_PARTICIPANT_TTL - 1,
            },
            "jack-client": {
                "client_id": "jack-client",
                "player_id": 332490596004659201,
                "player_name": "Jack",
                "priority": 1,
                "in_champ_select": True,
                "updated": now,
            },
        }
    }
    leader = bot._elect_leader(row, now)
    assert leader["client_id"] == "jack-client"
    assert "cian-client" not in row["participants"]


def test_relay_does_not_elect_idle_clients():
    now = time.time()
    row = {
        "participants": {
            "cian-client": {
                "client_id": "cian-client",
                "player_id": 139837324438536193,
                "player_name": "Cian",
                "priority": 0,
                "in_champ_select": False,
                "updated": now,
            }
        }
    }
    assert bot._elect_leader(row, now) is None


def test_auto_identity_uses_local_riot_seat_and_team_priority(monkeypatch):
    snapshot = {
        "status": {"lcu_in_champ_select": True},
        "state": {
            "local_cell_id": 3,
            "our_team": [
                {"cell_id": 0, "user_id": 332490596004659201, "display_name": "JackJak", "is_local": False},
                {"cell_id": 3, "user_id": 139837324438536193, "display_name": "FakerChovyHilary", "is_local": True},
            ],
        },
    }
    monkeypatch.setattr(bridge, "_json_get", lambda *args, **kwargs: snapshot)
    identity = bridge._auto_identity()
    assert identity["player_id"] == 139837324438536193
    assert identity["player_name"] == "FakerChovyHilary"
    assert identity["priority"] == 0
    assert identity["in_champ_select"] is True


def test_auto_cloud_mode_is_valid():
    old = (bridge._cloud_base, bridge._cloud_key, bridge._cloud_mode)
    try:
        bridge.configure_cloud("https://relay.invalid", "abcdefghijklmnop12345678", "auto")
        assert bridge._cloud_mode == "auto"
        assert bridge._room_label().startswith("FLEX-")
    finally:
        bridge.configure_cloud(*old)
