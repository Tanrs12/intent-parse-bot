from __future__ import annotations

import asyncio
import logging
import os
import re
import time

from aiohttp import web

import discord
from discord.ext import commands

import db as database
from config import LEAGUE_GUILD_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("intent-league-bot")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if __name__ == "__main__" and not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing")

GUILD = discord.Object(id=LEAGUE_GUILD_ID)

_SYNC_SESSIONS: dict[str, dict] = {}
_SYNC_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SYNC_TTL = 30.0


def _sync_key(request: web.Request) -> str:
    key = request.match_info.get("key", "")
    if not _SYNC_KEY.fullmatch(key):
        raise web.HTTPBadRequest(text="invalid sync key")
    return key


async def _relay_health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "intent-draft-sync"})


async def _relay_put(request: web.Request) -> web.Response:
    key = _sync_key(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="expected object")
    current = _SYNC_SESSIONS.get(key, {})
    row = {
        "payload": payload,
        "updated": time.time(),
        "preference": current.get("preference"),
        "preference_version": int(current.get("preference_version") or 0),
    }
    _SYNC_SESSIONS[key] = row
    return web.json_response({"ok": True, "preference": row["preference"],
                              "preference_version": row["preference_version"]})


async def _relay_get(request: web.Request) -> web.Response:
    key = _sync_key(request)
    row = _SYNC_SESSIONS.get(key)
    if not row or time.time() - float(row.get("updated") or 0) > _SYNC_TTL:
        raise web.HTTPServiceUnavailable(text="draft session is offline or stale")
    return web.json_response(row["payload"], headers={"Cache-Control": "no-store"})


async def _relay_preference(request: web.Request) -> web.Response:
    key = _sync_key(request)
    row = _SYNC_SESSIONS.setdefault(key, {"payload": None, "updated": 0.0, "preference": None, "preference_version": 0})
    payload = await request.json()
    signature = str(payload.get("signature") or "").strip() or None
    row["preference"] = signature
    row["preference_version"] = int(row.get("preference_version") or 0) + 1
    return web.json_response({"ok": True, "signature": signature, "preference_version": row["preference_version"]})


async def start_draft_sync_relay() -> web.AppRunner:
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app.router.add_get("/health", _relay_health)
    app.router.add_put("/draft-sync/{key}", _relay_put)
    app.router.add_get("/draft-sync/{key}", _relay_get)
    app.router.add_post("/draft-sync/{key}/preference", _relay_preference)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Draft sync relay listening on :%s", port)
    return runner


class LeagueBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self._identity_checked = False
        self._sync_relay_runner = None

    async def setup_hook(self):
        await database.init_db()
        try:
            self._sync_relay_runner = await start_draft_sync_relay()
        except Exception:
            log.exception("Could not start draft sync relay")
        await self.load_extension("cogs.league_flex")
        try:
            synced = await self.tree.sync(guild=GUILD)
            log.info("Synced %s League commands", len(synced))
        except discord.HTTPException as exc:
            log.warning("League command sync waiting for guild invite: %s", exc)

    async def _apply_identity(self):
        if self.user is None:
            return
        if not self._identity_checked:
            self._identity_checked = True
            if self.user.name != "League Bot":
                try:
                    edited = await self.user.edit(username="League Bot")
                    log.info("Renamed Discord bot account to %s", edited.name)
                except discord.HTTPException as exc:
                    log.warning("Could not rename bot account globally: %s", exc)
        guild = self.get_guild(LEAGUE_GUILD_ID)
        if guild is not None and guild.me is not None and guild.me.display_name != "League Bot":
            try:
                await guild.me.edit(nick="League Bot", reason="Dedicated Ranked Flex bot")
            except discord.HTTPException as exc:
                log.warning("Could not set League-server nickname: %s", exc)

    async def _adopt_signup_board(self, guild: discord.Guild):
        cog = self.get_cog("LeagueFlexCog")
        if cog is None or self.user is None:
            return False
        event = await cog.get_event(guild.id)
        if not event:
            return False
        channel = guild.get_channel(int(event["channel_id"]))
        if channel is None:
            try:
                channel = await guild.fetch_channel(int(event["channel_id"]))
            except discord.HTTPException:
                return False
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False
        if event["message_id"]:
            try:
                existing = await channel.fetch_message(int(event["message_id"]))
                if existing.author.id == self.user.id:
                    return True
            except discord.HTTPException:
                pass

        from cogs.league_flex import LeagueEventView, now_iso
        embed = await cog.build_embed(guild.id)
        message = await channel.send(embed=embed, view=LeagueEventView(cog))
        db = await database.connect()
        try:
            await db.execute(
                "UPDATE league_flex_events SET channel_id=?,message_id=?,updated_at=? WHERE guild_id=?",
                (channel.id, message.id, now_iso(), guild.id),
            )
            await db.commit()
        finally:
            await db.close()
        log.info("Adopted League signup board with new message %s", message.id)
        return True

    async def _sync_if_present(self):
        guild = self.get_guild(LEAGUE_GUILD_ID)
        if guild is None:
            return False
        await self._apply_identity()
        try:
            synced = await self.tree.sync(guild=GUILD)
            log.info("Synced %s League commands", len(synced))
        except discord.HTTPException:
            log.exception("Could not sync League commands")
        try:
            return await self._adopt_signup_board(guild)
        except Exception:
            log.exception("Could not adopt the existing League signup board")
            return False

    async def _delayed_ready_sync(self):
        # Guild/member caches can lag the READY event by a moment on a fresh deployment.
        # Retry adoption rather than letting reminder startup race the old bot-authored board.
        for attempt in range(1, 6):
            await asyncio.sleep(2 if attempt == 1 else 3)
            guild = self.get_guild(LEAGUE_GUILD_ID)
            if guild is None:
                log.info("League guild cache not ready yet (attempt %s/5)", attempt)
                continue
            if await self._sync_if_present():
                return
            log.info("League board adoption not complete yet (attempt %s/5)", attempt)

    async def on_ready(self):
        log.info("League bot ready as %s (%s)", self.user, self.user.id if self.user else "unknown")
        await self._apply_identity()
        asyncio.create_task(self._delayed_ready_sync())
        await self.change_presence(activity=discord.Game(name="Ranked Flex drafts"))

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id == LEAGUE_GUILD_ID:
            await self._sync_if_present()

    async def close(self):
        if self._sync_relay_runner is not None:
            await self._sync_relay_runner.cleanup()
        await database.close_pool()
        await super().close()


bot = LeagueBot()

if __name__ == "__main__":
    bot.run(TOKEN)
