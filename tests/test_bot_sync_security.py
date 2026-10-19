from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web

import bot


class FakeRequest:
    def __init__(self, key="test-session-token-1234567890", headers=None, payload=None):
        self.match_info = {"key": key}
        self.headers = headers or {}
        self.payload = payload or {"comps": [{"champions": {"Top": "Camille"}}]}
    async def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def clean_rooms():
    bot._SYNC_SESSIONS.clear()
    yield
    bot._SYNC_SESSIONS.clear()


def test_elected_host_cannot_be_bypassed_by_missing_client_header():
    key = "test-session-token-1234567890"
    room = bot._session_row(key)
    room["participants"]["elected-client"] = {
        "client_id": "elected-client", "priority": 1, "player_id": 7,
        "in_champ_select": True, "updated": time.time(),
    }
    for headers in ({}, {"X-Intent-Client": "wrong-client"}):
        with pytest.raises(web.HTTPConflict):
            asyncio.run(bot._relay_put(FakeRequest(key, headers)))
    assert room["payload"] is None
    response = asyncio.run(bot._relay_put(FakeRequest(
        key, {"X-Intent-Client": "elected-client"},
    )))
    assert response.status == 200
    assert room["publisher_id"] == "elected-client"


def test_manual_publisher_without_leader_still_allowed():
    key = "manual-session-token-123456789"
    response = asyncio.run(bot._relay_put(FakeRequest(key)))
    assert response.status == 200


def test_room_capacity_and_idle_expiration(monkeypatch):
    monkeypatch.setattr(bot, "_MAX_SYNC_SESSIONS", 2)
    bot._session_row("room-a-123456789")
    bot._session_row("room-b-123456789")
    with pytest.raises(web.HTTPTooManyRequests):
        bot._session_row("room-c-123456789")
    bot._SYNC_SESSIONS["room-a-123456789"]["last_seen"] = time.time() - 1000
    bot._session_row("room-c-123456789")
    assert "room-a-123456789" not in bot._SYNC_SESSIONS
    assert len(bot._SYNC_SESSIONS) == 2


def test_relay_limits_participant_count(monkeypatch):
    monkeypatch.setattr(bot, "_MAX_SYNC_PARTICIPANTS", 1)
    key = "room-participants-123456789"
    request = FakeRequest(key, payload={"client_id": "client-001", "player_id": 1})
    assert asyncio.run(bot._relay_heartbeat(request)).status == 200
    other = FakeRequest(key, payload={"client_id": "client-002", "player_id": 2})
    with pytest.raises(web.HTTPTooManyRequests):
        asyncio.run(bot._relay_heartbeat(other))
