from __future__ import annotations

import asyncio
import hashlib
import itertools
import io
import json
import logging
import math
import os
from pathlib import Path
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFont
from discord import app_commands
from discord.ext import commands, tasks

import db as database
from config import LEAGUE_EVENT_CHANNEL_ID, LEAGUE_GUILD_ID
from league_comp_engine import (
    ALL_IN_ADCS,
    BLOCKED_ROLE_PICKS,
    ENCHANTER_SUPPORTS,
    ENGAGE_SUPPORTS,
    POKE_SUPPORTS,
    attach_learn_suggestions,
    DATA_PATH as LEAGUE_META_PATH,
    build_draft_book,
    clear_player_profile_caches,
    counter_ban_recommendations,
    load_lane_matchups,
    load_meta as load_league_comp_meta,
    load_player_score_index,
    load_solo_queue_winrates,
    recommend_comps,
    role_experience_score,
)
from league_flex_core import (  # noqa: F401  shared drafting brain
    CURRENT_ROLE_SAMPLE_MIN,
    ROLES,
    _CURATED_ROLE_CHAMPIONS,
    _assignment_preference_cost,
    _assignment_priority3_users,
    _attach_role_alternatives,
    _build_role_champions,
    _conditional_ban_annotations,
    _conditional_ban_branches,
    _filter_unavailable,
    _normalised_role_priorities,
    _pro_pick_order,
    _recommendation_respects_selected_roles,
    _role_preference_penalty,
    _role_priority_level,
    _roster_real_flex_value,
    _select_diverse_recommendations,
    generate_team_options,
    role_option_limit,
)
from league_shared_data import sync_shared_league_data
from league_pro_data import refresh_recent_pro_data

log = logging.getLogger(__name__)

DUBLIN_TZ = ZoneInfo("Europe/Dublin")
LEAGUE_REMINDER_USERS = (
    400745307467415553,  # The Cheeky Chuckler
    287960676444405761,  # The Silent King
    1051188024668602440, # Pimpoion mcgribz
    680046777872678942,  # Great Tide
    548212317515677697,  # JustKayla
    332490596004659201,  # Jackjak
    265219035019477002,  # Ste's Third
    286592835850076162,  # Weekendstev
)

ROLE_ICONS = {
    "Top": "🛡️",
    "Jungle": "🌲",
    "Mid": "✨",
    "ADC": "🏹",
    "Support": "💚",
}


# Signup champion menus are deliberately broader than the old hand-maintained list.
# Meraki supplies stable role positions; current LoLalytics data catches newer champs and
# genuine role shifts that have enough live sample to be worth offering to players.


ROLE_CHAMPIONS = _build_role_champions()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _role_priority_prompt(signup: dict | None) -> str:
    priorities = _normalised_role_priorities(signup or {}) if signup else {}
    summary = []
    for role in ROLES:
        level = priorities.get(role)
        if level:
            summary.append(f"{ROLE_ICONS[role]} **{role}: {level}**")
    current = " • ".join(summary) if summary else "No roles selected yet."
    return (
        "**Set a 1–3 priority for every role you can genuinely play.**\n"
        "**1 = Preferred** • **2 = Comfortable** • **3 = Flex if the team/comp needs it** • "
        "**Not playing = never assign me here**\n\n"
        "Priority 3 is still a real playable role: the drafter may use it when it meaningfully "
        "improves the team, but it will prefer 1/2 where the drafts are otherwise close.\n\n"
        f"**Current:** {current}"
    )


class RolePrioritySelect(discord.ui.Select):
    def __init__(self, cog: "LeagueFlexCog", role: str, signup: dict | None, row: int):
        priorities = _normalised_role_priorities(signup or {}) if signup else {}
        current = priorities.get(role)
        options = [
            discord.SelectOption(
                label="Not playing this role", value="0", emoji="❌", default=current is None
            ),
            discord.SelectOption(
                label="1 — Preferred", value="1", emoji="🟢", default=current == 1
            ),
            discord.SelectOption(
                label="2 — Comfortable", value="2", emoji="🟡", default=current == 2
            ),
            discord.SelectOption(
                label="3 — Flex if needed", value="3", emoji="🟠", default=current == 3
            ),
        ]
        super().__init__(
            placeholder=f"{role} priority",
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )
        self.cog = cog
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer()
        raw = int(self.values[0])
        level = raw if raw in {1, 2, 3} else None
        ok, message = await self.cog.save_role_priority_level(
            interaction.guild.id, interaction.user, self.role, level
        )
        signup = await self.cog.get_signup(interaction.guild.id, interaction.user.id)
        await self.cog.refresh_event_message(interaction.guild.id)
        content = _role_priority_prompt(signup)
        if not ok:
            content += f"\n\n⚠️ {message}"
        await interaction.edit_original_response(
            content=content,
            view=RolePriorityView(self.cog, signup),
        )


class RolePriorityView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog", signup: dict | None):
        super().__init__(timeout=300)
        for row, role in enumerate(ROLES):
            self.add_item(RolePrioritySelect(cog, role, signup, row))


class ChampionRoleSelect(discord.ui.Select):
    def __init__(self, cog: "LeagueFlexCog", roles: list[str]):
        options = [
            discord.SelectOption(label=role, value=role, emoji=ROLE_ICONS[role])
            for role in roles
        ]
        super().__init__(
            placeholder="Which role's champion pool do you want to edit?",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        role = self.values[0]
        signup = await self.cog.get_signup(interaction.guild.id, interaction.user.id)
        existing = (signup or {}).get("champions", {}).get(role, [])
        await interaction.response.send_message(
            f"**{ROLE_ICONS[role]} {role} champions**\n"
            "Select as many as you genuinely want to play. Each menu is one alphabetical page.",
            view=ChampionPageView(self.cog, role, existing),
            ephemeral=True,
        )


class ChampionRolePickerView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog", roles: list[str]):
        super().__init__(timeout=300)
        self.add_item(ChampionRoleSelect(cog, roles))


class ChampionChunkSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "LeagueFlexCog",
        role: str,
        chunk: tuple[str, ...],
        existing: list[str],
        page: int,
        total_pages: int,
    ):
        options = [
            discord.SelectOption(label=name, value=name, default=name in existing)
            for name in chunk
        ]
        super().__init__(
            placeholder=f"{role} champs — page {page}/{total_pages}",
            min_values=0,
            max_values=len(options),
            options=options,
        )
        self.cog = cog
        self.role = role
        self.chunk = set(chunk)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog.save_champion_chunk(
            interaction.guild.id,
            interaction.user.id,
            self.role,
            self.chunk,
            set(self.values),
        )
        signup = await self.cog.get_signup(interaction.guild.id, interaction.user.id)
        total = sum(len(picks) for picks in (signup or {}).get("champions", {}).values())
        await self.cog.refresh_event_message(interaction.guild.id)
        await interaction.followup.send(
            f"✅ **{self.role}** champion picks updated. **{total} total champion picks** saved.",
            ephemeral=True,
        )


class ChampionPageView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog", role: str, existing: list[str]):
        super().__init__(timeout=300)
        champs = ROLE_CHAMPIONS[role]
        chunks = [champs[i:i + 25] for i in range(0, len(champs), 25)]
        for index, chunk in enumerate(chunks, start=1):
            self.add_item(
                ChampionChunkSelect(
                    cog,
                    role,
                    chunk,
                    existing,
                    index,
                    len(chunks),
                )
            )


class LeaveConfirmView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog"):
        super().__init__(timeout=60)
        self.cog = cog

    @discord.ui.button(label="Yes, remove me", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        await self.cog.delete_signup(interaction.guild.id, interaction.user.id)
        await self.cog.refresh_event_message(interaction.guild.id)
        await interaction.response.edit_message(content="You have been removed from the event.", view=None)


class LeagueEventView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Sign Up / Edit Roles",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="intent:league:flex:roles",
    )
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        event = await self.cog.get_event(interaction.guild.id)
        if not event or event["status"] != "open":
            await interaction.response.send_message("Signups are currently closed.", ephemeral=True)
            return
        signup = await self.cog.get_signup(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(
            _role_priority_prompt(signup),
            view=RolePriorityView(self.cog, signup),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Champion Picks",
        emoji="🎯",
        style=discord.ButtonStyle.primary,
        custom_id="intent:league:flex:champions",
    )
    async def champions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        signup = await self.cog.get_signup(interaction.guild.id, interaction.user.id)
        if not signup or not signup["roles"]:
            await interaction.response.send_message("Pick your roles first.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose a role, then tick the champions you actually want to play.",
            view=ChampionRolePickerView(self.cog, signup["roles"]),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Team Options",
        emoji="🧩",
        style=discord.ButtonStyle.secondary,
        custom_id="intent:league:flex:teams",
    )
    async def teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        await self.cog.send_team_options(interaction)

    @discord.ui.button(
        label="Draft Comps",
        emoji="🧠",
        style=discord.ButtonStyle.success,
        custom_id="intent:league:flex:draft_comps",
    )
    async def draft_comps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        await self.cog.send_draft_comps(interaction)

    @discord.ui.button(
        label="Draft Book",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="intent:league:flex:draft_book",
    )
    async def draft_book(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        await self.cog.send_draft_book(interaction)

    @discord.ui.button(
        label="Leave Event",
        emoji="💀",
        style=discord.ButtonStyle.danger,
        custom_id="intent:league:flex:leave",
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Remove your signup entirely?",
            view=LeaveConfirmView(self.cog),
            ephemeral=True,
        )


_CHAMPION_ICON_URLS: dict[str, str] | None = None
_CHAMPION_ICON_BYTES: dict[str, bytes] = {}
_CHAMPION_ICON_MANIFEST: dict[str, str] | None = None


def _champion_icon_manifest() -> dict[str, str]:
    """Production-safe portrait manifest committed with the bot."""
    global _CHAMPION_ICON_MANIFEST
    if _CHAMPION_ICON_MANIFEST is not None:
        return _CHAMPION_ICON_MANIFEST
    path = Path(__file__).resolve().parents[1] / "assets" / "league_champions" / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _CHAMPION_ICON_MANIFEST = {str(k): str(v) for k, v in raw.items()}
    except Exception:
        log.exception("Could not load bundled champion portrait manifest")
        _CHAMPION_ICON_MANIFEST = {}
    return _CHAMPION_ICON_MANIFEST


def _champion_icon_urls() -> dict[str, str]:
    """Development/network fallback for champions newer than the bundled assets."""
    global _CHAMPION_ICON_URLS
    if _CHAMPION_ICON_URLS is not None:
        return _CHAMPION_ICON_URLS
    mapping: dict[str, str] = {}
    path = Path(__file__).resolve().parents[1] / "data" / "research" / "meraki_champions.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for champion in data.values():
            name = str(champion.get("name") or "")
            url = str(champion.get("icon") or "")
            if name and url:
                mapping[name] = url.replace("http://", "https://")
    except Exception:
        # This research file is intentionally not required in production.
        pass
    _CHAMPION_ICON_URLS = mapping
    return mapping


async def _fetch_champion_icons(champions: set[str]) -> dict[str, bytes]:
    """Load committed portraits first; only use the network as a fallback."""
    wanted = [champ for champ in champions if champ not in _CHAMPION_ICON_BYTES]
    manifest = _champion_icon_manifest()
    asset_dir = Path(__file__).resolve().parents[1] / "assets" / "league_champions"

    for champion in list(wanted):
        filename = manifest.get(champion)
        if not filename:
            continue
        try:
            data = (asset_dir / filename).read_bytes()
            if len(data) > 1000:
                _CHAMPION_ICON_BYTES[champion] = data
        except OSError:
            log.warning("Bundled champion portrait missing for %s", champion)

    remaining = [champ for champ in wanted if champ not in _CHAMPION_ICON_BYTES]
    if remaining:
        urls = _champion_icon_urls()
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async def fetch(champion: str):
                url = urls.get(champion)
                if not url:
                    return
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.read()
                            if len(data) > 1000:
                                _CHAMPION_ICON_BYTES[champion] = data
                except Exception:
                    log.debug("Champion icon fetch failed for %s", champion, exc_info=True)
            await asyncio.gather(*(fetch(champion) for champion in remaining))

    missing = sorted(champ for champ in champions if champ not in _CHAMPION_ICON_BYTES)
    if missing:
        log.warning("No champion portrait available for: %s", ", ".join(missing))
    return {champion: _CHAMPION_ICON_BYTES[champion] for champion in champions if champion in _CHAMPION_ICON_BYTES}


def _draft_font(size: int, bold: bool = False):
    candidates = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    )
    for font_name in candidates:
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _paste_champion_icon(canvas: Image.Image, icons: dict[str, bytes], champion: str, xy: tuple[int, int], size: int):
    data = icons.get(champion)
    if data:
        try:
            icon = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            canvas.alpha_composite(icon, xy)
            return
        except Exception:
            pass
    draw = ImageDraw.Draw(canvas)
    x, y = xy
    draw.rounded_rectangle((x, y, x + size, y + size), radius=8, fill=(55, 65, 81, 255))
    draw.text((x + size // 2, y + size // 2), champion[:2].upper(), font=_draft_font(max(12, size // 3), True), anchor="mm", fill="white")


def _draw_draft_card(
    rec: dict,
    index: int,
    blue_order: list[tuple[str, str]],
    blue_bans: list[dict],
    red_order: list[tuple[str, str]],
    red_bans: list[dict],
    icons: dict[str, bytes],
    blue_conditional: list[dict] | None = None,
    red_conditional: list[dict] | None = None,
) -> bytes:
    width, height = 1200, (1095 if rec.get("mid_jungle_packages") else 965)
    image = Image.new("RGBA", (width, height), (15, 18, 24, 255))
    draw = ImageDraw.Draw(image)
    blue = (58, 130, 246, 255)
    red = (239, 68, 68, 255)
    neutral = (226, 190, 92, 255)
    panel = (29, 34, 44, 255)
    muted = (165, 174, 190, 255)
    white = (240, 243, 248, 255)

    title = f"DRAFT {index}  •  {str(rec.get('team_plan', 'balanced')).upper()}"
    draw.text((30, 18), title, font=_draft_font(28, True), fill=white)
    draw.text((30, 51), "B1  →  R1/R2  →  B2/B3  →  R3/R4  →  B4/B5  →  R5", font=_draft_font(12, True), fill=muted)
    draw.rounded_rectangle((30, 70, 1170, 76), radius=3, fill=neutral)

    # Five role slots. Only show alternatives where the engine says the same job/package survives.
    col_w = 224
    for i, role in enumerate(ROLES):
        x = 30 + i * 228
        y = 96
        draw.rounded_rectangle((x, y, x + col_w - 8, y + 220), radius=14, fill=panel)
        player = str(rec["players"][role]["display_name"])
        draw.text((x + 12, y + 12), role.upper(), font=_draft_font(20, True), fill=neutral)
        draw.text((x + 12, y + 40), player[:20], font=_draft_font(15), fill=muted)
        player_id = rec["players"][role].get("user_id")
        option_count = role_option_limit(role, player_id)
        owned_options = list(rec.get("role_alternatives", {}).get(role, []))[:option_count - 1]
        # Ste's extra slots are only for scored, submitted alternatives.
        # Do not fill them with unprepared historical "learn" suggestions.
        learn_all = list(rec.get("learn_suggestions", {}).get(role, []))
        learn_allowance = 0 if role == "Mid" and int(player_id or 0) == 286592835850076162 else max(0, option_count - 1 - len(owned_options))
        learn_options = learn_all[:learn_allowance]
        options = [rec["champions"][role]] + owned_options + learn_options
        primary_is_history = rec.get("pick_sources", rec.get("pool_sources", {})).get(role) == "history"
        if len(options) == 1:
            _paste_champion_icon(image, icons, options[0], (x + 60, y + 72), 92)
            draw.text((x + col_w // 2 - 4, y + 174), options[0], font=_draft_font(18, True), anchor="ma", fill=white)
            if primary_is_history:
                draw.text((x + col_w // 2 - 4, y + 200), "OP.GG suggestion", font=_draft_font(11, True), anchor="ma", fill=(96, 165, 250, 255))
        else:
            compact = len(options) > 3
            icon_size = 36 if compact else 58
            gap = 6 if compact else 8
            total = len(options) * icon_size + (len(options) - 1) * gap
            start = x + (col_w - 8 - total) // 2
            for j, champion in enumerate(options):
                ix = start + j * (icon_size + gap)
                _paste_champion_icon(image, icons, champion, (ix, y + 87), icon_size)
            if compact:
                draw.text((x + col_w // 2 - 4, y + 148), " / ".join(options[:3]), font=_draft_font(11, True), anchor="ma", fill=white)
                draw.text((x + col_w // 2 - 4, y + 166), " / ".join(options[3:]), font=_draft_font(11, True), anchor="ma", fill=white)
            else:
                draw.text((x + col_w // 2 - 4, y + 157), " / ".join(options), font=_draft_font(15, True), anchor="ma", fill=white)
            notes = []
            if owned_options:
                notes.append("same-plan owned")
            if learn_options:
                notes.append("LEARN: " + "/".join(learn_options))
            if primary_is_history:
                notes.append("OP.GG primary")
            option_note = " • ".join(notes) or "same-plan option"
            draw.text((x + col_w // 2 - 4, y + 186), option_note[:34], font=_draft_font(11), anchor="ma", fill=muted)

    def draw_side_plan(label: str, side_letter: str, accent, order, bans, conditional, top_y: int):
        draw.text((30, top_y), f"{label} SIDE", font=_draft_font(21, True), fill=accent)
        draw.text((165, top_y + 2), "PICK ORDER", font=_draft_font(17, True), fill=white)
        order_y = top_y + 31
        for i, (role, champion) in enumerate(order, start=1):
            x = 30 + (i - 1) * 228
            draw.rounded_rectangle((x, order_y, x + 216, order_y + 78), radius=12, fill=panel)
            _paste_champion_icon(image, icons, champion, (x + 10, order_y + 11), 56)
            draw.text((x + 78, order_y + 8), f"{side_letter}{i}", font=_draft_font(21, True), fill=accent)
            draw.text((x + 78, order_y + 34), champion, font=_draft_font(16, True), fill=white)
            draw.text((x + 78, order_y + 55), role, font=_draft_font(12), fill=muted)

        ban_title_y = order_y + 91
        draw.text((30, ban_title_y), "PRIMARY-PICK BANS", font=_draft_font(17, True), fill=white)
        bans_y = ban_title_y + 27
        annotations = _conditional_ban_annotations(bans[:5], conditional or [])
        if bans:
            for i, ban in enumerate(bans[:5]):
                x = 30 + i * 228
                draw.rounded_rectangle((x, bans_y, x + 216, bans_y + 70), radius=12, fill=panel)
                champ = ban["champion"]
                _paste_champion_icon(image, icons, champ, (x + 8, bans_y + 11), 48)
                reason = (ban.get("reasons") or [{}])[0]
                protect = str(reason.get("our_champion") or "")
                role = str(reason.get("role") or "")
                pick_no = reason.get("pick_number") or "?"
                swaps = annotations.get(champ, [])[:2]
                alt_names = [row["ban"] for row in swaps]
                name_line = champ if not alt_names else champ + " / " + "/".join(alt_names)
                draw.text((x + 66, bans_y + 7), name_line[:22], font=_draft_font(14, True), fill=white)
                if swaps:
                    swap_text = " • ".join(
                        f"{row['ban']} if {row['condition']}" for row in swaps
                    )
                    draw.text((x + 66, bans_y + 31), swap_text[:28], font=_draft_font(10, True), fill=accent)
                    draw.text((x + 66, bans_y + 49), f"primary protects {protect[:9]} {side_letter}{pick_no}", font=_draft_font(9), fill=muted)
                else:
                    draw.text((x + 66, bans_y + 34), f"protect {protect[:10]} • {role} {side_letter}{pick_no}", font=_draft_font(11), fill=muted)
        else:
            draw.text((30, bans_y + 12), "No high-confidence direct counter ban found", font=_draft_font(15), fill=muted)

    draw_side_plan("BLUE", "B", blue, blue_order, blue_bans, blue_conditional, 330)
    draw_side_plan("RED", "R", red, red_order, red_bans, red_conditional, 575)

    # Conditional packages are displayed as a LINKED ADC+Support choice,
    # never as two independent champions that could be recombined at random.
    packages = list(rec.get("bot_lane_packages") or [])[:3]
    if packages:
        draw.text((30, 812), "LINKED BOT LANE ALTERNATIVES  ·  SWAP BOTH PICKS TOGETHER", font=_draft_font(16, True), fill=neutral)
        for i, variant in enumerate(packages):
            x = 30 + i * 380
            draw.rounded_rectangle((x, 837, x + 370, 917), radius=12, fill=panel)
            adc, support = variant["adc"], variant["support"]
            _paste_champion_icon(image, icons, adc, (x + 8, 849), 46)
            _paste_champion_icon(image, icons, support, (x + 58, 849), 46)
            label = f"{adc} + {support}"
            draw.text((x + 112, 847), label[:28], font=_draft_font(16, True), fill=white)
            plan = str(variant.get("team_plan") or "")
            suffix = "  [PLAN CHANGE]" if variant.get("plan_change") else ""
            draw.text((x + 112, 877), (plan + suffix)[:38], font=_draft_font(11), fill=muted)

    mj_pairs = list(rec.get("mid_jungle_packages") or [])[:2]
    if mj_pairs:
        draw.text((30, 932), "LINKED MID + JUNGLE ALTERNATIVES - CHANGE BOTH PICKS", font=_draft_font(16, True), fill=neutral)
        for i, variant in enumerate(mj_pairs):
            x = 30 + i * 570
            draw.rounded_rectangle((x, 954, x + 555, 1035), radius=12, fill=panel)
            jungle, mid = variant["jungle"], variant["mid"]
            _paste_champion_icon(image, icons, jungle, (x + 8, 969), 46)
            _paste_champion_icon(image, icons, mid, (x + 58, 969), 46)
            draw.text((x + 112, 964), (jungle + " + " + mid)[:34], font=_draft_font(16, True), fill=white)
            note = str(variant.get("team_plan") or "")
            if variant.get("plan_change"):
                note += " [PLAN CHANGE]"
            draw.text((x + 112, 997), note[:46], font=_draft_font(11), fill=muted)

    carry_options = list(rec.get("carry_options") or [])
    if carry_options:
        leverage = " / ".join(
            f"{str(row.get('display_name', row.get('role', '')) )[:10]}-{row.get('champion', '')}"
            for row in carry_options[:2]
        )
        footer = f"Carry leverage: {leverage}  |  Team-plan fit {rec.get('team_plan_score', 5.0):.1f}/10"
    else:
        carry = rec.get("carry_plan") or {}
        footer = f"Win condition: {carry.get('champion', 'shared')} ({carry.get('role', 'team')})  |  Team-plan fit {rec.get('team_plan_score', 5.0):.1f}/10"
    footer_y = 1065 if mj_pairs else (940 if packages else 868)
    draw.text((30, footer_y), footer[:120], font=_draft_font(16), fill=muted)

    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


async def _build_draft_visuals(recommendations: list[dict], unavailable: set[str]) -> tuple[list[discord.Embed], list[discord.File]]:
    meta = load_league_comp_meta()
    presentation = []
    all_champions: set[str] = set()
    for index, rec in enumerate(recommendations, start=1):
        blue_order = _pro_pick_order(rec, meta, "blue")
        red_order = _pro_pick_order(rec, meta, "red")
        blue_bans = counter_ban_recommendations(rec["champions"], blue_order, "blue", unavailable=unavailable, limit=5)
        red_bans = counter_ban_recommendations(rec["champions"], red_order, "red", unavailable=unavailable, limit=5)
        blue_conditional = _conditional_ban_branches(rec, meta, "blue", unavailable, blue_bans)
        red_conditional = _conditional_ban_branches(rec, meta, "red", unavailable, red_bans)
        presentation.append((
            index, rec, blue_order, blue_bans, red_order, red_bans,
            blue_conditional, red_conditional,
        ))
        all_champions.update(rec["champions"].values())
        for options in rec.get("role_alternatives", {}).values():
            all_champions.update(options)
        for pair in rec.get("bot_lane_packages", []):
            all_champions.update((pair["adc"], pair["support"]))
        for pair in rec.get("mid_jungle_packages", []):
            all_champions.update((pair["jungle"], pair["mid"]))
        for options in rec.get("learn_suggestions", {}).values():
            all_champions.update(options)
        all_champions.update(ban["champion"] for ban in blue_bans)
        all_champions.update(ban["champion"] for ban in red_bans)
        for branch in blue_conditional + red_conditional:
            all_champions.update(branch.get("bans", []))
    icons = await _fetch_champion_icons(all_champions)
    embeds: list[discord.Embed] = []
    files: list[discord.File] = []
    for (
        index, rec, blue_order, blue_bans, red_order, red_bans,
        blue_conditional, red_conditional,
    ) in presentation:
        png = await asyncio.to_thread(
            _draw_draft_card,
            rec,
            index,
            blue_order,
            blue_bans,
            red_order,
            red_bans,
            icons,
            blue_conditional,
            red_conditional,
        )
        filename = f"league_draft_{index}.png"
        file = discord.File(io.BytesIO(png), filename=filename)
        embed = discord.Embed(
            title=f"Draft {index} | {rec.get('team_plan', 'balanced')}",
            description="Primary bans and pick-conditional replacement bans are shown together on the card.",
            colour=discord.Colour.gold(),
        )
        carry_options = list(rec.get("carry_options") or [])
        if carry_options:
            carry_text = " | ".join(
                f"{row.get('display_name', row.get('role'))}: {row.get('champion')}"
                for row in carry_options[:3]
            )
            embed.add_field(name="Carry allocation", value=carry_text[:1024], inline=False)
        embed.set_image(url=f"attachment://{filename}")
        files.append(file)
        embeds.append(embed)
    return embeds, files


class UnavailableChampionsModal(discord.ui.Modal, title="Banned / taken champions"):
    champions = discord.ui.TextInput(
        label="Champions unavailable",
        placeholder="Morgana, Jarvan IV, Ahri",
        required=False,
        max_length=400,
    )

    def __init__(self, cog: "LeagueFlexCog", user_ids: set[int], current: set[str] | None = None, locked_assignment: dict | None = None):
        super().__init__()
        self.cog = cog
        self.user_ids = user_ids
        self.locked_assignment = locked_assignment
        if current:
            self.champions.default = ", ".join(sorted(current))

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.champions.value or "")
        names = {part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()}
        await self.cog.send_draft_comps(
            interaction,
            selected_user_ids=self.user_ids,
            unavailable_champions=names,
            locked_assignment=self.locked_assignment,
        )


class DraftChoiceSelect(discord.ui.Select):
    def __init__(self, parent: "DraftControlsView", recommendations: list[dict]):
        self.parent_view = parent
        options = [discord.SelectOption(label=f"Lock Draft {i}", value=str(i - 1)) for i in range(1, len(recommendations) + 1)]
        super().__init__(placeholder="Lock the draft you're playing…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        rec = self.parent_view.recommendations[index]
        self.parent_view.locked_assignment = {
            role: {
                "user_id": rec["players"][role]["user_id"],
                "display_name": rec["players"][role]["display_name"],
            }
            for role in ROLES
        }
        await interaction.response.send_message(
            f"🔒 **Draft {index + 1} roles locked.** Future bans/enemy picks will change champions only — not who is playing each role.",
            ephemeral=True,
        )


class DraftControlsView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog", user_ids: set[int], unavailable: set[str] | None = None, recommendations: list[dict] | None = None, locked_assignment: dict | None = None):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_ids = user_ids
        self.unavailable = unavailable or set()
        self.recommendations = recommendations or []
        self.locked_assignment = locked_assignment
        if self.recommendations and self.locked_assignment is None:
            self.add_item(DraftChoiceSelect(self, self.recommendations))

    @discord.ui.button(label="Update bans / enemy picks", emoji="🚫", style=discord.ButtonStyle.danger)
    async def unavailable_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.locked_assignment is None:
            await interaction.response.send_message(
                "Pick **one of the drafts to lock** first so I know which role assignment you're actually playing. Then bans/picks will never reshuffle roles.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            UnavailableChampionsModal(self.cog, self.user_ids, self.unavailable, self.locked_assignment)
        )


class CurrentGamePlayerSelect(discord.ui.UserSelect):
    def __init__(self, cog: "LeagueFlexCog"):
        super().__init__(
            placeholder="Pick the 5 people playing this game…",
            min_values=5,
            max_values=5,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        selected_ids = {user.id for user in self.values}
        await self.cog.send_draft_comps(interaction, selected_user_ids=selected_ids)


class CurrentGamePlayerView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog"):
        super().__init__(timeout=300)
        self.add_item(CurrentGamePlayerSelect(cog))


class LeagueReminderView(discord.ui.View):
    def __init__(self, cog: "LeagueFlexCog", event_url: str | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        if event_url:
            self.add_item(discord.ui.Button(label="Go to Event", emoji="🎮", url=event_url))

    @discord.ui.button(
        label="Don't Notify Me",
        emoji="🔕",
        style=discord.ButtonStyle.secondary,
        custom_id="intent:league:flex:mute_reminder",
    )
    async def mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        await self.cog.set_reminder_optout(interaction.guild.id, interaction.user.id, True)
        await interaction.response.send_message(
            "🔕 Fair enough. You won't be pinged by future Ranked Flex reminders.",
            ephemeral=True,
        )


class LeagueFlexCog(commands.Cog):
    league = app_commands.Group(
        name="league",
        description="Ranked Flex to Challenger event tools.",
    )

    # Reuse the old ARAMMERS role so existing permissions/ordering stay intact.
    FLEXERS_ROLE_ID = 1471203423587401902
    FLEXERS_ROLE_NAME = "💪 FLEXERS"
    DRAFT_CHANNEL_NAME = "league-drafts"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(LeagueEventView(self))
        self._legacy_reminder_cleanup = asyncio.create_task(self.remove_existing_reminder())
        self._pro_meta_refresh_task = asyncio.create_task(self.refresh_pro_meta_forever())
        self._player_data_refresh_task = asyncio.create_task(self.refresh_player_data_forever())
        # Only the explicitly authorised private Render deployment opts into
        # permitted historical archives; CI/public source never stores the
        # provider's raw aggregate exports. Never download in champ select.
        self._pocketpick_sync_task = None
        if os.getenv("RENDER_SERVICE_ID") or os.getenv("DRAFTCOACH_POCKETPICK_SYNC") == "1":
            self._pocketpick_sync_task = asyncio.create_task(self._sync_private_history_once())

    async def _sync_private_history_once(self):
        try:
            from tools.sync_pocketpick_data import sync_archive
            payload = await asyncio.to_thread(sync_archive)
            log.info("PocketPick private historical cache: %s aggregate matchups available",
                     payload["manifest"]["matchups"])
        except Exception:
            # Network failures or site changes must not block bot startup.
            log.exception("Permissioned PocketPick cache unavailable; using existing draft evidence")

    def cog_unload(self):
        if not self._legacy_reminder_cleanup.done():
            self._legacy_reminder_cleanup.cancel()
        if not self._pro_meta_refresh_task.done():
            self._pro_meta_refresh_task.cancel()
        if not self._player_data_refresh_task.done():
            self._player_data_refresh_task.cancel()
        if getattr(self, "_pocketpick_sync_task", None) and not self._pocketpick_sync_task.done():
            self._pocketpick_sync_task.cancel()

    async def refresh_pro_meta_forever(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                data = await refresh_recent_pro_data()
                log.info(
                    "League live pro data refreshed: %s games, latest patch %s",
                    data.get("game_count", 0),
                    data.get("latest_patch", "unknown"),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("League live pro data refresh failed; retaining last good snapshot")
            await asyncio.sleep(6 * 60 * 60)

    async def refresh_player_data_forever(self):
        # Discord and the desktop app consume exactly the SAME two versioned
        # snapshots on the GitHub main branch. Only the scheduled GitHub job
        # collects linked-account matches; this process downloads validated data
        # without racing the app or maintaining another scraper's private state.
        # A stale/failed source never wipes the last known working snapshot.
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)
        while True:
            try:
                data = await asyncio.to_thread(
                    sync_shared_league_data,
                    LEAGUE_META_PATH.parent,
                )
                changed = set(data["changed"])
                if "league_player_profiles.json" in changed:
                    clear_player_profile_caches()
                if "league_lane_matchups.json" in changed:
                    load_lane_matchups.cache_clear()
                    load_solo_queue_winrates.cache_clear()
                if "league_open_pro_pairs.json" in changed:
                    from league_multi_source import _read_pairs
                    _read_pairs.cache_clear()
                if "league_licensed_soloq.json" in changed:
                    from league_aggregate_sources import _read, _indices
                    _read.cache_clear()
                    _indices.cache_clear()
                    load_lane_matchups.cache_clear()
                    load_solo_queue_winrates.cache_clear()
                log.info(
                    "League shared data checked: %s, updated %s, errors %s",
                    data.get("checked"), data.get("changed"), data.get("errors"),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("League shared data refresh failed; retaining last good snapshot")
            await asyncio.sleep(60 * 60)

    async def get_draft_state(self, guild_id: int):
        db = await database.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM league_flex_draft_state WHERE guild_id=?",
                (guild_id,),
            )
            return await cur.fetchone()
        finally:
            await db.close()

    async def save_draft_state(
        self, guild_id: int, channel_id: int | None, message_id: int | None
    ):
        db = await database.connect()
        try:
            await db.execute(
                """INSERT INTO league_flex_draft_state
                   (guild_id,channel_id,message_id,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                   channel_id=excluded.channel_id,
                   message_id=excluded.message_id,
                   updated_at=excluded.updated_at""",
                (guild_id, channel_id, message_id, now_iso()),
            )
            await db.commit()
        finally:
            await db.close()

    async def _ensure_draft_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        state = await self.get_draft_state(guild.id)
        if state and state["channel_id"]:
            existing = guild.get_channel(int(state["channel_id"]))
            if isinstance(existing, discord.TextChannel):
                return existing

        existing = discord.utils.get(guild.text_channels, name=self.DRAFT_CHANNEL_NAME)
        if isinstance(existing, discord.TextChannel):
            await self.save_draft_state(guild.id, existing.id, state["message_id"] if state else None)
            return existing

        role = guild.get_role(self.FLEXERS_ROLE_ID)
        me = guild.me
        if role is None or me is None:
            log.warning("Cannot create League draft channel: FLEXERS role or bot member is unavailable")
            return None

        event_channel = guild.get_channel(LEAGUE_EVENT_CHANNEL_ID)
        category = event_channel.category if isinstance(event_channel, discord.TextChannel) else None
        def player_overwrite():
            return discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: player_overwrite(),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        # Explicit member access means the private draft room still works even if the
        # bot cannot manage the FLEXERS role itself.
        for signup in await self.list_signups(guild.id):
            member = guild.get_member(int(signup["user_id"]))
            if member is not None:
                overwrites[member] = player_overwrite()

        channel = await guild.create_text_channel(
            self.DRAFT_CHANNEL_NAME,
            category=category,
            overwrites=overwrites,
            topic="Ranked Flex draft boards \u2022 newest /league game board replaces the previous one.",
            reason="Dedicated private League draft channel for FLEXERS",
        )
        await self.save_draft_state(guild.id, channel.id, None)
        log.info("Created private League draft channel #%s (%s)", channel.name, channel.id)
        return channel

    async def _find_legacy_draft_state(
        self, guild: discord.Guild, invoking_channel=None
    ):
        """Find one pre-state draft board so the first new board can clean it up too."""
        channels = []
        if isinstance(invoking_channel, discord.TextChannel):
            channels.append(invoking_channel)
        event_channel = guild.get_channel(LEAGUE_EVENT_CHANNEL_ID)
        if isinstance(event_channel, discord.TextChannel) and event_channel not in channels:
            channels.append(event_channel)
        named = discord.utils.get(guild.text_channels, name=self.DRAFT_CHANNEL_NAME)
        if isinstance(named, discord.TextChannel) and named not in channels:
            channels.append(named)
        bot_user_id = self.bot.user.id if self.bot.user else None
        if bot_user_id is None:
            return None
        for channel in channels:
            try:
                async for message in channel.history(limit=50):
                    if message.author.id != bot_user_id:
                        continue
                    if "Pre-game draft options" not in (message.content or ""):
                        continue
                    return {"channel_id": channel.id, "message_id": message.id}
            except (discord.Forbidden, discord.HTTPException):
                continue
        return None

    async def _delete_previous_draft(
        self, guild: discord.Guild, state, *, keep_message_id: int | None = None
    ):
        if not state or not state["channel_id"] or not state["message_id"]:
            return
        message_id = int(state["message_id"])
        if keep_message_id and message_id == keep_message_id:
            return
        channel = guild.get_channel(int(state["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await guild.fetch_channel(int(state["channel_id"]))
                channel = fetched if isinstance(fetched, discord.TextChannel) else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not delete previous League draft board %s", message_id)

    async def _flexers_role(self, guild: discord.Guild):
        role = guild.get_role(self.FLEXERS_ROLE_ID)
        if role is None:
            log.warning("League FLEXERS role %s is missing", self.FLEXERS_ROLE_ID)
            return None
        if role.name != self.FLEXERS_ROLE_NAME or not role.mentionable:
            role = await role.edit(
                name=self.FLEXERS_ROLE_NAME,
                mentionable=True,
                reason="Keep Ranked Flex signup role current",
            )
        return role

    async def _set_flexers_role(self, guild_id: int, user_id: int, enabled: bool):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False
        role = await self._flexers_role(guild)
        if role is None:
            return False
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False
        has_role = role in member.roles
        if enabled and not has_role:
            await member.add_roles(role, reason="Signed up for Ranked Flex event")
        elif not enabled and has_role:
            await member.remove_roles(role, reason="Removed Ranked Flex event signup")
        return True

    async def sync_flexers_role(self):
        guild = self.bot.get_guild(LEAGUE_GUILD_ID)
        if guild is None:
            return
        role = await self._flexers_role(guild)
        if role is None:
            return
        signup_ids = {signup["user_id"] for signup in await self.list_signups(LEAGUE_GUILD_ID)}
        for user_id in signup_ids:
            await self._set_flexers_role(LEAGUE_GUILD_ID, user_id, True)
        # Keep the role truthful if somebody used the event's remove-signup button.
        for member in list(role.members):
            if member.id not in signup_ids:
                await member.remove_roles(role, reason="No longer signed up for Ranked Flex event")

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.sync_flexers_role()
        except discord.HTTPException:
            log.exception("Could not sync League FLEXERS role")
        try:
            guild = self.bot.get_guild(LEAGUE_GUILD_ID)
            if guild is not None:
                await self._ensure_draft_channel(guild)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not ensure private League draft channel")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or message.guild.id != LEAGUE_GUILD_ID:
            return
        raw = message.content or ""
        role_mention = f"<@&{self.FLEXERS_ROLE_ID}>"
        if role_mention not in raw and "@arammers" not in raw.lower():
            return
        try:
            await message.reply(
                "🤓 You mean **@💪 FLEXERS**.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Could not post ARAMMERS correction in League server")

    async def get_event(self, guild_id: int):
        db = await database.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM league_flex_events WHERE guild_id=?",
                (guild_id,),
            )
            return await cur.fetchone()
        finally:
            await db.close()

    async def get_signup(self, guild_id: int, user_id: int):
        db = await database.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM league_flex_signups WHERE guild_id=? AND discord_user_id=?",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
        finally:
            await db.close()
        if not row:
            return None
        return {
            "user_id": int(row["discord_user_id"]),
            "display_name": row["display_name"],
            "roles": _loads(row["roles_json"], []),
            "role_priority": _loads(row["role_priority_json"], {}),
            "champions": _loads(row["champions_json"], {}),
        }

    async def list_signups(self, guild_id: int) -> list[dict]:
        db = await database.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM league_flex_signups WHERE guild_id=? ORDER BY updated_at",
                (guild_id,),
            )
            rows = await cur.fetchall()
        finally:
            await db.close()
        return [
            {
                "user_id": int(row["discord_user_id"]),
                "display_name": row["display_name"],
                "roles": _loads(row["roles_json"], []),
                "role_priority": _loads(row["role_priority_json"], {}),
                "champions": _loads(row["champions_json"], {}),
            }
            for row in rows
        ]

    async def remove_existing_reminder(self):
        """Delete the last legacy signup reminder, if one is still sitting in Discord."""
        await self.bot.wait_until_ready()
        state = await self.get_reminder_state(LEAGUE_GUILD_ID)
        event = await self.get_event(LEAGUE_GUILD_ID)
        if not state or not state["reminder_message_id"] or not event:
            return

        guild = self.bot.get_guild(LEAGUE_GUILD_ID)
        if guild is None:
            return
        channel = guild.get_channel(int(event["channel_id"]))
        if channel is None:
            try:
                channel = await guild.fetch_channel(int(event["channel_id"]))
            except discord.HTTPException:
                channel = None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                previous = await channel.fetch_message(int(state["reminder_message_id"]))
                await previous.delete()
            except discord.HTTPException:
                pass
        await self.save_reminder_state(
            LEAGUE_GUILD_ID,
            str(state["last_slot"] or "disabled"),
            None,
        )

    async def set_reminder_optout(self, guild_id: int, user_id: int, opted_out: bool):
        db = await database.connect()
        try:
            if opted_out:
                await db.execute(
                    """INSERT INTO league_flex_reminder_optouts
                       (guild_id,discord_user_id,opted_out_at)
                       VALUES (?,?,?)
                       ON CONFLICT(guild_id,discord_user_id) DO UPDATE SET
                       opted_out_at=excluded.opted_out_at""",
                    (guild_id, user_id, now_iso()),
                )
            else:
                await db.execute(
                    "DELETE FROM league_flex_reminder_optouts WHERE guild_id=? AND discord_user_id=?",
                    (guild_id, user_id),
                )
            await db.commit()
        finally:
            await db.close()

    async def list_reminder_optouts(self, guild_id: int) -> set[int]:
        db = await database.connect()
        try:
            cur = await db.execute(
                "SELECT discord_user_id FROM league_flex_reminder_optouts WHERE guild_id=?",
                (guild_id,),
            )
            return {int(row[0]) for row in await cur.fetchall()}
        finally:
            await db.close()

    async def get_reminder_state(self, guild_id: int):
        db = await database.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM league_flex_reminder_state WHERE guild_id=?",
                (guild_id,),
            )
            return await cur.fetchone()
        finally:
            await db.close()

    async def save_reminder_state(
        self,
        guild_id: int,
        slot_key: str,
        reminder_message_id: int | None,
    ):
        db = await database.connect()
        try:
            await db.execute(
                """INSERT INTO league_flex_reminder_state
                   (guild_id,last_slot,reminder_message_id,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                   last_slot=excluded.last_slot,
                   reminder_message_id=excluded.reminder_message_id,
                   updated_at=excluded.updated_at""",
                (guild_id, slot_key, reminder_message_id, now_iso()),
            )
            await db.commit()
        finally:
            await db.close()

    async def post_reminder(self, guild_id: int, slot_key: str, *, force: bool = False):
        event = await self.get_event(guild_id)
        if not event or event["status"] != "open" or not event["message_id"]:
            return None

        state = await self.get_reminder_state(guild_id)
        if not force and state and state["last_slot"] == slot_key:
            return None

        signed_up = {signup["user_id"] for signup in await self.list_signups(guild_id)}
        opted_out = await self.list_reminder_optouts(guild_id)
        pending = [
            user_id
            for user_id in LEAGUE_REMINDER_USERS
            if user_id not in signed_up and user_id not in opted_out
        ]

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(int(event["channel_id"]))
        if channel is None:
            channel = await guild.fetch_channel(int(event["channel_id"]))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None

        if state and state["reminder_message_id"]:
            try:
                previous = await channel.fetch_message(int(state["reminder_message_id"]))
                await previous.delete()
            except discord.HTTPException:
                pass

        if not pending:
            await self.save_reminder_state(guild_id, slot_key, None)
            return None

        event_url = (
            f"https://discord.com/channels/{guild_id}/"
            f"{event['channel_id']}/{event['message_id']}"
        )
        content = (
            "🏆 **RANKED FLEX → CHALLENGER REMINDER**\n"
            "You still haven't signed up. Pick your roles and join the climb, "
            "or hit **Don't Notify Me** and we'll stop harassing you.\n"
            "**Improvement is the goal. Allegedly.**"
        )
        message = await channel.send(
            content,
            view=LeagueReminderView(self, event_url),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.save_reminder_state(guild_id, slot_key, message.id)
        return message

    @staticmethod
    def _reminder_slot_key(now: datetime) -> str | None:
        if now.hour < 7:
            return None
        slot_hour = 7 if now.hour < 19 else 19
        return f"{now.date().isoformat()}:{slot_hour:02d}"

    @tasks.loop(time=(
        time(hour=7, minute=0, tzinfo=DUBLIN_TZ),
        time(hour=19, minute=0, tzinfo=DUBLIN_TZ),
    ))
    async def reminder_loop(self):
        now = datetime.now(DUBLIN_TZ)
        slot_key = self._reminder_slot_key(now)
        if slot_key is None:
            return
        try:
            await self.post_reminder(LEAGUE_GUILD_ID, slot_key)
        except Exception:
            log.exception("Could not post League Ranked Flex reminder")

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()
        # Let the dedicated bot adopt the migrated signup board before reminders use
        # its saved URL. This is effectively free after the first startup.
        await asyncio.sleep(7)
        try:
            await self.refresh_event_message(LEAGUE_GUILD_ID)
            slot_key = self._reminder_slot_key(datetime.now(DUBLIN_TZ))
            if slot_key is not None:
                await self.post_reminder(LEAGUE_GUILD_ID, slot_key)
        except Exception:
            log.exception("Could not initialise League event reminders")

    async def save_roles(self, guild_id: int, member: discord.Member, roles: list[str]):
        """Compatibility helper for any older callers; new UI saves one priority at a time."""
        signup = await self.get_signup(guild_id, member.id)
        champions = (signup or {}).get("champions", {})
        champions = {role: picks for role, picks in champions.items() if role in roles}
        existing = _normalised_role_priorities(signup or {}) if signup else {}
        priorities = {role: existing.get(role, 2) for role in roles}
        db = await database.connect()
        try:
            await db.execute(
                """INSERT INTO league_flex_signups
                   (guild_id,discord_user_id,display_name,roles_json,role_priority_json,champions_json,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(guild_id,discord_user_id) DO UPDATE SET
                   display_name=excluded.display_name,roles_json=excluded.roles_json,
                   role_priority_json=excluded.role_priority_json,
                   champions_json=excluded.champions_json,updated_at=excluded.updated_at""",
                (
                    guild_id,
                    member.id,
                    member.display_name[:80],
                    json.dumps(roles),
                    json.dumps(priorities),
                    json.dumps(champions),
                    now_iso(),
                ),
            )
            await db.commit()
        finally:
            await db.close()
        try:
            await self._set_flexers_role(guild_id, member.id, True)
        except discord.HTTPException:
            log.warning("Could not update FLEXERS role for %s; signup itself was saved", member.id)

    async def save_role_priority_level(
        self,
        guild_id: int,
        member: discord.Member,
        role: str,
        level: int | None,
    ):
        if role not in ROLES or (level is not None and level not in {1, 2, 3}):
            return False, "Invalid role priority."

        signup = await self.get_signup(guild_id, member.id)
        if signup is None and level is None:
            return False, "Choose at least one playable role first."
        roles = list((signup or {}).get("roles", []))
        priorities = _normalised_role_priorities(signup or {}) if signup else {}
        champions = dict((signup or {}).get("champions", {}))

        if level is None:
            if role in roles and len(roles) == 1:
                return False, "Keep at least one playable role selected, or use **Leave Event**."
            roles = [r for r in roles if r != role]
            priorities.pop(role, None)
            champions.pop(role, None)
        else:
            if role not in roles:
                roles.append(role)
            roles = [r for r in ROLES if r in roles]
            priorities[role] = level

        priorities = {r: priorities.get(r, 2) for r in roles}
        db = await database.connect()
        try:
            await db.execute(
                """INSERT INTO league_flex_signups
                   (guild_id,discord_user_id,display_name,roles_json,role_priority_json,champions_json,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(guild_id,discord_user_id) DO UPDATE SET
                   display_name=excluded.display_name,roles_json=excluded.roles_json,
                   role_priority_json=excluded.role_priority_json,
                   champions_json=excluded.champions_json,updated_at=excluded.updated_at""",
                (
                    guild_id,
                    member.id,
                    member.display_name[:80],
                    json.dumps(roles),
                    json.dumps(priorities),
                    json.dumps(champions),
                    now_iso(),
                ),
            )
            await db.commit()
        finally:
            await db.close()

        try:
            await self._set_flexers_role(guild_id, member.id, True)
        except discord.HTTPException:
            log.warning("Could not update FLEXERS role for %s; signup itself was saved", member.id)
        return True, priorities

    async def save_champion_chunk(
        self,
        guild_id: int,
        user_id: int,
        role: str,
        chunk: set[str],
        selected: set[str],
    ):
        signup = await self.get_signup(guild_id, user_id)
        if not signup or role not in signup["roles"]:
            return
        champions = signup["champions"]
        current = set(champions.get(role, []))
        current.difference_update(chunk)
        current.update(selected)
        champions[role] = sorted(current)
        db = await database.connect()
        try:
            await db.execute(
                """UPDATE league_flex_signups
                   SET champions_json=?,updated_at=?
                   WHERE guild_id=? AND discord_user_id=?""",
                (json.dumps(champions), now_iso(), guild_id, user_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def delete_signup(self, guild_id: int, user_id: int):
        db = await database.connect()
        try:
            await db.execute(
                "DELETE FROM league_flex_signups WHERE guild_id=? AND discord_user_id=?",
                (guild_id, user_id),
            )
            await db.commit()
        finally:
            await db.close()
        await self._set_flexers_role(guild_id, user_id, False)

    async def build_embed(self, guild_id: int, *, image_url: str | None = None):
        event = await self.get_event(guild_id)
        signups = await self.list_signups(guild_id)
        status = "OPEN" if event and event["status"] == "open" else "CLOSED"
        embed = discord.Embed(
            title="🏆 LEAGUE RANKED FLEX → CHALLENGER",
            description=(
                "**Improvement is the goal. Fun with friends. Mental damage optional.**\n\n"
                "Hit **Sign Up / Edit Roles** and give every playable role a priority: "
                "**1 Preferred / 2 Comfortable / 3 Flex if needed**. Leave roles you cannot play "
                "unselected. Then add champion picks for those roles.\n\n"
                f"**Signups: {status}**"
            ),
            colour=discord.Colour.from_rgb(45, 111, 255),
        )
        regular_ids = set(LEAGUE_REMINDER_USERS)
        regular_signups = sum(signup["user_id"] in regular_ids for signup in signups)
        if signups:
            lines = []
            for signup in signups[:20]:
                priorities = _normalised_role_priorities(signup)
                role_bits = [f"{role} {priorities.get(role, 2)}" for role in signup["roles"]]
                roles = " / ".join(role_bits) or "No roles"
                champ_count = sum(len(v) for v in signup["champions"].values())
                champ_note = f" • 🎯 {champ_count} champ picks" if champ_count else ""
                lines.append(f"• <@{signup['user_id']}> — **{roles}**{champ_note}")
            if len(signups) > 20:
                lines.append(f"…and {len(signups) - 20} more")
            embed.add_field(
                name=(
                    f"Degenerates signed up ({len(signups)}) "
                    f"• regulars {regular_signups}/{len(LEAGUE_REMINDER_USERS)}"
                ),
                value="\n".join(lines)[:1024],
                inline=False,
            )
        else:
            embed.add_field(
                name=f"Degenerates signed up (0) • regulars 0/{len(LEAGUE_REMINDER_USERS)}",
                value="Be the first person to make a questionable commitment.",
                inline=False,
            )
        role_counts = {
            role: sum(role in signup["roles"] for signup in signups)
            for role in ROLES
        }
        embed.add_field(
            name="Role coverage",
            value=" • ".join(f"{ROLE_ICONS[r]} {r}: **{role_counts[r]}**" for r in ROLES),
            inline=False,
        )
        embed.set_footer(text="Same idiots. Different ranks. Higher hopes.")
        if image_url:
            embed.set_image(url=image_url)
        return embed

    async def refresh_event_message(self, guild_id: int):
        event = await self.get_event(guild_id)
        if not event or not event["message_id"]:
            return
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(int(event["channel_id"]))
        if channel is None:
            channel = await guild.fetch_channel(int(event["channel_id"]))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(int(event["message_id"]))
        except discord.HTTPException:
            return
        # During the one-time bot migration, the saved board can still belong to the
        # old WoW bot. Discord never permits another bot to edit that message.
        if self.bot.user is not None and message.author.id != self.bot.user.id:
            return
        image_url = message.attachments[0].url if message.attachments else None
        embed = await self.build_embed(guild_id, image_url=image_url)
        await message.edit(embed=embed, view=LeagueEventView(self))

    async def post_role_priority_notice(self, guild: discord.Guild, board_url: str):
        signups = await self.list_signups(guild.id)
        if not signups:
            return None
        try:
            channel = await self._ensure_draft_channel(guild)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not open League draft channel for role-priority notice")
            return None
        if not isinstance(channel, discord.TextChannel):
            return None

        # Current signups get explicit access as a fallback for the bot's known
        # Manage-Roles limitation on the FLEXERS role.
        overwrite = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        )
        mentioned_users = []
        for signup in signups:
            member = guild.get_member(int(signup["user_id"]))
            if member is None:
                try:
                    member = await guild.fetch_member(int(signup["user_id"]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
            if member is not None:
                mentioned_users.append(member)
                try:
                    await channel.set_permissions(
                        member,
                        overwrite=overwrite,
                        reason="Signed-up Ranked Flex player needs draft-room access",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    log.warning("Could not grant League draft-room access to %s", member.id)

        mentions = " ".join(f"<@{signup['user_id']}>" for signup in signups)
        return await channel.send(
            "📣 **ROLE PRIORITIES — PLEASE UPDATE YOUR SIGNUP**\n"
            f"{mentions}\n\n"
            "Use the fresh signup board below and review every role you can play:\n"
            "**1 = Preferred** • **2 = Comfortable** • **3 = Flex if needed** • "
            "leave roles you cannot play unselected.\n\n"
            "Priority **3 is still usable by the drafter** when it materially improves the comp; "
            "it just carries a stronger preference penalty than 1/2.\n"
            f"{board_url}",
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(id=int(signup["user_id"])) for signup in signups],
            ),
        )

    async def send_team_options(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        signups = await self.list_signups(interaction.guild.id)
        options = generate_team_options(signups, limit=5)
        if not options:
            counts = {role: sum(role in s["roles"] for s in signups) for role in ROLES}
            coverage = " • ".join(f"{role}: {counts[role]}" for role in ROLES)
            await interaction.response.send_message(
                "I can't build a legal five-player comp from the current selections yet.\n"
                f"**Coverage:** {coverage}",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="🧩 Legal team options",
            description="Built only from roles people said they are willing to play.",
            colour=discord.Colour.gold(),
        )
        for index, option in enumerate(options, start=1):
            lines = []
            for role in ROLES:
                player = option[role]
                picks = player["champions"].get(role, [])
                champ_text = f" — {', '.join(picks[:4])}" if picks else ""
                lines.append(
                    f"{ROLE_ICONS[role]} **{role}:** <@{player['user_id']}>{champ_text}"
                )
            embed.add_field(
                name=f"Teamcomp {index}",
                value="\n".join(lines),
                inline=False,
            )
        embed.set_footer(text="Champion picks are preferences, not locks.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def send_draft_comps(
        self,
        interaction: discord.Interaction,
        selected_user_ids: set[int] | None = None,
        unavailable_champions: set[str] | None = None,
        locked_assignment: dict | None = None,
    ):
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        signups = await self.list_signups(interaction.guild.id)
        unavailable_champions = unavailable_champions or set()
        if selected_user_ids is not None:
            signups = [s for s in signups if s["user_id"] in selected_user_ids]
            signed_ids = {s["user_id"] for s in signups}
            missing_signup_ids = selected_user_ids - signed_ids
            if missing_signup_ids:
                missing_mentions = ", ".join(f"<@{user_id}>" for user_id in sorted(missing_signup_ids))
                await interaction.followup.send(
                    "These selected players have not completed the League signup/champion form yet: "
                    + missing_mentions,
                    ephemeral=True,
                )
                return
            if len(signups) != 5:
                await interaction.followup.send(
                    "Pick exactly **5** signed-up players for the current game.",
                    ephemeral=True,
                )
                return

        signups = _filter_unavailable(signups, unavailable_champions)
        if locked_assignment:
            by_id = {int(s["user_id"]): s for s in signups}
            locked = {}
            for role in ROLES:
                uid = int(locked_assignment[role]["user_id"])
                if uid not in by_id or role not in by_id[uid].get("roles", []):
                    locked = {}
                    break
                locked[role] = by_id[uid]
            assignments = [locked] if locked else []
        else:
            # Ten visible drafts need enough legal role layouts before scoring.
            # Eight options were saturated by P1 defaults and repeated champion shells.
            assignments = generate_team_options(signups, limit=16)
        if not assignments:
            counts = {role: sum(role in s["roles"] for s in signups) for role in ROLES}
            coverage = " • ".join(f"{role}: {counts[role]}" for role in ROLES)
            await interaction.followup.send(
                "I can't build a legal five-player role assignment yet.\n"
                f"**Coverage:** {coverage}",
                ephemeral=True,
            )
            return

        try:
            candidate_pool, missing = await asyncio.to_thread(
                recommend_comps,
                assignments,
                limit=300,
                pool_limit=10,
                rotation_token=None if locked_assignment else str(interaction.id),
            )
            recommendations = _select_diverse_recommendations(
                candidate_pool,
                limit=10,
                core_limit=4,
                role_mix_slots=0 if locked_assignment else 6,
                rotation_token=None if locked_assignment else str(interaction.id),
            )
            recommendations = _attach_role_alternatives(recommendations, candidate_pool)
            recommendations = attach_learn_suggestions(recommendations)
            source = load_league_comp_meta().get("source", {})
        except Exception:
            log.exception("League composition engine failed")
            await interaction.followup.send(
                "The composition model hit an error. The normal **Team Options** button still works.",
                ephemeral=True,
            )
            return

        if not recommendations:
            details = ""
            if missing:
                details = "\n\nMissing champion pools for: " + ", ".join(missing[:12])
            await interaction.followup.send(
                "I have legal role assignments, but not enough role-specific champion picks "
                "to build a five-champion draft yet." + details,
                ephemeral=True,
            )
            return

        window_start = str(source.get("window_start", ""))[:10]
        window_end = str(source.get("window_end", ""))[:10]
        games = source.get("games", "?")

        # Current-game champ select gets the compact visual board. Keep the analytical
        # text embed as a fallback and for the generic event Draft Comps button.
        if selected_user_ids is not None:
            try:
                visual_embeds, visual_files = await _build_draft_visuals(
                    recommendations, unavailable_champions
                )
                view = DraftControlsView(
                    self, selected_user_ids, unavailable_champions, recommendations, locked_assignment
                )
                old_state = await self.get_draft_state(interaction.guild.id)
                if not old_state or not old_state["message_id"]:
                    old_state = await self._find_legacy_draft_state(
                        interaction.guild, interaction.channel
                    )
                try:
                    draft_channel = await self._ensure_draft_channel(interaction.guild)
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not create/use private League draft channel; using invoking channel")
                    draft_channel = None
                target_channel = draft_channel or interaction.channel
                if target_channel is None or not hasattr(target_channel, "send"):
                    raise RuntimeError("No messageable League draft channel is available")
                new_message = await target_channel.send(
                    content="\U0001f9e0 **Pre-game draft options \u2022 Blue + Red plans ready**",
                    embeds=visual_embeds,
                    files=visual_files,
                    view=view,
                )
                await self.save_draft_state(
                    interaction.guild.id,
                    new_message.channel.id,
                    new_message.id,
                )
                await self._delete_previous_draft(
                    interaction.guild,
                    old_state,
                    keep_message_id=new_message.id,
                )
                await interaction.followup.send(
                    f"\u2705 Draft board updated in <#{new_message.channel.id}>. The previous board was removed.",
                    ephemeral=True,
                )
                return
            except Exception:
                log.exception("League visual draft board failed; falling back to text")

        embed = discord.Embed(
            title="🧠 Recommended pre-game comps",
            description=(
                "Uses each player's **submitted champion pool as the primary-pick boundary**. "
                "OP.GG season + mastery history helps rank comfort and optional suggestions, but does not "
                "silently assign an unsubmitted champion. The score combines comp structure, player fit and current role strength.\n"
                f"Recent pro signal: **{games} LCK/LPL/LEC/LCS games** "
                f"({window_start} → {window_end})."
            ),
            colour=discord.Colour.green(),
        )

        for index, rec in enumerate(recommendations, start=1):
            lines = []
            inferred_used = False
            for role in ROLES:
                player = rec["players"][role]
                champion = rec["champions"][role]
                role_options = [champion] + rec.get("role_alternatives", {}).get(role, [])
                champion_text = " / ".join(role_options)
                inferred = rec.get("pick_sources", rec.get("pool_sources", {})).get(role) == "history"
                inferred_used = inferred_used or inferred
                source_mark = " ~" if inferred else ""
                lines.append(
                    f"{ROLE_ICONS[role]} **{role}:** <@{player['user_id']}> — **{champion_text}**{source_mark}"
                )
            meta = load_league_comp_meta()
            blue_order = _pro_pick_order(rec, meta, "blue")
            red_order = _pro_pick_order(rec, meta, "red")
            blue_pick_text = " → ".join(f"B{i} {champion} ({role})" for i, (role, champion) in enumerate(blue_order, start=1))
            red_pick_text = " → ".join(f"R{i} {champion} ({role})" for i, (role, champion) in enumerate(red_order, start=1))

            def format_bans(side: str, order: list[tuple[str, str]]) -> str:
                side_letter = "B" if side == "blue" else "R"
                bans = counter_ban_recommendations(
                    rec["champions"], order, side, unavailable=unavailable_champions, limit=5
                )
                bits = []
                for ban in bans:
                    reason = ban.get("reasons", [{}])[0]
                    protect = reason.get("our_champion")
                    role_name = reason.get("role")
                    pick_no = reason.get("pick_number")
                    bits.append(f"**{ban['champion']}** (protect {protect} {role_name} {side_letter}{pick_no})")
                return " • ".join(bits) if bits else "no strong direct counter ban found"

            blue_ban_text = format_bans("blue", blue_order)
            red_ban_text = format_bans("red", red_order)
            identity = " / ".join(rec["archetypes"])
            strengths = ", ".join(rec["strengths"]) if rec["strengths"] else "none flagged"
            gaps = ", ".join(rec["gaps"]) if rec["gaps"] else "no major structural gap flagged"
            lines.extend(
                [
                    f"**Identity:** {identity}",
                    f"**Strengths:** {strengths}",
                    f"**Watch:** {gaps}",
                    f"**Damage:** {rec['damage_mix']}",
                    (
                        f"**Bot lane:** {rec['champions']['ADC']} + {rec['champions']['Support']} — "
                        f"{rec.get('bot_lane_score', 5.0):.1f}/10 ({rec.get('bot_lane_grade', 'playable')})"
                        + (
                            f" — {', '.join(rec.get('bot_lane_reasons', [])[:2])}"
                            if rec.get('bot_lane_reasons') else ""
                        )
                    ),
                    (
                        "**Linked mid/jungle alternatives:** "
                        + ", ".join(
                            f"{v['jungle']} + {v['mid']}"
                            + (" (plan change)" if v.get("plan_change") else "")
                            for v in rec.get("mid_jungle_packages", [])[:2]
                        )
                        if rec.get("mid_jungle_packages")
                        else "**Linked mid/jungle alternatives:** none"
                    ),
                    (
                        "**Linked bot alternatives:** "
                        + " • ".join(
                            f"{v['adc']} + {v['support']}"
                            + (" (plan change)" if v.get("plan_change") else "")
                            for v in rec.get("bot_lane_packages", [])[:3]
                        )
                        if rec.get("bot_lane_packages") else "**Linked bot alternatives:** none"
                    ),
                    (
                        f"**Mid–Jungle:** {rec['champions']['Mid']} + {rec['champions']['Jungle']} — "
                        f"{rec.get('mid_jungle_score', 5.0):.1f}/10 ({rec.get('mid_jungle_grade', 'playable')})"
                        + (
                            f" — {', '.join(rec.get('mid_jungle_reasons', [])[:2])}"
                            if rec.get('mid_jungle_reasons') else ""
                        )
                    ),
                    (
                        f"**Team plan:** {rec.get('team_plan', 'balanced')} — "
                        f"{rec.get('team_plan_score', 5.0):.1f}/10 ({rec.get('team_plan_grade', 'playable')})"
                        + (
                            f" — {', '.join(rec.get('team_plan_reasons', [])[:2])}"
                            if rec.get('team_plan_reasons') else ""
                        )
                        + (
                            f" | missing: {', '.join(rec.get('team_plan_missing', [])[:2])}"
                            if rec.get('team_plan_missing') else ""
                        )
                    ),
                    f"**Blue pick order:** {blue_pick_text}",
                    f"**Blue bans:** {blue_ban_text}",
                    f"**Red pick order:** {red_pick_text}",
                    f"**Red bans:** {red_ban_text}",
                    (
                        f"**SoloQ role WR:** {rec['average_solo_queue_win_rate']:.2f}% avg "
                        f"({rec['solo_queue_champions_covered']}/5 covered)"
                        if rec.get("average_solo_queue_win_rate") is not None
                        else "**SoloQ role WR:** no current sample"
                    ),
                    (
                        f"**Primary win condition:** <@{rec['carry_plan']['user_id']}> — "
                        f"**{rec['carry_plan']['champion']}** ({rec['carry_plan']['role']})"
                        if rec.get("carry_plan") else "**Primary win condition:** balanced/shared"
                    ),
                    f"**Player-fit adjustment:** {rec.get('player_fit_adjustment', 0):+.1f}",
                    f"**Fit score:** {rec['score']}/100",
                ]
            )
            if rec.get("player_fit_notes"):
                lines.append("**Player-fit note:** " + "; ".join(rec["player_fit_notes"]))
            if inferred_used:
                lines.append("~ **historical fallback**: no saved champion pool for that assigned role")
            embed.add_field(
                name=f"Draft {index}",
                value="\n".join(lines),
                inline=False,
            )

        if unavailable_champions:
            embed.add_field(
                name="🚫 Unavailable this draft",
                value=", ".join(sorted(unavailable_champions))[:1024],
                inline=False,
            )
        embed.set_footer(
            text=(
                "Drafts prefer 3+ champion changes where quality allows. Player history uses OP.GG "
                "season aggregates/mastery; current SoloQ role WR, comfort, comp fit and pro signal are also scored."
            )
        )
        if selected_user_ids is not None:
            view = DraftControlsView(self, selected_user_ids, unavailable_champions, recommendations, locked_assignment)
            await interaction.channel.send(embed=embed, view=view)
            await interaction.followup.send("✅ Updated draft posted publicly.", ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def send_draft_book(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        signups = await self.list_signups(interaction.guild.id)
        assignments = generate_team_options(signups, limit=12)
        if not assignments:
            await interaction.followup.send(
                "I need a legal five-player role assignment before I can build the Draft Book.",
                ephemeral=True,
            )
            return

        try:
            base, branches, missing = await asyncio.to_thread(
                build_draft_book,
                assignments,
                base_limit=20,
                branches_per_role=3,
            )
            matchup_source = load_lane_matchups().get("source", {})
        except Exception:
            log.exception("League Draft Book generation failed")
            await interaction.followup.send(
                "The Draft Book model hit an error. **Draft Comps** still works independently.",
                ephemeral=True,
            )
            return

        if not base:
            detail = ""
            if missing:
                detail = "\nMissing champion pools for: " + ", ".join(missing[:12])
            await interaction.followup.send(
                "I need complete role-specific champion pools for a five-player lineup first."
                + detail,
                ephemeral=True,
            )
            return

        patches = ", ".join(matchup_source.get("patches_detected", [])) or "current"
        tier = matchup_source.get("tier", "all")
        embed = discord.Embed(
            title="📖 Pre-game Draft Book",
            description=(
                "This prepares branches **before queueing**. Start from one of the base drafts; "
                "if a common enemy pick appears, use the matching branch to see which prepared "
                "draft already contains the better lane response without sacrificing the whole comp.\n\n"
                f"Lane matchup signal: **LoLalytics patch {patches}, {tier} ranks**. "
                "Enemy champions are seeded from the most common recent pro picks."
            ),
            colour=discord.Colour.blurple(),
        )

        for index, rec in enumerate(base[:4], start=1):
            picks = " • ".join(
                f"{ROLE_ICONS[role]} {rec['champions'][role]}"
                for role in ROLES
            )
            embed.add_field(
                name=f"Base Draft {index} — {rec['score']}/100",
                value=(
                    f"{picks}\n"
                    f"**Identity:** {' / '.join(rec['archetypes'])}"
                )[:1024],
                inline=False,
            )

        grouped = {role: [] for role in ROLES}
        for branch in branches:
            grouped[branch["enemy_role"]].append(branch)

        for role in ROLES:
            lines = []
            for branch in grouped[role]:
                advantage = branch["response_advantage"]
                sign = "+" if advantage >= 0 else ""
                lines.append(
                    f"Enemy **{branch['enemy_champion']}** → Draft "
                    f"**{branch['draft_index']}**, **{branch['response_champion']}** "
                    f"({sign}{advantage:.1f} normalized matchup; "
                    f"{branch['matchup_games']:,} games)"
                )
            if lines:
                embed.add_field(
                    name=f"{ROLE_ICONS[role]} If they show {role}…",
                    value="\n".join(lines)[:1024],
                    inline=False,
                )

        embed.set_footer(
            text=(
                "Positive matchup numbers favour our response after normalising champion win rates. "
                "Branches re-rank prepared comps; they do not replace player comfort."
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staticmethod
    def _manager(interaction: discord.Interaction) -> bool:
        return (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild
        )

    @league.command(name="priority", description="Edit your 1/2/3 Ranked Flex role priorities.")
    async def priority(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID:
            await interaction.response.send_message("This command belongs in the League server.", ephemeral=True)
            return
        signup = await self.get_signup(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            _role_priority_prompt(signup),
            view=RolePriorityView(self, signup),
            ephemeral=True,
        )

    @league.command(
        name="game",
        description="Pick the 5 players and prepare Blue + Red draft plans for that lineup.",
    )
    async def game(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID:
            await interaction.response.send_message(
                "This command belongs in the League server.",
                ephemeral=True,
            )
            return
        # Acknowledge immediately in the League guild so Discord does not hit its
        # 3-second slash-command timeout during a reconnect/redeploy window.
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            "**Who is playing this game?**\n"
            "Pick exactly **5 players** below. I'll prepare **both Blue-side and Red-side** "
            "pick orders + protection bans before champ select starts.",
            view=CurrentGamePlayerView(self),
            ephemeral=True,
        )

    @league.command(
        name="coach",
        description="Export the current signups (roles, priorities, champion pools) as a Draft Coach loadfile.",
    )
    async def coach(self, interaction: discord.Interaction):
        """Drop the attached file into Draft Coach's loadfiles folder (or onto its coach page) and the
        desktop app ranks with exactly the pools and priorities people set up in Discord."""
        if interaction.guild_id != LEAGUE_GUILD_ID:
            await interaction.response.send_message("This command belongs in the League server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        signups = await self.list_signups(interaction.guild_id)
        players = []
        for signup in signups:
            priority = {role: int(level) for role, level in (signup.get("role_priority") or {}).items() if role in ROLES}
            roles = sorted([r for r in (signup.get("roles") or []) if r in ROLES], key=lambda r: priority.get(r, 9))
            pools = {role: [{"champion": name} for name in names]
                     for role, names in (signup.get("champions") or {}).items() if role in ROLES and names}
            players.append({
                "user_id": int(signup["user_id"]),
                "name": signup.get("display_name") or str(signup["user_id"]),
                "roles": roles,
                "role_priority": priority,
                "pools": pools,
            })
        payload = {
            "format": "flexers-loadfile",
            "version": 1,
            "mode": "replace",
            "source": "discord-signups",
            "exported_at": now_iso(),
            "players": players,
        }
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        await interaction.followup.send(
            f"Draft Coach loadfile with **{len(players)}** signups. Drop it into Draft Coach's `loadfiles` folder "
            "(or onto the coach page) and it replaces each player's pools and role priorities.",
            file=discord.File(io.BytesIO(data), filename="discord-signups.json"),
            ephemeral=True,
        )

    @league.command(name="post", description="Post the Ranked Flex signup event.")
    @app_commands.describe(
        poster="Optional event image to show above the signup controls.",
        ping_role="Optional role to ping when the event is posted.",
    )
    async def post(
        self,
        interaction: discord.Interaction,
        poster: discord.Attachment | None = None,
        ping_role: discord.Role | None = None,
    ):
        if interaction.guild_id != LEAGUE_GUILD_ID or not self._manager(interaction):
            await interaction.response.send_message("Server manager only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(LEAGUE_EVENT_CHANNEL_ID)
        if channel is None:
            channel = await guild.fetch_channel(LEAGUE_EVENT_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("Configured League channel is unavailable.", ephemeral=True)
            return

        existing = await self.get_event(guild.id)
        previous_image_url = None
        if existing and existing["message_id"]:
            try:
                old_channel = guild.get_channel(int(existing["channel_id"])) or await guild.fetch_channel(int(existing["channel_id"]))
                if isinstance(old_channel, (discord.TextChannel, discord.Thread)):
                    old_message = await old_channel.fetch_message(int(existing["message_id"]))
                    if old_message.attachments:
                        previous_image_url = old_message.attachments[0].url
                    elif old_message.embeds:
                        previous_image_url = getattr(old_message.embeds[0].image, "url", None)
            except discord.HTTPException:
                pass
        timestamp = now_iso()
        db = await database.connect()
        try:
            await db.execute(
                """INSERT INTO league_flex_events
                   (guild_id,channel_id,message_id,status,created_by,created_at,updated_at)
                   VALUES (?,?,NULL,'open',?,?,?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                   channel_id=excluded.channel_id,status='open',
                   created_by=excluded.created_by,updated_at=excluded.updated_at""",
                (guild.id, channel.id, interaction.user.id, timestamp, timestamp),
            )
            await db.commit()
        finally:
            await db.close()

        embed = await self.build_embed(guild.id, image_url=previous_image_url if poster is None else None)
        file = None
        if poster is not None:
            file = await poster.to_file(filename="league-flex-poster.png")
            embed.set_image(url="attachment://league-flex-poster.png")
        content = ping_role.mention if ping_role else None
        kwargs = {
            "content": content,
            "embed": embed,
            "view": LeagueEventView(self),
            "allowed_mentions": discord.AllowedMentions(everyone=False, users=False, roles=True),
        }
        if file is not None:
            kwargs["file"] = file
        message = await channel.send(**kwargs)
        db = await database.connect()
        try:
            await db.execute(
                "UPDATE league_flex_events SET message_id=?,updated_at=? WHERE guild_id=?",
                (message.id, now_iso(), guild.id),
            )
            await db.commit()
        finally:
            await db.close()
        notice = await self.post_role_priority_notice(guild, message.jump_url)
        extra = f" • role-priority ping posted in <#{notice.channel.id}>" if notice else ""
        await interaction.followup.send(
            f"✅ Ranked Flex event posted: {message.jump_url}{extra}",
            ephemeral=True,
        )

    @league.command(name="close", description="Close Ranked Flex signups.")
    async def close(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID or not self._manager(interaction):
            await interaction.response.send_message("Server manager only.", ephemeral=True)
            return
        db = await database.connect()
        try:
            await db.execute(
                "UPDATE league_flex_events SET status='closed',updated_at=? WHERE guild_id=?",
                (now_iso(), interaction.guild_id),
            )
            await db.commit()
        finally:
            await db.close()
        await self.refresh_event_message(interaction.guild_id)
        await interaction.response.send_message("🔒 League signups closed.", ephemeral=True)

    @league.command(name="reopen", description="Reopen Ranked Flex signups.")
    async def reopen(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID or not self._manager(interaction):
            await interaction.response.send_message("Server manager only.", ephemeral=True)
            return
        db = await database.connect()
        try:
            await db.execute(
                "UPDATE league_flex_events SET status='open',updated_at=? WHERE guild_id=?",
                (now_iso(), interaction.guild_id),
            )
            await db.commit()
        finally:
            await db.close()
        await self.refresh_event_message(interaction.guild_id)
        await interaction.response.send_message("✅ League signups reopened.", ephemeral=True)

    @league.command(name="remind", description="Post a Ranked Flex reminder now.")
    async def remind(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID or not self._manager(interaction):
            await interaction.response.send_message("Server manager only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self.post_reminder(
            interaction.guild_id,
            f"manual:{datetime.now(timezone.utc).timestamp()}",
            force=True,
        )
        if message:
            await interaction.followup.send(f"✅ Reminder posted: {message.jump_url}", ephemeral=True)
        else:
            await interaction.followup.send(
                "No reminder was posted — either signups are closed or everyone is signed up/opted out.",
                ephemeral=True,
            )

    @league.command(name="notifyme", description="Turn Ranked Flex reminder pings back on for yourself.")
    async def notifyme(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID:
            await interaction.response.send_message("This command belongs in the League server.", ephemeral=True)
            return
        await self.set_reminder_optout(interaction.guild_id, interaction.user.id, False)
        await interaction.response.send_message(
            "🔔 Ranked Flex reminder pings are enabled for you again.",
            ephemeral=True,
        )

    @league.command(name="status", description="Show the Ranked Flex event status.")
    async def status(self, interaction: discord.Interaction):
        if interaction.guild_id != LEAGUE_GUILD_ID:
            await interaction.response.send_message("This command belongs in the League server.", ephemeral=True)
            return
        event = await self.get_event(interaction.guild_id)
        signups = await self.list_signups(interaction.guild_id)
        if not event or not event["message_id"]:
            await interaction.response.send_message("The League event has not been posted yet.", ephemeral=True)
            return
        link = (
            f"https://discord.com/channels/{interaction.guild_id}/"
            f"{event['channel_id']}/{event['message_id']}"
        )
        await interaction.response.send_message(
            f"**Status:** {event['status'].title()}\n"
            f"**Signups:** {len(signups)}\n{link}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    guild = discord.Object(id=LEAGUE_GUILD_ID)
    await bot.add_cog(LeagueFlexCog(bot), guilds=[guild])
