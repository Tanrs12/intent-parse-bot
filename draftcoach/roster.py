"""The live team roster: players, champion pools, priorities, scouting and policy.

Everything a loadfile can change lives here, and the whole thing round-trips
through ``to_dict`` / ``from_dict`` so it can be persisted to ``var/roster.json``
and exported back out as a loadfile.

Pools are ordered lists of ``PoolEntry`` (champion + priority 1..5 + note).
Priority 1 is the player's best champion in that role; 0 means "unranked".
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ENGINE_DIR  # noqa: F401  (makes the engine importable)
from . import champions as champ_map

import league_comp_engine as engine  # type: ignore  # noqa: E402

ROLES = list(engine.ROLES)
ROLE_ALIASES = {
    "top": "Top", "jungle": "Jungle", "jg": "Jungle", "jng": "Jungle", "jun": "Jungle", "mid": "Mid",
    "middle": "Mid", "adc": "ADC", "bot": "ADC", "bottom": "ADC", "ad": "ADC", "support": "Support",
    "sup": "Support", "supp": "Support", "utility": "Support",
}


def role_name(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text in ROLES:
        return text
    return ROLE_ALIASES.get(text.casefold())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-") or "player"


FAMILIARITY = ("comfort", "refresh", "developing")


@dataclass
class PoolEntry:
    champion: str
    priority: int = 0            # preference among ready options (1 = first choice)
    note: str = ""
    familiarity: str = ""        # comfort | refresh | developing | "" (unknown)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"champion": self.champion}
        if self.priority:
            out["priority"] = self.priority
        if self.familiarity:
            out["familiarity"] = self.familiarity
        if self.note:
            out["note"] = self.note
        return out


def _entries_from(value: Any) -> list[PoolEntry]:
    out: list[PoolEntry] = []
    for item in value or []:
        if isinstance(item, PoolEntry):
            out.append(PoolEntry(item.champion, item.priority, item.note, item.familiarity))
        elif isinstance(item, dict):
            out.append(PoolEntry(str(item.get("champion") or item.get("name") or ""), int(item.get("priority") or 0),
                                 str(item.get("note") or ""), str(item.get("familiarity") or "")))
        else:
            out.append(PoolEntry(str(item)))
    return [e for e in out if e.champion]


@dataclass
class Player:
    key: str
    name: str
    user_id: int | None = None
    riot_ids: list[str] = field(default_factory=list)
    puuids: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    pools: dict[str, list[PoolEntry]] = field(default_factory=dict)
    avoid: list[str] = field(default_factory=list)
    notes: str = ""
    source: str = "config"
    updated_at: float = 0.0

    def champions(self, role: str) -> list[str]:
        return [e.champion for e in self.pools.get(role, [])]

    def priority(self, role: str, champion: str) -> int:
        for entry in self.pools.get(role, []):
            if entry.champion == champion:
                return entry.priority
        return 0

    def familiarity(self, role: str, champion: str) -> str:
        for entry in self.pools.get(role, []):
            if entry.champion == champion:
                return entry.familiarity
        return ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "user_id": self.user_id, "riot_ids": list(self.riot_ids),
            "puuids": list(self.puuids), "roles": list(self.roles),
            "pools": {role: [e.to_dict() for e in entries] for role, entries in self.pools.items()},
            "avoid": list(self.avoid), "notes": self.notes, "source": self.source, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        player = cls(key=str(data.get("key") or slug(data.get("name") or "player")), name=str(data.get("name") or ""))
        player.user_id = int(data["user_id"]) if data.get("user_id") else None
        player.riot_ids = [str(r) for r in data.get("riot_ids", [])]
        player.puuids = [str(p) for p in data.get("puuids", [])]
        player.roles = [r for r in (role_name(x) for x in data.get("roles", [])) if r]
        player.pools = {role: _entries_from(v) for role, v in (data.get("pools") or {}).items() if role_name(role)}
        player.pools = {role_name(r): v for r, v in player.pools.items()}
        player.avoid = [str(c) for c in data.get("avoid", [])]
        player.notes = str(data.get("notes") or "")
        player.source = str(data.get("source") or "config")
        player.updated_at = float(data.get("updated_at") or 0.0)
        return player


@dataclass
class Opponent:
    """A scouted enemy player. Pools here sharpen ban targets and counter risk."""

    key: str
    name: str
    team: str = ""
    riot_ids: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    pools: dict[str, list[PoolEntry]] = field(default_factory=dict)
    notes: str = ""
    updated_at: float = 0.0

    def champions(self, role: str | None = None) -> list[str]:
        if role:
            return [e.champion for e in self.pools.get(role, [])]
        seen: list[str] = []
        for entries in self.pools.values():
            for e in entries:
                if e.champion not in seen:
                    seen.append(e.champion)
        return seen

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "team": self.team, "riot_ids": list(self.riot_ids),
                "roles": list(self.roles), "pools": {r: [e.to_dict() for e in v] for r, v in self.pools.items()},
                "notes": self.notes, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, data: dict) -> "Opponent":
        opp = cls(key=str(data.get("key") or slug(data.get("name") or "enemy")), name=str(data.get("name") or ""))
        opp.team = str(data.get("team") or "")
        opp.riot_ids = [str(r) for r in data.get("riot_ids", [])]
        opp.roles = [r for r in (role_name(x) for x in data.get("roles", [])) if r]
        opp.pools = {role_name(r): _entries_from(v) for r, v in (data.get("pools") or {}).items() if role_name(r)}
        opp.notes = str(data.get("notes") or "")
        opp.updated_at = float(data.get("updated_at") or 0.0)
        return opp


DEFAULT_SETTINGS = {
    "pool_limit": 8,          # champions per role handed to the engine
    "comps_limit": 14,        # compositions explored per update
    "picks_shown": 6,
    "bans_shown": 5,
    "priority_weight": 0.35,  # preference bonus per priority tier (tier 1 +0.7, tier 5 -0.7): preference, not mastery
    "mode": "competitive",    # competitive | practice: how hard developing picks are penalised
    "committed_plan": "",     # playbook id the team has chosen to draft towards ("" = weigh every open plan)
    "scouting_weight": 1.0,   # how much scouted enemy pools move ban/counter scores
    "draft_type": "tournament",  # tournament | ranked | fearless
    "plan_weight": 4.0,       # pick-score bonus for completing a viable playbook composition
}


class Roster:
    """Our players, pools, scouting and policy, plus the mapping onto client seats."""

    def __init__(self, auto_profiles: bool = True):
        self.players: dict[str, Player] = {}
        self.default_pools: dict[str, list[PoolEntry]] = {}
        self.ban_priority: list[dict] = []        # [{champion, weight, note}]
        self.never_pick: list[str] = []
        self.opponents: dict[str, Opponent] = {}
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.lineup: list[str | None] = [None] * 5   # manual board seats (pick order) -> player key
        self.enemy_lineup: list[str | None] = [None] * 5   # enemy seats -> opponent key (manual board / fallback)
        self.history: list[dict] = []             # import log, newest last
        self.series: list[dict] = []              # fearless: [{"game": 1, "ours": {...}, "theirs": {...}}]
        self.playbook: list[dict] = []            # target compositions: [{id, name, style, picks{role: {player, champion, familiarity}}, ...}]
        self.version = 0
        self.auto_profiles = auto_profiles
        self._profile_accounts: dict[str, int] = {}   # riot key -> user_id discovered from engine profiles
        self._profile_names: dict[int, str] = {}
        if auto_profiles:
            self._load_profile_accounts()

    # ---- config / persistence --------------------------------------------
    @classmethod
    def from_config(cls, config: dict | None, auto_profiles: bool | None = None) -> "Roster":
        config = config or {}
        roster = cls(auto_profiles=config.get("auto_profiles", True) if auto_profiles is None else auto_profiles)
        roster.settings.update({k: v for k, v in (config.get("settings") or {}).items() if k in DEFAULT_SETTINGS})
        from . import loadfiles  # local import: loadfiles depends on this module

        if any(k in config for k in ("players", "default_pools", "opponents", "ban_priority", "never_pick")):
            loadfiles.apply_loadfile(roster, config, name="config.json")
        return roster

    def to_dict(self) -> dict:
        return {
            "format": "flexers-roster", "version": self.version, "saved_at": time.time(),
            "players": [p.to_dict() for p in self.players.values()],
            "default_pools": {r: [e.to_dict() for e in v] for r, v in self.default_pools.items()},
            "ban_priority": list(self.ban_priority), "never_pick": list(self.never_pick),
            "opponents": [o.to_dict() for o in self.opponents.values()],
            "settings": dict(self.settings), "lineup": list(self.lineup), "enemy_lineup": list(self.enemy_lineup),
            "history": list(self.history[-100:]), "series": list(self.series), "playbook": list(self.playbook),
        }

    def load_dict(self, data: dict) -> None:
        self.players = {}
        for row in data.get("players", []):
            player = Player.from_dict(row)
            self.players[player.key] = player
        self.default_pools = {role_name(r): _entries_from(v) for r, v in (data.get("default_pools") or {}).items() if role_name(r)}
        self.ban_priority = [dict(b) for b in data.get("ban_priority", [])]
        self.never_pick = [str(c) for c in data.get("never_pick", [])]
        self.opponents = {}
        for row in data.get("opponents", []):
            opp = Opponent.from_dict(row)
            self.opponents[opp.key] = opp
        self.settings = {**DEFAULT_SETTINGS, **{k: v for k, v in (data.get("settings") or {}).items() if k in DEFAULT_SETTINGS}}
        lineup = list(data.get("lineup") or [])
        self.lineup = (lineup + [None] * 5)[:5]
        enemy = list(data.get("enemy_lineup") or [])
        self.enemy_lineup = (enemy + [None] * 5)[:5]
        self.history = list(data.get("history") or [])
        self.series = list(data.get("series") or [])
        self.playbook = list(data.get("playbook") or [])
        self.version = int(data.get("version") or 0)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, auto_profiles: bool = True) -> "Roster":
        roster = cls(auto_profiles=False)
        roster.load_dict(json.loads(path.read_text(encoding="utf-8")))
        roster.auto_profiles = auto_profiles
        if auto_profiles:
            roster._load_profile_accounts()
        return roster

    def touch(self) -> None:
        self.version += 1

    # ---- players -------------------------------------------------------------
    @staticmethod
    def riot_key(riot_id: str) -> str:
        return str(riot_id).replace(" ", "").casefold()

    def _load_profile_accounts(self) -> None:
        """Riot accounts already known to the engine's player profiles."""
        try:
            profiles = engine.load_player_profiles().get("players", {})
        except Exception:  # noqa: BLE001 - optional file
            return
        for uid, profile in profiles.items():
            try:
                user_id = int(uid)
            except ValueError:
                continue
            display = str(profile.get("discord_name") or uid)
            self._profile_names[user_id] = display
            riot_ids: list[str] = []
            for account in profile.get("accounts", []):
                account_slug = str(account.get("slug") or "")
                if "-" not in account_slug:
                    continue
                name, _, tag = account_slug.rpartition("-")
                self._profile_accounts[self.riot_key(f"{name}#{tag}")] = user_id
                riot_ids.append(f"{name}#{tag}")
            # Every engine profile is a roster player from the start: the lobby then
            # matches their accounts and the advisor can draw on their account history
            # until a loadfile supplies a submitted pool.
            if self.find_player(user_id=user_id) is None:
                hints = sorted(profile.get("position_hints", []), key=lambda h: 0 if h.get("tag") == "MAIN" else 1)
                roles = [r for r in (role_name(h.get("position")) for h in hints) if r]
                player = Player(key=slug(display), name=display, user_id=user_id, riot_ids=riot_ids,
                                roles=list(dict.fromkeys(roles)), source="profile")
                if player.key in self.players:
                    player.key = f"{player.key}-{user_id}"
                self.players[player.key] = player

    def find_player(self, *, key: str | None = None, name: str | None = None, user_id: int | None = None,
                    riot_id: str | None = None, puuid: str | None = None) -> Player | None:
        if key and key in self.players:
            return self.players[key]
        if user_id:
            for p in self.players.values():
                if p.user_id == int(user_id):
                    return p
        if puuid:
            for p in self.players.values():
                if puuid in p.puuids:
                    return p
        if riot_id:
            rk = self.riot_key(riot_id)
            for p in self.players.values():
                if rk in {self.riot_key(r) for r in p.riot_ids}:
                    return p
            uid = self._profile_accounts.get(rk)
            if uid:
                for p in self.players.values():
                    if p.user_id == uid:
                        return p
        if name:
            s = slug(name)
            if s in self.players:
                return self.players[s]
            for p in self.players.values():
                if p.name.casefold() == str(name).casefold():
                    return p
            rk = self.riot_key(name)
            for p in self.players.values():
                if any(self.riot_key(r).split("#")[0] == rk for r in p.riot_ids):
                    return p
        return None

    def ensure_player(self, name: str, user_id: int | None = None, source: str = "config") -> Player:
        player = self.find_player(name=name, user_id=user_id)
        if player is None:
            player = Player(key=slug(name), name=str(name), user_id=int(user_id) if user_id else None, source=source)
            if player.user_id is None and self.auto_profiles:
                # A display name that matches a profile's discord name adopts its id.
                for uid, discord_name in self._profile_names.items():
                    if discord_name.casefold() == str(name).casefold():
                        player.user_id = uid
            self.players[player.key] = player
        elif user_id and not player.user_id:
            player.user_id = int(user_id)
        return player

    def remove_player(self, key: str) -> bool:
        if key in self.players:
            del self.players[key]
            self.lineup = [k if k != key else None for k in self.lineup]
            return True
        return False

    def remove_opponent(self, key: str) -> bool:
        if key in self.opponents:
            del self.opponents[key]
            self.enemy_lineup = [k if k != key else None for k in self.enemy_lineup]
            return True
        return False

    def profile_user_id_for(self, riot_id: str | None) -> int | None:
        """Engine profile id for a Riot id; falls back to the bare game name when it is unambiguous."""
        if not riot_id:
            return None
        key = self.riot_key(riot_id)
        hit = self._profile_accounts.get(key)
        if hit:
            return hit
        bare = key.split("#")[0]
        matches = {uid for k, uid in self._profile_accounts.items() if k.split("#")[0] == bare}
        return matches.pop() if len(matches) == 1 else None

    # ---- scouting ------------------------------------------------------------
    def ensure_opponent(self, name: str, team: str = "") -> Opponent:
        key = slug(name)
        opp = self.opponents.get(key)
        if opp is None:
            for candidate in self.opponents.values():
                if candidate.name.casefold() == str(name).casefold():
                    opp = candidate
                    break
        if opp is None:
            opp = Opponent(key=key, name=str(name), team=team)
            self.opponents[key] = opp
        if team and not opp.team:
            opp.team = team
        return opp

    def find_opponent(self, *, riot_id: str | None = None, name: str | None = None) -> Opponent | None:
        if riot_id:
            rk = self.riot_key(riot_id)
            for o in self.opponents.values():
                if rk in {self.riot_key(r) for r in o.riot_ids}:
                    return o
            bare = rk.split("#")[0]
            for o in self.opponents.values():
                if any(self.riot_key(r).split("#")[0] == bare for r in o.riot_ids):
                    return o
        if name:
            s = slug(name)
            if s in self.opponents:
                return self.opponents[s]
            for o in self.opponents.values():
                if o.name.casefold() == str(name).casefold():
                    return o
        return None

    # ---- lineup (manual board) ------------------------------------------------
    def set_lineup(self, keys: list[str | None]) -> None:
        cleaned: list[str | None] = []
        for k in (list(keys) + [None] * 5)[:5]:
            cleaned.append(k if k in self.players else None)
        self.lineup = cleaned

    def set_enemy_lineup(self, keys: list[str | None]) -> None:
        cleaned: list[str | None] = []
        for k in (list(keys) + [None] * 5)[:5]:
            cleaned.append(k if k in self.opponents else None)
        self.enemy_lineup = cleaned

    def lineup_players(self) -> list[Player | None]:
        return [self.players.get(k) if k else None for k in self.lineup]

    def auto_lineup(self) -> None:
        """Fill empty seats with players whose roles make a full five, in role order."""
        used = {k for k in self.lineup if k}
        for role in ROLES:
            if any(p and role in (p.roles[:1] or []) for p in self.lineup_players()):
                continue
            for player in self.players.values():
                if player.key in used:
                    continue
                if role in player.roles or not player.roles:
                    free = [i for i, k in enumerate(self.lineup) if not k]
                    if free:
                        self.lineup[free[0]] = player.key
                        used.add(player.key)
                    break

    # ---- fearless series ---------------------------------------------------------------
    def series_champions(self) -> set[str]:
        out: set[str] = set()
        for game in self.series:
            out |= set((game.get("ours") or {}).values()) | set((game.get("theirs") or {}).values())
        return {c for c in out if c}

    # ---- summary -----------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "version": self.version,
            "players": [
                {"key": p.key, "name": p.name, "user_id": p.user_id, "roles": p.roles,
                 "pool_sizes": {r: len(v) for r, v in p.pools.items()}, "riot_ids": p.riot_ids, "source": p.source}
                for p in self.players.values()
            ],
            "default_pool_sizes": {r: len(v) for r, v in self.default_pools.items()},
            "opponents": [{"key": o.key, "name": o.name, "team": o.team, "roles": o.roles,
                           "pool_sizes": {r: len(v) for r, v in o.pools.items()}} for o in self.opponents.values()],
            "ban_priority": self.ban_priority, "never_pick": self.never_pick, "settings": self.settings,
            "lineup": self.lineup, "enemy_lineup": self.enemy_lineup, "imports": len(self.history),
            "series": self.series, "series_unavailable": sorted(self.series_champions()),
            "playbook": [{"id": c.get("id"), "name": c.get("name"), "style": c.get("style")} for c in self.playbook],
        }
