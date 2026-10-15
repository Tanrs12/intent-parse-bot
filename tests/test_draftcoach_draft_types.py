from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from draftcoach import simulate
from draftcoach.draft_order import DRAFT_TYPES, ManualDraft, normalise_type, sequence
from draftcoach.server import create_app

POOLS = {
    "Top": ["Ornn", "Gnar", "Jax", "Camille"],
    "Jungle": ["Vi", "Jarvan IV", "Nocturne", "Sejuani"],
    "Mid": ["Ahri", "Orianna", "Syndra", "Galio"],
    "ADC": ["Jinx", "Kai'Sa", "Ezreal", "Ashe"],
    "Support": ["Nautilus", "Lulu", "Karma", "Rell"],
}


@pytest.fixture
def client(tmp_path):
    app = create_app({"default_pools": POOLS, "auto_profiles": False, "debounce_ms": 0,
                      "players": [{"name": "Chud", "roles": ["Jungle"], "pools": {"Jungle": [{"champion": "Vi", "priority": 1}]}}]},
                     enable_lcu=False, roster_path=tmp_path / "roster.json")
    with TestClient(app) as test_client:
        yield test_client


def test_draft_type_sequences():
    assert set(DRAFT_TYPES) == {"tournament", "ranked", "fearless"}
    assert "".join(s.team for s in sequence("ranked")) == "BRBRBRBRBR" + "BRRBBRRBBR"
    assert [s.kind for s in sequence("ranked")][:10] == ["ban"] * 10
    assert [s.label for s in sequence("fearless")] == [s.label for s in sequence("tournament")]
    assert normalise_type("Normal") == "ranked" and normalise_type("nonsense") == "tournament"


def test_fearless_board_refuses_series_champions():
    draft = ManualDraft(draft_type="fearless")
    draft.series_unavailable = {"Ornn"}
    for c in ["Rumble", "Vi", "Yone", "Jinx", "Aurora", "Kalista"]:
        draft.act(c)
    with pytest.raises(ValueError):
        draft.act("Ornn")
    draft.act("Gnar")
    assert draft.picks("B")[0].champion == "Gnar"


def test_switching_type_changes_turn_order_and_persists(client):
    snap = client.post("/api/draft/type", json={"draft_type": "ranked"}).json()
    assert snap["draft_type"] == "ranked" and snap["draft"]["current"]["label"] == "BB1"
    for c in ["Rumble", "Jinx", "Yone", "Ashe", "Aurora", "Kalista", "Olaf", "Poppy", "Zed", "Lux"]:
        client.post("/api/draft/action", json={"champion": c})
    snap = client.get("/api/state").json()
    assert snap["draft"]["current"]["label"] == "B1" and snap["guidance"]["picking_now"]
    assert snap["state"]["our_bans"] == ["Rumble", "Yone", "Aurora", "Olaf", "Zed"]
    assert snap["roster"]["settings"]["draft_type"] == "ranked"


def test_fearless_series_removes_used_champions(client):
    client.post("/api/draft/type", json={"draft_type": "fearless"})
    for c in ["Rumble", "Vi", "Yone", "Jinx", "Aurora", "Kalista", "Ornn", "Lee Sin", "Syndra"]:
        client.post("/api/draft/action", json={"champion": c})
    snap = client.post("/api/series/next").json()
    assert snap["series"][0]["ours"] == {"Top": "Ornn"} and snap["series"][0]["theirs"] == {"Jungle": "Lee Sin", "Mid": "Syndra"}
    assert snap["draft"]["current"]["label"] == "BB1" and snap["draft"]["series_unavailable"] == ["Lee Sin", "Ornn", "Syndra"]
    assert client.post("/api/draft/action", json={"champion": "Ornn"}).status_code == 400
    for c in ["Rumble", "Vi", "Yone", "Jinx", "Aurora", "Kalista"]:
        client.post("/api/draft/action", json={"champion": c})
    snap = client.get("/api/state").json()
    assert "Ornn" not in {p["champion"] for p in snap["guidance"]["picks"]}
    assert "Ornn" in snap["state"]["series_unavailable"]
    snap = client.post("/api/series/clear").json()
    assert snap["series"] == [] and snap["draft"]["series_unavailable"] == []


def test_last_change_explains_enemy_bans(client):
    client.post("/api/draft/action", json={"champion": "Rumble"})
    snap = client.get("/api/state").json()
    top = snap["guidance"]["picks"][0]["champion"]
    snap = client.post("/api/draft/action", json={"champion": top}).json()      # enemy bans our top pick
    change = snap["last_change"]
    assert change["event"] == f"Enemy banned {top}"
    assert "is gone" in change["effect"] and top not in {p["champion"] for p in snap["guidance"]["picks"]}


def test_lobby_detection_and_seat_linking(client):
    steps = simulate.DraftScript({"side": "blue"}).steps()
    client.post("/api/import", json={"players": [{"name": "Loony", "riot_ids": ["Loony#SIM"], "roles": ["Mid"], "pools": {"Mid": ["Ahri"]}}]})
    client.post("/api/lcu-session", json={"session": steps[3][1], "gameflow_phase": "ChampSelect"})
    lobby = {l["name"]: l for l in client.get("/api/state").json()["lobby"]}
    assert lobby["Chud"]["matched"] and lobby["Chud"]["pool_status"] == "partial"    # matched by name; seated Top, pool is Jungle
    assert "no Top pool" in lobby["Chud"]["detail"]
    assert lobby["Loony"]["matched"] and lobby["Loony"]["riot_id"] == "Loony#SIM"      # matched by riot id
    assert not lobby["Skill"]["matched"] and lobby["Skill"]["pool_status"] == "default"
    r = client.post("/api/roster/link", json={"riot_id": "Skill#SIM", "name": "Skill"}).json()
    assert r["player"]["riot_ids"] == ["Skill#SIM"]
    lobby = {l["name"]: l for l in client.get("/api/state").json()["lobby"]}
    assert lobby["Skill"]["matched"] and lobby["Skill"]["player_key"] == "skill"
    r = client.post("/api/roster/link", json={"riot_id": "Schy#SIM", "player_key": "chud"}).json()
    assert "Schy#SIM" in r["player"]["riot_ids"]
    assert client.post("/api/roster/link", json={"riot_id": "X#Y", "player_key": "nobody"}).status_code == 404
    assert client.post("/api/draft/action", json={"champion": "Ornn"}).status_code == 409   # live draft: board locked
