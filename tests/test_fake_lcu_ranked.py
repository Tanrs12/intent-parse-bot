"""The fake client's ranked draft: simultaneous bans, hidden enemy information, role and pick-order swaps."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from aiohttp import web  # noqa: E402

import fake_lcu  # noqa: E402
from draftcoach import assist  # noqa: E402

OUR = ["Adittive#EUW", "FakerChovyHilary#ALLY", "LargeLarry#EUW", "JackJak#EUW", "ReformUKGaming#7710"]


def ranked_lobby(**kw) -> fake_lcu.Lobby:
    lobby = fake_lcu.Lobby(OUR, {}, kw.get("side", "red"), 440, 1.0, 30.0, 30.0, 0.0, False)
    lobby.format = "ranked"
    lobby.assigned = kw.get("assigned", ["middle", "jungle", "top", "bottom", "utility"])

    async def go():
        lobby.start()
        lobby._task.cancel()          # the test drives the clock itself
    asyncio.run(go())
    return lobby


def test_ranked_groups_simultaneous_bans_then_1_2_2_2_2_1():
    lobby = ranked_lobby()
    kinds = [[a["type"] for a in g] for g in lobby.groups]
    assert kinds[0] == ["ban"] * 10
    assert [[a["actorCellId"] for a in g] for g in lobby.groups[1:]] == fake_lcu.RANKED_PICKS
    assert lobby.session["hasSimultaneousBans"] is True


def test_view_hides_enemy_lanes_hovers_and_bans_until_the_phase_ends():
    lobby = ranked_lobby()
    lobby.session["timer"]["phase"] = "BAN_PICK"
    lobby._start_group(0)
    enemy_ban = next(a for a in lobby.groups[0] if a["actorCellId"] in lobby.their_cells)
    lobby._complete(enemy_ban, fake_lcu.cid("Sylas"))
    view = lobby.view(lobby.our_cells[0])
    assert all(m["assignedPosition"] == "" for m in view["theirTeam"])
    assert view["bans"]["theirTeamBans"] == []
    hidden = next(a for g in view["actions"] for a in g if a["id"] == enemy_ban["id"])
    assert hidden["championId"] == 0 and hidden["completed"] is False
    for a in lobby.groups[0]:
        if not a["completed"]:
            lobby._complete(a, 0)
    assert fake_lcu.cid("Sylas") in lobby.view(lobby.our_cells[0])["bans"]["theirTeamBans"]


def test_role_swap_request_accept_moves_assigned_positions():
    lobby = ranked_lobby()
    me, other = lobby.our_cells[0], lobby.our_cells[2]          # mid seat and top seat
    offers = lobby.swaps_for(me, "position-swaps")
    entry = next(o for o in offers if o["cellId"] == other)
    assert entry["state"] == "AVAILABLE"
    lobby.swap(me, "position-swaps", entry["id"], "request")
    assert next(o for o in lobby.swaps_for(other, "position-swaps") if o["cellId"] == me)["state"] == "RECEIVED"
    lobby.swap(other, "position-swaps", fake_lcu.SWAP_FAMILIES["position-swaps"] + me, "accept")
    assert lobby.member(me)["assignedPosition"] == "top" and lobby.member(other)["assignedPosition"] == "middle"


def test_pick_order_swap_moves_the_pick_turn_and_closes_after_a_pick():
    lobby = ranked_lobby()
    first, last = lobby.our_cells[0], lobby.our_cells[4]        # red: cell 5 picks R1, cell 9 picks R5
    sid = fake_lcu.SWAP_FAMILIES["pick-order-swaps"] + last
    lobby.swap(first, "pick-order-swaps", sid, "request")
    lobby.swap(last, "pick-order-swaps", fake_lcu.SWAP_FAMILIES["pick-order-swaps"] + first, "accept")
    pick_cells = [a["actorCellId"] for g in lobby.groups for a in g if a["type"] == "pick" and a["actorCellId"] in lobby.our_cells]
    assert pick_cells[0] == last and pick_cells[-1] == first
    lobby.session["timer"]["phase"] = "BAN_PICK"
    pick = next(a for g in lobby.groups for a in g if a["type"] == "pick" and a["actorCellId"] == last)
    lobby._complete(pick, fake_lcu.cid("Sejuani"))
    assert next(o for o in lobby.swaps_for(first, "pick-order-swaps") if o["cellId"] == last)["state"] == "INVALID"


def test_only_the_recipient_accepts_and_only_the_sender_cancels():
    lobby = ranked_lobby()
    a, b = lobby.our_cells[0], lobby.our_cells[1]
    base = fake_lcu.SWAP_FAMILIES["position-swaps"]
    lobby.swap(a, "position-swaps", base + b, "request")
    with pytest.raises(web.HTTPBadRequest):
        lobby.swap(a, "position-swaps", base + b, "accept")
    with pytest.raises(web.HTTPBadRequest):
        lobby.swap(b, "position-swaps", base + a, "cancel")
    lobby.swap(a, "position-swaps", base + b, "cancel")
    assert next(o for o in lobby.swaps_for(a, "position-swaps") if o["cellId"] == b)["state"] == "AVAILABLE"


def test_app_swap_plan_reads_the_fake_session():
    """assist.swap_plan (what auto-swap runs) finds the request to make from the fake ranked session."""
    lobby = ranked_lobby()
    session = lobby.view(lobby.our_cells[0])          # Kayla's seat, assigned mid, wants top
    plan = assist.swap_plan(session, "Top", 4)
    assert plan["position"]["with_cell"] == lobby.our_cells[2] and plan["position"]["state"] == "AVAILABLE"
    assert plan["pick_order"]["with_cell"] == lobby.our_cells[4]


def _settle(lobby, targets, rounds=30):
    """Every seat runs what its app's auto-swap would: accept per the plan, else one request at a time."""
    for _ in range(rounds):
        moved = False
        for cell in lobby.our_cells:
            role, slot = targets[cell]
            session = lobby.view(cell)
            plan = assist.swap_plan(session, role, slot, {c: t for c, t in targets.items() if c != cell})
            for row in plan["accept"]:
                lobby.swap(cell, row["family"], row["swap_id"], "accept")
                moved = True
            if plan["accept"]:
                continue
            pending = any(o["state"] == "SENT" for fam in ("positionSwaps", "pickOrderSwaps") for o in session[fam])
            for kind in ("position", "pick_order"):
                row = plan.get(kind)
                if not pending and row and row["state"] == "AVAILABLE":
                    lobby.swap(cell, row["family"], row["swap_id"], "request")
                    moved = True
                    break
        if not moved:
            break


def _pick_slot(lobby, cell):
    order = [a["actorCellId"] for g in lobby.groups for a in g if a["type"] == "pick" and a["actorCellId"] in lobby.our_cells]
    return order.index(cell)


def test_three_way_pick_order_rotation_resolves():
    """Kayla (pick 1) wants pick 5, Jezz (pick 5) wants pick 2, Cian (pick 2) wants pick 1: no single swap seats
    anyone who asks, so each app must also accept a request that seats the asker."""
    lobby = ranked_lobby(assigned=["top", "jungle", "middle", "bottom", "utility"])
    kayla, cian, ste, jack, jezz = lobby.our_cells
    roles = {kayla: "Top", cian: "Jungle", ste: "Mid", jack: "ADC", jezz: "Support"}
    slots = {kayla: 4, cian: 0, ste: 2, jack: 3, jezz: 1}
    targets = {c: (roles[c], slots[c]) for c in lobby.our_cells}
    _settle(lobby, targets)
    assert {c: _pick_slot(lobby, c) for c in lobby.our_cells} == slots


def test_role_rotation_resolves_and_seated_players_never_move():
    lobby = ranked_lobby(assigned=["middle", "top", "jungle", "bottom", "utility"])
    kayla, cian, ste, jack, jezz = lobby.our_cells
    want = {kayla: "Top", cian: "Jungle", ste: "Mid", jack: "ADC", jezz: "Support"}
    targets = {c: (want[c], None) for c in lobby.our_cells}
    _settle(lobby, targets)
    from draftcoach.state import role_from_position
    assert {c: role_from_position(lobby.member(c)["assignedPosition"]) for c in lobby.our_cells} == want
    accepted = [t for t in lobby.swaps if t["state"] == "ACCEPTED"]
    assert all(t["to"] not in (jack, jezz) and t["from"] not in (jack, jezz) for t in accepted)   # already seated


def test_a_pending_swap_cannot_be_accepted_after_the_window_closes():
    lobby = ranked_lobby()
    a, b = lobby.our_cells[0], lobby.our_cells[2]
    base = fake_lcu.SWAP_FAMILIES["position-swaps"]
    lobby.swap(a, "position-swaps", base + b, "request")
    lobby.session["timer"]["phase"] = "FINALIZATION"
    with pytest.raises(web.HTTPBadRequest):
        lobby.swap(b, "position-swaps", base + a, "accept")
    assert lobby.member(a)["assignedPosition"] == "middle"
