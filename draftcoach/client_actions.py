"""Acting in champion select through the local League client.

Each League client may only act for its own seat, so this is what makes a
five-machine setup work: every player runs the app locally, the overlay shows
the decision for *their* seat, and "Accept" performs it through their client:

* hover / lock a ban or a pick: ``PATCH /lol-champ-select/v1/session/actions/{id}``
  with ``{"championId": X}`` (hover) or ``{"championId": X, "completed": true}`` (lock),
* request the champion trade the advisor expects: ``POST .../trades/{id}/request``,
* accept an incoming trade: ``POST .../trades/{id}/accept``.

The pure functions here work on the raw session JSON so they can be unit-tested
without a client; ``ClientActions`` wraps them around a ``LcuRest``.
"""

from __future__ import annotations

from typing import Any

from . import champions as champ_map


def local_cell(session: dict) -> int | None:
    cell = session.get("localPlayerCellId")
    try:
        cell = int(cell)
    except (TypeError, ValueError):
        return None
    return cell if cell >= 0 else None


def active_action(session: dict, cell: int) -> dict | None:
    """The in-progress, incomplete ban/pick action owned by ``cell`` (None if it is not our turn)."""
    for group in session.get("actions") or []:
        for action in group:
            if int(action.get("actorCellId", -1)) != cell:
                continue
            if action.get("completed") or not action.get("isInProgress"):
                continue
            if action.get("type") in ("ban", "pick"):
                return action
    return None


def trade_for_cell(session: dict, cell: int) -> dict | None:
    """The trade entry that targets ``cell`` (our client's trade offer towards that seat)."""
    for trade in session.get("trades") or []:
        if int(trade.get("cellId", -1)) == cell:
            return trade
    return None


def incoming_trade(session: dict) -> dict | None:
    for trade in session.get("trades") or []:
        if str(trade.get("state") or "").upper() == "RECEIVED":
            return trade
    return None


def champion_name_for_cell(session: dict, cell: int) -> str | None:
    for member in (session.get("myTeam") or []) + (session.get("theirTeam") or []):
        if int(member.get("cellId", -1)) == cell:
            cid = int(member.get("championId") or 0)
            return champ_map.name_for(cid) if cid else None
    return None


class ClientActions:
    """Perform champion-select actions through the local client (blocking; run in a thread)."""

    def __init__(self, rest: Any):
        self.rest = rest

    def _action_url(self, action: dict) -> str:
        return f"/lol-champ-select/v1/session/actions/{int(action['id'])}"

    def hover(self, session: dict, champion: str) -> dict:
        cell = local_cell(session)
        action = active_action(session, cell) if cell is not None else None
        if action is None:
            raise RuntimeError("it is not this seat's turn")
        cid = champ_map.id_for(champion)
        if not cid:
            raise RuntimeError(f"unknown champion: {champion}")
        self.rest.request("PATCH", self._action_url(action), {"championId": cid})
        return {"action_id": int(action["id"]), "kind": action.get("type"), "champion": champion, "completed": False}

    def lock(self, session: dict, champion: str) -> dict:
        cell = local_cell(session)
        action = active_action(session, cell) if cell is not None else None
        if action is None:
            raise RuntimeError("it is not this seat's turn")
        cid = champ_map.id_for(champion)
        if not cid:
            raise RuntimeError(f"unknown champion: {champion}")
        self.rest.request("PATCH", self._action_url(action), {"championId": cid})
        self.rest.request("PATCH", self._action_url(action), {"championId": cid, "completed": True})
        return {"action_id": int(action["id"]), "kind": action.get("type"), "champion": champion, "completed": True}

    def request_trade(self, session: dict, with_cell: int) -> dict:
        trade = trade_for_cell(session, with_cell)
        if trade is None:
            raise RuntimeError("no trade is available with that seat")
        state = str(trade.get("state") or "").upper()
        if state not in ("AVAILABLE", "RECEIVED", "SENT"):
            raise RuntimeError(f"trade with that seat is {state.lower() or 'unavailable'}")
        if state == "RECEIVED":
            self.rest.request("POST", f"/lol-champ-select/v1/session/trades/{int(trade['id'])}/accept")
            return {"trade_id": int(trade["id"]), "accepted": True}
        if state == "SENT":
            return {"trade_id": int(trade["id"]), "pending": True}
        self.rest.request("POST", f"/lol-champ-select/v1/session/trades/{int(trade['id'])}/request")
        return {"trade_id": int(trade["id"]), "requested": True}

    def accept_trade(self, session: dict, trade_id: int | None = None) -> dict:
        trade = None
        if trade_id is not None:
            trade = next((t for t in session.get("trades") or [] if int(t.get("id", -1)) == int(trade_id)), None)
        trade = trade or incoming_trade(session)
        if trade is None:
            raise RuntimeError("no incoming trade to accept")
        self.rest.request("POST", f"/lol-champ-select/v1/session/trades/{int(trade['id'])}/accept")
        return {"trade_id": int(trade["id"]), "accepted": True}

    def decline_trade(self, session: dict, trade_id: int | None = None) -> dict:
        trade = incoming_trade(session) if trade_id is None else next((t for t in session.get("trades") or [] if int(t.get("id", -1)) == int(trade_id)), None)
        if trade is None:
            raise RuntimeError("no incoming trade to decline")
        self.rest.request("POST", f"/lol-champ-select/v1/session/trades/{int(trade['id'])}/decline")
        return {"trade_id": int(trade["id"]), "declined": True}
