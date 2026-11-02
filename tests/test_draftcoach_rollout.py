"""Draft transitions must revoke old decisions, even with work still in flight."""
import asyncio
import json
from io import BytesIO

import pytest

from draftcoach.hub import Hub
from draftcoach.roster import Roster
from draftcoach.simulate import DraftScript
from draftcoach import state as ds
from draftcoach import server
from draftcoach import lcu


def picking_session():
    return next(s for label, s in DraftScript({"side": "blue"}).steps() if label.startswith("B1/B2 picking"))


def test_dodge_during_debounce_cannot_resurrect_champion_select(monkeypatch):
    monkeypatch.setenv("INTENT_LIVE", "1")
    async def run():
        hub = Hub(Roster(auto_profiles=False), debounce_ms=30, prewarm=0)
        await hub.ingest_lcu_session(picking_session(), "ChampSelect", {"id": 440})
        await asyncio.sleep(0.01)
        await hub.ingest_lcu_session(None, "Lobby")
        await hub.settle()
        assert hub.state is None
        assert hub.local_view() is None
    asyncio.run(run())


def test_idle_lcu_does_not_clear_an_explicit_manual_board(monkeypatch):
    monkeypatch.setenv("INTENT_LIVE", "1")
    async def run():
        hub = Hub(Roster(auto_profiles=False), debounce_ms=0, prewarm=0)
        await hub.ingest_manual()
        await hub.settle()
        await hub.ingest_lcu_session(None, "Lobby")
        assert hub.state is not None and hub.state.source == "manual"
    asyncio.run(run())


def test_local_suggestion_never_falls_back_to_another_role():
    hub = Hub(Roster(auto_profiles=False), prewarm=0)
    hub.last_session = picking_session()
    hub.state = ds.state_from_lcu_session(hub.last_session, hub.roster, "ChampSelect")
    hub.guidance = {"picks": [], "bans": []}
    hub.intent_guidance = {"picks": [{"champion": "Nami", "role": "Support"}]}
    assert hub.local_view()["suggestion"] is None


def test_stale_session_cannot_offer_or_execute_client_actions():
    hub = Hub(Roster(auto_profiles=False), prewarm=0)
    hub.last_session = picking_session()
    hub.state = ds.state_from_lcu_session(hub.last_session, hub.roster, "ChampSelect")
    hub.guidance = {"picks": [], "bans": []}
    hub.status["stale"] = True
    hub.rest = object()  # a retained handle is not evidence that its session is fresh
    assert hub.local_view()["can_act"] is False
    with pytest.raises(RuntimeError, match="stale"):
        asyncio.run(hub.client_act("lock", "Ahri"))


def test_bridge_poller_discards_response_from_previous_game(monkeypatch):
    hub = Hub(Roster(auto_profiles=False), prewarm=0)
    hub.state = ds.state_from_lcu_session(picking_session(), hub.roster, "ChampSelect")
    hub.state.game_id = "new"
    old = {"active": True, "game_id": "old", "guidance": {"picks": [{"champion": "Nami", "role": "Support"}]}}
    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: BytesIO(json.dumps(old).encode()))
    async def run():
        task = asyncio.create_task(server._poll_bridge(hub, "http://unused"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert hub.intent_guidance is None
    asyncio.run(run())


def test_normal_websocket_close_is_reconnecting_not_an_app_error(monkeypatch):
    from websockets.exceptions import ConnectionClosedOK
    from websockets.frames import Close
    async def run():
        statuses = []
        async def session(*_):
            raise ConnectionClosedOK(Close(1001, "client restart"), Close(1001, "client restart"), True)
        async def status(state, detail):
            statuses.append(state)
            watcher.stop()
        watcher = lcu.LcuWatcher(lambda *_: None, status)
        monkeypatch.setattr(lcu, "find_lockfile", lambda *_: object())
        monkeypatch.setattr(lcu, "LcuRest", lambda _: object())
        monkeypatch.setattr(watcher, "_session_loop", session)
        await watcher.run()
        assert statuses == ["waiting"]
    asyncio.run(run())
