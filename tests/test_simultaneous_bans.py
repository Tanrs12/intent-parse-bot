"""Simultaneous bans: each of our seats is offered a different ban, never a teammate's choice."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fake_lcu  # noqa: E402
from draftcoach import champions as champ_map  # noqa: E402
from draftcoach.server import create_app  # noqa: E402

OUR = ["Adittive#EUW", "FakerChovyHilary#ALLY", "LargeLarry#EUW", "JackJak#EUW", "ReformUKGaming#7710"]
POOLS = {"Top": ["Ornn", "Gnar"], "Jungle": ["Vi", "Nocturne"], "Mid": ["Ahri", "Syndra"], "ADC": ["Jinx", "Ashe"],
         "Support": ["Lulu", "Rell"]}


def ban_phase_lobby() -> fake_lcu.Lobby:
    lobby = fake_lcu.Lobby(OUR, {}, "blue", 440, 1.0, 30.0, 30.0, 999.0, False)
    lobby.format = "ranked"

    async def go():
        lobby.start()
        lobby._task.cancel()
    asyncio.run(go())
    lobby.session["timer"]["phase"] = "BAN_PICK"
    lobby._start_group(0)
    return lobby


def suggestion_for(app, lobby, cell: int) -> str | None:
    hub = app.state.hub
    session = lobby.view(cell)
    hub.last_session = session
    TestClient(app).post("/api/lcu-session", json={"session": session, "gameflow_phase": "ChampSelect"})
    hub.state.source = "lcu"
    view = hub.local_view()
    assert view and view["kind"] == "ban"
    return (view.get("suggestion") or {}).get("champion")


def test_each_seat_gets_a_different_ban_in_seat_order():
    app = create_app({"auto_profiles": False, "debounce_ms": 0, "default_pools": POOLS}, enable_lcu=False)
    with TestClient(app):
        lobby = ban_phase_lobby()
        offered = [suggestion_for(app, lobby, cell) for cell in lobby.our_cells]
        assert all(offered) and len(set(offered)) == 5, offered
        ranked = [b["champion"] for b in app.state.hub.guidance.get("bans") or []]
        assert offered == ranked[:5]


def test_a_teammates_hover_is_never_offered_and_frees_the_next_one():
    app = create_app({"auto_profiles": False, "debounce_ms": 0, "default_pools": POOLS}, enable_lcu=False)
    with TestClient(app):
        lobby = ban_phase_lobby()
        before = suggestion_for(app, lobby, lobby.our_cells[1])
        # Seat 0 (ahead of seat 1) hovers seat 1's suggestion: seat 1 must move on, not duplicate it.
        seat0 = next(a for a in lobby.groups[0] if a["actorCellId"] == lobby.our_cells[0])
        lobby.act(lobby.our_cells[0], seat0["id"], {"championId": champ_map.id_for(before), "completed": False})
        after = suggestion_for(app, lobby, lobby.our_cells[1])
        assert after and after != before


def test_a_hover_reserves_its_ban_and_nobody_else_is_offered_it():
    """Red side, seat 0 hovers the second-ranked ban: all five suggestions stay distinct, seat 0 keeps its hover."""
    app = create_app({"auto_profiles": False, "debounce_ms": 0, "default_pools": POOLS}, enable_lcu=False)
    with TestClient(app):
        lobby = ban_phase_lobby()
        suggestion_for(app, lobby, lobby.our_cells[0])            # scores the lobby
        ranked = [b["champion"] for b in app.state.hub.guidance.get("bans") or []]
        first = lobby.our_cells[0]
        action = next(a for a in lobby.groups[0] if a["actorCellId"] == first)
        lobby.act(first, action["id"], {"championId": champ_map.id_for(ranked[1]), "completed": False})
        offered = [suggestion_for(app, lobby, cell) for cell in lobby.our_cells]
        assert offered[0] == ranked[1]
        assert all(offered) and len(set(offered)) == 5, offered


def test_tournament_bans_take_the_top_ban():
    """One ban at a time (tournament draft): no distribution, the best remaining ban."""
    app = create_app({"auto_profiles": False, "debounce_ms": 0, "default_pools": POOLS}, enable_lcu=False)
    with TestClient(app):
        lobby = fake_lcu.Lobby(OUR, {}, "blue", 0, 1.0, 30.0, 30.0, 999.0, False)

        async def go():
            lobby.start()
            lobby._task.cancel()
        asyncio.run(go())
        lobby.session["timer"]["phase"] = "BAN_PICK"
        lobby._start_group(0)                     # B0 bans alone
        offered = suggestion_for(app, lobby, lobby.our_cells[0])
        assert offered == [b["champion"] for b in app.state.hub.guidance.get("bans") or []][0]


def test_two_seats_hovering_the_same_ban_still_get_five_different_bans():
    app = create_app({"auto_profiles": False, "debounce_ms": 0, "default_pools": POOLS}, enable_lcu=False)
    with TestClient(app):
        lobby = ban_phase_lobby()
        suggestion_for(app, lobby, lobby.our_cells[0])
        ranked = [b["champion"] for b in app.state.hub.guidance.get("bans") or []]
        for cell in lobby.our_cells[:2]:           # seats 0 and 1 both hover the second-ranked ban
            action = next(a for a in lobby.groups[0] if a["actorCellId"] == cell)
            lobby.act(cell, action["id"], {"championId": champ_map.id_for(ranked[1]), "completed": False})
        offered = [suggestion_for(app, lobby, cell) for cell in lobby.our_cells]
        assert offered[0] == ranked[1]                 # the lowest seat keeps it
        assert all(offered) and len(set(offered)) == 5, offered
