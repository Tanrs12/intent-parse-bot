from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from draftcoach import client_actions as ca
from draftcoach import simulate
from draftcoach.server import create_app


class FakeRest:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, path, body=None, **_):
        self.calls.append((method, path, body))
        return {}


def _session_at(description_prefix: str) -> dict:
    steps = simulate.DraftScript({"side": "blue"}).steps()
    return next(s for d, s in steps if d.startswith(description_prefix))


def test_active_action_is_only_the_local_seats_in_progress_action():
    session = _session_at("B0 banning")
    assert ca.local_cell(session) == 2                     # the simulator seats us in cell 2
    assert ca.active_action(session, 2) is None            # B0 is banning, not us
    assert ca.active_action(session, 0)["type"] == "ban"
    session = _session_at("B1/B2 picking")
    assert ca.active_action(session, 2)["type"] == "pick"


def test_hover_lock_and_trade_calls():
    rest = FakeRest()
    actions = ca.ClientActions(rest)
    session = _session_at("B1/B2 picking")
    with pytest.raises(RuntimeError):
        actions.lock(session, "Not A Champion")
    result = actions.hover(session, "Ahri")
    assert result["completed"] is False and rest.calls[-1][0] == "PATCH" and rest.calls[-1][2] == {"championId": 103}
    result = actions.lock(session, "Ahri")
    assert result["completed"] is True and rest.calls[-1][2] == {"championId": 103, "completed": True}
    with pytest.raises(RuntimeError):
        actions.lock(_session_at("B0 banning"), "Ahri")     # not our turn
    # trades
    session = _session_at("finalization")
    session["trades"] = [{"cellId": 0, "id": 7, "state": "AVAILABLE"}, {"cellId": 1, "id": 8, "state": "RECEIVED"}]
    assert actions.request_trade(session, 0) == {"trade_id": 7, "requested": True}
    assert rest.calls[-1] == ("POST", "/lol-champ-select/v1/session/trades/7/request", None)
    assert actions.accept_trade(session) == {"trade_id": 8, "accepted": True}
    with pytest.raises(RuntimeError):
        actions.request_trade(session, 4)


def test_local_view_and_endpoints_without_a_client():
    app = create_app({"auto_profiles": False, "debounce_ms": 0, "default_pools": {
        "Top": ["Ornn", "Gnar"], "Jungle": ["Vi", "Nocturne"], "Mid": ["Ahri", "Syndra"], "ADC": ["Jinx", "Ashe"], "Support": ["Lulu", "Rell"]}},
        enable_lcu=False)
    with TestClient(app) as client:
        assert client.get("/api/client/local").json() == {"local": None, "client": False}
        assert client.post("/api/client/lock", json={"champion": "Ahri"}).status_code == 409
        assert client.post("/api/client/nonsense").status_code == 404
        # A client session where our seat (cell 2, mid) is picking: the view carries the suggestion, but
        # nothing can be performed because no League client is connected on this machine.
        hub = app.state.hub
        hub.last_session = _session_at("B1/B2 picking")
        client.post("/api/lcu-session", json={"session": hub.last_session, "gameflow_phase": "ChampSelect"})
        hub.state.source = "lcu"
        view = hub.local_view()
        assert view["cell_id"] == 2 and view["kind"] == "pick" and view["role"] == "Mid"
        # B0 locked Ahri for the mid laner, so our mid seat is really picking Top (and will trade).
        assert view["picking_role"] == "Top" and view["suggestion"]["kind"] == "pick" and view["suggestion"]["role"] == "Top"
        assert view["can_act"] is False and view["client"] is False
        assert client.post("/api/client/lock").status_code == 409
