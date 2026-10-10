from __future__ import annotations

import asyncio
import os
from typing import Any

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()

LEAGUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS league_flex_events (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_by BIGINT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS league_flex_signups (
    guild_id BIGINT NOT NULL,
    discord_user_id BIGINT NOT NULL,
    display_name TEXT NOT NULL,
    roles_json TEXT NOT NULL DEFAULT '[]',
    role_priority_json TEXT NOT NULL DEFAULT '{}',
    champions_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, discord_user_id),
    FOREIGN KEY (guild_id) REFERENCES league_flex_events(guild_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_league_flex_signups_guild
ON league_flex_signups (guild_id, updated_at);
CREATE TABLE IF NOT EXISTS league_flex_reminder_optouts (
    guild_id BIGINT NOT NULL,
    discord_user_id BIGINT NOT NULL,
    opted_out_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, discord_user_id),
    FOREIGN KEY (guild_id) REFERENCES league_flex_events(guild_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS league_flex_reminder_state (
    guild_id BIGINT PRIMARY KEY,
    last_slot TEXT,
    reminder_message_id BIGINT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES league_flex_events(guild_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS league_flex_draft_state (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT,
    message_id BIGINT,
    updated_at TEXT NOT NULL
);
"""


def _pg_sql(sql: str) -> str:
    index = 0
    out: list[str] = []
    for ch in sql:
        if ch == "?":
            index += 1
            out.append(f"${index}")
        else:
            out.append(ch)
    return "".join(out)


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            if not DATABASE_URL:
                raise RuntimeError("DATABASE_URL is not configured for the League bot")
            _pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=1,
                max_size=3,
                command_timeout=30,
            )
    return _pool


class Cursor:
    def __init__(self, rows: list[asyncpg.Record] | None = None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class Connection:
    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Cursor:
        pool = await _get_pool()
        translated = _pg_sql(sql)
        leading = translated.lstrip().split(None, 1)[0].upper() if translated.strip() else ""
        async with pool.acquire() as conn:
            if leading in {"SELECT", "WITH"}:
                rows = await conn.fetch(translated, *params)
                return Cursor(list(rows))
            await conn.execute(translated, *params)
            return Cursor()

    async def commit(self):
        # Each asyncpg statement above commits atomically when it completes.
        return None

    async def close(self):
        return None


async def init_db():
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(LEAGUE_SCHEMA)


async def connect() -> Connection:
    await _get_pool()
    return Connection()


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
