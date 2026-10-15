"""Normalised draft state.

Three producers feed the same ``DraftState``:

* ``state_from_lcu_session``  - the League client's champ-select session JSON
* ``state_from_manual_draft`` - the coach panel's click-driven board
* ``state_from_payload``      - a simple REST shape for other tools (Discord bot)

The advisor only ever sees a ``DraftState``, and the roster is applied here so
every slot carries its player's pools, priorities and avoid list.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from . import ENGINE_DIR
from . import champions as champ_map
from .draft_order import ManualDraft
from .roster import Opponent, Player, Roster, role_name

import league_comp_engine as engine  # type: ignore  # noqa: E402

ROLES = list(engine.ROLES)
LCU_POSITION_TO_ROLE = {"top": "Top", "jungle": "Jungle", "middle": "Mid", "mid": "Mid", "bottom": "ADC",
                        "adc": "ADC", "bot": "ADC", "utility": "Support", "support": "Support"}


def role_from_position(value: str | None) -> str | None:
    if not value:
        return None
    return LCU_POSITION_TO_ROLE.get(str(value).strip().casefold())


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class PlayerSlot:
    """One of the five seats on a team."""

    cell_id: int
    display_name: str = ""
    role: str | None = None
    role_source: str = "unknown"          # lcu / config / inferred / manual / unknown
    user_id: int | None = None
    puuid: str | None = None
    riot_id: str | None = None
    locked: str | None = None
    hover: str | None = None
    pool: dict[str, list[str]] = field(default_factory=dict)         # role -> champions (ordered by priority)
    priorities: dict[str, dict[str, int]] = field(default_factory=dict)
    familiarity: dict[str, dict[str, str]] = field(default_factory=dict)   # role -> champion -> comfort/refresh/developing
    avoid: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)
    is_local: bool = False
    pool_source: str = "none"              # player / default / none
    player_key: str | None = None
    scouted: dict | None = None            # enemy slots: {key, name, team, pools: {role: [champions]}}


@dataclass
class ActiveAction:
    kind: str                              # ban | pick | none
    ours: bool
    cell_ids: list[int]
    action_ids: dict[int, int] = field(default_factory=dict)   # cell -> client action id (client sessions only)


@dataclass
class DraftState:
    side: str = "blue"
    phase: str = "IDLE"                    # IDLE | PLANNING | BAN_PICK | FINALIZATION | GAME_STARTING
    time_left_ms: int = 0
    pick_number: int = 0
    step_index: int | None = None          # position in the 20-step tournament sequence when known
    our_bans: list[str] = field(default_factory=list)
    enemy_bans: list[str] = field(default_factory=list)
    our_team: list[PlayerSlot] = field(default_factory=list)
    enemy_team: list[PlayerSlot] = field(default_factory=list)
    active: ActiveAction = field(default_factory=lambda: ActiveAction("none", False, []))
    our_pick_cells: list[int] = field(default_factory=list)
    enemy_pick_cells: list[int] = field(default_factory=list)
    actions_remaining: list[dict] = field(default_factory=list)   # [{kind, ours, cell_id}]
    trades: list[dict] = field(default_factory=list)
    source: str = "manual"                 # lcu | manual | rest | simulation
    counter: int = 0
    game_id: int | None = None
    notes: list[str] = field(default_factory=list)
    never_pick: list[str] = field(default_factory=list)
    draft_type: str = "tournament"
    series_unavailable: list[str] = field(default_factory=list)   # fearless: used earlier in the series
    deadline: float | None = None       # unix time when the current phase timer runs out (set when ingested)
    local_cell_id: int | None = None    # the seat this machine's League client controls

    def unavailable(self) -> set[str]:
        taken = set(self.our_bans) | set(self.enemy_bans) | set(self.series_unavailable)
        taken |= {p.locked for p in self.our_team if p.locked}
        taken |= {p.locked for p in self.enemy_team if p.locked}
        return taken

    def our_locked(self) -> dict[str, str]:
        return {p.role: p.locked for p in self.our_team if p.locked and p.role}

    def enemy_locked(self) -> dict[str, str]:
        return {p.role: p.locked for p in self.enemy_team if p.locked and p.role}

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Role inference
# --------------------------------------------------------------------------- #

_ROLE_CHAMPIONS: dict[str, set[str]] | None = None


def _role_champions() -> dict[str, set[str]]:
    global _ROLE_CHAMPIONS
    if _ROLE_CHAMPIONS is None:
        path = ENGINE_DIR / "data" / "league_role_champions.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _ROLE_CHAMPIONS = {role: set(v) for role, v in data.get("roles", {}).items()}
    return _ROLE_CHAMPIONS


def reset_role_cache() -> None:
    global _ROLE_CHAMPIONS, _META_CACHE
    _ROLE_CHAMPIONS = None
    _META_CACHE = None


def role_candidates(champion: str, meta: dict | None = None) -> list[tuple[str, float]]:
    """Roles a champion is played in, strongest evidence first."""
    meta = meta or engine.load_meta()
    traits = meta.get("champion_traits", {}).get(champion, {})
    positions = {str(p).upper() for p in traits.get("positions", [])}
    role_map = _role_champions()
    scores: dict[str, float] = {}
    for role in ROLES:
        weight = 0.0
        pro_games = float(meta.get("role_stats", {}).get(role, {}).get(champion, {}).get("games", 0) or 0)
        weight += math.log1p(pro_games) * 2.0
        if champion in role_map.get(role, set()):
            weight += 3.0
        lcu_pos = {"Top": "TOP", "Jungle": "JUNGLE", "Mid": "MIDDLE", "ADC": "BOTTOM", "Support": "UTILITY"}[role]
        if lcu_pos in positions:
            weight += 2.5
        if weight > 0:
            scores[role] = weight
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def infer_roles(team: list[PlayerSlot], meta: dict | None = None, *, fill_blank: bool = True) -> None:
    """Fill in missing roles from locked/hovered champions, one seat per role.

    ``fill_blank=False`` leaves seats with no champion evidence at ``role=None`` (used for the
    enemy team: a guessed role must not become evidence about which lanes are closed).
    """
    meta = meta or engine.load_meta()
    taken = {p.role for p in team if p.role}
    open_cells = [p for p in team if not p.role]
    if not open_cells:
        return
    options: list[tuple[float, int, str]] = []
    for p in open_cells:
        champion = p.locked or p.hover
        if not champion:
            continue
        for role, weight in role_candidates(champion, meta):
            if role not in taken:
                options.append((weight, p.cell_id, role))
    options.sort(reverse=True)
    assigned: set[int] = set()
    for weight, cell_id, role in options:
        if role in taken or cell_id in assigned:
            continue
        slot = next(p for p in team if p.cell_id == cell_id)
        slot.role, slot.role_source = role, "inferred"
        taken.add(role)
        assigned.add(cell_id)
    if not fill_blank:
        return
    leftover = [r for r in ROLES if r not in taken]
    for p in open_cells:
        if p.role or not leftover:
            continue
        p.role, p.role_source = leftover.pop(0), "inferred"


def resolve_locked_swaps(team: list[PlayerSlot], meta: dict | None = None) -> tuple[dict[int, str], list[dict]]:
    """Work out which locked champions are really headed for a teammate's role.

    In organised play a player often locks a champion for a teammate and they
    trade after the draft. Returns ``cell_id -> effective champion`` with
    implausible locks moved to the seat whose role fits, plus a description of
    each expected move. Only champions with no evidence in their seat's role may
    move, so genuine flex picks are never "optimised".
    """
    meta = meta or engine.load_meta()
    locked = [p for p in team if p.locked and p.role in ROLES]
    literal = {p.cell_id: p.locked for p in locked}

    def weight(champion: str, role: str) -> float:
        return dict(role_candidates(champion, meta)).get(role, 0.0)

    movable = [p for p in locked if weight(p.locked, p.role) < 1.0]
    if not movable:
        return literal, []
    open_cells = [p for p in team if not p.locked and p.role in ROLES]
    targets = movable + open_cells
    champs = [p.locked for p in movable]
    best_cells, best_score = None, sum(weight(p.locked, p.role) for p in movable)
    for cells in itertools.permutations(targets, len(champs)):
        if list(cells) == movable:
            continue
        score = sum(weight(champion, cell.role) for champion, cell in zip(champs, cells))
        if score > best_score + 4.0:
            best_cells, best_score = cells, score
    if best_cells is None:
        return literal, []
    effective = {p.cell_id: p.locked for p in locked if p not in movable}
    swaps = []
    for champion, cell, origin in zip(champs, best_cells, movable):
        effective[cell.cell_id] = champion
        if cell is not origin:
            swaps.append({"champion": champion, "from_cell": origin.cell_id, "from_role": origin.role,
                          "to_cell": cell.cell_id, "to_role": cell.role, "from_player": origin.display_name,
                          "to_player": cell.display_name})
    return effective, swaps


# --------------------------------------------------------------------------- #
# Roster application
# --------------------------------------------------------------------------- #


_META_CACHE: dict | None = None


def _meta() -> dict:
    global _META_CACHE
    if _META_CACHE is None:
        _META_CACHE = engine.load_meta()
    return _META_CACHE


def history_pools(user_id: int, limit: int = 10) -> dict[str, list[str]]:
    """The engine's account-history pool per role for a profiled player (empty roles omitted)."""
    out: dict[str, list[str]] = {}
    meta = _meta()
    for role in ROLES:
        try:
            champions = engine._historical_pool(int(user_id), role, meta, limit=limit)
        except Exception:  # noqa: BLE001
            champions = []
        champions = [c for c in champions if c not in engine.DISPLAY_EXCLUDED_CHAMPIONS]
        if champions:
            out[role] = champions
    return out


def apply_player(slot: PlayerSlot, player: Player | None, roster: Roster) -> None:
    """Attach a roster player's identity, pools and priorities to a seat.

    A player without a submitted pool but with an engine profile drafts from
    their account history (``pool_source == "history"``), so the lobby, the
    board and the advice all show the same champions.
    """
    if player is not None:
        slot.player_key = player.key
        slot.user_id = player.user_id or slot.user_id
        if player.name:
            slot.display_name = player.name
        if player.pools:
            slot.pool = {role: list(player.champions(role)) for role in player.pools}
            slot.priorities = {role: {e.champion: e.priority for e in entries if e.priority}
                               for role, entries in player.pools.items()}
            slot.familiarity = {role: {e.champion: e.familiarity for e in entries if e.familiarity}
                                for role, entries in player.pools.items()}
            slot.pool_source = "player"
        elif player.user_id:
            history = history_pools(player.user_id)
            if history:
                slot.pool = history
                slot.pool_source = "history"
        slot.avoid = list(player.avoid)
        if not slot.allowed_roles:
            slot.allowed_roles = list(player.roles)
        if not slot.role and len(slot.allowed_roles) == 1:
            slot.role, slot.role_source = slot.allowed_roles[0], "config"
    if slot.user_id is None and slot.riot_id:
        slot.user_id = roster.profile_user_id_for(slot.riot_id)
    if not slot.pool and slot.user_id and player is None:
        history = history_pools(slot.user_id)
        if history:
            slot.pool, slot.pool_source = history, "history"
    if not slot.pool and roster.default_pools:
        slot.pool = {role: [e.champion for e in entries] for role, entries in roster.default_pools.items()}
        slot.priorities = {role: {e.champion: e.priority for e in entries if e.priority}
                           for role, entries in roster.default_pools.items()}
        slot.pool_source = "default"
    # Team policy applies to everyone.
    if roster.never_pick:
        for role, champions in slot.pool.items():
            slot.pool[role] = [c for c in champions if c not in roster.never_pick]


def apply_opponent(slot: PlayerSlot, opponent: Opponent | None) -> None:
    if opponent is None:
        slot.scouted = None
        return
    slot.scouted = {"key": opponent.key, "name": opponent.name, "team": opponent.team,
                    "pools": {role: [e.champion for e in entries] for role, entries in opponent.pools.items()},
                    "priorities": {role: {e.champion: e.priority for e in entries if e.priority}
                                   for role, entries in opponent.pools.items()}}
    if opponent.name and (not slot.display_name or slot.display_name.startswith(("Enemy", "Cell"))):
        slot.display_name = opponent.name
    if not slot.role and len(opponent.roles) == 1:
        slot.role, slot.role_source = opponent.roles[0], "config"
    if not slot.allowed_roles:
        slot.allowed_roles = list(opponent.roles)


def _finish(state: DraftState, roster: Roster | None) -> DraftState:
    meta = engine.load_meta()
    infer_roles(state.our_team, meta)
    infer_roles(state.enemy_team, meta, fill_blank=False)
    if roster is not None:
        state.never_pick = list(roster.never_pick)
    return state


# --------------------------------------------------------------------------- #
# League client session -> DraftState
# --------------------------------------------------------------------------- #


def _names(ids: list[int] | None) -> list[str]:
    out = []
    for cid in ids or []:
        name = champ_map.name_for(cid)
        if name:
            out.append(name)
    return out


def state_from_lcu_session(session: dict, roster: Roster | None = None, gameflow_phase: str | None = None) -> DraftState:
    """Translate ``/lol-champ-select/v1/session`` JSON into a DraftState."""
    state = DraftState(source="lcu")
    my_team = session.get("myTeam") or []
    their_team = session.get("theirTeam") or []
    local_cell = session.get("localPlayerCellId", -1)

    team_number = None
    for member in my_team:
        if member.get("team") in (1, 2):
            team_number = int(member["team"])
            break
    if team_number is None and my_team:
        team_number = 1 if int(my_team[0].get("cellId", 0)) < 5 else 2
    state.side = "red" if team_number == 2 else "blue"

    def build(members: list[dict], ours: bool) -> list[PlayerSlot]:
        slots = []
        for index, m in enumerate(members):
            cell = int(m.get("cellId", -1))
            slot = PlayerSlot(cell_id=cell)
            slot.role = role_from_position(m.get("assignedPosition"))
            slot.role_source = "lcu" if slot.role else "unknown"
            slot.puuid = m.get("puuid") or None
            game_name = m.get("gameName") or m.get("summonerName") or ""
            tag = m.get("tagLine") or ""
            slot.riot_id = f"{game_name}#{tag}" if game_name and tag else (game_name or None)
            slot.display_name = game_name or (f"Cell {cell}")
            slot.is_local = cell == local_cell
            champion_id = int(m.get("championId") or 0)
            intent_id = int(m.get("championPickIntent") or 0)
            slot.hover = champ_map.name_for(intent_id) if intent_id else None
            if champion_id:
                slot.hover = champ_map.name_for(champion_id)
            if roster is not None:
                if ours:
                    player = roster.find_player(riot_id=slot.riot_id, puuid=slot.puuid, name=game_name or None)
                    if player is None and index < 5 and roster.lineup[index] and not game_name:
                        player = roster.players.get(roster.lineup[index])
                    apply_player(slot, player, roster)
                else:
                    opp = roster.find_opponent(riot_id=slot.riot_id, name=game_name or None)
                    if opp is None and index < 5 and roster.enemy_lineup[index]:
                        opp = roster.opponents.get(roster.enemy_lineup[index])
                    apply_opponent(slot, opp)
            slots.append(slot)
        return slots

    state.our_team = build(my_team, True)
    state.enemy_team = build(their_team, False)
    # A solo custom / versus-AI lobby lists fewer than five humans. Pad the missing seats from the
    # roster lineup so the draft can still be ranked as a full five (ranked flex always has five).
    if len(state.our_team) < 5:
        used_cells = {p.cell_id for p in state.our_team}
        used_keys = {p.player_key for p in state.our_team if p.player_key}
        for index in range(len(state.our_team), 5):
            slot = PlayerSlot(cell_id=min(-1, -1 - index))
            while slot.cell_id in used_cells:
                slot.cell_id -= 1
            used_cells.add(slot.cell_id)
            slot.display_name = f"Seat {index + 1}"
            slot.role_source = "unknown"
            if roster is not None:
                key = roster.lineup[index] if index < len(roster.lineup) else None
                player = roster.players.get(key) if key else None
                if player is None or player.key in used_keys:
                    player = next((pl for pl in roster.lineup_players() if pl and pl.key not in used_keys), None)
                if player is not None:
                    used_keys.add(player.key)
                    apply_player(slot, player, roster)
            state.our_team.append(slot)

    our_cells = {p.cell_id for p in state.our_team}
    locked_by_cell: dict[int, str] = {}
    pick_sequence: list[tuple[bool, int]] = []
    remaining: list[dict] = []
    active_kind, active_cells, active_ours = "none", [], False
    active_ids: dict[int, int] = {}
    picks_completed = 0
    step_counter = 0
    step_index = None
    for group in session.get("actions") or []:
        for action in group:
            kind = str(action.get("type") or "")
            cell = int(action.get("actorCellId", -1))
            ours = bool(action.get("isAllyAction")) if "isAllyAction" in action else cell in our_cells
            champion_id = int(action.get("championId") or 0)
            completed = bool(action.get("completed"))
            in_progress = bool(action.get("isInProgress"))
            if kind in ("pick", "ban"):
                step_counter += 1
            if kind == "pick":
                pick_sequence.append((ours, cell))
            if completed and champion_id and kind == "pick":
                locked_by_cell[cell] = champ_map.name_for(champion_id) or ""
                picks_completed += 1
            if in_progress and not completed:
                if active_kind == "none":
                    active_kind, active_ours = kind, ours
                    step_index = step_counter - 1
                if kind == active_kind:
                    active_cells.append(cell)
                    if action.get("id") is not None:
                        active_ids[cell] = int(action["id"])
            if not completed:
                remaining.append({"kind": kind, "ours": ours, "cell_id": cell})
    state.active = ActiveAction(active_kind, active_ours, active_cells, active_ids)
    state.local_cell_id = int(local_cell) if isinstance(local_cell, int) and local_cell >= 0 else None
    state.pick_number = picks_completed + 1 if any(a["kind"] == "pick" for a in remaining) else 0
    if active_kind != "pick":
        state.pick_number = picks_completed + 1 if any(a["kind"] == "pick" for a in remaining) else 0
    state.step_index = step_index
    state.actions_remaining = remaining
    state.our_pick_cells = [cell for ours, cell in pick_sequence if ours]
    state.enemy_pick_cells = [cell for ours, cell in pick_sequence if not ours]

    for slot in state.our_team + state.enemy_team:
        if slot.cell_id in locked_by_cell:
            slot.locked = locked_by_cell[slot.cell_id]
            slot.hover = None

    bans = session.get("bans") or {}
    state.our_bans = _names(bans.get("myTeamBans"))
    state.enemy_bans = _names(bans.get("theirTeamBans"))
    if not state.our_bans and not state.enemy_bans:
        for group in session.get("actions") or []:
            for action in group:
                if action.get("type") != "ban" or not action.get("completed"):
                    continue
                name = champ_map.name_for(int(action.get("championId") or 0))
                if not name:
                    continue
                cell = int(action.get("actorCellId", -1))
                (state.our_bans if cell in our_cells else state.enemy_bans).append(name)

    timer = session.get("timer") or {}
    phase = str(timer.get("phase") or "")
    state.phase = phase.upper() if phase else ("BAN_PICK" if gameflow_phase == "ChampSelect" else "IDLE")
    state.draft_type = "ranked" if session.get("hasSimultaneousBans") else "tournament"
    state.time_left_ms = int(timer.get("adjustedTimeLeftInPhase") or timer.get("timeLeftInPhase") or 0)
    state.counter = int(session.get("counter") or 0)
    state.game_id = session.get("gameId")
    state.trades = [
        {"cell_id": t.get("cellId"), "state": t.get("state"), "id": t.get("id")}
        for t in (session.get("trades") or []) + (session.get("pickOrderSwaps") or [])
        if t.get("state") not in (None, "INVALID", "AVAILABLE")
    ]
    return _finish(state, roster)


# --------------------------------------------------------------------------- #
# Manual board -> DraftState
# --------------------------------------------------------------------------- #


def _seat_roles(players: list[Player | None]) -> list[str | None]:
    """Give the five lineup seats distinct roles from the players' role lists."""
    roles: list[str | None] = [None] * 5
    taken: set[str] = set()
    # Lineup order is positional (top, jungle, mid, bot, support): honour it when the player plays that role.
    for i, player in enumerate(players[:5]):
        if player is not None and (ROLES[i] in player.roles or not player.roles):
            roles[i], _ = ROLES[i], taken.add(ROLES[i])
    # Then first choice roles, then any listed role, then whatever is left.
    for depth in range(3):
        for i, player in enumerate(players):
            if roles[i] or player is None:
                continue
            candidates = player.roles[depth:depth + 1] if depth < 2 else player.roles
            for role in candidates:
                if role not in taken:
                    roles[i], _ = role, taken.add(role)
                    break
    leftover = [r for r in ROLES if r not in taken]
    for i in range(5):
        if roles[i] is None and leftover:
            roles[i] = leftover.pop(0)
    return roles


def state_from_manual_draft(draft: ManualDraft, roster: Roster) -> DraftState:
    state = DraftState(source="manual", side=draft.side, draft_type=draft.draft_type)
    state.series_unavailable = sorted(draft.series_unavailable)
    steps = draft.sequence
    ours = draft.our_letter
    theirs = "R" if ours == "B" else "B"
    base_ours = 0 if ours == "B" else 5
    base_theirs = 5 if ours == "B" else 0

    players = roster.lineup_players()
    seat_roles = _seat_roles(players)
    for i in range(5):
        slot = PlayerSlot(cell_id=base_ours + i, display_name=(players[i].name if players[i] else f"Seat {i + 1}"))
        slot.role = seat_roles[i]
        slot.role_source = "config" if players[i] else "manual"
        apply_player(slot, players[i], roster)
        slot.role = seat_roles[i]  # apply_player may have set a single role; the seat assignment wins
        state.our_team.append(slot)
    for i in range(5):
        slot = PlayerSlot(cell_id=base_theirs + i, display_name=f"Enemy {i + 1}")
        opp = roster.opponents.get(roster.enemy_lineup[i]) if roster.enemy_lineup[i] else None
        apply_opponent(slot, opp)
        state.enemy_team.append(slot)

    meta = engine.load_meta()
    for action in draft.actions:
        if action.kind == "ban":
            (state.our_bans if action.team == ours else state.enemy_bans).append(action.champion)
            continue
        team = state.our_team if action.team == ours else state.enemy_team
        role = action.role
        target = None
        if action.team == ours and action.cell_id is not None:
            target = next((p for p in team if p.cell_id == action.cell_id and not p.locked), None)
        if role is None and target is not None:
            role = target.role
        if role is None:
            open_roles = [r for r in ROLES if r not in {p.role for p in team if p.locked}]
            if action.team == ours:
                # Prefer the open seat whose pool lists this champion for its role.
                owners = [p for p in team if not p.locked and p.role in open_roles and action.champion in p.pool.get(p.role, [])]
                if len(owners) == 1:
                    role = owners[0].role
            if role is None:
                for candidate, _ in role_candidates(action.champion, meta):
                    if candidate in open_roles:
                        role = candidate
                        break
            role = role or (open_roles[0] if open_roles else None)
        if target is None:
            target = next((p for p in team if p.role == role and not p.locked), None)
        if target is None:
            target = next((p for p in team if not p.locked), None)
        if target is None:
            continue
        if role and target.role != role:
            # Keep roles unique: hand the seat's old role to whoever had this one.
            other = next((p for p in team if p is not target and p.role == role and not p.locked), None)
            if other is not None:
                other.role, other.role_source = target.role, "manual"
            target.role, target.role_source = role, "manual"
        target.locked = action.champion
        target.hover = None

    for letter, champion in draft.hovers.items():
        if not champion:
            continue
        team = state.our_team if letter == ours else state.enemy_team
        slot = next((p for p in team if not p.locked), None)
        if slot is not None:
            slot.hover = champion

    step = draft.current()
    done = len(draft.actions)
    state.step_index = step.index if step else None
    if step is None:
        state.phase = "FINALIZATION"
        state.active = ActiveAction("none", False, [])
    else:
        state.phase = "BAN_PICK"
        is_ours = step.team == ours
        cells: list[int] = []
        if step.kind == "pick":
            team = state.our_team if is_ours else state.enemy_team
            # The board does not know which seat picks next, so every open seat is "up".
            cells = [p.cell_id for p in team if not p.locked]
        state.active = ActiveAction(step.kind, is_ours, cells)
    remaining = []
    for s in steps[done:]:
        remaining.append({"kind": s.kind, "ours": s.team == ours, "cell_id": None})
    state.actions_remaining = remaining
    state.pick_number = sum(1 for a in draft.actions if a.kind == "pick") + 1 if any(r["kind"] == "pick" for r in remaining) else 0
    state.our_pick_cells = [p.cell_id for p in state.our_team]
    state.enemy_pick_cells = [p.cell_id for p in state.enemy_team]
    return _finish(state, roster)


# --------------------------------------------------------------------------- #
# REST payload -> DraftState
# --------------------------------------------------------------------------- #


def state_from_payload(payload: dict, roster: Roster | None = None) -> DraftState:
    """Build a DraftState from a simple JSON shape (for bots and scripts).

    {
      "side": "blue", "phase": "BAN_PICK", "pick_number": 3,
      "our_bans": [...], "enemy_bans": [...],
      "our_picks": {"Top": "Ornn"}, "enemy_picks": ["Rumble", ...] or {"Top": "Rumble"},
      "players": [{"role": "Top", "name": "...", "user_id": 123, "champions": {"Top": [...]}, "locked": null, "hover": null}],
      "enemy_hovers": [...], "active": {"kind": "pick", "roles": ["Mid"], "ours": true}
    }
    """
    state = DraftState(source=str(payload.get("source") or "rest"))
    state.side = "red" if str(payload.get("side", "blue")).casefold().startswith("r") else "blue"
    state.phase = str(payload.get("phase") or "BAN_PICK").upper()
    state.pick_number = int(payload.get("pick_number") or 0)
    state.time_left_ms = int(payload.get("time_left_ms") or 0)
    state.our_bans = [champ_map.canonical_name(c) for c in payload.get("our_bans", []) if c]
    state.enemy_bans = [champ_map.canonical_name(c) for c in payload.get("enemy_bans", []) if c]

    our_picks = payload.get("our_picks") or {}
    players = payload.get("players")
    if not players:
        if roster is not None and any(roster.lineup):
            players = [{"name": p.name if p else None, "role": r}
                       for p, r in zip(roster.lineup_players(), _seat_roles(roster.lineup_players()))]
        else:
            players = [{"role": role} for role in ROLES]
    for index, p in enumerate(players):
        slot = PlayerSlot(cell_id=int(p.get("cell_id", index)))
        slot.role = role_name(p.get("role"))
        slot.role_source = "config" if slot.role else "unknown"
        slot.display_name = str(p.get("name") or p.get("display_name") or p.get("riot_id") or slot.role or f"Player {index + 1}")
        slot.riot_id = p.get("riot_id")
        slot.puuid = p.get("puuid")
        slot.user_id = int(p["user_id"]) if p.get("user_id") else None
        explicit = p.get("champions") or p.get("pools") or {}
        slot.allowed_roles = [r for r in (role_name(x) for x in (p.get("roles") or [])) if r] or ([slot.role] if slot.role else [])
        slot.locked = champ_map.canonical_name(p.get("locked")) if p.get("locked") else None
        slot.hover = champ_map.canonical_name(p.get("hover")) if p.get("hover") else None
        if slot.role and not slot.locked and our_picks.get(slot.role):
            slot.locked = champ_map.canonical_name(our_picks[slot.role])
        if roster is not None:
            player = roster.find_player(riot_id=slot.riot_id, puuid=slot.puuid, user_id=slot.user_id,
                                        name=p.get("name") or p.get("display_name"))
            apply_player(slot, player, roster)
        if explicit:
            slot.pool = {role_name(r) or r: [champ_map.canonical_name(c) for c in v] for r, v in explicit.items()}
            slot.pool_source = "payload"
        state.our_team.append(slot)

    enemy = payload.get("enemy_picks") or []
    if isinstance(enemy, dict):
        for index, (role, champion) in enumerate(enemy.items()):
            if champion:
                state.enemy_team.append(PlayerSlot(cell_id=5 + index, role=role_name(role), role_source="config",
                                                   locked=champ_map.canonical_name(champion), display_name=f"Enemy {index + 1}"))
    else:
        for index, champion in enumerate(enemy):
            state.enemy_team.append(PlayerSlot(cell_id=5 + index, display_name=f"Enemy {index + 1}",
                                               locked=champ_map.canonical_name(champion) if champion else None))
    for champion in payload.get("enemy_hovers") or []:
        state.enemy_team.append(PlayerSlot(cell_id=5 + len(state.enemy_team), hover=champ_map.canonical_name(champion),
                                           display_name=f"Enemy {len(state.enemy_team) + 1}"))
    while len(state.enemy_team) < 5:
        state.enemy_team.append(PlayerSlot(cell_id=5 + len(state.enemy_team), display_name=f"Enemy {len(state.enemy_team) + 1}"))
    if roster is not None:
        for index, slot in enumerate(state.enemy_team[:5]):
            if roster.enemy_lineup[index]:
                apply_opponent(slot, roster.opponents.get(roster.enemy_lineup[index]))

    active = payload.get("active") or {}
    kind = str(active.get("kind") or ("pick" if state.pick_number else "none"))
    roles = [r for r in (role_name(x) for x in active.get("roles", [])) if r]
    cells = [p.cell_id for p in state.our_team if p.role in roles]
    state.active = ActiveAction(kind, bool(active.get("ours", True)), cells)
    state.our_pick_cells = [p.cell_id for p in state.our_team]
    return _finish(state, roster)
