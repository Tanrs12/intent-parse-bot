from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord.ext import commands

import db as database
from config import LEAGUE_GUILD_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("intent-league-bot")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing")

GUILD = discord.Object(id=LEAGUE_GUILD_ID)


class LeagueBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self._identity_checked = False

    async def setup_hook(self):
        await database.init_db()
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
        await database.close_pool()
        await super().close()


bot = LeagueBot()
bot.run(TOKEN)
