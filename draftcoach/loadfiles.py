"""Loadfiles: drop-in updates to champion pools, scouting, policy and engine data.

A loadfile is a JSON or CSV file. It can be imported from the coach panel
(drag and drop), from the CLI (``run.py --load file``), over REST
(``POST /api/import``) or by dropping it into the watched ``loadfiles/``
folder. Every import produces an ``ImportResult`` that lists exactly what
changed and what was ignored (unknown champions, bad roles), and the result is
appended to the roster's history so the panel can show it.

JSON shape (every key optional)::

    {
      "format": "flexers-loadfile", "version": 1, "note": "week 3 pools",
      "mode": "merge",                       # default for every section: merge | replace
      "players": [
        {"name": "Chud", "user_id": 1398..., "riot_ids": ["Chud#EUW"], "roles": ["Jungle", "Mid"],
         "pools": {"Jungle": ["Lee Sin", {"champion": "Vi", "priority": 1, "note": "best"}]},
         "remove": {"Jungle": ["Nocturne"]}, "avoid": ["Teemo"], "mode": "replace", "delete": false}
      ],
      "default_pools": {"Top": ["Ornn", "..."]},
      "ban_priority": ["Rumble", {"champion": "Yone", "weight": 2, "note": "their mid one-trick"}],
      "never_pick": ["Teemo"],
      "opponents": [{"name": "EnemyMid", "team": "Rivals", "riot_ids": ["EnemyMid#EUW"], "roles": ["Mid"],
                     "pools": {"Mid": [{"champion": "Yone", "priority": 1}]}}],
      "lineup": ["Chud", "Atomic", "Loony", "Skill", "Schy"],
      "settings": {"pool_limit": 8}
    }

``mode: replace`` on a player replaces each *listed* role's pool; roles that are
not mentioned are left alone. ``remove`` always removes. A player with
``"delete": true`` is dropped from the roster.

CSV shape (header required, ``kind`` and ``note`` optional)::

    kind,player,role,champion,priority,action,note
    pool,Chud,Jungle,Lee Sin,1,add,
    pool,,Top,Ornn,,add,            <- blank player = default pool
    scout,EnemyMid,Mid,Yone,1,add,their one-trick
    ban,,,Rumble,2,add,
    avoid,Chud,,Teemo,,add,

The engine's own data snapshots (``league_lane_matchups.json`` and friends)
are recognised by their keys and installed into ``engine/data`` with a backup
of the previous file, so a refreshed matchup table is a drag-and-drop too.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ENGINE_DIR, VAR_DIR
from . import champions as champ_map
from . import engine_cache
from .roster import DEFAULT_SETTINGS, ROLES, Opponent, Player, PoolEntry, Roster, role_name, slug

import league_comp_engine as engine  # type: ignore  # noqa: E402

log = logging.getLogger("draftcoach.loadfiles")

LOADFILE_FORMAT = "flexers-loadfile"
FAMILIARITY_TIERS = {"comfort": 1, "main": 1, "signature": 1, "refresh": 2, "secondary": 2, "developing": 3, "learning": 3, "trial": 4}
ROSTER_FORMAT = "flexers-roster"

ENGINE_DATA_SIGNATURES = {
    # file name: keys that identify it
    "league_lane_matchups.json": ("matchups",),
    "league_open_pro_pairs.json": ("pairs",),
    "league_licensed_soloq.json": ("sources", "champions", "matchups"),
    "league_comp_meta.json": ("champion_traits", "role_stats"),
    "league_player_profiles.json": ("players", "source"),
    "league_recent_pro.json": ("games", "latest_patch"),
    "league_role_champions.json": ("roles",),
}


@dataclass
class ImportResult:
    name: str
    kind: str = "loadfile"              # loadfile | engine-data | roster-snapshot | unknown
    applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    data_changed: bool = False
    note: str = ""
    at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "ok": self.ok, "applied": list(self.applied),
                "warnings": list(self.warnings), "errors": list(self.errors), "data_changed": self.data_changed,
                "note": self.note, "at": self.at, "changes": len(self.applied)}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_text(text: str, filename: str = "") -> dict:
    """Parse JSON or CSV loadfile text into the JSON loadfile shape."""
    stripped = text.lstrip("﻿ \t\r\n")
    lower = filename.casefold()
    if lower.endswith(".csv") or (not lower.endswith(".json") and stripped and stripped[0] not in "{["):
        return parse_csv(stripped)
    data = json.loads(stripped)
    if isinstance(data, list):
        # A bare list of player objects.
        data = {"players": data}
    if not isinstance(data, dict):
        raise ValueError("loadfile must be a JSON object")
    return data


def parse_csv(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV loadfile has no header row")
    fields = {f.strip().casefold(): f for f in reader.fieldnames if f}
    if "champion" not in fields:
        raise ValueError("CSV loadfile needs a 'champion' column")

    def get(row: dict, key: str) -> str:
        col = fields.get(key)
        return str(row.get(col) or "").strip() if col else ""

    players: dict[str, dict] = {}
    opponents: dict[str, dict] = {}
    default_pools: dict[str, list] = {}
    ban_priority: list[dict] = []
    unban: list[str] = []
    never_pick: list[str] = []
    allow_pick: list[str] = []
    for row in reader:
        champion = get(row, "champion")
        if not champion:
            continue
        kind = (get(row, "kind") or "pool").casefold()
        player = get(row, "player") or get(row, "name")
        role = get(row, "role")
        priority = get(row, "priority")
        action = (get(row, "action") or "add").casefold()
        note = get(row, "note")
        team = get(row, "team")
        entry: dict[str, Any] = {"champion": champion}
        if get(row, "familiarity"):
            entry["familiarity"] = get(row, "familiarity")
        if priority:
            try:
                entry["priority"] = int(float(priority))
            except ValueError:
                entry["note"] = (note + " " if note else "") + f"priority '{priority}' ignored"
        if note:
            entry["note"] = note
        remove = action in ("remove", "del", "delete", "drop", "-")
        if kind in ("pool", "pools", "champ", "champion", "pick"):
            if not role:
                raise ValueError(f"row for {champion}: pool rows need a role")
            if player:
                target = players.setdefault(player, {"name": player, "pools": {}, "remove": {}})
            else:
                target = {"pools": default_pools, "remove": {}}
                if remove:
                    target = players.setdefault("", {"name": "", "pools": {}, "remove": {}})
            bucket = target["remove" if remove else "pools"]
            bucket.setdefault(role, []).append(champion if remove else entry)
        elif kind in ("scout", "opponent", "enemy"):
            if not player:
                raise ValueError(f"scout row for {champion}: needs the opponent's name in 'player'")
            target = opponents.setdefault(player, {"name": player, "team": team, "pools": {}, "remove": {}})
            if team and not target.get("team"):
                target["team"] = team
            role = role or "Any"
            target["remove" if remove else "pools"].setdefault(role, []).append(champion if remove else entry)
        elif kind in ("ban", "bans", "ban_priority"):
            if remove:
                unban.append(champion)
            else:
                ban_priority.append({"champion": champion, "weight": entry.get("priority", 1), "note": note})
        elif kind in ("avoid", "never", "never_pick"):
            if player:
                target = players.setdefault(player, {"name": player, "pools": {}, "remove": {}})
                target.setdefault("avoid" if not remove else "unavoid", []).append(champion)
            else:
                (allow_pick if remove else never_pick).append(champion)
        else:
            raise ValueError(f"row for {champion}: unknown kind '{kind}'")
    out: dict[str, Any] = {"format": LOADFILE_FORMAT, "version": 1}
    default_removals = players.pop("", None)
    if players:
        out["players"] = list(players.values())
    if default_pools:
        out["default_pools"] = default_pools
    if default_removals and default_removals.get("remove"):
        out["default_pools_remove"] = default_removals["remove"]
    if opponents:
        out["opponents"] = list(opponents.values())
    if ban_priority:
        out["ban_priority"] = ban_priority
    if unban:
        out["unban_priority"] = unban
    if never_pick:
        out["never_pick"] = never_pick
    if allow_pick:
        out["allow_pick"] = allow_pick
    return out


def detect_kind(data: dict) -> tuple[str, str | None]:
    """(kind, engine file name) for a parsed document."""
    fmt = str(data.get("format") or "")
    if fmt == ROSTER_FORMAT:
        return "roster-snapshot", None
    if fmt == LOADFILE_FORMAT:
        return "loadfile", None
    if "schema_version" in data or ("roles" in data and "source" in data and len(data) <= 3):
        for filename, keys in ENGINE_DATA_SIGNATURES.items():
            if all(k in data for k in keys):
                if filename == "league_player_profiles.json" and not isinstance(data.get("players"), dict):
                    continue
                return "engine-data", filename
    if any(k in data for k in ("players", "default_pools", "opponents", "ban_priority", "never_pick", "lineup",
                               "enemy_lineup", "settings", "default_pools_remove", "allow_pick", "unban_priority", "playbook")):
        return "loadfile", None
    return "unknown", None


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #


def _resolve_entries(items: Any, result: ImportResult, context: str) -> list[PoolEntry]:
    out: list[PoolEntry] = []
    for item in items or []:
        if isinstance(item, dict):
            raw = str(item.get("champion") or item.get("name") or "")
            priority = item.get("priority") or item.get("tier") or 0
            familiarity = str(item.get("familiarity") or "").casefold()
            if familiarity and familiarity not in FAMILIARITY_TIERS:
                result.warnings.append(f"{context}: unknown familiarity '{familiarity}' for {raw} ignored")
                familiarity = ""
            familiarity = {"main": "comfort", "signature": "comfort", "secondary": "refresh", "learning": "developing", "trial": "developing"}.get(familiarity, familiarity)
            note = str(item.get("note") or "")
        else:
            raw, priority, note, familiarity = str(item), 0, "", ""
        if not raw.strip():
            continue
        name = champ_map.resolve(raw)
        if not name:
            result.warnings.append(f"{context}: unknown champion '{raw}' skipped")
            continue
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            result.warnings.append(f"{context}: priority '{priority}' for {name} ignored")
            priority = 0
        priority = max(0, min(5, priority))
        if any(e.champion == name for e in out):
            continue
        out.append(PoolEntry(name, priority, note, familiarity))
    return out


def _resolve_names(items: Any, result: ImportResult, context: str) -> list[str]:
    return [e.champion for e in _resolve_entries(items, result, context)]


def _merge_pool(existing: list[PoolEntry], incoming: list[PoolEntry], mode: str) -> tuple[list[PoolEntry], list[str]]:
    """Merge or replace one role's pool, returning (pool, change descriptions)."""
    changes: list[str] = []
    if mode == "replace":
        before = {e.champion for e in existing}
        after = {e.champion for e in incoming}
        added, removed = sorted(after - before), sorted(before - after)
        if added:
            changes.append("added " + ", ".join(added))
        if removed:
            changes.append("removed " + ", ".join(removed))
        if not added and not removed and [e.priority for e in existing] != [e.priority for e in incoming]:
            changes.append("priorities updated")
        return list(incoming), changes
    merged = [PoolEntry(e.champion, e.priority, e.note, e.familiarity) for e in existing]
    added: list[str] = []
    updated: list[str] = []
    for entry in incoming:
        current = next((e for e in merged if e.champion == entry.champion), None)
        if current is None:
            merged.append(entry)
            added.append(entry.champion)
        else:
            if entry.priority and entry.priority != current.priority:
                current.priority = entry.priority
                updated.append(f"{entry.champion} p{entry.priority}")
            if entry.familiarity and entry.familiarity != current.familiarity:
                current.familiarity = entry.familiarity
                updated.append(f"{entry.champion} {entry.familiarity}")
            if entry.note:
                current.note = entry.note
    if added:
        changes.append("added " + ", ".join(added))
    if updated:
        changes.append("priority " + ", ".join(updated))
    merged.sort(key=lambda e: (e.priority == 0, e.priority))
    return merged, changes


def _remove_from_pool(existing: list[PoolEntry], names: list[str]) -> tuple[list[PoolEntry], list[str]]:
    removed = [e.champion for e in existing if e.champion in names]
    return [e for e in existing if e.champion not in names], removed


def _pool_sections(obj: dict) -> dict:
    pools = obj.get("pools") or obj.get("champions") or obj.get("champion_pools") or {}
    if isinstance(pools, list):
        # A flat list means "every role listed for the player", or Any for opponents.
        return {"*": pools}
    return pools


def _apply_player(roster: Roster, row: dict, default_mode: str, result: ImportResult, source: str) -> None:
    name = str(row.get("name") or row.get("display_name") or row.get("player") or "").strip()
    user_id = row.get("user_id") or row.get("discord_id")
    if not name and not user_id:
        result.warnings.append("player entry without a name skipped")
        return
    try:
        user_id = int(user_id) if user_id else None
    except (TypeError, ValueError):
        result.warnings.append(f"{name}: user_id '{user_id}' is not a number, ignored")
        user_id = None
    existing = roster.find_player(name=name or None, user_id=user_id)
    if row.get("delete"):
        if existing and roster.remove_player(existing.key):
            result.applied.append(f"removed player {existing.name}")
        return
    if existing is None and not name:
        name = roster._profile_names.get(int(user_id), f"player-{user_id}")
    player = roster.ensure_player(name, user_id, source=source)
    if existing is None:
        result.applied.append(f"added player {player.name}")
    if row.get("rename"):
        player.name = str(row["rename"])
        result.applied.append(f"{player.name}: renamed")
    mode = str(row.get("mode") or default_mode).casefold()
    changed = False
    for rid in row.get("riot_ids") or ([row["riot_id"]] if row.get("riot_id") else []):
        if roster.riot_key(rid) not in {roster.riot_key(r) for r in player.riot_ids}:
            player.riot_ids.append(str(rid))
            result.applied.append(f"{player.name}: riot id {rid}")
            changed = True
    for puuid in row.get("puuids") or []:
        if puuid not in player.puuids:
            player.puuids.append(str(puuid))
            changed = True
    if player.user_id is None:
        for rid in player.riot_ids:
            uid = roster.profile_user_id_for(rid)
            if uid:
                player.user_id = uid
                result.applied.append(f"{player.name}: linked to engine profile {uid}")
                changed = True
                break
    if row.get("roles") is not None:
        roles = [r for r in (role_name(x) for x in row["roles"]) if r]
        bad = [x for x in row["roles"] if not role_name(x)]
        if isinstance(row.get("role_priority"), dict):
            # Discord signups carry 1/2/3 role priorities: the roster keeps roles ordered by them.
            levels = {role_name(k): int(v) for k, v in row["role_priority"].items() if role_name(k) and str(v).lstrip("-").isdigit()}
            roles = sorted(roles, key=lambda r: (levels.get(r, 9), roles.index(r)))
        for b in bad:
            result.warnings.append(f"{player.name}: unknown role '{b}' ignored")
        if roles != player.roles:
            # Explicit roles outrank roles inferred from account history: the file's order wins.
            player.roles = roles if mode == "replace" or not player.roles else list(dict.fromkeys(roles + player.roles))
            result.applied.append(f"{player.name}: roles {', '.join(player.roles)}")
            changed = True
    pools = _pool_sections(row)
    for raw_role, items in pools.items():
        targets = [raw_role] if raw_role != "*" else (player.roles or [])
        if raw_role == "*" and not targets:
            result.warnings.append(f"{player.name}: flat champion list needs roles")
        for raw_target in targets:
            role = role_name(raw_target)
            if not role:
                result.warnings.append(f"{player.name}: unknown role '{raw_target}' ignored")
                continue
            incoming = _resolve_entries(items, result, f"{player.name} {role}")
            merged, changes = _merge_pool(player.pools.get(role, []), incoming, mode)
            if changes:
                player.pools[role] = merged
                result.applied.append(f"{player.name} {role}: " + "; ".join(changes))
                changed = True
            elif role not in player.pools and incoming:
                player.pools[role] = merged
            if role not in player.roles and merged:
                player.roles.append(role)
    for raw_role, items in (row.get("remove") or {}).items():
        role = role_name(raw_role)
        if not role:
            result.warnings.append(f"{player.name}: unknown role '{raw_role}' in remove ignored")
            continue
        names = _resolve_names(items, result, f"{player.name} {role} remove")
        pool, removed = _remove_from_pool(player.pools.get(role, []), names)
        if removed:
            player.pools[role] = pool
            result.applied.append(f"{player.name} {role}: removed " + ", ".join(removed))
            changed = True
    if row.get("avoid"):
        for n in _resolve_names(row["avoid"], result, f"{player.name} avoid"):
            if n not in player.avoid:
                player.avoid.append(n)
                result.applied.append(f"{player.name}: avoid {n}")
                changed = True
    if row.get("unavoid"):
        for n in _resolve_names(row["unavoid"], result, f"{player.name} unavoid"):
            if n in player.avoid:
                player.avoid.remove(n)
                result.applied.append(f"{player.name}: no longer avoiding {n}")
                changed = True
    if row.get("notes") is not None:
        player.notes = str(row["notes"])
    if changed:
        player.updated_at = time.time()
        player.source = source


def _apply_opponent(roster: Roster, row: dict, default_mode: str, result: ImportResult) -> None:
    name = str(row.get("name") or row.get("player") or "").strip()
    if not name:
        result.warnings.append("opponent entry without a name skipped")
        return
    existing = roster.find_opponent(name=name) or roster.find_opponent(riot_id=(row.get("riot_ids") or [None])[0])
    if row.get("delete"):
        if existing:
            del roster.opponents[existing.key]
            result.applied.append(f"removed opponent {existing.name}")
        return
    opp = existing or roster.ensure_opponent(name, str(row.get("team") or ""))
    if existing is None:
        result.applied.append(f"added opponent {opp.name}" + (f" ({opp.team})" if opp.team else ""))
    elif row.get("team"):
        opp.team = str(row["team"])
    mode = str(row.get("mode") or default_mode).casefold()
    for rid in row.get("riot_ids") or ([row["riot_id"]] if row.get("riot_id") else []):
        if roster.riot_key(rid) not in {roster.riot_key(r) for r in opp.riot_ids}:
            opp.riot_ids.append(str(rid))
            result.applied.append(f"{opp.name}: riot id {rid}")
    if row.get("roles") is not None:
        opp.roles = [r for r in (role_name(x) for x in row["roles"]) if r]
    for raw_role, items in _pool_sections(row).items():
        role = "Any" if raw_role in ("*", "Any", "any") else role_name(raw_role)
        if not role:
            result.warnings.append(f"{opp.name}: unknown role '{raw_role}' ignored")
            continue
        incoming = _resolve_entries(items, result, f"{opp.name} {role}")
        merged, changes = _merge_pool(opp.pools.get(role, []), incoming, mode)
        if changes:
            opp.pools[role] = merged
            result.applied.append(f"{opp.name} {role}: " + "; ".join(changes))
            opp.updated_at = time.time()
        if role != "Any" and role not in opp.roles and merged:
            opp.roles.append(role)
    for raw_role, items in (row.get("remove") or {}).items():
        role = "Any" if raw_role in ("*", "Any", "any") else role_name(raw_role)
        if not role:
            continue
        names = _resolve_names(items, result, f"{opp.name} {role} remove")
        pool, removed = _remove_from_pool(opp.pools.get(role, []), names)
        if removed:
            opp.pools[role] = pool
            result.applied.append(f"{opp.name} {role}: removed " + ", ".join(removed))
    if row.get("notes") is not None:
        opp.notes = str(row["notes"])


def apply_loadfile(roster: Roster, data: dict, name: str = "loadfile") -> ImportResult:
    """Apply a parsed loadfile to the roster in place."""
    result = ImportResult(name=name, kind="loadfile", note=str(data.get("note") or ""))
    default_mode = str(data.get("mode") or "merge").casefold()
    if default_mode not in ("merge", "replace"):
        result.warnings.append(f"unknown mode '{default_mode}', using merge")
        default_mode = "merge"
    source = f"loadfile:{name}"

    for row in data.get("players") or []:
        if isinstance(row, dict):
            _apply_player(roster, row, default_mode, result, source)
        else:
            result.warnings.append(f"player entry {row!r} is not an object")

    for raw_role, items in (data.get("default_pools") or {}).items():
        role = role_name(raw_role)
        if not role:
            result.warnings.append(f"default pools: unknown role '{raw_role}' ignored")
            continue
        incoming = _resolve_entries(items, result, f"default {role}")
        merged, changes = _merge_pool(roster.default_pools.get(role, []), incoming, default_mode)
        if changes or role not in roster.default_pools:
            roster.default_pools[role] = merged
        if changes:
            result.applied.append(f"default {role}: " + "; ".join(changes))
    for raw_role, items in (data.get("default_pools_remove") or {}).items():
        role = role_name(raw_role)
        if not role:
            continue
        pool, removed = _remove_from_pool(roster.default_pools.get(role, []), _resolve_names(items, result, f"default {role}"))
        if removed:
            roster.default_pools[role] = pool
            result.applied.append(f"default {role}: removed " + ", ".join(removed))

    if "ban_priority" in data:
        incoming: list[dict] = []
        for item in data.get("ban_priority") or []:
            raw = item.get("champion") if isinstance(item, dict) else item
            champion = champ_map.resolve(str(raw or ""))
            if not champion:
                result.warnings.append(f"ban priority: unknown champion '{raw}' skipped")
                continue
            weight = float(item.get("weight") or item.get("priority") or 1) if isinstance(item, dict) else 1.0
            incoming.append({"champion": champion, "weight": max(0.1, min(5.0, weight)),
                             "note": str(item.get("note") or "") if isinstance(item, dict) else ""})
        if default_mode == "replace":
            roster.ban_priority = incoming
            result.applied.append("ban priority: " + (", ".join(b["champion"] for b in incoming) or "cleared"))
        else:
            for item in incoming:
                current = next((b for b in roster.ban_priority if b["champion"] == item["champion"]), None)
                if current is None:
                    roster.ban_priority.append(item)
                    result.applied.append(f"ban priority: {item['champion']} (w{item['weight']:g})")
                elif current["weight"] != item["weight"] or (item["note"] and item["note"] != current.get("note")):
                    current.update(item)
                    result.applied.append(f"ban priority: {item['champion']} updated")
    for raw in data.get("unban_priority") or []:
        champion = champ_map.resolve(str(raw))
        before = len(roster.ban_priority)
        roster.ban_priority = [b for b in roster.ban_priority if b["champion"] != champion]
        if len(roster.ban_priority) != before:
            result.applied.append(f"ban priority: removed {champion}")

    if "never_pick" in data:
        names = _resolve_names(data.get("never_pick"), result, "never_pick")
        if default_mode == "replace":
            roster.never_pick = names
            result.applied.append("never pick: " + (", ".join(names) or "cleared"))
        else:
            for n in names:
                if n not in roster.never_pick:
                    roster.never_pick.append(n)
                    result.applied.append(f"never pick: {n}")
    for n in _resolve_names(data.get("allow_pick"), result, "allow_pick"):
        if n in roster.never_pick:
            roster.never_pick.remove(n)
            result.applied.append(f"never pick: removed {n}")

    for row in data.get("opponents") or []:
        if isinstance(row, dict):
            _apply_opponent(roster, row, default_mode, result)

    if data.get("lineup") is not None:
        keys: list[str | None] = []
        for item in list(data["lineup"])[:5]:
            player = roster.find_player(name=str(item)) if item else None
            if item and player is None:
                result.warnings.append(f"lineup: unknown player '{item}'")
            keys.append(player.key if player else None)
        roster.set_lineup(keys)
        result.applied.append("lineup: " + ", ".join(roster.players[k].name if k else "-" for k in roster.lineup))

    if data.get("playbook") is not None:
        incoming_plans = []
        for comp in data.get("playbook") or []:
            if not isinstance(comp, dict) or not comp.get("picks"):
                result.warnings.append("playbook: composition without picks skipped")
                continue
            plan = {"id": str(comp.get("id") or slug(comp.get("name") or "plan")), "name": str(comp.get("name") or comp.get("id") or "plan"),
                    "style": str(comp.get("style") or ""), "win": str(comp.get("win") or ""), "fail": str(comp.get("fail") or ""),
                    "readiness": str(comp.get("readiness") or ""), "picks": {}, "substitutions": []}
            ok = True
            for raw_role, pick in (comp.get("picks") or {}).items():
                role = role_name(raw_role)
                champion = champ_map.resolve(str((pick or {}).get("champion") if isinstance(pick, dict) else pick))
                if not role or not champion:
                    result.warnings.append(f"playbook {plan['name']}: bad pick {raw_role}={pick}")
                    ok = False
                    break
                entry = {"champion": champion}
                if isinstance(pick, dict):
                    if pick.get("player"):
                        player = roster.find_player(name=str(pick["player"]))
                        entry["player"] = player.key if player else slug(str(pick["player"]))
                        entry["player_name"] = player.name if player else str(pick["player"])
                    if pick.get("familiarity"):
                        entry["familiarity"] = str(pick["familiarity"])
                plan["picks"][role] = entry
            if not ok or len(plan["picks"]) != 5:
                if ok:
                    result.warnings.append(f"playbook {plan['name']}: needs all five roles")
                continue
            for sub in comp.get("substitutions") or []:
                role = role_name((sub or {}).get("role"))
                champion = champ_map.resolve(str((sub or {}).get("champion") or ""))
                if role and champion:
                    player = roster.find_player(name=str(sub.get("player") or "")) if sub.get("player") else None
                    plan["substitutions"].append({"role": role, "champion": champion, "player": player.key if player else None,
                                                  "note": str(sub.get("note") or "")})
            # Approved variants: the base lineup plus one variant per single substitution (never combined).
            base = {r: e["champion"] for r, e in plan["picks"].items()}
            plan["variants"] = [{"id": "base", "champions": dict(base), "cost": 0.0, "note": ""}]
            for sub in plan["substitutions"]:
                champs = dict(base, **{sub["role"]: sub["champion"]})
                plan["variants"].append({"id": f"{sub['role'].lower()}-{sub['champion'].lower().replace(' ', '-')}", "champions": champs,
                                         "cost": 0.5, "note": sub.get("note", "")})
            incoming_plans.append(plan)
        if default_mode == "replace":
            roster.playbook = incoming_plans
        else:
            by_id = {c["id"]: c for c in roster.playbook}
            for plan in incoming_plans:
                by_id[plan["id"]] = plan
            roster.playbook = list(by_id.values())
        if incoming_plans:
            result.applied.append("playbook: " + ", ".join(c["name"] for c in incoming_plans))

    if data.get("enemy_lineup") is not None:
        keys: list[str | None] = []
        for item in list(data["enemy_lineup"])[:5]:
            opp = roster.find_opponent(name=str(item)) if item else None
            if item and opp is None:
                result.warnings.append(f"enemy lineup: unknown opponent '{item}'")
            keys.append(opp.key if opp else None)
        roster.set_enemy_lineup(keys)
        result.applied.append("enemy lineup: " + ", ".join(roster.opponents[k].name if k else "-" for k in roster.enemy_lineup))

    for key, value in (data.get("settings") or {}).items():
        if key not in DEFAULT_SETTINGS:
            result.warnings.append(f"settings: unknown key '{key}' ignored")
            continue
        try:
            value = type(DEFAULT_SETTINGS[key])(value)
        except (TypeError, ValueError):
            result.warnings.append(f"settings: bad value for {key}")
            continue
        if roster.settings.get(key) != value:
            roster.settings[key] = value
            result.applied.append(f"setting {key} = {value}")

    if result.applied:
        roster.touch()
    _record(roster, result)
    return result


def apply_roster_snapshot(roster: Roster, data: dict, name: str) -> ImportResult:
    result = ImportResult(name=name, kind="roster-snapshot")
    history = list(roster.history)
    roster.load_dict(data)
    roster.history = history
    roster.touch()
    result.applied.append(f"restored roster snapshot: {len(roster.players)} players, {len(roster.opponents)} opponents")
    _record(roster, result)
    return result


def import_engine_data(data: dict, filename: str, name: str, engine_dir: Path | None = None,
                       backup_dir: Path | None = None) -> ImportResult:
    """Install an engine data snapshot and flush every cache that depends on it."""
    engine_dir = engine_dir or ENGINE_DIR
    backup_dir = backup_dir or (VAR_DIR / "backups")
    result = ImportResult(name=name, kind="engine-data")
    target = engine_dir / "data" / filename
    if filename == "league_lane_matchups.json" and not isinstance(data.get("matchups"), dict):
        result.errors.append("matchup snapshot has no 'matchups' object")
        return result
    if filename == "league_comp_meta.json" and not isinstance(data.get("champion_traits"), dict):
        result.errors.append("meta snapshot has no 'champion_traits' object")
        return result
    try:
        if target.is_file():
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(target, backup_dir / f"{target.stem}.{stamp}{target.suffix}")
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        result.errors.append(f"could not write {target.name}: {exc}")
        return result
    flush_engine_caches()
    result.data_changed = True
    generated = data.get("generated_at") or ""
    result.applied.append(f"installed {filename}" + (f" (generated {generated})" if generated else ""))
    return result


def flush_engine_caches() -> None:
    for attr in dir(engine):
        value = getattr(engine, attr, None)
        if hasattr(value, "cache_clear"):
            try:
                value.cache_clear()
            except Exception:  # noqa: BLE001
                pass
    try:
        engine.clear_player_profile_caches()
    except Exception:  # noqa: BLE001
        pass
    engine_cache.clear()


def signups_to_loadfile(rows: list[dict], source: str = "discord-signups") -> dict:
    """Discord `/league` signup rows (the shape `league_flex_core` consumes, also what the live
    bridge caches in ``data/live_flex_signups.json``) as a replace-mode loadfile."""
    players = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("user_id") is None:
            continue
        priority = {}
        for role, level in (row.get("role_priority") or {}).items():
            canon = role_name(role)
            if canon and str(level).lstrip("-").isdigit():
                priority[canon] = int(level)
        roles = [r for r in (role_name(x) for x in (row.get("roles") or [])) if r]
        roles = sorted(dict.fromkeys(roles), key=lambda r: (priority.get(r, 9), roles.index(r)))
        pools = {}
        for role, names in (row.get("champions") or {}).items():
            canon = role_name(role)
            if canon and names:
                pools[canon] = [{"champion": str(n)} for n in names]
        players.append({"user_id": int(row["user_id"]), "name": str(row.get("display_name") or row["user_id"]),
                        "roles": roles, "role_priority": priority, "pools": pools})
    return {"format": LOADFILE_FORMAT, "version": 1, "mode": "replace", "source": source, "players": players}


def import_document(roster: Roster, data: dict, name: str, **engine_kwargs) -> ImportResult:
    if isinstance(data, list):
        data = signups_to_loadfile(data, source=name)
    elif isinstance(data, dict) and isinstance(data.get("signups"), list) and "players" not in data:
        data = signups_to_loadfile(data["signups"], source=name)
    kind, filename = detect_kind(data)
    if kind == "loadfile":
        return apply_loadfile(roster, data, name)
    if kind == "roster-snapshot":
        return apply_roster_snapshot(roster, data, name)
    if kind == "engine-data" and filename:
        result = import_engine_data(data, filename, name, **engine_kwargs)
        _record(roster, result)
        return result
    result = ImportResult(name=name, kind="unknown")
    result.errors.append("not a loadfile: expected players/default_pools/opponents/ban_priority keys, "
                         "a CSV with a champion column, or an engine data snapshot")
    _record(roster, result)
    return result


def import_text(roster: Roster, text: str, name: str, **engine_kwargs) -> ImportResult:
    try:
        data = parse_text(text, name)
    except (ValueError, json.JSONDecodeError) as exc:
        result = ImportResult(name=name, kind="unknown")
        result.errors.append(f"could not parse: {exc}")
        _record(roster, result)
        return result
    return import_document(roster, data, name, **engine_kwargs)


def import_path(roster: Roster, path: Path, **engine_kwargs) -> ImportResult:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        result = ImportResult(name=Path(path).name, kind="unknown")
        result.errors.append(f"could not read: {exc}")
        _record(roster, result)
        return result
    return import_text(roster, text, Path(path).name, **engine_kwargs)


def _record(roster: Roster, result: ImportResult) -> None:
    roster.history.append(result.to_dict())
    del roster.history[:-100]


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def export_loadfile(roster: Roster, note: str = "") -> dict:
    """The current roster as a replace-mode loadfile (re-importable anywhere)."""
    players = []
    for p in roster.players.values():
        row: dict[str, Any] = {"name": p.name, "roles": list(p.roles),
                               "pools": {r: [e.to_dict() for e in v] for r, v in p.pools.items()}}
        if p.user_id:
            row["user_id"] = p.user_id
        if p.riot_ids:
            row["riot_ids"] = list(p.riot_ids)
        if p.avoid:
            row["avoid"] = list(p.avoid)
        if p.notes:
            row["notes"] = p.notes
        players.append(row)
    opponents = []
    for o in roster.opponents.values():
        row = {"name": o.name, "roles": list(o.roles), "pools": {r: [e.to_dict() for e in v] for r, v in o.pools.items()}}
        if o.team:
            row["team"] = o.team
        if o.riot_ids:
            row["riot_ids"] = list(o.riot_ids)
        if o.notes:
            row["notes"] = o.notes
        opponents.append(row)
    return {
        "format": LOADFILE_FORMAT, "version": 1, "mode": "replace",
        "note": note or f"exported {time.strftime('%Y-%m-%d %H:%M')}",
        "players": players,
        "default_pools": {r: [e.to_dict() for e in v] for r, v in roster.default_pools.items()},
        "ban_priority": list(roster.ban_priority), "never_pick": list(roster.never_pick),
        "opponents": opponents,
        "lineup": [roster.players[k].name if k and k in roster.players else None for k in roster.lineup],
        "enemy_lineup": [roster.opponents[k].name if k and k in roster.opponents else None for k in roster.enemy_lineup],
        "playbook": [{"id": c["id"], "name": c["name"], "style": c.get("style", ""), "win": c.get("win", ""), "fail": c.get("fail", ""),
                      "readiness": c.get("readiness", ""),
                      "picks": {r: {"player": e.get("player_name") or e.get("player"), "champion": e["champion"], "familiarity": e.get("familiarity")}
                                for r, e in c["picks"].items()},
                      "substitutions": [{"role": x["role"], "champion": x["champion"], "player": (roster.players[x["player"]].name if x.get("player") in roster.players else x.get("player")), "note": x.get("note", "")}
                                        for x in c.get("substitutions", [])]}
                     for c in roster.playbook],
        "settings": dict(roster.settings),
    }


def export_csv(roster: Roster) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["kind", "player", "role", "champion", "priority", "action", "note", "familiarity"])
    for p in roster.players.values():
        for role in ROLES:
            for e in p.pools.get(role, []):
                writer.writerow(["pool", p.name, role, e.champion, e.priority or "", "add", e.note, e.familiarity])
        for c in p.avoid:
            writer.writerow(["avoid", p.name, "", c, "", "add", ""])
    for role in ROLES:
        for e in roster.default_pools.get(role, []):
            writer.writerow(["pool", "", role, e.champion, e.priority or "", "add", e.note])
    for o in roster.opponents.values():
        for role, entries in o.pools.items():
            for e in entries:
                writer.writerow(["scout", o.name, role if role != "Any" else "", e.champion, e.priority or "", "add", e.note])
    for b in roster.ban_priority:
        writer.writerow(["ban", "", "", b["champion"], b.get("weight", 1), "add", b.get("note", "")])
    for c in roster.never_pick:
        writer.writerow(["avoid", "", "", c, "", "add", ""])
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Watched folder
# --------------------------------------------------------------------------- #


class LoadfileWatcher:
    """Polls a folder and imports new or changed loadfiles.

    Processed files are remembered by (path, mtime, size) in ``var/loadfiles.json``
    so a restart does not re-apply them. Files are never moved or deleted.
    """

    def __init__(self, folder: Path, state_path: Path):
        self.folder = Path(folder)
        self.state_path = Path(state_path)
        self.seen: dict[str, list] = {}
        try:
            self.seen = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.seen = {}

    def pending(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        out: list[Path] = []
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path.suffix.casefold() not in (".json", ".csv") or path.name.startswith((".", "_")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            stamp = [stat.st_mtime, stat.st_size]
            if self.seen.get(str(path)) == stamp:
                continue
            out.append(path)
        return out

    def mark(self, path: Path) -> None:
        try:
            stat = path.stat()
            self.seen[str(path)] = [stat.st_mtime, stat.st_size]
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.seen), encoding="utf-8")
        except OSError:
            pass
