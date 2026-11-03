"""Pre-game drafts inside the app: pick five, build the drafts, send one to the team.

Nothing here needs Discord. The players come from the same signup cache the bridge keeps
(``data/live_flex_signups.json``: the pools and 1/2/3 role priorities people set themselves, seeded
from the roster when the bot's database is out of reach) and the drafts come from
``league_draft_backend.plan_game``, which is the ``/league game`` pipeline unchanged. A chosen draft
is pushed to the team through the bridge's shared control, exactly like a draft locked in Discord.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import ENGINE_DIR, backend
from .roster import ROLES

log = logging.getLogger(__name__)

try:  # the brain lives at the repository root (or _internal/engine in the packaged build)
    from league_draft_backend import plan_game as _plan_game
except Exception as exc:  # noqa: BLE001
    _plan_game = None
    log.warning("pre-game planner unavailable: %s", exc)

SIGNUP_CACHE = ENGINE_DIR / "data" / "live_flex_signups.json"
MAX_UNAVAILABLE = 20


class PlanError(ValueError):
    """A request the planner cannot act on; the message is meant for the player."""


def available() -> bool:
    return _plan_game is not None


def load_signups(roster: Any, path: Path | str | None = None) -> list[dict]:
    """Every player the app can draft for, in the signup shape ``/league game`` uses.

    The Discord signup cache wins for a player it knows (those are the pools and priorities the
    player set); roster players the cache does not know fall back to their seeded roster pools.
    """
    rows: dict[int, dict] = {}
    target = Path(path) if path else SIGNUP_CACHE
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = []
    for row in raw if isinstance(raw, list) else []:
        # a row whose pools are all empty is no row at all: the roster's pools may still cover the player
        if not isinstance(row, dict) or not row.get("user_id") or not any((row.get("champions") or {}).values()):
            continue
        try:
            rows[int(row["user_id"])] = dict(row)
        except (TypeError, ValueError):
            continue
    if roster is not None:
        for row in backend.signup_rows_from_roster(roster):
            rows.setdefault(int(row["user_id"]), row)
    return sorted(rows.values(), key=lambda r: str(r.get("display_name") or "").casefold())


def players_summary(signups: list[dict]) -> list[dict]:
    """What the page needs to offer a player: name, roles and how deep each role's pool is."""
    out = []
    for row in signups:
        champions = row.get("champions") or {}
        roles = [r for r in (row.get("roles") or []) if r in ROLES]
        out.append({
            "user_id": str(int(row["user_id"])),      # a string: see _js_safe
            "display_name": str(row.get("display_name") or row["user_id"]),
            "roles": roles,
            "pools": {r: len(champions.get(r) or []) for r in ROLES if champions.get(r)},
        })
    return out


def clean_unavailable(raw: Any) -> list[str]:
    """Champion names typed or sent by the page: trimmed, de-duplicated, bounded."""
    if isinstance(raw, str):
        raw = raw.replace("\n", ",").split(",")
    names: list[str] = []
    for value in raw or []:
        name = str(value or "").strip()[:40]
        if name and name not in names:
            names.append(name)
    return names[:MAX_UNAVAILABLE]


JS_SAFE_INT = 2 ** 53


def _js_safe(value: Any) -> Any:
    """Discord ids are bigger than a JavaScript number can hold exactly: the browser would round
    139837324438536193 to ...192 and send back an id nobody has. Big ints travel as strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) >= JS_SAFE_INT else value
    if isinstance(value, dict):
        return {str(k): _js_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_js_safe(v) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    """The engine's records may carry sets and tuples; the page gets plain, JavaScript-safe JSON."""
    from .hub import _json_default
    return _js_safe(json.loads(json.dumps(value, default=_json_default)))


class GamePlanner:
    """Builds and remembers the current pre-game plan for this app."""

    def __init__(self) -> None:
        self._lock = threading.Lock()        # one calculation at a time: the engine caches are per thread
        self.last: dict | None = None

    def snapshot(self, roster: Any) -> dict:
        signups = load_signups(roster)
        return {"available": available(), "players": players_summary(signups), "result": self.last}

    def build_sync(self, roster: Any, user_ids: list[Any], *, unavailable: Any = None, token: Any = None) -> dict:
        if _plan_game is None:
            raise PlanError("The draft engine is not available in this build.")
        try:
            wanted = [int(uid) for uid in (user_ids or [])]
        except (TypeError, ValueError) as exc:
            raise PlanError("Player ids must be numbers.") from exc
        if len(wanted) != 5 or len(set(wanted)) != 5:
            raise PlanError("Pick exactly five different players.")
        signups = load_signups(roster)
        by_id = {int(row["user_id"]): row for row in signups}
        missing = [uid for uid in wanted if uid not in by_id]
        if missing:
            # name the player when a previous plan knew them; otherwise the id is all there is
            known = {str(p.get("user_id")): str(p.get("display_name") or "") for p in (self.last or {}).get("players") or []}
            who = ", ".join(known.get(str(uid)) or f"id {uid}" for uid in missing)
            raise PlanError(f"No champion pools for {who}: not in the signup list any more. Unpick and choose again.")
        five = [by_id[uid] for uid in wanted]
        unavailable_names = clean_unavailable(unavailable)
        rotation = str(token).strip()[:40] if token not in (None, "", 0, "0") else None
        started = time.perf_counter()
        with self._lock:
            result = _plan_game(five, unavailable=unavailable_names, rotation_token=rotation)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        payload = {
            "ok": True,
            "players": [{"user_id": int(row["user_id"]), "display_name": row.get("display_name")} for row in five],
            "unavailable": unavailable_names,
            "token": rotation,
            "built_at": time.time(),
            "elapsed_ms": elapsed_ms,
            **_jsonable(result),
        }
        payload = _js_safe(payload)
        self.last = payload
        log.info("game plan: %d drafts for %s in %d ms", len(payload.get("recommendations") or []),
                 ", ".join(str(p["display_name"]) for p in payload["players"]), elapsed_ms)
        return payload

    async def build(self, roster: Any, user_ids: list[Any], *, unavailable: Any = None, token: Any = None) -> dict:
        return await asyncio.to_thread(self.build_sync, roster, user_ids, unavailable=unavailable, token=token)
