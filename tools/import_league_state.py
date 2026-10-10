from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

from db import LEAGUE_SCHEMA

TABLE_ORDER = [
    "league_flex_events",
    "league_flex_signups",
    "league_flex_reminder_optouts",
    "league_flex_reminder_state",
    "league_flex_draft_state",
]
DELETE_ORDER = list(reversed(TABLE_ORDER))


async def main(path: str):
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is missing")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(LEAGUE_SCHEMA)
        async with conn.transaction():
            for table in DELETE_ORDER:
                await conn.execute(f"DELETE FROM {table}")
            for table in TABLE_ORDER:
                rows = list(data.get(table, []))
                if not rows:
                    continue
                columns = list(rows[0].keys())
                placeholders = ",".join(f"${i}" for i in range(1, len(columns) + 1))
                sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
                values = [tuple(row.get(column) for column in columns) for row in rows]
                await conn.executemany(sql, values)
        counts = {}
        for table in TABLE_ORDER:
            counts[table] = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        print(counts)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
