# Intent Parse Bot v2 — Northflank Free Setup

This build uses PostgreSQL instead of a local SQLite file so it can use Northflank's free Developer Sandbox database and remain online without your PC.

## What is already included
- Existing Intent roster imported from the SQLite database supplied to ChatGPT
- Existing current SoO scores/roles used as initial SoO PBs
- Bubblesx and Wagster set to HPS; other imported raiders set to DPS
- Current SoO tracking and permanent PB/history
- MSV / HoF+ToES / ToT / SoO historical backfill
- /profile, /parse, /leaderboard, /pbs, /improvements, /nextcolour, /close, /history, /compare, /raidrecap
- Automatic sync, PB announcements and colour-promotion announcements

## Required Northflank runtime variables
- DISCORD_TOKEN
- WCL_CLIENT_ID
- WCL_CLIENT_SECRET
- DATABASE_URL (alias Northflank PostgreSQL POSTGRES_URI to this)
- SYNC_INTERVAL_HOURS=6
- ALLOW_DEMOTIONS=false
- PB_ANNOUNCE_MIN=0.50
- SOO_PARTITION=5

## First boot
If PostgreSQL is empty, the bot automatically imports `seed_intent_parse_bot.db` into PostgreSQL and upgrades the schema. The seed file contains the roster/settings, not your API secrets.

Then in Discord:
1. /parserroster
2. /syncparses
3. /backfill
4. /profile
5. /pbs

Do not run the same Discord token locally and on Northflank at the same time.
