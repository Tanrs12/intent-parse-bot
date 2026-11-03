"""Any-queue champion-select assistance: enemy pick prediction, comfort-first picks, swap plans.

These work with strangers on both teams (Solo/Duo, normals), where the roster knows nobody:

* ``predict_enemy``   - for each enemy role still open, the champions the enemy is likeliest to pick:
  the solo-queue pick rate in that role, nudged by what their bans say. A ban is weak evidence: a
  player who bans the lane counter of champion X makes X a little likelier. Shown as "possible picks",
  never as a probability we can stand behind.
* ``comfort_picks``   - the champions this seat should consider for its role, ranked by role win rate
  but dominated by what the player actually plays (mastery, recent games, submitted pool).
* ``swap_plan``       - which position swap / pick-order swap moves this seat towards the role and
  pick slot the team chose, and whether an incoming swap request moves us there.

``data/league_soloq_tierlist.json`` (tools/refresh_soloq_tierlist.py) carries the role statistics.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

from . import ENGINE_DIR
from . import champions as champ_map

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
TIERLIST_PATH = Path(ENGINE_DIR) / "data" / "league_soloq_tierlist.json"
MIN_ROLE_PICK = 0.5           # % of the role's games: below this a champion is not "played there"
BAN_SIGNAL_CAP = 0.6          # bans can lift a champion's prior by at most 60%
COMFORT_WEIGHT = 8.0          # points for a fully familiar champion (win-rate deltas are ~±3)
POOL_BONUS = 5.0              # submitted to the roster for this role: beats a ~4-point win-rate gap
RECENT_BONUS = 1.5            # played in the last 30 days

_lock = threading.Lock()
_tierlist: dict | None = None


def load_tierlist(path: Path | None = None) -> dict:
    """{"patch", "roles": {role: {champion: {win, pick, ban, games}}}}; empty roles when missing."""
    global _tierlist
    if path is not None:
        return _read(path)
    with _lock:
        if _tierlist is None:
            _tierlist = _read(TIERLIST_PATH)
        return _tierlist


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"patch": None, "roles": {}}
    roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    return {"patch": data.get("patch"), "tier": data.get("tier"), "roles": roles}


def _row(tier: dict, role: str, champion: str) -> dict:
    return ((tier.get("roles") or {}).get(role) or {}).get(champion) or {}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _advantage(matchups: dict, role: str, enemy: str, response: str) -> float:
    """How much ``response`` beats ``enemy`` in ``role`` (win-rate points; 0 when unknown)."""
    rows = (((matchups.get("matchups") or {}).get(role) or {}).get(enemy) or {}).get("responses") or {}
    return _num((rows.get(response) or {}).get("response_advantage"))


# --------------------------------------------------------------------------- #
# Enemy prediction
# --------------------------------------------------------------------------- #


def predict_enemy(state, tier: dict, matchups: dict, *, per_role: int = 3) -> list[dict]:
    """Likely enemy picks for each enemy role that is still open.

    ``state`` is a DraftState. Enemy roles count as filled only when an enemy champion is locked
    with a known role; enemy hovers are never visible to us."""
    unavailable = state.unavailable()
    filled = {p.role for p in state.enemy_team if p.locked and p.role in ROLES and confident_lane(p, tier)}
    enemy_bans = list(dict.fromkeys(state.enemy_bans))
    out = []
    for role in ROLES:
        if role in filled:
            continue
        rows = (tier.get("roles") or {}).get(role) or {}
        scored = []
        for champion, row in rows.items():
            pick = _num(row.get("pick"))
            if champion in unavailable or pick < MIN_ROLE_PICK:
                continue
            # Every enemy ban that answers this champion in its lane is a hint someone plays it.
            hints = [b for b in enemy_bans if _advantage(matchups, role, champion, b) > 0]
            lift = min(BAN_SIGNAL_CAP, sum(_advantage(matchups, role, champion, b) for b in hints) / 10.0)
            scored.append((pick * (1.0 + lift), champion, pick, hints, _num(row.get("win"), 50.0)))
        total = sum(s[0] for s in scored) or 1.0
        for weight, champion, pick, hints, win in sorted(scored, key=lambda s: (-s[0], s[1]))[:per_role]:
            why = f"{pick:.1f}% of {role} games"
            if hints:
                why += "; their ban of " + ", ".join(hints[:2]) + " protects it"
            out.append({"role": role, "champion": champion, "champion_id": champ_map.id_for(champion),
                        "share": round(weight / total, 3), "pick_rate": pick, "win_rate": win,
                        "ban_hints": hints, "why": why})
    return out


CONFIDENT_LANE_SHARE = 0.75   # an inferred lane counts when 75%+ of the champion's games are played there


def confident_lane(slot, tier: dict | None = None) -> bool:
    """Is this enemy's role known, not just a guess? Client/config roles are; an inferred role is
    only when the champion is overwhelmingly played in that lane (Garen top yes, Pantheon no)."""
    if slot.role not in ROLES:
        return False
    if slot.role_source not in ("inferred", "guessed"):
        return True
    if slot.role_source == "guessed" or not slot.locked:
        return False
    roles = (tier or load_tierlist()).get("roles") or {}
    games = {r: _num(((roles.get(r) or {}).get(slot.locked) or {}).get("games")) for r in ROLES}
    total = sum(games.values())
    return total > 0 and games.get(slot.role, 0.0) / total >= CONFIDENT_LANE_SHARE


# --------------------------------------------------------------------------- #
# Comfort-first picks
# --------------------------------------------------------------------------- #


def comfort_from_mastery(rows: list[dict] | None, now_ms: float | None = None) -> dict[str, dict]:
    """champion -> {"comfort": 0..1, "points", "recent"} from ``/lol-champion-mastery`` rows."""
    import time

    now_ms = now_ms if now_ms is not None else time.time() * 1000
    out: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = champ_map.name_for(int(_num(row.get("championId"))))
        points = _num(row.get("championPoints"))
        if not name or points <= 0:
            continue
        # 1k points ~ 0.33, 10k ~ 0.67, 100k+ ~ 1.0: a few games already count, mains saturate.
        comfort = max(0.0, min(1.0, (math.log10(points) - 2.0) / 3.0))
        recent = now_ms - _num(row.get("lastPlayTime")) <= 30 * 86_400_000
        out[name] = {"comfort": round(comfort, 3), "points": int(points), "recent": bool(recent)}
    return out


def comfort_picks(role: str, unavailable: set[str], tier: dict, matchups: dict, *,
                  comfort: dict[str, dict] | None = None, pool: list[str] | None = None,
                  enemy_laner: str | None = None, avoid: set[str] | None = None, limit: int = 6) -> list[dict]:
    """Champions for ``role`` ranked by win rate, skewed heavily towards what this player plays."""
    comfort = comfort or {}
    pool = [c for c in (pool or []) if c]
    blocked = set(unavailable) | set(avoid or ())
    rows = (tier.get("roles") or {}).get(role) or {}
    candidates = set(pool)
    candidates |= {c for c in comfort if _num(_row(tier, role, c).get("pick")) >= MIN_ROLE_PICK}
    candidates |= {c for c, r in rows.items() if _num(r.get("pick")) >= 1.0}
    out = []
    for champion in candidates - blocked:
        row = rows.get(champion) or {}
        win = _num(row.get("win"), 50.0)
        mine = comfort.get(champion) or {}
        score = (win - 50.0) + COMFORT_WEIGHT * _num(mine.get("comfort"))
        reasons = []
        if champion in pool:
            score += POOL_BONUS
            reasons.append("in your pool")
        if mine:
            reasons.append(f"{mine['points']:,} mastery")
            if mine.get("recent"):
                score += RECENT_BONUS
                reasons.append("played recently")
        if row:
            reasons.append(f"{win:.1f}% win as {role}")
        lane = 0.0
        if enemy_laner:
            lane = _advantage(matchups, role, enemy_laner, champion)
            score += lane / 2.0
            if lane:
                reasons.append(f"{'+' if lane > 0 else ''}{lane:.1f} vs {enemy_laner}")
        out.append({"role": role, "champion": champion, "champion_id": champ_map.id_for(champion),
                    "score": round(score, 2), "win_rate": win, "comfort": _num(mine.get("comfort")),
                    "in_pool": champion in pool, "lane_edge": round(lane, 2), "why": reasons})
    out.sort(key=lambda r: (-r["score"], r["champion"]))
    return out[:limit]


# --------------------------------------------------------------------------- #
# Swap plan
# --------------------------------------------------------------------------- #


def _swap_entry(entries: list[dict] | None, cell: int) -> dict | None:
    for entry in entries or []:
        try:
            if int(entry.get("cellId", -1)) == cell:
                return entry
        except (TypeError, ValueError):
            continue
    return None


def _state_of(entry: dict | None) -> str:
    return str((entry or {}).get("state") or "").upper()


def swap_plan(session: dict, target_role: str | None, target_pick_slot: int | None,
              team_targets: dict[int, tuple] | None = None) -> dict:
    """What this client should do to reach ``target_role`` and pick slot ``target_pick_slot``.

    ``team_targets`` ({cell: (planned role, planned pick slot)} for teammates, from the team draft)
    lets this seat also accept a request that puts the requester in THEIR planned place while we are
    not yet in ours. Without it a three-way rotation deadlocks: each seat only accepts the one swap
    that lands it exactly, and none exists. Every such accept seats one player for good, so it ends.

    ``session`` is the raw champ-select session. ``target_pick_slot`` is the 0-based index among our
    team's picks (0 = first pick). Returns {"position": {...}|None, "pick_order": {...}|None,
    "accept": [...], "decline": [...]} where each action names the swap id and endpoint family."""
    local = session.get("localPlayerCellId")
    plan: dict = {"position": None, "pick_order": None, "accept": [], "decline": [], "target_role": target_role,
                  "target_pick_slot": target_pick_slot, "current_role": None, "current_pick_slot": None}
    try:
        local = int(local)
    except (TypeError, ValueError):
        return plan
    team = [m for m in session.get("myTeam") or [] if isinstance(m, dict)]
    role_of = {}
    for member in team:
        from .state import role_from_position

        try:
            role_of[int(member.get("cellId", -1))] = role_from_position(member.get("assignedPosition"))
        except (TypeError, ValueError):
            continue
    pick_cells = []
    for group in session.get("actions") or []:
        for action in group or []:
            if isinstance(action, dict) and action.get("type") == "pick" and action.get("isAllyAction", True):
                try:
                    cell = int(action.get("actorCellId", -1))
                except (TypeError, ValueError):
                    continue
                if cell in role_of and cell not in pick_cells:
                    pick_cells.append(cell)
    current_role = role_of.get(local)
    current_slot = pick_cells.index(local) if local in pick_cells else None
    plan["current_role"], plan["current_pick_slot"] = current_role, current_slot
    position_swaps = session.get("positionSwaps") or []
    order_swaps = session.get("pickOrderSwaps") or []

    if target_role in ROLES and current_role and current_role != target_role:
        holder = next((c for c, r in role_of.items() if r == target_role and c != local), None)
        entry = _swap_entry(position_swaps, holder) if holder is not None else None
        if entry is not None:
            plan["position"] = {"swap_id": entry.get("id"), "with_cell": holder, "state": _state_of(entry),
                                "from_role": current_role, "to_role": target_role, "family": "position-swaps"}
    if target_pick_slot is not None and current_slot is not None and 0 <= target_pick_slot < len(pick_cells) \
            and target_pick_slot != current_slot:
        holder = pick_cells[target_pick_slot]
        entry = _swap_entry(order_swaps, holder)
        if entry is not None:
            plan["pick_order"] = {"swap_id": entry.get("id"), "with_cell": holder, "state": _state_of(entry),
                                  "from_slot": current_slot, "to_slot": target_pick_slot, "family": "pick-order-swaps"}

    # Incoming requests: accept the ones that move us where we want to be, decline the rest only
    # when we have a target (a stranger's request is otherwise left to the player).
    for family, entries in (("position-swaps", position_swaps), ("pick-order-swaps", order_swaps)):
        for entry in entries:
            if _state_of(entry) != "RECEIVED":
                continue
            try:
                cell = int(entry.get("cellId", -1))
            except (TypeError, ValueError):
                continue
            their_role, their_slot = (team_targets or {}).get(cell, (None, None))
            if family == "position-swaps":
                wanted, has_target = role_of.get(cell) == target_role, target_role in ROLES
                helps = has_target and current_role != target_role and their_role is not None and their_role == current_role
            else:
                wanted = cell in pick_cells and pick_cells.index(cell) == target_pick_slot
                has_target = target_pick_slot is not None
                helps = has_target and current_slot != target_pick_slot and their_slot is not None and their_slot == current_slot
            if wanted or helps:
                plan["accept"].append({"family": family, "swap_id": entry.get("id"), "with_cell": cell})
            elif has_target:
                plan["decline"].append({"family": family, "swap_id": entry.get("id"), "with_cell": cell})
    return plan


VERBS_BY_STATE = {"AVAILABLE": ("request",), "SENT": ("cancel",), "RECEIVED": ("accept", "decline")}


def swap_offer_rows(session: dict, offers: dict | None, names: dict | None = None) -> list[dict]:
    """Every role / pick-order swap the client offers this seat, as buttons for the overlay.

    ``offers`` is {"position-swaps": [...], "pick-order-swaps": [...]} from the client (the session's
    own ``positionSwaps`` / ``pickOrderSwaps`` win when present); ``names`` maps cell -> display name.
    Each row says who it is with, what we would get, and which verbs the offer's state allows."""
    from .state import role_from_position

    names = names or {}
    team = [m for m in session.get("myTeam") or [] if isinstance(m, dict)]
    role_of, pick_cells = {}, []
    for member in team:
        try:
            role_of[int(member.get("cellId", -1))] = role_from_position(member.get("assignedPosition"))
        except (TypeError, ValueError):
            continue
    for group in session.get("actions") or []:
        for action in group or []:
            if isinstance(action, dict) and action.get("type") == "pick" and action.get("isAllyAction", True):
                try:
                    cell = int(action.get("actorCellId", -1))
                except (TypeError, ValueError):
                    continue
                if cell in role_of and cell not in pick_cells:
                    pick_cells.append(cell)
    sources = {"position-swaps": session.get("positionSwaps"), "pick-order-swaps": session.get("pickOrderSwaps")}
    rows = []
    for family in ("position-swaps", "pick-order-swaps"):
        entries = sources[family] if sources[family] is not None else (offers or {}).get(family) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            state = _state_of(entry)
            verbs = VERBS_BY_STATE.get(state)
            try:
                cell, swap_id = int(entry.get("cellId", -1)), int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if not verbs or cell not in role_of:
                continue
            row = {"family": family, "swap_id": swap_id, "cell": cell, "state": state, "verbs": list(verbs),
                   "name": names.get(cell) or f"Seat {cell + 1}", "role": role_of.get(cell)}
            if family == "pick-order-swaps":
                row["pick_slot"] = pick_cells.index(cell) if cell in pick_cells else None
            rows.append(row)
    return rows
