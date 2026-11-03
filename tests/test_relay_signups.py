"""GET /draft-sync/{key}/signups: the relay serves the Discord-submitted champion pools to the app."""
from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot
from config import LEAGUE_GUILD_ID

KEY = "signup-session-token-1234567890"

ROWS = [
    {
        "discord_user_id": 548212317515677697,
        "display_name": "Zoe",
        "roles_json": json.dumps(["Top", "Jungle"]),
        "role_priority_json": json.dumps({"Top": 1, "Jungle": 2}),
        "champions_json": json.dumps({"Top": ["Camille", "Fiora"], "Jungle": ["Lee Sin"]}),
        "updated_at": "2026-11-02T20:00:00+00:00",
    },
    {
        "discord_user_id": "123456789012345678",
        "display_name": "amy",
        "roles_json": json.dumps(["Mid"]),
        "role_priority_json": "not json",
        "champions_json": None,
        "updated_at": "2026-11-03T09:30:00+00:00",
    },
]


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, rows=None, error=None, calls=None):
        self.rows, self.error, self.calls = rows or [], error, calls
        self.closed = False

    async def execute(self, sql, params=()):
        if self.calls is not None:
            self.calls.append((sql, params))
        if self.error is not None:
            raise self.error
        return FakeCursor(self.rows)

    async def close(self):
        self.closed = True


def _app():
    app = web.Application()
    app.router.add_get("/draft-sync/{key}/signups", bot._relay_signups)
    return app


def test_signups_come_back_in_the_bridge_shape_ordered_by_name(monkeypatch):
    calls, conns = [], []

    async def connect():
        conn = FakeConnection(ROWS, calls=calls)
        conns.append(conn)
        return conn

    monkeypatch.setattr(bot.database, "connect", connect)

    async def go():
        async with TestClient(TestServer(_app())) as client:
            r = await client.get(f"/draft-sync/{KEY}/signups")
            assert r.status == 200
            assert r.headers["Cache-Control"] == "no-store"
            return await r.json()

    body = asyncio.run(go())
    assert body["ok"] is True and body["count"] == 2
    assert body["updated_at"] == "2026-11-03T09:30:00+00:00"
    assert [p["display_name"] for p in body["signups"]] == ["amy", "Zoe"]
    amy, zoe = body["signups"]
    assert zoe == {
        "user_id": 548212317515677697,
        "display_name": "Zoe",
        "roles": ["Top", "Jungle"],
        "role_priority": {"Top": 1, "Jungle": 2},
        "champions": {"Top": ["Camille", "Fiora"], "Jungle": ["Lee Sin"]},
    }
    # user ids are ints even when the driver hands back text; broken json falls back to empty containers
    assert amy == {"user_id": 123456789012345678, "display_name": "amy", "roles": ["Mid"], "role_priority": {}, "champions": {}}
    assert set(amy) == {"user_id", "display_name", "roles", "role_priority", "champions"}
    # the query is scoped to the league guild and ordered like the Northflank export
    (sql, params), = calls
    assert "FROM league_flex_signups" in sql and "WHERE guild_id=?" in sql and "ORDER BY display_name" in sql
    assert params == (LEAGUE_GUILD_ID,)
    assert conns[0].closed is True


def test_invalid_key_is_rejected_before_touching_the_database(monkeypatch):
    touched = []

    async def connect():
        touched.append(True)
        return FakeConnection(ROWS)

    monkeypatch.setattr(bot.database, "connect", connect)

    async def go():
        async with TestClient(TestServer(_app())) as client:
            r = await client.get("/draft-sync/bad!key/signups")
            assert r.status == 400
            r = await client.get("/draft-sync/short/signups")
            assert r.status == 400

    asyncio.run(go())
    assert touched == []


@pytest.mark.parametrize("failure", ["connect", "execute"])
def test_database_errors_become_a_short_503(monkeypatch, failure):
    secret = "postgres://user:hunter2@db.internal/intent"

    async def connect():
        if failure == "connect":
            raise ConnectionError(secret)
        return FakeConnection(error=RuntimeError(secret))

    monkeypatch.setattr(bot.database, "connect", connect)

    async def go():
        async with TestClient(TestServer(_app())) as client:
            r = await client.get(f"/draft-sync/{KEY}/signups")
            return r.status, await r.text()

    status, text = asyncio.run(go())
    assert status == 503
    assert "Traceback" not in text and "hunter2" not in text and "RuntimeError" not in text
    assert len(text) < 200
