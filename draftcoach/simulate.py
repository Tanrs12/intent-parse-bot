"""Scripted champ select that produces League-client-shaped session JSON.

Lets you develop and demo the overlay without the League client. The script
walks a standard tournament draft (3 bans each, 6 picks, 2 bans each, 4
picks), including hovers before locks and a champion swap between two of our
players after the draft completes. Each step is emitted either in-process
(``run.py --simulate``) or POSTed to a running server
(``python -m draftcoach.simulate --url http://127.0.0.1:8765``).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import time
import urllib.request

from . import champions as champ_map

BAN_PHASE_1 = [("B", 0), ("R", 5), ("B", 1), ("R", 6), ("B", 2), ("R", 7)]
PICK_PHASE_1 = [("B", 0), ("R", 5), ("R", 6), ("B", 1), ("B", 2), ("R", 7)]
BAN_PHASE_2 = [("R", 8), ("B", 3), ("R", 9), ("B", 4)]
PICK_PHASE_2 = [("R", 8), ("B", 3), ("B", 4), ("R", 9)]

POSITIONS = ["top", "jungle", "middle", "bottom", "utility"]

DEFAULT_SCRIPT = {
    "side": "blue",
    "our_names": ["Chud", "Atomic", "Loony", "Skill", "Schy"],
    "enemy_names": ["Enemy1", "Enemy2", "Enemy3", "Enemy4", "Enemy5"],
    "bans": {"B": ["Rumble", "Yone", "Aurora", "Kalista", "Poppy"], "R": ["Vi", "Jinx", "Orianna", "Jarvan IV", "Galio"]},
    # Our top-lane cell (B0) first-picks Ahri for the mid laner, the mid cell (B2) later
    # takes Ornn for top, and the two trade in finalisation, as teams do in organised play.
    "picks": {"B": ["Ahri", "Nocturne", "Ornn", "Kai'Sa", "Nautilus"], "R": ["Ambessa", "Lee Sin", "Syndra", "Ezreal", "Karma"]},
    "hover_first": {"B": ["Syndra", "Sejuani", "Gnar", "Jinx", "Rell"], "R": ["Jax", None, "Ahri", None, None]},
    "swap": [0, 2],
}


def _member(cell: int, team: int, position: str, name: str, tag: str = "SIM") -> dict:
    if "#" in name:
        name, _, tag = name.partition("#")
    return {"cellId": cell, "team": team, "assignedPosition": position, "championId": 0, "championPickIntent": 0,
            "puuid": f"sim-{cell}", "gameName": name, "tagLine": tag, "summonerId": 1000 + cell, "nameVisibilityType": "VISIBLE"}


class DraftScript:
    """Produces a sequence of LCU session dicts for a scripted draft."""

    def __init__(self, script: dict | None = None):
        self.script = {**DEFAULT_SCRIPT, **(script or {})}
        self.our_team = 1 if self.script["side"] == "blue" else 2
        self.our_letter = "B" if self.our_team == 1 else "R"
        self.session = self._base_session()
        self.action_id = 0
        self.counter = 0

    def _base_session(self) -> dict:
        my_cells = range(0, 5) if self.our_team == 1 else range(5, 10)
        their_cells = range(5, 10) if self.our_team == 1 else range(0, 5)
        names = self.script["our_names"]
        enemy_names = self.script.get("enemy_names") or [f"Enemy{i + 1}" for i in range(5)]
        my_team = [_member(c, self.our_team, POSITIONS[i], names[i]) for i, c in enumerate(my_cells)]
        their_team = [_member(c, 3 - self.our_team, "", enemy_names[i]) for i, c in enumerate(their_cells)]
        return {
            "gameId": 424242, "localPlayerCellId": list(my_cells)[2], "myTeam": my_team, "theirTeam": their_team,
            "bans": {"myTeamBans": [], "theirTeamBans": [], "numBans": 10}, "actions": [],
            "timer": {"phase": "PLANNING", "adjustedTimeLeftInPhase": 20000, "totalTimeInPhase": 20000},
            "trades": [], "pickOrderSwaps": [], "counter": 0, "isCustomGame": True,
            "hasSimultaneousBans": False, "hasSimultaneousPicks": False,
        }

    def _next_action(self, cell: int, kind: str, ours: bool) -> dict:
        self.action_id += 1
        return {"id": self.action_id, "actorCellId": cell, "type": kind, "championId": 0, "completed": False,
                "isInProgress": False, "isAllyAction": ours, "pickTurn": self.action_id}

    def _all_cells(self, letter: str) -> list[int]:
        return list(range(0, 5)) if letter == "B" else list(range(5, 10))

    def _is_ours(self, letter: str) -> bool:
        return letter == self.our_letter

    def _emit(self, time_left: int = 27000) -> dict:
        self.counter += 1
        self.session["counter"] = self.counter
        self.session["timer"]["adjustedTimeLeftInPhase"] = time_left
        return copy.deepcopy(self.session)

    def _member_for(self, cell: int) -> dict:
        for m in self.session["myTeam"] + self.session["theirTeam"]:
            if m["cellId"] == cell:
                return m
        raise KeyError(cell)

    def _group_picks(self, order: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
        groups: list[list[tuple[str, int]]] = []
        for letter, cell in order:
            if groups and groups[-1][0][0] == letter and len(groups[-1]) < 2:
                groups[-1].append((letter, cell))
            else:
                groups.append([(letter, cell)])
        return groups

    def _build_actions(self) -> list[list[dict]]:
        actions: list[list[dict]] = []
        for letter, cell in BAN_PHASE_1:
            actions.append([self._next_action(cell, "ban", self._is_ours(letter))])
        for group in self._group_picks(PICK_PHASE_1):
            actions.append([self._next_action(cell, "pick", self._is_ours(letter)) for letter, cell in group])
        for letter, cell in BAN_PHASE_2:
            actions.append([self._next_action(cell, "ban", self._is_ours(letter))])
        for group in self._group_picks(PICK_PHASE_2):
            actions.append([self._next_action(cell, "pick", self._is_ours(letter)) for letter, cell in group])
        return actions

    def steps(self) -> list[tuple[str, dict]]:
        """Return (description, session) pairs for the whole draft."""
        out: list[tuple[str, dict]] = []
        picks_done = {"B": 0, "R": 0}
        bans_done = {"B": 0, "R": 0}
        self.session["actions"] = self._build_actions()
        out.append(("planning phase", self._emit(20000)))
        self.session["timer"]["phase"] = "BAN_PICK"

        for group in self.session["actions"]:
            for action in group:
                action["isInProgress"] = True
            kind = group[0]["type"]
            labels = "/".join(f"{'B' if a['actorCellId'] < 5 else 'R'}{a['actorCellId']}" for a in group)
            out.append((f"{labels} {'banning' if kind == 'ban' else 'picking'}", self._emit()))
            for action in group:
                cell = action["actorCellId"]
                letter = "B" if cell < 5 else "R"
                if kind == "ban":
                    champion = self.script["bans"][letter][bans_done[letter]]
                    bans_done[letter] += 1
                    action.update({"championId": champ_map.id_for(champion) or 0, "completed": True, "isInProgress": False})
                    key = "myTeamBans" if self._is_ours(letter) else "theirTeamBans"
                    self.session["bans"][key].append(champ_map.id_for(champion) or 0)
                    out.append((f"{letter}{cell} banned {champion}", self._emit()))
                    continue
                index = picks_done[letter]
                champion = self.script["picks"][letter][index]
                hover = (self.script.get("hover_first", {}).get(letter) or [None] * 5)[index]
                member = self._member_for(cell)
                if hover:
                    member["championId"] = champ_map.id_for(hover) or 0
                    action["championId"] = member["championId"]
                    out.append((f"{letter}{cell} hovering {hover}", self._emit(18000)))
                member["championId"] = champ_map.id_for(champion) or 0
                action["championId"] = member["championId"]
                out.append((f"{letter}{cell} hovering {champion}", self._emit(12000)))
                action.update({"completed": True, "isInProgress": False})
                picks_done[letter] += 1
                out.append((f"{letter}{cell} locked {champion}", self._emit(27000)))

        self.session["timer"]["phase"] = "FINALIZATION"
        out.append(("finalization", self._emit(30000)))
        swap = self.script.get("swap")
        if swap:
            my_cells = self._all_cells(self.our_letter)
            a, b = self._member_for(my_cells[swap[0]]), self._member_for(my_cells[swap[1]])
            self.session["trades"] = [{"cellId": b["cellId"], "id": 1, "state": "SENT"}]
            out.append((f"{a['gameName']} requested a champion swap with {b['gameName']}", self._emit(25000)))
            a["championId"], b["championId"] = b["championId"], a["championId"]
            for group in self.session["actions"]:
                for action in group:
                    if action["type"] == "pick" and action["actorCellId"] in (a["cellId"], b["cellId"]):
                        action["championId"] = self._member_for(action["actorCellId"])["championId"]
            self.session["trades"] = []
            out.append(("champion swap accepted", self._emit(20000)))
        self.session["timer"]["phase"] = "GAME_STARTING"
        out.append(("game starting", self._emit(3000)))
        return out


async def play(sink, *, delay: float = 1.0, script: dict | None = None, loop_forever: bool = False) -> None:
    """Feed every step to ``sink(session_or_none, description)`` with ``delay`` seconds between steps."""
    while True:
        for description, session in DraftScript(script).steps():
            await sink(session, description)
            await asyncio.sleep(delay)
        await sink(None, "left champ select")
        if not loop_forever:
            return
        await asyncio.sleep(max(delay * 3, 2.0))


def _post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"{}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Replay a scripted draft into a running overlay server")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="overlay server base URL")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between draft events")
    parser.add_argument("--side", choices=["blue", "red"], default="blue")
    parser.add_argument("--script", help="JSON file overriding the default draft script")
    parser.add_argument("--loop", action="store_true", help="replay forever")
    args = parser.parse_args(argv)
    script = {"side": args.side}
    if args.script:
        with open(args.script, encoding="utf-8") as fh:
            script.update(json.load(fh))

    async def sink(session, description):
        started = time.perf_counter()
        result = _post(args.url.rstrip("/") + "/api/lcu-session", {"session": session, "gameflow_phase": "ChampSelect" if session else "Lobby"})
        print(f"{description:<48} -> round trip {(time.perf_counter() - started) * 1000:6.1f} ms, scoring {result.get('compute_ms')} ms")

    asyncio.run(play(sink, delay=args.delay, script=script, loop_forever=args.loop))


if __name__ == "__main__":
    main()
