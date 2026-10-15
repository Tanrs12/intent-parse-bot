"""Draft types, their action sequences, and the manual draft board.

Draft types
-----------
* ``tournament`` - pro/tournament draft: 3 bans each, 6 picks, 2 bans each, 4 picks.
* ``ranked``     - ranked / normal draft: all ten bans at once, then picks.
* ``fearless``   - tournament order, but every champion picked in an earlier game
                   of the series is unavailable to both teams.

The League client reports its own action list, so with a client connected the
type is detected (``hasSimultaneousBans``) and only matters for fearless
tracking. On the manual board the type decides whose turn it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import champions as champ_map

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]

TOURNAMENT_SEQUENCE: list[tuple[str, str]] = (
    [("B", "ban"), ("R", "ban")] * 3
    + [("B", "pick"), ("R", "pick"), ("R", "pick"), ("B", "pick"), ("B", "pick"), ("R", "pick")]
    + [("R", "ban"), ("B", "ban")] * 2
    + [("R", "pick"), ("B", "pick"), ("B", "pick"), ("R", "pick")]
)
RANKED_SEQUENCE: list[tuple[str, str]] = (
    [("B", "ban"), ("R", "ban")] * 5
    + [("B", "pick"), ("R", "pick"), ("R", "pick"), ("B", "pick"), ("B", "pick"),
       ("R", "pick"), ("R", "pick"), ("B", "pick"), ("B", "pick"), ("R", "pick")]
)

DRAFT_TYPES: dict[str, dict] = {
    "tournament": {"label": "Tournament draft", "sequence": TOURNAMENT_SEQUENCE, "fearless": False,
                   "description": "3 bans each, 6 picks, 2 bans each, 4 picks"},
    "ranked": {"label": "Ranked / normal draft", "sequence": RANKED_SEQUENCE, "fearless": False,
               "description": "10 simultaneous bans, then picks"},
    "fearless": {"label": "Fearless (series)", "sequence": TOURNAMENT_SEQUENCE, "fearless": True,
                 "description": "tournament order; champions picked earlier in the series are gone"},
}


def normalise_type(value: str | None) -> str:
    key = str(value or "tournament").strip().casefold()
    aliases = {"pro": "tournament", "competitive": "tournament", "tourney": "tournament", "normal": "ranked",
               "soloq": "ranked", "solo": "ranked", "draft": "ranked", "fearless draft": "fearless"}
    key = aliases.get(key, key)
    return key if key in DRAFT_TYPES else "tournament"


@dataclass
class Step:
    index: int
    team: str                   # "B" | "R"
    kind: str                   # "ban" | "pick"
    number: int                 # 1-based number of this kind for this team
    label: str                  # "B1", "R2" for picks; "BB1", "RB4" for bans


def sequence(draft_type: str = "tournament") -> list[Step]:
    steps: list[Step] = []
    counters = {("B", "ban"): 0, ("R", "ban"): 0, ("B", "pick"): 0, ("R", "pick"): 0}
    for index, (team, kind) in enumerate(DRAFT_TYPES[normalise_type(draft_type)]["sequence"]):
        counters[(team, kind)] += 1
        number = counters[(team, kind)]
        label = f"{team}{number}" if kind == "pick" else f"{team}B{number}"
        steps.append(Step(index, team, kind, number, label))
    return steps


SEQUENCE = sequence("tournament")


@dataclass
class ManualAction:
    index: int
    team: str
    kind: str
    champion: str
    role: str | None = None
    cell_id: int | None = None

    def to_dict(self, steps: list[Step] | None = None) -> dict:
        steps = steps or SEQUENCE
        return {"index": self.index, "team": self.team, "kind": self.kind, "champion": self.champion,
                "role": self.role, "cell_id": self.cell_id, "label": steps[self.index].label if self.index < len(steps) else "?"}


@dataclass
class ManualDraft:
    """Click-driven draft board state."""

    side: str = "blue"
    draft_type: str = "tournament"
    actions: list[ManualAction] = field(default_factory=list)
    redo: list[ManualAction] = field(default_factory=list)
    hovers: dict[str, str | None] = field(default_factory=dict)
    series_unavailable: set[str] = field(default_factory=set)   # fearless: picked earlier in the series

    @property
    def sequence(self) -> list[Step]:
        return sequence(self.draft_type)

    @property
    def our_letter(self) -> str:
        return "B" if self.side == "blue" else "R"

    def current(self) -> Step | None:
        steps = self.sequence
        if len(self.actions) >= len(steps):
            return None
        return steps[len(self.actions)]

    def is_complete(self) -> bool:
        return len(self.actions) >= len(self.sequence)

    def taken(self) -> set[str]:
        return {a.champion for a in self.actions}

    def picks(self, team: str) -> list[ManualAction]:
        return [a for a in self.actions if a.kind == "pick" and a.team == team]

    def bans(self, team: str) -> list[str]:
        return [a.champion for a in self.actions if a.kind == "ban" and a.team == team]

    def set_type(self, draft_type: str) -> None:
        new_type = normalise_type(draft_type)
        if new_type != self.draft_type:
            self.draft_type = new_type
            if len(self.actions) > len(self.sequence):
                del self.actions[len(self.sequence):]
            self.redo.clear()

    def act(self, champion: str, role: str | None = None, cell_id: int | None = None) -> ManualAction:
        step = self.current()
        if step is None:
            raise ValueError("draft is complete")
        name = champ_map.resolve(champion)
        if not name:
            raise ValueError(f"unknown champion: {champion}")
        if name in self.taken():
            raise ValueError(f"{name} is already banned or picked")
        if name in self.series_unavailable:
            raise ValueError(f"{name} was already used in this series (fearless)")
        if role is not None and role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        if step.kind == "ban":
            role = None
        action = ManualAction(step.index, step.team, step.kind, name, role, cell_id)
        self.actions.append(action)
        self.redo.clear()
        self.hovers[step.team] = None
        return action

    def hover(self, team: str, champion: str | None) -> None:
        self.hovers[team] = champ_map.resolve(champion) if champion else None

    def undo(self) -> ManualAction | None:
        if not self.actions:
            return None
        action = self.actions.pop()
        self.redo.append(action)
        return action

    def redo_last(self) -> ManualAction | None:
        if not self.redo:
            return None
        action = self.redo.pop()
        self.actions.append(action)
        return action

    def reset(self, side: str | None = None) -> None:
        self.actions.clear()
        self.redo.clear()
        self.hovers.clear()
        if side:
            self.side = "red" if str(side).casefold().startswith("r") else "blue"

    def set_role(self, index: int, role: str | None) -> None:
        if role is not None and role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        for action in self.actions:
            if action.index == index and action.kind == "pick":
                action.role = role
                return
        raise ValueError(f"no pick at step {index}")

    def to_dict(self) -> dict:
        steps = self.sequence
        step = self.current()
        return {
            "side": self.side,
            "draft_type": self.draft_type,
            "complete": self.is_complete(),
            "current": {"index": step.index, "team": step.team, "kind": step.kind, "label": step.label,
                        "ours": step.team == self.our_letter} if step else None,
            "actions": [a.to_dict(steps) for a in self.actions],
            "can_undo": bool(self.actions),
            "can_redo": bool(self.redo),
            "hovers": dict(self.hovers),
            "sequence": [{"index": s.index, "team": s.team, "kind": s.kind, "label": s.label} for s in steps],
            "series_unavailable": sorted(self.series_unavailable),
        }
