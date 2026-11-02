"""A fake League client lobby: N League clients sharing one champion select.

Each fake client is a small HTTP + websocket server that speaks the subset of the LCU the app
uses (lockfile, basic auth, champ-select session, action PATCH, trades, gameflow, queue,
champion summary) so the real app connects to it exactly as it connects to League. All clients
share one lobby, so five app instances on one PC see one draft and act on their own seats.

The draft runs like the real client: a planning phase, alternating ban and pick turns with a
timer, enemy seats scripted, our seats waiting for the app's PATCH (or auto-locking the hover when
the timer runs out), then finalisation with champion trades, then game start.

    python tools/fake_lcu.py --dir var/party/lobby --control-port 8700 --client-port 8710 \
        --our "FakerChovyHilary#ALLY,Adittive#EUW,RISE OF PETE#PING,JackJak#EUW,ReformUKGaming#7710"

Control API (for the party test driver, no auth):
    GET  /control/state                 lobby phase, session, action groups, event log tail
    POST /control/start                 {"planning": s, "turn": s, "finalization": s} -> enter champ select
    POST /control/advance               expire the current turn now
    POST /control/hover/{cell}          {"champion": "..."}  act as that seat's own client UI
    POST /control/lock/{cell}           {"champion": "..."}
    POST /control/trade/{from}/{to}     request a trade as the "from" seat
    POST /control/ws-drop/{client}      close that client's websockets (the client "hiccups")
    POST /control/rotate/{client}       restart that client on a new port + password (client restart)
    POST /control/leave                 leave champion select (dodge / game start)
    POST /control/end                   game starting: InProgress
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import logging
import secrets
import sys
import time
from pathlib import Path

from aiohttp import web

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from draftcoach import champions as champ_map  # noqa: E402
from draftcoach.simulate import BAN_PHASE_1, BAN_PHASE_2, PICK_PHASE_1, PICK_PHASE_2, POSITIONS  # noqa: E402

log = logging.getLogger("fake-lcu")
CHAMP_SELECT_EVENT = "OnJsonApiEvent_lol-champ-select_v1_session"
GAMEFLOW_EVENT = "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"
QUEUES = {440: {"id": 440, "name": "Ranked Flex", "shortName": "Ranked Flex", "type": "RANKED_FLEX_SR", "gameMode": "CLASSIC",
                "category": "PvP", "description": "Ranked Flex"},
          0: {"id": 0, "name": "Custom", "shortName": "Custom", "type": "CUSTOM", "gameMode": "CLASSIC", "category": "Custom",
              "description": "Custom game"},
          420: {"id": 420, "name": "Ranked Solo/Duo", "shortName": "Ranked Solo", "type": "RANKED_SOLO_5x5", "gameMode": "CLASSIC",
                "category": "PvP", "description": "Ranked Solo/Duo"}}

DEFAULT_ENEMY = {
    "names": ["Enemy One#EUW", "Enemy Two#EUW", "Enemy Three#EUW", "Enemy Four#EUW", "Enemy Five#EUW"],
    "bans": ["Vi", "Jinx", "Orianna", "Jarvan IV", "Galio"],
    "picks": ["Ambessa", "Lee Sin", "Syndra", "Ezreal", "Karma"],
    "hover_first": ["Jax", None, "Ahri", None, None],
}
# What our seats fall back to when the timer expires with nothing hovered (ranked: a random pick).
FALLBACK = {"top": ["Garen", "Malphite", "Sett"], "jungle": ["Warwick", "Amumu", "Nocturne"],
            "middle": ["Annie", "Lux", "Malzahar"], "bottom": ["Ashe", "Miss Fortune", "Caitlyn"],
            "utility": ["Soraka", "Leona", "Janna"]}


def cid(name: str | None) -> int:
    return int(champ_map.id_for(name) or 0) if name else 0


class Lobby:
    def __init__(self, our_names: list[str], enemy: dict, side: str, queue_id: int, planning: float,
                 turn: float, finalization: float, enemy_delay: float, auto_lock: bool):
        self.our_names, self.enemy, self.side = our_names, {**DEFAULT_ENEMY, **(enemy or {})}, side
        self.queue_id, self.planning, self.turn, self.finalization = queue_id, planning, turn, finalization
        self.enemy_delay, self.auto_lock = enemy_delay, auto_lock
        self.our_team = 1 if side == "blue" else 2
        self.our_letter = "B" if self.our_team == 1 else "R"
        self.our_cells = list(range(0, 5)) if self.our_team == 1 else list(range(5, 10))
        self.their_cells = list(range(5, 10)) if self.our_team == 1 else list(range(0, 5))
        self.phase = "Lobby"                     # gameflow phase
        self.session: dict | None = None
        self.groups: list[list[dict]] = []
        self.group_index = -1
        self.group_started = 0.0
        self.phase_started = 0.0
        self.counter = 0
        self.events: list[dict] = []
        self.clients: list[FakeClient] = []
        self.enemy_done = {"ban": 0, "pick": 0}
        self.trades: list[dict] = []            # {"id", "from", "to", "state"}
        self.last_timer_push = 0.0
        self._task: asyncio.Task | None = None

    # ---- helpers -----------------------------------------------------------------------------
    def log_event(self, text: str, **extra) -> None:
        self.events.append({"at": round(time.time(), 3), "text": text, **extra})
        log.info(text)

    def member(self, cell: int) -> dict:
        for m in (self.session or {}).get("myTeam", []) + (self.session or {}).get("theirTeam", []):
            if m["cellId"] == cell:
                return m
        raise KeyError(cell)

    def is_ours(self, cell: int) -> bool:
        return cell in self.our_cells

    def unavailable(self) -> set[int]:
        out = set(self.session["bans"]["myTeamBans"]) | set(self.session["bans"]["theirTeamBans"])
        for group in self.groups:
            for a in group:
                if a["type"] == "pick" and a["completed"] and a["championId"]:
                    out.add(a["championId"])
        return out

    def _member_row(self, cell: int, team: int, position: str, riot: str) -> dict:
        name, _, tag = riot.partition("#")
        return {"cellId": cell, "team": team, "assignedPosition": position, "championId": 0, "championPickIntent": 0,
                "puuid": f"fake-{name.lower().replace(' ', '-')}", "gameName": name, "tagLine": tag or "EUW",
                "summonerId": 1000 + cell, "nameVisibilityType": "VISIBLE", "selectedSkinId": 0, "spell1Id": 4, "spell2Id": 12,
                "wardSkinId": -1, "entitledFeatureType": "", "isHumanoid": True}

    def _action(self, aid: int, cell: int, kind: str) -> dict:
        return {"id": aid, "actorCellId": cell, "type": kind, "championId": 0, "completed": False, "isInProgress": False,
                "isAllyAction": self.is_ours(cell), "pickTurn": aid}

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        my = [self._member_row(c, self.our_team, POSITIONS[i], self.our_names[i]) for i, c in enumerate(self.our_cells)]
        their = [self._member_row(c, 3 - self.our_team, POSITIONS[i], self.enemy["names"][i]) for i, c in enumerate(self.their_cells)]
        aid = 0
        groups: list[list[dict]] = []
        for _, cell in BAN_PHASE_1:
            aid += 1
            groups.append([self._action(aid, cell, "ban")])
        for group in _group_picks(PICK_PHASE_1):
            row = []
            for _, cell in group:
                aid += 1
                row.append(self._action(aid, cell, "pick"))
            groups.append(row)
        for _, cell in BAN_PHASE_2:
            aid += 1
            groups.append([self._action(aid, cell, "ban")])
        for group in _group_picks(PICK_PHASE_2):
            row = []
            for _, cell in group:
                aid += 1
                row.append(self._action(aid, cell, "pick"))
            groups.append(row)
        self.groups = groups
        self.session = {
            "gameId": int(time.time()), "localPlayerCellId": self.our_cells[0], "myTeam": my, "theirTeam": their,
            "bans": {"myTeamBans": [], "theirTeamBans": [], "numBans": 10}, "actions": groups,
            "timer": {"phase": "PLANNING", "adjustedTimeLeftInPhase": int(self.planning * 1000),
                      "totalTimeInPhase": int(self.planning * 1000), "internalNowInEpochMs": int(time.time() * 1000), "isInfinite": False},
            "trades": [], "pickOrderSwaps": [], "counter": 0, "isCustomGame": self.queue_id == 0, "allowBattleBoost": False,
            "allowDuplicatePicks": False, "allowLockedEvents": False, "allowRerolling": False, "allowSkinSelection": True,
            "hasSimultaneousBans": False, "hasSimultaneousPicks": False, "isSpectating": False, "lockedEventIndex": -1,
            "rerollsRemaining": 0, "skipChampionSelect": False, "benchEnabled": False, "benchChampions": [],
            "chatDetails": {"chatRoomName": "fake", "chatRoomPassword": ""}, "recoveryCounter": 0,
        }
        self.group_index = -1
        self.enemy_done = {"ban": 0, "pick": 0}
        self.trades = []
        self.phase = "ChampSelect"
        self.phase_started = time.time()
        self.log_event("champion select started", phase="PLANNING")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._tick_loop())

    def leave(self, reason: str = "left champion select") -> None:
        self.session = None
        self.groups = []
        self.group_index = -1
        self.phase = "Lobby"
        self.log_event(reason)

    def end_game_start(self) -> None:
        if self.session:
            self.session["timer"]["phase"] = "GAME_STARTING"
        self.phase = "InProgress"
        self.log_event("game starting")

    # ---- the clock -----------------------------------------------------------------------------
    async def _tick_loop(self) -> None:
        while True:
            try:
                changed = self.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
                changed = False
            if changed:
                await self.broadcast()
            await asyncio.sleep(0.25)

    def tick(self) -> bool:
        if not self.session or self.phase != "ChampSelect":
            return False
        now = time.time()
        timer = self.session["timer"]
        changed = False
        if timer["phase"] == "PLANNING":
            left = self.planning - (now - self.phase_started)
            if left <= 0:
                timer["phase"] = "BAN_PICK"
                self._start_group(0)
                return True
        elif timer["phase"] == "BAN_PICK":
            group = self.groups[self.group_index]
            elapsed = now - self.group_started
            left = self.turn - elapsed
            for action in group:
                if action["completed"]:
                    continue
                cell = action["actorCellId"]
                if not self.is_ours(cell):
                    changed |= self._enemy_step(action, elapsed)
                elif left <= 0 and self.auto_lock:
                    self._auto_lock(action)
                    changed = True
            if all(a["completed"] for a in group):
                if self.group_index + 1 < len(self.groups):
                    self._start_group(self.group_index + 1)
                else:
                    timer["phase"] = "FINALIZATION"
                    self.phase_started = now
                    self.trades = [{"id": 100 + i, "cellId": c, "state": "AVAILABLE"} for i, c in enumerate(self.our_cells)]
                    self.log_event("finalization: trades open")
                return True
        elif timer["phase"] == "FINALIZATION":
            left = self.finalization - (now - self.phase_started)
            if left <= 0:
                self.end_game_start()
                return True
        else:
            return False
        if now - self.last_timer_push >= 1.0:
            self.last_timer_push = now
            changed = True
        self._refresh_timer()
        return changed

    def _refresh_timer(self) -> None:
        now = time.time()
        timer = self.session["timer"]
        if timer["phase"] == "PLANNING":
            left = self.planning - (now - self.phase_started)
            total = self.planning
        elif timer["phase"] == "BAN_PICK":
            left = self.turn - (now - self.group_started)
            total = self.turn
        elif timer["phase"] == "FINALIZATION":
            left = self.finalization - (now - self.phase_started)
            total = self.finalization
        else:
            left, total = 0, 1
        timer["adjustedTimeLeftInPhase"] = max(0, int(left * 1000))
        timer["totalTimeInPhase"] = int(total * 1000)
        timer["internalNowInEpochMs"] = int(now * 1000)

    def _start_group(self, index: int) -> None:
        self.group_index = index
        self.group_started = time.time()
        for action in self.groups[index]:
            action["isInProgress"] = True
        labels = ", ".join(f"{'B' if a['actorCellId'] < 5 else 'R'}{a['actorCellId']}" for a in self.groups[index])
        self.log_event(f"turn {index + 1}/{len(self.groups)}: {self.groups[index][0]['type']} by {labels}", group=index)

    def _available(self, wanted: str | None, position: str) -> int:
        """The scripted champion if it is still available, else the first free fallback for that lane,
        else any free champion: a real enemy never fails to lock because our team took their plan."""
        gone = self.unavailable()
        for name in [wanted] + FALLBACK.get(position, []):
            if name and cid(name) and cid(name) not in gone:
                return cid(name)
        for name, champion_id in champ_map.all_ids().items():
            if champion_id not in gone:
                return champion_id
        return 0

    def _enemy_step(self, action: dict, elapsed: float) -> bool:
        kind = action["type"]
        index = self.enemy_done[kind]
        position = self.member(action["actorCellId"])["assignedPosition"]
        if kind == "ban":
            target = self.enemy["bans"][index % len(self.enemy["bans"])]
            if elapsed >= self.enemy_delay:
                self._complete(action, self._available(target, position))
                self.enemy_done[kind] += 1
                return True
            return False
        hover = (self.enemy["hover_first"] + [None] * 5)[index]
        pick = self.enemy["picks"][index % len(self.enemy["picks"])]
        want = self._available(pick, position) if elapsed >= self.enemy_delay * 1.5 or not hover else self._available(hover, position)
        changed = False
        if elapsed >= self.enemy_delay * 0.5 and action["championId"] != want and want:
            action["championId"] = want
            self.member(action["actorCellId"])["championId"] = want
            changed = True
        if elapsed >= self.enemy_delay * 2.0 and want:
            self._complete(action, want)
            self.enemy_done[kind] += 1
            changed = True
        return changed

    def _auto_lock(self, action: dict) -> None:
        cell = action["actorCellId"]
        chosen = action["championId"] if action["championId"] and action["championId"] not in self.unavailable() else 0
        if action["type"] == "pick" and not chosen:
            position = self.member(cell)["assignedPosition"]
            chosen = next((cid(x) for x in FALLBACK.get(position, []) if cid(x) not in self.unavailable()), 0)
        self._complete(action, chosen)
        self.log_event(f"timer expired: seat {cell} auto-{action['type']} {champ_map.name_for(chosen) or 'nothing'}", cell=cell)

    def _complete(self, action: dict, champion_id: int) -> None:
        action["championId"] = champion_id
        action["completed"] = True
        action["isInProgress"] = False
        cell = action["actorCellId"]
        if action["type"] == "ban":
            key = "myTeamBans" if self.is_ours(cell) else "theirTeamBans"
            if champion_id:
                self.session["bans"][key].append(champion_id)
        else:
            self.member(cell)["championId"] = champion_id
        who = "ours" if self.is_ours(cell) else "enemy"
        self.log_event(f"{who} seat {cell} {action['type']} locked {champ_map.name_for(champion_id) or 'nothing'}",
                       cell=cell, kind=action["type"], champion=champ_map.name_for(champion_id))

    # ---- what the clients do ---------------------------------------------------------------------
    def act(self, cell: int, action_id: int, body: dict) -> dict:
        if not self.session or self.session["timer"]["phase"] != "BAN_PICK":
            raise web.HTTPBadRequest(text=json.dumps({"message": "not in a pick or ban phase"}))
        action = next((a for g in self.groups for a in g if a["id"] == action_id), None)
        if action is None:
            raise web.HTTPNotFound(text=json.dumps({"message": "no such action"}))
        if action["actorCellId"] != cell:
            raise web.HTTPForbidden(text=json.dumps({"message": "action belongs to another cell"}))
        if action["completed"] or not action["isInProgress"]:
            raise web.HTTPBadRequest(text=json.dumps({"message": "action is not in progress"}))
        champion_id = int(body.get("championId") or 0)
        if champion_id and champion_id in self.unavailable():
            raise web.HTTPBadRequest(text=json.dumps({"message": "champion is unavailable"}))
        if champion_id and not champ_map.name_for(champion_id):
            raise web.HTTPBadRequest(text=json.dumps({"message": "unknown champion"}))
        if body.get("completed"):
            if not champion_id:
                raise web.HTTPBadRequest(text=json.dumps({"message": "cannot lock nothing"}))
            self._complete(action, champion_id)
        else:
            action["championId"] = champion_id
            if action["type"] == "pick":
                self.member(cell)["championId"] = champion_id
            self.log_event(f"ours seat {cell} {action['type']} hover {champ_map.name_for(champion_id)}", cell=cell,
                           kind=action["type"], hover=champ_map.name_for(champion_id))
        return {"ok": True}

    def trades_for(self, cell: int) -> list[dict]:
        """The LCU lists one trade entry per teammate from the local player's point of view."""
        if not self.session or self.session["timer"]["phase"] != "FINALIZATION":
            return []
        out = []
        for other in self.our_cells:
            if other == cell:
                continue
            state = "AVAILABLE"
            for t in self.trades:
                if t["state"] in ("SENT", "RECEIVED") and {t["from"], t["to"]} == {cell, other}:
                    state = "SENT" if t["from"] == cell else "RECEIVED"
                elif t["state"] in ("SENT", "RECEIVED") and cell in (t["from"], t["to"]):
                    state = "BUSY"
            out.append({"id": 100 + other, "cellId": other, "state": state})
        return out

    def trade(self, cell: int, trade_id: int, verb: str) -> dict:
        other = trade_id - 100
        if self.session is None or self.session["timer"]["phase"] != "FINALIZATION" or other not in self.our_cells:
            raise web.HTTPBadRequest(text=json.dumps({"message": "trade not available"}))
        pending = next((t for t in self.trades if t["state"] in ("SENT", "RECEIVED") and {t["from"], t["to"]} == {cell, other}), None)
        if verb == "request":
            if pending:
                raise web.HTTPBadRequest(text=json.dumps({"message": "trade already pending"}))
            self.trades.append({"id": trade_id, "from": cell, "to": other, "state": "SENT"})
            self.log_event(f"seat {cell} requested a trade with seat {other}", cell=cell, trade="request")
        elif verb == "accept":
            if not pending or pending["to"] != cell:
                raise web.HTTPBadRequest(text=json.dumps({"message": "no incoming trade"}))
            a, b = self.member(cell), self.member(other)
            a["championId"], b["championId"] = b["championId"], a["championId"]
            for g in self.groups:
                for action in g:
                    if action["type"] == "pick" and action["actorCellId"] in (cell, other):
                        action["championId"] = self.member(action["actorCellId"])["championId"]
            pending["state"] = "DONE"
            self.log_event(f"seat {cell} accepted the trade with seat {other}", cell=cell, trade="accept")
        elif verb == "decline":
            if not pending:
                raise web.HTTPBadRequest(text=json.dumps({"message": "no pending trade"}))
            pending["state"] = "DECLINED"
            self.log_event(f"seat {cell} declined the trade with seat {other}", cell=cell, trade="decline")
        else:
            raise web.HTTPNotFound()
        return {"ok": True}

    def view(self, cell: int) -> dict | None:
        if not self.session:
            return None
        self.counter += 1
        self._refresh_timer()
        view = copy.deepcopy(self.session)
        view["localPlayerCellId"] = cell
        view["counter"] = self.counter
        view["trades"] = self.trades_for(cell)
        return view

    def summary(self) -> dict:
        groups = []
        for i, g in enumerate(self.groups):
            groups.append([{"id": a["id"], "cell": a["actorCellId"], "type": a["type"], "champion": champ_map.name_for(a["championId"]),
                           "completed": a["completed"], "in_progress": a["isInProgress"], "ours": self.is_ours(a["actorCellId"]),
                           "current": i == self.group_index} for a in g])
        seats = []
        if self.session:
            for m in self.session["myTeam"] + self.session["theirTeam"]:
                seats.append({"cell": m["cellId"], "name": f"{m['gameName']}#{m['tagLine']}", "position": m["assignedPosition"],
                              "champion": champ_map.name_for(m["championId"]), "ours": self.is_ours(m["cellId"])})
        return {"phase": self.phase, "timer": (self.session or {}).get("timer"), "group_index": self.group_index,
                "groups": groups, "seats": seats, "trades": self.trades, "queue": self.queue_id,
                "clients": [{"index": c.index, "cell": c.cell, "port": c.port, "sockets": len(c.sockets), "lockfile": str(c.lockfile)}
                            for c in self.clients],
                "events": self.events[-40:]}

    async def broadcast(self) -> None:
        for client in self.clients:
            await client.push_session()


def _group_picks(order: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
    groups: list[list[tuple[str, int]]] = []
    for letter, cell in order:
        if groups and groups[-1][0][0] == letter and len(groups[-1]) < 2:
            groups[-1].append((letter, cell))
        else:
            groups.append([(letter, cell)])
    return groups


class FakeClient:
    """One League client: lockfile, basic-auth REST and the wamp-style websocket."""

    def __init__(self, lobby: Lobby, index: int, port: int, directory: Path):
        self.lobby, self.index, self.port = lobby, index, port
        self.cell = lobby.our_cells[index]
        self.password = secrets.token_urlsafe(16)
        self.directory = directory
        self.lockfile = directory / "lockfile"
        self.sockets: set[web.WebSocketResponse] = set()
        self.subscriptions: dict[web.WebSocketResponse, set[str]] = {}
        self.runner: web.AppRunner | None = None
        self.last_phase_sent: str | None = None

    @property
    def riot_id(self) -> str:
        return self.lobby.our_names[self.index]

    async def start(self) -> None:
        app = web.Application(middlewares=[self.auth])
        app.router.add_get("/lol-champ-select/v1/session", self.get_session)
        app.router.add_patch("/lol-champ-select/v1/session/actions/{aid}", self.patch_action)
        app.router.add_post("/lol-champ-select/v1/session/trades/{tid}/request", self.trade)
        app.router.add_post("/lol-champ-select/v1/session/trades/{tid}/accept", self.trade)
        app.router.add_post("/lol-champ-select/v1/session/trades/{tid}/decline", self.trade)
        app.router.add_get("/lol-gameflow/v1/gameflow-phase", self.gameflow_phase)
        app.router.add_get("/lol-gameflow/v1/session", self.gameflow_session)
        app.router.add_get("/lol-lobby/v2/lobby", self.lobby_view)
        app.router.add_get("/lol-game-data/assets/v1/champion-summary.json", self.champion_summary)
        app.router.add_get("/lol-game-data/assets/v1/champion-icons/{cid}.png", self.not_found)
        app.router.add_get("/lol-game-data/assets/v1/champions/{cid}.json", self.not_found)
        app.router.add_get("/lol-summoner/v2/summoners/puuid/{puuid}", self.summoner)
        app.router.add_get("/lol-summoner/v1/current-summoner", self.current_summoner)
        app.router.add_get("/lol-ranked/v1/current-ranked-stats", self.ranked_stats)
        app.router.add_get("/lol-match-history/v1/products/lol/{puuid}/matches", self.match_list)
        app.router.add_get("/lol-match-history/v1/games/{game_id}", self.match_game)
        app.router.add_get("/", self.websocket)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lockfile.write_text(f"LeagueClient:{4000 + self.index}:{self.port}:{self.password}:http", encoding="utf-8")
        log.info("client %d (%s, cell %d) on port %d, lockfile %s", self.index, self.riot_id, self.cell, self.port, self.lockfile)

    async def stop(self) -> None:
        for ws in list(self.sockets):
            await ws.close()
        if self.runner:
            await self.runner.cleanup()
        try:
            self.lockfile.unlink()
        except OSError:
            pass

    async def rotate(self, new_port: int) -> None:
        """The client restarted: new port, new password, new lockfile."""
        await self.stop()
        self.port = new_port
        self.password = secrets.token_urlsafe(16)
        await self.start()
        self.lobby.log_event(f"client {self.index} restarted on port {new_port}", client=self.index)

    async def drop_sockets(self) -> int:
        n = len(self.sockets)
        for ws in list(self.sockets):
            await ws.close(code=1001, message=b"client hiccup")
        self.lobby.log_event(f"client {self.index} dropped {n} websocket(s)", client=self.index)
        return n

    # ---- auth ---------------------------------------------------------------------------------
    @web.middleware
    async def auth(self, request: web.Request, handler):
        expected = "Basic " + base64.b64encode(f"riot:{self.password}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            raise web.HTTPUnauthorized(text=json.dumps({"message": "bad auth"}), content_type="application/json")
        return await handler(request)

    # ---- REST ----------------------------------------------------------------------------------
    async def get_session(self, _request: web.Request) -> web.Response:
        view = self.lobby.view(self.cell)
        if view is None:
            raise web.HTTPNotFound(text=json.dumps({"errorCode": "RPC_ERROR", "httpStatus": 404, "message": "No active delegate"}),
                                   content_type="application/json")
        return web.json_response(view)

    async def patch_action(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.lobby.act(self.cell, int(request.match_info["aid"]), body)
        await self.lobby.broadcast()
        return web.Response(status=204)

    async def trade(self, request: web.Request) -> web.Response:
        verb = request.path.rsplit("/", 1)[-1]
        self.lobby.trade(self.cell, int(request.match_info["tid"]), verb)
        await self.lobby.broadcast()
        return web.json_response(self.lobby.trades_for(self.cell))

    async def gameflow_phase(self, _request: web.Request) -> web.Response:
        return web.json_response(self.lobby.phase)

    async def gameflow_session(self, _request: web.Request) -> web.Response:
        queue = QUEUES.get(self.lobby.queue_id, {**QUEUES[440], "id": self.lobby.queue_id})
        return web.json_response({"phase": self.lobby.phase, "gameData": {"queue": queue, "isCustomGame": self.lobby.queue_id == 0},
                                  "map": {"id": 11, "name": "Summoner's Rift"}})

    async def lobby_view(self, _request: web.Request) -> web.Response:
        return web.json_response({"gameConfig": {"queueId": self.lobby.queue_id, "gameMode": "CLASSIC", "mapId": 11,
                                                 "isCustom": self.lobby.queue_id == 0}})

    async def champion_summary(self, _request: web.Request) -> web.Response:
        rows = [{"id": i, "name": n, "alias": n.replace("'", "").replace(" ", ""), "roles": []} for n, i in champ_map.all_ids().items()]
        return web.json_response(rows)

    async def not_found(self, _request: web.Request) -> web.Response:
        raise web.HTTPNotFound(text=json.dumps({"message": "not bundled by the fake client"}), content_type="application/json")

    async def summoner(self, request: web.Request) -> web.Response:
        puuid = request.match_info["puuid"]
        if self.lobby.session:
            for m in self.lobby.session["myTeam"] + self.lobby.session["theirTeam"]:
                if m["puuid"] == puuid:
                    return web.json_response({"puuid": puuid, "gameName": m["gameName"], "tagLine": m["tagLine"],
                                              "displayName": m["gameName"], "summonerId": m["summonerId"]})
        raise web.HTTPNotFound()

    async def current_summoner(self, _request: web.Request) -> web.Response:
        name, _, tag = self.riot_id.partition("#")
        return web.json_response({"puuid": f"fake-{name.lower().replace(' ', '-')}", "gameName": name, "tagLine": tag,
                                  "displayName": name, "summonerId": 1000 + self.cell})

    # ---- the player's own history (synthetic, deterministic per player) ------------------------------
    @property
    def puuid(self) -> str:
        name = self.riot_id.partition("#")[0]
        return f"fake-{name.lower().replace(' ', '-')}"

    def _history(self) -> list[dict]:
        if not hasattr(self, "_history_cache"):
            from fake_matches import match_history
            self._history_cache = match_history(self.riot_id, self.puuid, self.index, 30, now_ms=int(time.time() * 1000))
        return self._history_cache

    async def ranked_stats(self, _request: web.Request) -> web.Response:
        from fake_matches import ranked_stats
        return web.json_response(ranked_stats(self.riot_id))

    async def match_list(self, request: web.Request) -> web.Response:
        if request.match_info["puuid"] != self.puuid:
            raise web.HTTPNotFound()
        beg = int(request.query.get("begIndex", 0)); end = int(request.query.get("endIndex", 20))
        rows = []
        for g in self._history()[beg:end]:
            mine = [p for p in g["participants"] if any(i["participantId"] == p["participantId"] and i["player"]["puuid"] == self.puuid
                                                        for i in g["participantIdentities"])]
            rows.append({**g, "participants": mine, "participantIdentities": [i for i in g["participantIdentities"] if i["player"]["puuid"] == self.puuid]})
        return web.json_response({"accountId": 1000 + self.cell, "games": {"gameBeginDate": "", "gameCount": len(rows), "gameEndDate": "",
                                                                         "gameIndexBegin": beg, "gameIndexEnd": end, "games": rows}})

    async def match_game(self, request: web.Request) -> web.Response:
        game_id = int(request.match_info["game_id"])
        for g in self._history():
            if g["gameId"] == game_id:
                return web.json_response(g)
        raise web.HTTPNotFound()

    # ---- websocket --------------------------------------------------------------------------------
    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=("wamp",), heartbeat=20)
        await ws.prepare(request)
        self.sockets.add(ws)
        self.subscriptions[ws] = set()
        try:
            async for message in ws:
                if message.type != web.WSMsgType.TEXT:
                    continue
                try:
                    frame = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, list) and len(frame) >= 2 and frame[0] == 5:
                    self.subscriptions[ws].add(str(frame[1]))
                elif isinstance(frame, list) and len(frame) >= 2 and frame[0] == 6:
                    self.subscriptions[ws].discard(str(frame[1]))
        finally:
            self.sockets.discard(ws)
            self.subscriptions.pop(ws, None)
        return ws

    async def push_session(self) -> None:
        view = self.lobby.view(self.cell)
        if self.last_phase_sent != self.lobby.phase:
            self.last_phase_sent = self.lobby.phase
            await self._send(GAMEFLOW_EVENT, {"data": self.lobby.phase, "eventType": "Update", "uri": "/lol-gameflow/v1/gameflow-phase"})
        if view is None:
            await self._send(CHAMP_SELECT_EVENT, {"data": None, "eventType": "Delete", "uri": "/lol-champ-select/v1/session"})
        else:
            await self._send(CHAMP_SELECT_EVENT, {"data": view, "eventType": "Update", "uri": "/lol-champ-select/v1/session"})

    async def _send(self, event: str, payload: dict) -> None:
        frame = json.dumps([8, event, payload])
        for ws in list(self.sockets):
            if event not in self.subscriptions.get(ws, ()):
                continue
            try:
                await ws.send_str(frame)
            except Exception:  # noqa: BLE001
                self.sockets.discard(ws)


# ---- control API ----------------------------------------------------------------------------------
def control_app(lobby: Lobby, client_port_base: int, directory: Path) -> web.Application:
    app = web.Application()
    next_port = {"value": client_port_base + 50}

    async def state(_request: web.Request) -> web.Response:
        return web.json_response(lobby.summary())

    async def start(request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        for key in ("planning", "turn", "finalization", "enemy_delay"):
            if key in body:
                setattr(lobby, key, float(body[key]))
        if "auto_lock" in body:
            lobby.auto_lock = bool(body["auto_lock"])
        if "queue" in body:
            lobby.queue_id = int(body["queue"])
        lobby.start()
        await lobby.broadcast()
        return web.json_response(lobby.summary())

    async def advance(_request: web.Request) -> web.Response:
        if lobby.session:
            timer = lobby.session["timer"]["phase"]
            if timer == "PLANNING":
                lobby.phase_started -= lobby.planning
            elif timer == "BAN_PICK":
                lobby.group_started -= lobby.turn
            elif timer == "FINALIZATION":
                lobby.phase_started -= lobby.finalization
            lobby.tick()
            await lobby.broadcast()
        return web.json_response(lobby.summary())

    async def seat_act(request: web.Request) -> web.Response:
        cell = int(request.match_info["cell"])
        body = await request.json()
        verb = request.path.split("/")[2]
        champion = int(champ_map.id_for(body.get("champion")) or 0)
        action = next((a for g in lobby.groups for a in g if a["actorCellId"] == cell and a["isInProgress"] and not a["completed"]), None)
        if action is None:
            raise web.HTTPBadRequest(text="that seat has no action in progress")
        lobby.act(cell, action["id"], {"championId": champion, "completed": verb == "lock"})
        await lobby.broadcast()
        return web.json_response(lobby.summary())

    async def trade(request: web.Request) -> web.Response:
        a, b = int(request.match_info["a"]), int(request.match_info["b"])
        lobby.trade(a, 100 + b, "request")
        await lobby.broadcast()
        return web.json_response(lobby.summary())

    async def ws_drop(request: web.Request) -> web.Response:
        client = lobby.clients[int(request.match_info["client"])]
        n = await client.drop_sockets()
        return web.json_response({"ok": True, "dropped": n})

    async def rotate(request: web.Request) -> web.Response:
        client = lobby.clients[int(request.match_info["client"])]
        next_port["value"] += 1
        await client.rotate(next_port["value"])
        return web.json_response({"ok": True, "port": client.port, "lockfile": str(client.lockfile)})

    async def leave(_request: web.Request) -> web.Response:
        lobby.leave()
        await lobby.broadcast()
        return web.json_response(lobby.summary())

    async def end(_request: web.Request) -> web.Response:
        lobby.end_game_start()
        await lobby.broadcast()
        await asyncio.sleep(1.0)
        lobby.leave("game started")
        await lobby.broadcast()
        return web.json_response(lobby.summary())

    app.router.add_get("/control/state", state)
    app.router.add_post("/control/start", start)
    app.router.add_post("/control/advance", advance)
    app.router.add_post("/control/hover/{cell}", seat_act)
    app.router.add_post("/control/lock/{cell}", seat_act)
    app.router.add_post("/control/trade/{a}/{b}", trade)
    app.router.add_post("/control/ws-drop/{client}", ws_drop)
    app.router.add_post("/control/rotate/{client}", rotate)
    app.router.add_post("/control/leave", leave)
    app.router.add_post("/control/end", end)
    return app


async def serve(args: argparse.Namespace) -> None:
    our = [x.strip() for x in args.our.split(",") if x.strip()]
    if len(our) != 5:
        raise SystemExit("--our needs five Riot IDs (Name#TAG)")
    enemy = json.loads(args.enemy) if args.enemy else {}
    lobby = Lobby(our, enemy, args.side, args.queue, args.planning, args.turn, args.finalization, args.enemy_delay, not args.no_auto_lock)
    directory = Path(args.dir)
    for i in range(5):
        client = FakeClient(lobby, i, args.client_port + i, directory / f"client{i}")
        await client.start()
        lobby.clients.append(client)
    runner = web.AppRunner(control_app(lobby, args.client_port, directory))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", args.control_port).start()
    log.info("fake lobby control: http://127.0.0.1:%d/control/state", args.control_port)
    if args.start_after is not None:
        await asyncio.sleep(args.start_after)
        lobby.start()
        await lobby.broadcast()
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=str(REPO / "var" / "party" / "lobby"))
    parser.add_argument("--control-port", type=int, default=8700)
    parser.add_argument("--client-port", type=int, default=8710, help="first fake client port (five consecutive ports)")
    parser.add_argument("--our", required=True, help="five Riot IDs, comma separated, in Top,Jungle,Mid,Bot,Support order")
    parser.add_argument("--enemy", help="JSON overriding the enemy script (names, bans, picks, hover_first)")
    parser.add_argument("--side", choices=["blue", "red"], default="blue")
    parser.add_argument("--queue", type=int, default=440)
    parser.add_argument("--planning", type=float, default=8.0)
    parser.add_argument("--turn", type=float, default=27.0)
    parser.add_argument("--finalization", type=float, default=25.0)
    parser.add_argument("--enemy-delay", type=float, default=2.5)
    parser.add_argument("--no-auto-lock", action="store_true", help="our seats never auto-lock when the timer expires")
    parser.add_argument("--start-after", type=float, help="enter champion select automatically after N seconds")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
