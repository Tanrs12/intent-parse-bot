"""The `/league game` brain, driven from live champion select.

The desktop app does not rank compositions itself: it feeds the seated five, in the same signup
shape the Discord command uses, to ``league_draft_backend.recommend_live_drafts`` and re-calls it
after every pick or ban with the updated unavailable set and our locked champions. Role
assignment, composition scoring, draft diversity, role alternatives, roster-real pick order and
counter bans all come back from the repository's own code (see FLEXERS_DRAFT_COACH.md).

What this module adds is only the translation from the overlay's seats and roster to signups.
"""
from __future__ import annotations

import logging
from typing import Any

from .roster import ROLES

log = logging.getLogger(__name__)

try:  # the brain lives at the repository root (or _internal/engine in the packaged build)
    from league_draft_backend import recommend_live_drafts as _recommend_live_drafts
except Exception as exc:  # noqa: BLE001
    _recommend_live_drafts = None
    log.warning("shared draft backend unavailable, falling back to the engine search: %s", exc)


def available() -> bool:
    return _recommend_live_drafts is not None


def signup_for_seat(slot: Any, role: str, seat_pool: list[str], roster: Any, user_id: int) -> dict:
    """One seat as a `/league game` signup: every role the player has a prepared pool for, with
    the seat's role first, plus role priorities from the roster's role order."""
    player = None
    if roster is not None and getattr(slot, "player_key", None):
        player = roster.players.get(slot.player_key)
    champions: dict[str, list[str]] = {}
    roles: list[str] = []
    if player is not None:
        for r in ROLES:
            names = [e.champion for e in player.pools.get(r, [])]
            if names:
                champions[r] = names
        roles = [r for r in player.roles if r in ROLES]
    # The seat's own role always carries the pool the overlay resolved for it (loadfile, history
    # fallback or meta), so a seat without a prepared pool can still be assigned.
    champions[role] = list(dict.fromkeys(list(seat_pool) + champions.get(role, [])))
    if role not in roles:
        roles = [role] + roles
    role_priority = {r: min(3, i + 1) for i, r in enumerate(roles[:3])}
    role_priority.setdefault(role, 1)
    return {
        "user_id": int(user_id),
        "display_name": getattr(slot, "display_name", None) or (player.name if player is not None else role),
        "roles": roles,
        "role_priority": role_priority,
        "champions": champions,
    }


def recommend(signups: list[dict], *, unavailable: set[str], locked: dict[str, str], role_players: dict[str, int],
              side: str, limit: int, rotation_token: str | None = None) -> dict | None:
    """Call the shared backend; None when it is unavailable or raised."""
    if _recommend_live_drafts is None:
        return None
    try:
        return _recommend_live_drafts(signups, unavailable=unavailable, locked_champions=locked, role_players=role_players,
                                      side=side, limit=limit, rotation_token=rotation_token)
    except Exception as exc:  # noqa: BLE001
        log.warning("shared draft backend failed, falling back to the engine search: %s", exc)
        return None


def as_comps(result: dict | None) -> list[dict]:
    """Backend recommendations in the shape the advisor's composition list already uses."""
    if not result:
        return []
    comps = []
    for rec in result.get("recommendations") or []:
        row = dict(rec)
        row.setdefault("rank_score", float(row.get("score", 0.0)))
        row.setdefault("strengths", row.get("notes", [])[:3] if isinstance(row.get("notes"), list) else [])
        row.setdefault("gaps", [])
        row["backend"] = True
        comps.append(row)
    return comps


SEED_MARKER = "live_flex_signups.seeded"


def signup_rows_from_roster(roster: Any) -> list[dict]:
    """The roster's players with a Discord id, in the signup shape the bridge and the cog use."""
    rows = []
    for player in roster.players.values():
        if not player.user_id:
            continue
        roles = [r for r in player.roles if r in ROLES]
        champions = {r: [e.champion for e in player.pools.get(r, [])] for r in ROLES if player.pools.get(r)}
        if not champions:
            continue
        rows.append({"user_id": int(player.user_id), "display_name": player.name, "roles": roles,
                     "role_priority": {r: min(3, i + 1) for i, r in enumerate(roles[:3])}, "champions": champions})
    return rows


def write_signup_cache(roster: Any, path: Any) -> bool:
    """Write ``data/live_flex_signups.json`` from the roster unless Northflank (the bridge) owns it.

    The bridge refreshes that file from the deployed bot's database when the Northflank CLI is
    available. On every other machine the roster (team loadfile, `/league coach` export) is the
    source, and a marker file records that the cache was seeded here so it may be rewritten."""
    import json
    from pathlib import Path as _Path

    path = _Path(path)
    marker = path.with_name(SEED_MARKER)
    if path.exists() and not marker.exists():
        return False                      # written by the bridge from the real signups: leave it
    rows = signup_rows_from_roster(roster)
    if not rows:
        return False
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        marker.write_text("seeded from the Draft Coach roster\n", encoding="utf-8")
    except OSError:
        return False
    return True
