import asyncio
import base64
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import asyncpg
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
WCL_CLIENT_ID = os.getenv("WCL_CLIENT_ID", "").strip()
WCL_CLIENT_SECRET = os.getenv("WCL_CLIENT_SECRET", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SYNC_INTERVAL_HOURS = float(os.getenv("SYNC_INTERVAL_HOURS", "6"))
ALLOW_DEMOTIONS = os.getenv("ALLOW_DEMOTIONS", "false").strip().lower() in {"1", "true", "yes", "on"}
PB_ANNOUNCE_MIN = float(os.getenv("PB_ANNOUNCE_MIN", "0.50"))
SOO_PARTITION = int(os.getenv("SOO_PARTITION", "5"))

WCL_TOKEN_URL = "https://classic.warcraftlogs.com/oauth/token"
WCL_API_URL = "https://classic.warcraftlogs.com/api/v2/client"

DEFAULT_REGION = "EU"
DEFAULT_RAID_SIZE = 10
DEFAULT_DIFFICULTY = 4
CURRENT_ZONE_ID = 1054

# MoP Classic Warcraft Logs raid-zone IDs.
TIERS = [
    (1038, "Mogu'shan Vaults", "MSV"),
    (1040, "HoF / ToES", "HoF/ToES"),
    (1046, "Throne of Thunder", "ToT"),
    (1054, "Siege of Orgrimmar", "SoO"),
]
TIER_NAME = {zone_id: name for zone_id, name, _ in TIERS}
TIER_SHORT = {zone_id: short for zone_id, _, short in TIERS}

PARSE_ROLES = [
    ("Grey Parser", 0x666666, 0),
    ("Green Parser", 0x1EFF00, 25),
    ("Blue Parser", 0x0070FF, 50),
    ("Purple Parser", 0xA335EE, 75),
    ("Orange Parser", 0xFF8000, 95),
    ("Pink Parser", 0xE268A8, 99),
    ("Gold Parser", 0xE5CC80, 100),
]
ROLE_BY_NAME = {name: (colour, floor) for name, colour, floor in PARSE_ROLES}
ROLE_ORDER = {name: i for i, (name, _, _) in enumerate(PARSE_ROLES)}
ROLE_EMOJI = {
    "Grey Parser": "⚫",
    "Green Parser": "🟢",
    "Blue Parser": "🔵",
    "Purple Parser": "🟣",
    "Orange Parser": "🟠",
    "Pink Parser": "🩷",
    "Gold Parser": "🟡",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parser_role_for(score: float) -> str:
    if score >= 100:
        return "Gold Parser"
    if score >= 99:
        return "Pink Parser"
    if score >= 95:
        return "Orange Parser"
    if score >= 75:
        return "Purple Parser"
    if score >= 50:
        return "Blue Parser"
    if score >= 25:
        return "Green Parser"
    return "Grey Parser"


def next_role(score: float) -> tuple[Optional[str], float]:
    for name, _, floor in PARSE_ROLES:
        if floor > score:
            return name, max(0.0, floor - score)
    return None, 0.0


def role_colour(role_name: Optional[str]) -> discord.Colour:
    if role_name and role_name in ROLE_BY_NAME:
        return discord.Colour(ROLE_BY_NAME[role_name][0])
    return discord.Colour.blurple()


def slugify_realm(value: str) -> str:
    value = value.strip().lower().replace("’", "'")
    value = re.sub(r"['.]", "", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    return re.sub(r"-+", "-", value).strip("-")


def character_id_from_input(value: str) -> Optional[int]:
    value = value.strip()
    if value.isdigit():
        return int(value)
    match = re.search(r"/character/id/(\d+)", value, flags=re.I)
    return int(match.group(1)) if match else None


_pool: Optional[asyncpg.Pool] = None


def _pg_sql(sql: str) -> str:
    """Translate the bot's simple SQLite-style ? placeholders to asyncpg $1, $2..."""
    index = 0
    out = []
    for ch in sql:
        if ch == "?":
            index += 1
            out.append(f"${index}")
        else:
            out.append(ch)
    return "".join(out)


async def execute(sql: str, params: tuple = ()) -> str:
    if _pool is None:
        raise RuntimeError("Database is not initialised")
    async with _pool.acquire() as conn:
        return await conn.execute(_pg_sql(sql), *params)


async def fetchone(sql: str, params: tuple = ()):
    if _pool is None:
        raise RuntimeError("Database is not initialised")
    async with _pool.acquire() as conn:
        return await conn.fetchrow(_pg_sql(sql), *params)


async def fetchall(sql: str, params: tuple = ()):
    if _pool is None:
        raise RuntimeError("Database is not initialised")
    async with _pool.acquire() as conn:
        return await conn.fetch(_pg_sql(sql), *params)


def _seed_rows(table: str) -> list[dict]:
    seed = Path(__file__).with_name("seed_intent_parse_bot.db")
    if not seed.exists():
        return []
    con = sqlite3.connect(seed)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table}").fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()


async def _import_seed_if_empty(conn: asyncpg.Connection) -> None:
    existing = await conn.fetchval("SELECT COUNT(*) FROM guild_settings")
    existing_raiders = await conn.fetchval("SELECT COUNT(*) FROM raiders")
    if existing or existing_raiders:
        return

    settings_rows = _seed_rows("guild_settings")
    raider_rows = _seed_rows("raiders")
    if not settings_rows and not raider_rows:
        return

    for row in settings_rows:
        await conn.execute(
            """
            INSERT INTO guild_settings(
                guild_id, realm, region, zone_id, raid_size, announcement_channel_id, auto_sync
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (guild_id) DO NOTHING
            """,
            int(row["guild_id"]), row["realm"], row.get("region") or "EU",
            int(row.get("zone_id") or 1054), int(row.get("raid_size") or 10),
            row.get("announcement_channel_id"), int(row.get("auto_sync") or 0),
        )

    for row in raider_rows:
        name = row["character_name"]
        metric = "hps" if name.lower() in {"bubblesx", "wagster"} else "dps"
        await conn.execute(
            """
            INSERT INTO raiders(
                guild_id, discord_user_id, character_name, realm, last_parse, last_role, metric
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (guild_id, discord_user_id) DO NOTHING
            """,
            int(row["guild_id"]), int(row["discord_user_id"]), name, row["realm"],
            row.get("last_parse"), row.get("last_role"), metric,
        )
    print(f"Imported {len(raider_rows)} raiders from the existing Intent SQLite database into PostgreSQL")


async def init_db() -> None:
    global _pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing. On Northflank, link the PostgreSQL addon's POSTGRES_URI "
            "to the bot service with the alias DATABASE_URL."
        )

    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3, command_timeout=30)
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    realm TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'EU',
                    zone_id INTEGER NOT NULL DEFAULT 1054,
                    raid_size INTEGER NOT NULL DEFAULT 10,
                    announcement_channel_id BIGINT,
                    auto_sync INTEGER NOT NULL DEFAULT 0,
                    pb_announce_min DOUBLE PRECISION NOT NULL DEFAULT 0.50,
                    close_threshold DOUBLE PRECISION NOT NULL DEFAULT 3.0
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raiders (
                    guild_id BIGINT NOT NULL,
                    discord_user_id BIGINT NOT NULL,
                    character_name TEXT NOT NULL,
                    realm TEXT NOT NULL,
                    last_parse DOUBLE PRECISION,
                    last_role TEXT,
                    character_id BIGINT,
                    metric TEXT NOT NULL DEFAULT 'dps',
                    tracking_start_parse DOUBLE PRECISION,
                    tracking_start_at TEXT,
                    last_checked_at TEXT,
                    PRIMARY KEY (guild_id, discord_user_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parse_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    discord_user_id BIGINT NOT NULL,
                    zone_id INTEGER NOT NULL,
                    score DOUBLE PRECISION NOT NULL,
                    checked_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'sync'
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_raider_time
                ON parse_snapshots(guild_id, discord_user_id, zone_id, checked_at)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tier_bests (
                    guild_id BIGINT NOT NULL,
                    discord_user_id BIGINT NOT NULL,
                    zone_id INTEGER NOT NULL,
                    tier_name TEXT NOT NULL,
                    score DOUBLE PRECISION NOT NULL,
                    metric TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, discord_user_id, zone_id)
                )
                """
            )

            # Safe upgrades if a prior Northflank version already exists.
            await conn.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS pb_announce_min DOUBLE PRECISION NOT NULL DEFAULT 0.50")
            await conn.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS close_threshold DOUBLE PRECISION NOT NULL DEFAULT 3.0")
            await conn.execute("ALTER TABLE raiders ADD COLUMN IF NOT EXISTS character_id BIGINT")
            await conn.execute("ALTER TABLE raiders ADD COLUMN IF NOT EXISTS metric TEXT NOT NULL DEFAULT 'dps'")
            await conn.execute("ALTER TABLE raiders ADD COLUMN IF NOT EXISTS tracking_start_parse DOUBLE PRECISION")
            await conn.execute("ALTER TABLE raiders ADD COLUMN IF NOT EXISTS tracking_start_at TEXT")
            await conn.execute("ALTER TABLE raiders ADD COLUMN IF NOT EXISTS last_checked_at TEXT")

            await _import_seed_if_empty(conn)

            await conn.execute("UPDATE raiders SET metric='hps' WHERE lower(character_name) IN ('bubblesx','wagster')")
            await conn.execute("UPDATE raiders SET metric='dps' WHERE metric IS NULL OR metric NOT IN ('dps','hps')")

            stamp = now_iso()
            await conn.execute(
                """
                UPDATE raiders
                SET tracking_start_parse = COALESCE(tracking_start_parse, last_parse),
                    tracking_start_at = COALESCE(tracking_start_at, $1),
                    last_checked_at = COALESCE(last_checked_at, $2)
                WHERE last_parse IS NOT NULL
                """,
                stamp, stamp,
            )

            # Seed current SoO PB/history from the imported working bot.
            rows = await conn.fetch(
                "SELECT guild_id, discord_user_id, last_parse, metric FROM raiders WHERE last_parse IS NOT NULL"
            )
            for row in rows:
                score = float(row["last_parse"])
                metric = row["metric"] or "dps"
                await conn.execute(
                    """
                    INSERT INTO tier_bests(guild_id, discord_user_id, zone_id, tier_name, score, metric, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT(guild_id, discord_user_id, zone_id) DO NOTHING
                    """,
                    row["guild_id"], row["discord_user_id"], CURRENT_ZONE_ID,
                    TIER_NAME[CURRENT_ZONE_ID], score, metric, stamp,
                )
                exists = await conn.fetchval(
                    "SELECT 1 FROM parse_snapshots WHERE guild_id=$1 AND discord_user_id=$2 AND zone_id=$3 LIMIT 1",
                    row["guild_id"], row["discord_user_id"], CURRENT_ZONE_ID,
                )
                if not exists:
                    await conn.execute(
                        """
                        INSERT INTO parse_snapshots(guild_id, discord_user_id, zone_id, score, checked_at, source)
                        VALUES ($1,$2,$3,$4,$5,'migration')
                        """,
                        row["guild_id"], row["discord_user_id"], CURRENT_ZONE_ID, score, stamp,
                    )


async def get_settings(guild_id: int):
    return await fetchone("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,))


async def get_raiders(guild_id: int):
    return await fetchall(
        "SELECT * FROM raiders WHERE guild_id=? ORDER BY lower(character_name)", (guild_id,)
    )


async def get_raider(guild_id: int, user_id: int):
    return await fetchone(
        "SELECT * FROM raiders WHERE guild_id=? AND discord_user_id=?", (guild_id, user_id)
    )


async def get_tier_bests(guild_id: int, user_id: int):
    return await fetchall(
        "SELECT * FROM tier_bests WHERE guild_id=? AND discord_user_id=? ORDER BY zone_id",
        (guild_id, user_id),
    )


async def get_tier_best(guild_id: int, user_id: int, zone_id: int) -> Optional[float]:
    row = await fetchone(
        "SELECT score FROM tier_bests WHERE guild_id=? AND discord_user_id=? AND zone_id=?",
        (guild_id, user_id, zone_id),
    )
    return float(row["score"]) if row else None


async def upsert_settings(guild_id: int, realm: str, channel_id: Optional[int]) -> None:
    await execute(
        """
        INSERT INTO guild_settings(guild_id, realm, region, zone_id, raid_size, announcement_channel_id, auto_sync)
        VALUES (?, ?, 'EU', 1054, 10, ?, 0)
        ON CONFLICT(guild_id) DO UPDATE SET
            realm=excluded.realm,
            announcement_channel_id=COALESCE(excluded.announcement_channel_id, guild_settings.announcement_channel_id)
        """,
        (guild_id, realm, channel_id),
    )


async def set_auto_sync(guild_id: int, enabled: bool) -> None:
    await execute("UPDATE guild_settings SET auto_sync=? WHERE guild_id=?", (1 if enabled else 0, guild_id))


async def set_announcement_channel(guild_id: int, channel_id: int) -> None:
    await execute("UPDATE guild_settings SET announcement_channel_id=? WHERE guild_id=?", (channel_id, guild_id))


async def upsert_raider(
    guild_id: int,
    user_id: int,
    character: str,
    realm: str,
    character_id: Optional[int],
    metric: str,
) -> None:
    await execute(
        """
        INSERT INTO raiders(guild_id, discord_user_id, character_name, realm, character_id, metric)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, discord_user_id) DO UPDATE SET
            character_name=excluded.character_name,
            realm=excluded.realm,
            character_id=COALESCE(excluded.character_id, raiders.character_id),
            metric=excluded.metric
        """,
        (guild_id, user_id, character, realm, character_id, metric),
    )


async def remove_raider(guild_id: int, user_id: int) -> None:
    if _pool is None:
        raise RuntimeError("Database is not initialised")
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM raiders WHERE guild_id=$1 AND discord_user_id=$2", guild_id, user_id)
            await conn.execute("DELETE FROM parse_snapshots WHERE guild_id=$1 AND discord_user_id=$2", guild_id, user_id)
            await conn.execute("DELETE FROM tier_bests WHERE guild_id=$1 AND discord_user_id=$2", guild_id, user_id)


async def set_metric(guild_id: int, user_id: int, metric: str) -> None:
    if _pool is None:
        raise RuntimeError("Database is not initialised")
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE raiders
                SET metric=$1, last_parse=NULL, tracking_start_parse=NULL, tracking_start_at=NULL, last_checked_at=NULL
                WHERE guild_id=$2 AND discord_user_id=$3
                """,
                metric, guild_id, user_id,
            )
            await conn.execute("DELETE FROM parse_snapshots WHERE guild_id=$1 AND discord_user_id=$2", guild_id, user_id)
            await conn.execute("DELETE FROM tier_bests WHERE guild_id=$1 AND discord_user_id=$2", guild_id, user_id)


async def save_sync_result(
    guild_id: int,
    user_id: int,
    score: float,
    role_name: str,
    character_name: str,
    realm: str,
    character_id: Optional[int],
    metric: str,
    source: str,
) -> tuple[Optional[float], float, bool]:
    """Returns (old_pb, new_pb, is_new_pb)."""
    if _pool is None:
        raise RuntimeError("Database is not initialised")
    stamp = now_iso()
    async with _pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow(
                "SELECT score FROM tier_bests WHERE guild_id=$1 AND discord_user_id=$2 AND zone_id=$3 FOR UPDATE",
                guild_id, user_id, CURRENT_ZONE_ID,
            )
            old_pb = float(old["score"]) if old else None
            new_pb = score if old_pb is None else max(old_pb, score)
            is_new_pb = old_pb is None or score > old_pb + 1e-9

            await conn.execute(
                """
                UPDATE raiders
                SET character_name=$1, realm=$2, character_id=COALESCE($3, character_id), metric=$4,
                    last_parse=$5, last_role=$6,
                    tracking_start_parse=COALESCE(tracking_start_parse, $7),
                    tracking_start_at=COALESCE(tracking_start_at, $8),
                    last_checked_at=$9
                WHERE guild_id=$10 AND discord_user_id=$11
                """,
                character_name, realm, character_id, metric, score, role_name,
                score, stamp, stamp, guild_id, user_id,
            )
            await conn.execute(
                """
                INSERT INTO parse_snapshots(guild_id, discord_user_id, zone_id, score, checked_at, source)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                guild_id, user_id, CURRENT_ZONE_ID, score, stamp, source,
            )
            await conn.execute(
                """
                INSERT INTO tier_bests(guild_id, discord_user_id, zone_id, tier_name, score, metric, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT(guild_id, discord_user_id, zone_id) DO UPDATE SET
                    score=GREATEST(tier_bests.score, excluded.score),
                    metric=excluded.metric,
                    updated_at=CASE WHEN excluded.score > tier_bests.score THEN excluded.updated_at ELSE tier_bests.updated_at END
                """,
                guild_id, user_id, CURRENT_ZONE_ID, TIER_NAME[CURRENT_ZONE_ID], score, metric, stamp,
            )
            return old_pb, new_pb, is_new_pb


async def save_historical_best(guild_id: int, user_id: int, zone_id: int, score: float, metric: str) -> None:
    stamp = now_iso()
    await execute(
        """
        INSERT INTO tier_bests(guild_id, discord_user_id, zone_id, tier_name, score, metric, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, discord_user_id, zone_id) DO UPDATE SET
            score=GREATEST(tier_bests.score, excluded.score),
            metric=excluded.metric,
            updated_at=CASE WHEN excluded.score > tier_bests.score THEN excluded.updated_at ELSE tier_bests.updated_at END
        """,
        (guild_id, user_id, zone_id, TIER_NAME[zone_id], score, metric, stamp),
    )


async def previous_score(guild_id: int, user_id: int) -> Optional[float]:
    rows = await fetchall(
        """
        SELECT score FROM parse_snapshots
        WHERE guild_id=? AND discord_user_id=? AND zone_id=?
        ORDER BY checked_at DESC, id DESC LIMIT 2
        """,
        (guild_id, user_id, CURRENT_ZONE_ID),
    )
    return float(rows[1]["score"]) if len(rows) >= 2 else None


async def baseline_score(guild_id: int, user_id: int, period: str) -> Optional[float]:
    if period == "tracking":
        row = await get_raider(guild_id, user_id)
        return float(row["tracking_start_parse"]) if row and row["tracking_start_parse"] is not None else None
    if period == "last_sync":
        return await previous_score(guild_id, user_id)

    days = 7 if period == "7d" else 30
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    row = await fetchone(
        """
        SELECT score FROM parse_snapshots
        WHERE guild_id=? AND discord_user_id=? AND zone_id=? AND checked_at<=?
        ORDER BY checked_at DESC, id DESC LIMIT 1
        """,
        (guild_id, user_id, CURRENT_ZONE_ID, cutoff),
    )
    if row:
        return float(row["score"])
    row = await fetchone(
        """
        SELECT score FROM parse_snapshots
        WHERE guild_id=? AND discord_user_id=? AND zone_id=?
        ORDER BY checked_at ASC, id ASC LIMIT 1
        """,
        (guild_id, user_id, CURRENT_ZONE_ID),
    )
    return float(row["score"]) if row else None


@dataclass
class WCLResult:
    character: str
    character_id: int
    realm: str
    score: float


class WCLClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token

            basic = base64.b64encode(f"{WCL_CLIENT_ID}:{WCL_CLIENT_SECRET}".encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {basic}"}
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    WCL_TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    headers=headers,
                ) as response:
                    body = await response.text()
                    if response.status != 200:
                        raise RuntimeError(f"WCL authentication failed ({response.status}): {body[:250]}")
                    data = json.loads(body)
            self._token = data["access_token"]
            self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
            return self._token

    async def _graphql(self, query: str, variables: dict) -> dict:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(WCL_API_URL, json={"query": query, "variables": variables}, headers=headers) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    raise RuntimeError(f"WCL API error ({response.status}): {str(payload)[:250]}")
        if payload.get("errors"):
            message = payload["errors"][0].get("message", "Unknown GraphQL error")
            raise RuntimeError(f"WCL GraphQL error: {message}")
        return payload

    @staticmethod
    def _rank_score(raw, label: str) -> float:
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise RuntimeError(f"No {label} rankings found")
        score = raw.get("bestPerformanceAverage")
        if score is None:
            raise RuntimeError(f"No Best Performance Average found for {label}")
        return float(score)

    async def best_average(
        self,
        character: str,
        realm: str,
        region: str,
        metric: str,
        character_id: Optional[int] = None,
    ) -> WCLResult:
        metric = metric.lower().strip()
        if metric not in {"dps", "hps"}:
            raise RuntimeError(f"Unsupported metric: {metric}")

        args = """
          zoneRankings(
            zoneID: $zoneID,
            size: $size,
            metric: $metric,
            difficulty: $difficulty,
            partition: $partition,
            byBracket: false
          )
        """
        common_vars = {
            "zoneID": CURRENT_ZONE_ID,
            "size": DEFAULT_RAID_SIZE,
            "metric": metric,
            "difficulty": DEFAULT_DIFFICULTY,
            "partition": SOO_PARTITION,
        }

        if character_id is not None:
            query = f"""
            query CurrentByID($id: Int!, $zoneID: Int!, $size: Int!, $metric: CharacterPageRankingMetricType!, $difficulty: Int!, $partition: Int!) {{
              characterData {{
                character(id: $id) {{ id canonicalID name server {{ name }} {args} }}
              }}
            }}
            """
            variables = {"id": character_id, **common_vars}
        else:
            query = f"""
            query CurrentByName($name: String!, $serverSlug: String!, $serverRegion: String!, $zoneID: Int!, $size: Int!, $metric: CharacterPageRankingMetricType!, $difficulty: Int!, $partition: Int!) {{
              characterData {{
                character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {{ id canonicalID name server {{ name }} {args} }}
              }}
            }}
            """
            variables = {
                "name": character,
                "serverSlug": slugify_realm(realm),
                "serverRegion": region.lower(),
                **common_vars,
            }

        payload = await self._graphql(query, variables)
        char = payload.get("data", {}).get("characterData", {}).get("character")
        if not char:
            identifier = f"ID {character_id}" if character_id is not None else f"{character}-{realm}"
            raise RuntimeError(f"Character not found: {identifier}")
        score = self._rank_score(char.get("zoneRankings"), TIER_NAME[CURRENT_ZONE_ID])
        resolved_id = int(char.get("canonicalID") or char["id"])
        resolved_realm = (char.get("server") or {}).get("name") or realm
        return WCLResult(char.get("name", character), resolved_id, resolved_realm, score)

    async def historical_tiers(
        self,
        character: str,
        realm: str,
        region: str,
        metric: str,
        character_id: Optional[int] = None,
    ) -> tuple[WCLResult, dict[int, Optional[float]]]:
        metric = metric.lower().strip()
        if metric not in {"dps", "hps"}:
            raise RuntimeError(f"Unsupported metric: {metric}")

        rank_fields = "\n".join(
            f'''z{zone_id}: zoneRankings(zoneID: {zone_id}, size: $size, metric: $metric, difficulty: $difficulty, partition: -1, byBracket: false)'''
            for zone_id, _, _ in TIERS[:-1]
        )
        # Keep current SoO aligned with the user's current Partition 5 page rather than all historical phases.
        rank_fields += f'''\nz{CURRENT_ZONE_ID}: zoneRankings(zoneID: {CURRENT_ZONE_ID}, size: $size, metric: $metric, difficulty: $difficulty, partition: {SOO_PARTITION}, byBracket: false)'''

        vars_common = {"size": DEFAULT_RAID_SIZE, "metric": metric, "difficulty": DEFAULT_DIFFICULTY}
        if character_id is not None:
            query = f"""
            query HistoryByID($id: Int!, $size: Int!, $metric: CharacterPageRankingMetricType!, $difficulty: Int!) {{
              characterData {{ character(id: $id) {{ id canonicalID name server {{ name }} {rank_fields} }} }}
            }}
            """
            variables = {"id": character_id, **vars_common}
        else:
            query = f"""
            query HistoryByName($name: String!, $serverSlug: String!, $serverRegion: String!, $size: Int!, $metric: CharacterPageRankingMetricType!, $difficulty: Int!) {{
              characterData {{ character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {{ id canonicalID name server {{ name }} {rank_fields} }} }}
            }}
            """
            variables = {
                "name": character,
                "serverSlug": slugify_realm(realm),
                "serverRegion": region.lower(),
                **vars_common,
            }
        payload = await self._graphql(query, variables)
        char = payload.get("data", {}).get("characterData", {}).get("character")
        if not char:
            raise RuntimeError(f"Character not found: {character}-{realm}")

        scores: dict[int, Optional[float]] = {}
        for zone_id, name, _ in TIERS:
            raw = char.get(f"z{zone_id}")
            try:
                scores[zone_id] = self._rank_score(raw, name)
            except Exception:
                scores[zone_id] = None
        resolved_id = int(char.get("canonicalID") or char["id"])
        resolved_realm = (char.get("server") or {}).get("name") or realm
        current = WCLResult(char.get("name", character), resolved_id, resolved_realm, scores.get(CURRENT_ZONE_ID) or 0.0)
        return current, scores


wcl = WCLClient()


class IntentParseClient(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await init_db()
        await self.tree.sync()
        if not auto_sync_loop.is_running():
            auto_sync_loop.start()


client = IntentParseClient()
tree = client.tree


async def ensure_parse_roles(guild: discord.Guild) -> list[discord.Role]:
    existing = {r.name: r for r in guild.roles}
    found: list[discord.Role] = []
    for name, colour, _ in PARSE_ROLES:
        role = existing.get(name)
        if role is None:
            role = await guild.create_role(name=name, colour=discord.Colour(colour), reason="Intent Parse Bot setup")
            existing[name] = role
        found.append(role)
        me = guild.me
        if me and me.top_role.position > role.position:
            try:
                await role.edit(position=max(1, me.top_role.position - 1), reason="Keep parse roles below bot role")
            except discord.HTTPException:
                pass
    return found


def member_existing_parse_role(member: discord.Member) -> Optional[discord.Role]:
    roles = [r for r in member.roles if r.name in ROLE_BY_NAME]
    return max(roles, key=lambda r: ROLE_ORDER[r.name]) if roles else None


async def apply_parse_role(member: discord.Member, target_name: str) -> tuple[Optional[str], str, bool]:
    guild = member.guild
    target = discord.utils.get(guild.roles, name=target_name)
    if target is None:
        await ensure_parse_roles(guild)
        target = discord.utils.get(guild.roles, name=target_name)
    if target is None:
        raise RuntimeError(f"Could not create/find role {target_name}")

    current = member_existing_parse_role(member)
    current_name = current.name if current else None
    if current and not ALLOW_DEMOTIONS and ROLE_ORDER[current.name] > ROLE_ORDER[target_name]:
        return current_name, current.name, False

    remove = [r for r in member.roles if r.name in ROLE_BY_NAME and r.name != target_name]
    if remove:
        await member.remove_roles(*remove, reason="Warcraft Logs parse role sync")
    if target not in member.roles:
        await member.add_roles(target, reason="Warcraft Logs parse role sync")
    return current_name, target_name, current_name != target_name


async def member_for(interaction: discord.Interaction, member: Optional[discord.Member]) -> Optional[discord.Member]:
    if member is not None:
        return member
    return interaction.user if isinstance(interaction.user, discord.Member) else None


async def display_name(guild: discord.Guild, user_id: int, fallback: str) -> str:
    member = guild.get_member(user_id)
    return member.display_name if member else fallback


async def sync_guild(guild: discord.Guild, source: str = "sync") -> dict:
    settings = await get_settings(guild.id)
    if not settings:
        raise RuntimeError("Run /setup first.")
    await ensure_parse_roles(guild)
    raiders = await get_raiders(guild.id)
    if not raiders:
        raise RuntimeError("No raiders linked yet.")

    results, errors = [], []
    for row in raiders:
        try:
            member = guild.get_member(row["discord_user_id"])
            if member is None:
                member = await guild.fetch_member(row["discord_user_id"])

            result = await wcl.best_average(
                character=row["character_name"],
                realm=row["realm"],
                region=settings["region"],
                metric=row["metric"],
                character_id=row["character_id"],
            )
            old_current = float(row["last_parse"]) if row["last_parse"] is not None else None
            old_pb = await get_tier_best(guild.id, member.id, CURRENT_ZONE_ID)
            projected_pb = result.score if old_pb is None else max(old_pb, result.score)
            role_basis = result.score if ALLOW_DEMOTIONS else projected_pb
            target_role = parser_role_for(role_basis)
            old_role, applied_role, role_changed = await apply_parse_role(member, target_role)
            saved_old_pb, new_pb, is_new_pb = await save_sync_result(
                guild.id,
                member.id,
                result.score,
                applied_role,
                result.character,
                result.realm,
                result.character_id,
                row["metric"],
                source,
            )
            pb_gain = result.score - saved_old_pb if saved_old_pb is not None and is_new_pb else 0.0
            promotion = bool(
                role_changed
                and (old_role is None or ROLE_ORDER.get(applied_role, 0) > ROLE_ORDER.get(old_role, -1))
            )
            results.append(
                {
                    "member": member,
                    "character": result.character,
                    "metric": row["metric"],
                    "score": result.score,
                    "old_current": old_current,
                    "delta": result.score - old_current if old_current is not None else None,
                    "old_pb": saved_old_pb,
                    "new_pb": new_pb,
                    "is_new_pb": is_new_pb,
                    "pb_gain": pb_gain,
                    "old_role": old_role,
                    "new_role": applied_role,
                    "promotion": promotion,
                }
            )
            await asyncio.sleep(0.12)
        except Exception as exc:
            errors.append(f"{row['character_name']}: {exc}")
    return {"results": results, "errors": errors}


def full_sync_embed(data: dict, title: str = "Intent Parse Rankings Updated") -> discord.Embed:
    results = sorted(data["results"], key=lambda x: x["score"], reverse=True)
    embed = discord.Embed(title=title, colour=discord.Colour.blurple())
    lines = []
    for i, item in enumerate(results, 1):
        emoji = ROLE_EMOJI.get(item["new_role"], "")
        delta = ""
        if item["delta"] is not None and abs(item["delta"]) >= 0.01:
            delta = f" ({item['delta']:+.2f})"
        marker = " ⬆️" if item["promotion"] else ""
        lines.append(f"**{i}. {item['member'].display_name}** — {emoji} `{item['score']:.2f}`{delta}{marker}")
    if lines:
        embed.description = "\n".join(lines[:25])
    promotions = [x for x in results if x["promotion"]]
    if promotions:
        embed.add_field(
            name="🎉 Promotions",
            value="\n".join(
                f"**{x['member'].display_name}** — {x['old_role'] or 'Unranked'} → **{x['new_role']}**"
                for x in promotions[:12]
            ),
            inline=False,
        )
    if data["errors"]:
        embed.add_field(name="Could not update", value="\n".join(data["errors"][:8]), inline=False)
    embed.set_footer(text=f"SoO Heroic 10M • Partition {SOO_PARTITION} • WCL Best Performance Average")
    return embed


def announcement_embed(data: dict) -> Optional[discord.Embed]:
    promotions = [x for x in data["results"] if x["promotion"]]
    significant_pbs = [
        x for x in data["results"]
        if x["is_new_pb"] and x["old_pb"] is not None and x["pb_gain"] >= PB_ANNOUNCE_MIN
    ]
    if not promotions and not significant_pbs:
        return None
    embed = discord.Embed(title="📈 Intent Parse Update", colour=discord.Colour.blurple())
    if promotions:
        embed.add_field(
            name="🎉 Parser Promotions",
            value="\n".join(
                f"{ROLE_EMOJI.get(x['new_role'],'')} **{x['member'].display_name}** — {x['old_role'] or 'Unranked'} → **{x['new_role']}** (`{x['score']:.2f}`)"
                for x in promotions[:15]
            ),
            inline=False,
        )
    if significant_pbs:
        embed.add_field(
            name="✨ New SoO PBs",
            value="\n".join(
                f"**{x['member'].display_name}** — `{x['old_pb']:.2f}` → **`{x['new_pb']:.2f}`** ({x['pb_gain']:+.2f})"
                for x in significant_pbs[:15]
            ),
            inline=False,
        )
    embed.set_footer(text=f"Small PB changes under +{PB_ANNOUNCE_MIN:.2f} are saved silently")
    return embed


async def build_profile_embed(guild: discord.Guild, row) -> discord.Embed:
    user_id = row["discord_user_id"]
    name = await display_name(guild, user_id, row["character_name"])
    current = float(row["last_parse"]) if row["last_parse"] is not None else None
    tier_rows = await get_tier_bests(guild.id, user_id)
    tier_map = {int(r["zone_id"]): float(r["score"]) for r in tier_rows}
    soo_pb = tier_map.get(CURRENT_ZONE_ID)
    career = max(tier_map.items(), key=lambda x: x[1]) if tier_map else None
    role = row["last_role"] or (parser_role_for(soo_pb) if soo_pb is not None else None)
    embed = discord.Embed(
        title=f"{name} • {row['character_name']}",
        colour=role_colour(role),
    )
    current_text = f"**{current:.2f}**" if current is not None else "Not synced yet"
    pb_text = f"**{soo_pb:.2f}**" if soo_pb is not None else "—"
    embed.add_field(name="Current SoO", value=current_text, inline=True)
    embed.add_field(name="SoO PB", value=pb_text, inline=True)
    embed.add_field(name="Metric", value=row["metric"].upper(), inline=True)

    start = float(row["tracking_start_parse"]) if row["tracking_start_parse"] is not None else None
    if current is not None and start is not None:
        embed.add_field(name="Since tracking", value=f"{current - start:+.2f}", inline=True)

    leaderboard = [r for r in await get_raiders(guild.id) if r["last_parse"] is not None]
    leaderboard.sort(key=lambda r: float(r["last_parse"]), reverse=True)
    rank = next((i for i, r in enumerate(leaderboard, 1) if r["discord_user_id"] == user_id), None)
    if rank:
        embed.add_field(name="Guild rank", value=f"#{rank}", inline=True)

    basis = soo_pb if soo_pb is not None else (current or 0.0)
    nxt, distance = next_role(basis)
    if nxt:
        embed.add_field(name="Next colour", value=f"{ROLE_EMOJI[nxt]} **{nxt.replace(' Parser','')}** — {distance:.2f} away", inline=True)
    else:
        embed.add_field(name="Next colour", value="🟡 Gold achieved", inline=True)

    hist_lines = []
    for zone_id, _, short in TIERS:
        if zone_id in tier_map:
            s = tier_map[zone_id]
            rname = parser_role_for(s)
            marker = " 🏆" if career and zone_id == career[0] else ""
            hist_lines.append(f"**{short}** — {ROLE_EMOJI[rname]} `{s:.2f}`{marker}")
    if hist_lines:
        embed.add_field(name="MoP history", value="\n".join(hist_lines), inline=False)
    if career:
        zone_id, score = career
        embed.add_field(
            name="MoP Career PB",
            value=f"**{score:.2f}** — {TIER_SHORT[zone_id]} • {parser_role_for(score)}",
            inline=False,
        )
    embed.set_footer(text=f"{row['realm']} • {row['metric'].upper()} • PB roles never demote")
    return embed


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id if client.user else 'unknown'})")
    print("Database: PostgreSQL (Northflank)")


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ That command is officer-only (Manage Server permission)."
    else:
        message = f"❌ `{error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


# ---------- Officer/admin commands ----------

@tree.command(name="setup", description="Set up Intent Parse Bot for the guild.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(realm="Default WoW realm", announcement_channel="Channel for automatic PB/promotion posts")
async def setup_command(interaction: discord.Interaction, realm: str, announcement_channel: Optional[discord.TextChannel] = None):
    if not interaction.guild:
        return
    await interaction.response.defer(ephemeral=True)
    await ensure_parse_roles(interaction.guild)
    channel_id = announcement_channel.id if announcement_channel else interaction.channel_id
    await upsert_settings(interaction.guild.id, realm.strip(), channel_id)
    await interaction.followup.send(
        f"✅ Set up for **{realm.strip()} / EU / 10M Heroic SoO**. Existing roster/data is preserved.",
        ephemeral=True,
    )


@tree.command(name="addraider", description="Link a Discord member to a MoP Classic WCL character.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(metric=[app_commands.Choice(name="Damage (DPS)", value="dps"), app_commands.Choice(name="Healing (HPS)", value="hps")])
@app_commands.describe(member="Discord member", character="Character name, WCL ID, or full WCL character URL", realm="Only if different from guild realm", metric="DPS for damage/tanks; HPS for healers")
async def addraider_command(interaction: discord.Interaction, member: discord.Member, character: str, metric: str = "dps", realm: Optional[str] = None):
    if not interaction.guild:
        return
    settings = await get_settings(interaction.guild.id)
    if not settings:
        await interaction.response.send_message("Run `/setup` first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    raw = character.strip()
    char_id = character_id_from_input(raw)
    final_realm = (realm or settings["realm"]).strip()
    try:
        result = await wcl.best_average(raw if char_id is None else "", final_realm, settings["region"], metric, char_id)
        await upsert_raider(interaction.guild.id, member.id, result.character, result.realm, result.character_id, metric)
        await interaction.followup.send(
            f"✅ **{member.display_name}** → **{result.character}-{result.realm}** [{metric.upper()}], current SoO average **{result.score:.2f}**. Run `/syncparses` to apply/update their role.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(f"❌ Warcraft Logs could not verify that character: `{exc}`", ephemeral=True)


@tree.command(name="removeraider", description="Remove a raider and their stored parse history.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def removeraider_command(interaction: discord.Interaction, member: discord.Member):
    if not interaction.guild:
        return
    await remove_raider(interaction.guild.id, member.id)
    await interaction.response.send_message(f"✅ Removed **{member.display_name}** and their stored bot history.", ephemeral=True)


@tree.command(name="setmetric", description="Change a raider between DPS and HPS parsing.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(metric=[app_commands.Choice(name="Damage (DPS)", value="dps"), app_commands.Choice(name="Healing (HPS)", value="hps")])
async def setmetric_command(interaction: discord.Interaction, member: discord.Member, metric: str):
    if not interaction.guild:
        return
    row = await get_raider(interaction.guild.id, member.id)
    if not row:
        await interaction.response.send_message("That member isn't linked.", ephemeral=True)
        return
    await set_metric(interaction.guild.id, member.id, metric)
    await interaction.response.send_message(
        f"✅ **{member.display_name}** now uses **{metric.upper()}**. I cleared their bot parse history so DPS/HPS numbers never get mixed; run `/syncparses` then `/backfill`.",
        ephemeral=True,
    )


@tree.command(name="setchannel", description="Choose where automatic promotions and PBs are announced.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel_command(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.guild:
        return
    await set_announcement_channel(interaction.guild.id, channel.id)
    await interaction.response.send_message(f"✅ Automatic parse updates will post in {channel.mention}.", ephemeral=True)


@tree.command(name="syncparses", description="Check all current SoO averages and update roles now.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def syncparses_command(interaction: discord.Interaction):
    if not interaction.guild:
        return
    await interaction.response.defer()
    try:
        data = await sync_guild(interaction.guild, source="manual")
        await interaction.followup.send(embed=full_sync_embed(data))
    except Exception as exc:
        await interaction.followup.send(f"❌ Sync failed: `{exc}`", ephemeral=True)


@tree.command(name="autosync", description="Turn 24/7 automatic WCL syncing on or off.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def autosync_command(interaction: discord.Interaction, enabled: bool):
    if not interaction.guild:
        return
    await set_auto_sync(interaction.guild.id, enabled)
    await interaction.response.send_message(
        f"✅ Automatic syncing is **{'ON' if enabled else 'OFF'}**. Interval: about {SYNC_INTERVAL_HOURS:g} hours.",
        ephemeral=True,
    )


@tree.command(name="backfill", description="Pull MoP historical tier PBs (MSV, HoF/ToES, ToT and SoO).")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def backfill_command(interaction: discord.Interaction):
    if not interaction.guild:
        return
    settings = await get_settings(interaction.guild.id)
    if not settings:
        await interaction.response.send_message("Run `/setup` first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    rows = await get_raiders(interaction.guild.id)
    done, missing, errors = [], [], []
    for row in rows:
        try:
            resolved, scores = await wcl.historical_tiers(
                row["character_name"], row["realm"], settings["region"], row["metric"], row["character_id"]
            )
            await upsert_raider(interaction.guild.id, row["discord_user_id"], resolved.character, resolved.realm, resolved.character_id, row["metric"])
            found = []
            for zone_id, score in scores.items():
                if score is not None and score > 0:
                    await save_historical_best(interaction.guild.id, row["discord_user_id"], zone_id, score, row["metric"])
                    found.append(TIER_SHORT[zone_id])
            name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
            done.append(f"**{name}**: {', '.join(found) if found else 'no tier ranks found'}")
            if not found:
                missing.append(name)
            await asyncio.sleep(0.15)
        except Exception as exc:
            errors.append(f"{row['character_name']}: {exc}")
    embed = discord.Embed(title="🏛 MoP PB Backfill Complete", colour=discord.Colour.blurple())
    if done:
        embed.description = "\n".join(done[:20])
    if errors:
        embed.add_field(name="Errors", value="\n".join(errors[:8]), inline=False)
    embed.set_footer(text="Historical tiers use Heroic 10M overall rankings; SoO stays on current Partition 5")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="parserroster", description="Show the linked roster and each raider's metric.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def parserroster_command(interaction: discord.Interaction):
    if not interaction.guild:
        return
    rows = await get_raiders(interaction.guild.id)
    lines = []
    for row in rows:
        name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
        score = f" — {row['last_parse']:.2f}" if row["last_parse"] is not None else ""
        lines.append(f"• **{name}** → {row['character_name']}-{row['realm']} [{row['metric'].upper()}]{score}")
    await interaction.response.send_message("\n".join(lines) if lines else "No raiders linked.", ephemeral=True)


# ---------- Public/fun commands ----------

@tree.command(name="parse", description="Show a raider's latest stored SoO parse average.")
@app_commands.guild_only()
async def parse_command(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if not interaction.guild:
        return
    member = await member_for(interaction, member)
    if member is None:
        return
    row = await get_raider(interaction.guild.id, member.id)
    if not row:
        await interaction.response.send_message("That member isn't linked to the parse bot yet.", ephemeral=True)
        return
    if row["last_parse"] is None:
        await interaction.response.send_message("No stored parse yet — ask an officer to run `/syncparses`.")
        return
    score = float(row["last_parse"])
    pb = await get_tier_best(interaction.guild.id, member.id, CURRENT_ZONE_ID)
    role = parser_role_for(pb if pb is not None else score)
    await interaction.response.send_message(
        f"{ROLE_EMOJI[role]} **{member.display_name} / {row['character_name']}** — current {row['metric'].upper()} **{score:.2f}** • SoO PB **{(pb if pb is not None else score):.2f}** • {role}"
    )


@tree.command(name="profile", description="Show a raider's parse profile, PBs and MoP tier history.")
@app_commands.guild_only()
async def profile_command(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if not interaction.guild:
        return
    member = await member_for(interaction, member)
    if member is None:
        return
    row = await get_raider(interaction.guild.id, member.id)
    if not row:
        await interaction.response.send_message("That member isn't linked to the parse bot yet.", ephemeral=True)
        return
    await interaction.response.send_message(embed=await build_profile_embed(interaction.guild, row))


@tree.command(name="leaderboard", description="Current Siege of Orgrimmar Best Performance Average leaderboard.")
@app_commands.guild_only()
async def leaderboard_command(interaction: discord.Interaction):
    if not interaction.guild:
        return
    rows = [r for r in await get_raiders(interaction.guild.id) if r["last_parse"] is not None]
    rows.sort(key=lambda r: float(r["last_parse"]), reverse=True)
    if not rows:
        await interaction.response.send_message("No stored results yet.", ephemeral=True)
        return
    lines = []
    for i, row in enumerate(rows[:25], 1):
        name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
        pb = await get_tier_best(interaction.guild.id, row["discord_user_id"], CURRENT_ZONE_ID)
        role = parser_role_for(pb if pb is not None else float(row["last_parse"]))
        lines.append(f"**{i}. {name}** — {ROLE_EMOJI[role]} `{row['last_parse']:.2f}` [{row['metric'].upper()}]")
    embed = discord.Embed(title="🏆 Intent SoO Leaderboard", description="\n".join(lines), colour=discord.Colour.blurple())
    embed.set_footer(text=f"Heroic 10M • Partition {SOO_PARTITION} • latest autosync")
    await interaction.response.send_message(embed=embed)


@tree.command(name="pbs", description="Rank the guild by their best MoP tier average.")
@app_commands.guild_only()
async def pbs_command(interaction: discord.Interaction):
    if not interaction.guild:
        return
    rows = await get_raiders(interaction.guild.id)
    ranked = []
    for row in rows:
        tiers = await get_tier_bests(interaction.guild.id, row["discord_user_id"])
        if tiers:
            best = max(tiers, key=lambda r: float(r["score"]))
            ranked.append((float(best["score"]), int(best["zone_id"]), row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        await interaction.response.send_message("No PBs stored yet. An officer can run `/backfill`.", ephemeral=True)
        return
    lines = []
    for i, (score, zone_id, row) in enumerate(ranked[:25], 1):
        name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
        role = parser_role_for(score)
        lines.append(f"**{i}. {name}** — {ROLE_EMOJI[role]} **`{score:.2f}`** • {TIER_SHORT[zone_id]}")
    embed = discord.Embed(title="🥇 Intent MoP Career PBs", description="\n".join(lines), colour=discord.Colour.gold())
    await interaction.response.send_message(embed=embed)


@tree.command(name="nextcolour", description="Show how close a raider is to their next parse colour.")
@app_commands.guild_only()
async def nextcolour_command(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if not interaction.guild:
        return
    member = await member_for(interaction, member)
    if member is None:
        return
    row = await get_raider(interaction.guild.id, member.id)
    if not row:
        await interaction.response.send_message("That member isn't linked.", ephemeral=True)
        return
    pb = await get_tier_best(interaction.guild.id, member.id, CURRENT_ZONE_ID)
    if pb is None:
        await interaction.response.send_message("No SoO PB stored yet.", ephemeral=True)
        return
    role = parser_role_for(pb)
    nxt, distance = next_role(pb)
    if nxt is None:
        text = f"🟡 **{member.display_name} — {pb:.2f}**\nGold achieved. There is nowhere left to go."
    else:
        target_floor = ROLE_BY_NAME[nxt][1]
        text = f"{ROLE_EMOJI[role]} **{member.display_name} — {pb:.2f}**\n{ROLE_EMOJI[nxt]} **{nxt.replace(' Parser','')}** requires {target_floor:.0f} — **{distance:.2f} points to go**."
    await interaction.response.send_message(text)


@tree.command(name="close", description="Show raiders close to a new parse colour.")
@app_commands.guild_only()
@app_commands.describe(within="Maximum points away (default 3)")
async def close_command(interaction: discord.Interaction, within: float = 3.0):
    if not interaction.guild:
        return
    within = max(0.1, min(float(within), 10.0))
    rows = await get_raiders(interaction.guild.id)
    close = []
    for row in rows:
        pb = await get_tier_best(interaction.guild.id, row["discord_user_id"], CURRENT_ZONE_ID)
        if pb is None:
            continue
        nxt, distance = next_role(pb)
        if nxt and 0 < distance <= within:
            close.append((distance, pb, nxt, row))
    close.sort(key=lambda x: x[0])
    if not close:
        await interaction.response.send_message(f"Nobody is within {within:.1f} points of a new colour right now.")
        return
    lines = []
    for distance, pb, nxt, row in close:
        name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
        lines.append(f"**{name}** — `{pb:.2f}` → {ROLE_EMOJI[nxt]} **{distance:.2f} from {nxt.replace(' Parser','')}**")
    await interaction.response.send_message(embed=discord.Embed(title="👀 Promotion Watch", description="\n".join(lines), colour=discord.Colour.blurple()))


@tree.command(name="history", description="Show a raider's best average from each MoP raid tier.")
@app_commands.guild_only()
async def history_command(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if not interaction.guild:
        return
    member = await member_for(interaction, member)
    if member is None:
        return
    row = await get_raider(interaction.guild.id, member.id)
    if not row:
        await interaction.response.send_message("That member isn't linked.", ephemeral=True)
        return
    tiers = await get_tier_bests(interaction.guild.id, member.id)
    if not tiers:
        await interaction.response.send_message("No history stored yet. An officer can run `/backfill`.")
        return
    scores = {int(r["zone_id"]): float(r["score"]) for r in tiers}
    lines = []
    for zone_id, name, short in TIERS:
        if zone_id in scores:
            score = scores[zone_id]
            role = parser_role_for(score)
            lines.append(f"{ROLE_EMOJI[role]} **{short}** — `{score:.2f}` — {role}")
    await interaction.response.send_message(embed=discord.Embed(title=f"🏛 {member.display_name} • MoP History", description="\n".join(lines), colour=role_colour(row["last_role"])))


@tree.command(name="compare", description="Head-to-head parse comparison between two raiders.")
@app_commands.guild_only()
async def compare_command(interaction: discord.Interaction, player1: discord.Member, player2: discord.Member):
    if not interaction.guild:
        return
    r1 = await get_raider(interaction.guild.id, player1.id)
    r2 = await get_raider(interaction.guild.id, player2.id)
    if not r1 or not r2:
        await interaction.response.send_message("Both players need to be linked to the bot.", ephemeral=True)
        return
    p1 = await get_tier_best(interaction.guild.id, player1.id, CURRENT_ZONE_ID)
    p2 = await get_tier_best(interaction.guild.id, player2.id, CURRENT_ZONE_ID)
    c1 = float(r1["last_parse"]) if r1["last_parse"] is not None else 0.0
    c2 = float(r2["last_parse"]) if r2["last_parse"] is not None else 0.0
    t1 = await get_tier_bests(interaction.guild.id, player1.id)
    t2 = await get_tier_bests(interaction.guild.id, player2.id)
    career1 = max((float(x["score"]) for x in t1), default=0.0)
    career2 = max((float(x["score"]) for x in t2), default=0.0)
    desc = (
        f"**{player1.display_name}** vs **{player2.display_name}**\n\n"
        f"Current SoO: **{c1:.2f}** vs **{c2:.2f}**\n"
        f"SoO PB: **{(p1 or 0):.2f}** vs **{(p2 or 0):.2f}**\n"
        f"MoP Career PB: **{career1:.2f}** vs **{career2:.2f}**\n"
        f"Metric: {r1['metric'].upper()} vs {r2['metric'].upper()}"
    )
    await interaction.response.send_message(embed=discord.Embed(title="⚔️ Parse Head-to-Head", description=desc, colour=discord.Colour.blurple()))


@tree.command(name="improvements", description="Rank raiders by how much their SoO average has improved.")
@app_commands.guild_only()
@app_commands.choices(period=[
    app_commands.Choice(name="Since last sync", value="last_sync"),
    app_commands.Choice(name="Last 7 days", value="7d"),
    app_commands.Choice(name="Last 30 days", value="30d"),
    app_commands.Choice(name="Since tracking began", value="tracking"),
])
async def improvements_command(interaction: discord.Interaction, period: str = "7d"):
    if not interaction.guild:
        return
    rows = [r for r in await get_raiders(interaction.guild.id) if r["last_parse"] is not None]
    scores = []
    for row in rows:
        baseline = await baseline_score(interaction.guild.id, row["discord_user_id"], period)
        if baseline is None:
            continue
        current = float(row["last_parse"])
        scores.append((current - baseline, current, baseline, row))
    scores.sort(key=lambda x: x[0], reverse=True)
    if not scores:
        await interaction.response.send_message("Not enough history yet. Let autosync collect a few snapshots first.")
        return
    label = {"last_sync": "Since Last Sync", "7d": "Last 7 Days", "30d": "Last 30 Days", "tracking": "Since Tracking Began"}[period]
    lines = []
    for i, (delta, current, baseline, row) in enumerate(scores[:25], 1):
        name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
        arrow = "📈" if delta > 0 else ("📉" if delta < 0 else "➖")
        lines.append(f"**{i}. {name}** — {arrow} **{delta:+.2f}** (`{baseline:.2f}` → `{current:.2f}`)")
    await interaction.response.send_message(embed=discord.Embed(title=f"📈 Intent Improvements • {label}", description="\n".join(lines), colour=discord.Colour.blurple()))


@tree.command(name="raidrecap", description="Show the latest gains, PBs, promotions targets and current leaders.")
@app_commands.guild_only()
async def raidrecap_command(interaction: discord.Interaction):
    if not interaction.guild:
        return
    rows = [r for r in await get_raiders(interaction.guild.id) if r["last_parse"] is not None]
    if not rows:
        await interaction.response.send_message("No parse data stored yet.")
        return
    gains = []
    for row in rows:
        prev = await previous_score(interaction.guild.id, row["discord_user_id"])
        if prev is not None:
            gains.append((float(row["last_parse"]) - prev, row))
    gains.sort(key=lambda x: x[0], reverse=True)

    close = []
    for row in rows:
        pb = await get_tier_best(interaction.guild.id, row["discord_user_id"], CURRENT_ZONE_ID)
        if pb is not None:
            nxt, dist = next_role(pb)
            if nxt and dist <= 3:
                close.append((dist, nxt, row))
    close.sort(key=lambda x: x[0])

    leaders = sorted(rows, key=lambda r: float(r["last_parse"]), reverse=True)[:3]
    embed = discord.Embed(title="📊 Intent Raid Recap", colour=discord.Colour.blurple())
    if gains:
        gain_lines = []
        for delta, row in gains[:5]:
            name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
            gain_lines.append(f"**{name}** — {delta:+.2f}")
        embed.add_field(name="📈 Biggest gains since previous sync", value="\n".join(gain_lines), inline=False)
    if close:
        close_lines = []
        for dist, nxt, row in close[:5]:
            name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
            close_lines.append(f"**{name}** — {dist:.2f} from {ROLE_EMOJI[nxt]} {nxt.replace(' Parser','')}")
        embed.add_field(name="👀 Promotion watch", value="\n".join(close_lines), inline=False)
    leader_lines = []
    for i, row in enumerate(leaders, 1):
        name = await display_name(interaction.guild, row["discord_user_id"], row["character_name"])
        leader_lines.append(f"**{i}. {name}** — `{row['last_parse']:.2f}`")
    embed.add_field(name="🏆 Current top 3", value="\n".join(leader_lines), inline=False)
    embed.set_footer(text="Run after /syncparses for an immediate post-raid recap")
    await interaction.response.send_message(embed=embed)


@tasks.loop(hours=SYNC_INTERVAL_HOURS)
async def auto_sync_loop():
    for guild in client.guilds:
        settings = await get_settings(guild.id)
        if not settings or not settings["auto_sync"]:
            continue
        try:
            data = await sync_guild(guild, source="auto")
            embed = announcement_embed(data)
            channel = guild.get_channel(settings["announcement_channel_id"]) if settings["announcement_channel_id"] else None
            if embed and isinstance(channel, discord.TextChannel):
                await channel.send(embed=embed)
            if data["errors"]:
                print(f"Auto-sync partial errors for {guild.name}: {data['errors']}")
        except Exception as exc:
            print(f"Auto-sync failed for {guild.name}: {exc}")


@auto_sync_loop.before_loop
async def before_auto_sync_loop():
    await client.wait_until_ready()
    await asyncio.sleep(30)


def validate_environment() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not WCL_CLIENT_ID:
        missing.append("WCL_CLIENT_ID")
    if not WCL_CLIENT_SECRET:
        missing.append("WCL_CLIENT_SECRET")
    if missing:
        raise SystemExit("Missing required environment values: " + ", ".join(missing))


if __name__ == "__main__":
    validate_environment()
    client.run(DISCORD_TOKEN)
