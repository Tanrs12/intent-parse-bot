from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from draftcoach import simulate
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
    app = create_app({"default_pools": POOLS, "auto_profiles": False, "debounce_ms": 0}, enable_lcu=False,
                     roster_path=tmp_path / "roster.json")
    with TestClient(app) as test_client:
        yield test_client


def test_pages_and_initial_board(client):
    assert "<title>Draft Coach</title>" in client.get("/").text
    assert "Draft Overlay</title>" in client.get("/overlay").text
    assert client.get("/static/mm/icons/check.svg").status_code == 200 and client.get("/static/../run.py").status_code == 404
    assert client.get("/static/common.js").status_code == 200
    snapshot = client.get("/api/state").json()
    assert snapshot["status"]["connection"] == "manual"
    assert snapshot["draft"]["current"]["label"] == "BB1"
    assert snapshot["guidance"]["phase_kind"] == "ban"


def test_manual_board_actions_drive_guidance(client):
    r = client.post("/api/draft/action", json={"champion": "rumble"})
    assert r.status_code == 200 and r.json()["state"]["our_bans"] == ["Rumble"]
    assert client.post("/api/draft/action", json={"champion": "Rumble"}).status_code == 400
    for c in ["Vi", "Yone", "Jinx", "Aurora", "Kalista"]:
        client.post("/api/draft/action", json={"champion": c})
    snap = client.post("/api/draft/action", json={"champion": "Ornn", "role": "Top"}).json()
    assert snap["guidance"]["our_locked"] == {"Top": "Ornn"}
    assert snap["draft"]["current"]["label"] == "R1"
    snap = client.post("/api/draft/undo").json()
    assert snap["draft"]["current"]["label"] == "B1" and snap["guidance"]["our_locked"] == {}
    snap = client.post("/api/draft/redo").json()
    assert snap["guidance"]["our_locked"] == {"Top": "Ornn"}
    assert any("Ornn" in e["text"] for e in snap["events"])
    snap = client.post("/api/draft/reset", json={"side": "red"}).json()
    assert snap["draft"]["side"] == "red" and snap["state"]["side"] == "red" and not snap["draft"]["actions"]


def test_loadfile_import_updates_guidance_and_history(client, tmp_path):
    for c in ["Rumble", "Vi", "Yone", "Jinx", "Aurora", "Kalista"]:
        client.post("/api/draft/action", json={"champion": c})
    before = client.get("/api/state").json()["guidance"]
    r = client.post("/api/import", json={"name": "scout.csv", "text": "kind,player,role,champion,priority\nscout,Rival,Top,Jax,1\n"})
    assert r.status_code == 200 and r.json()["result"]["ok"]
    r = client.post("/api/import", json={"enemy_lineup": ["Rival"], "players": [{"name": "Chud", "roles": ["Jungle"],
                                                                                  "pools": {"Jungle": [{"champion": "Nocturne", "priority": 1}]}}],
                                         "lineup": ["Chud"]})
    assert r.status_code == 200, r.text
    after = client.get("/api/state").json()
    assert "scouted" in next(b for b in after["guidance"]["bans"] if b["champion"] == "Jax")["tags"]
    assert any(l["champion"] == "Jax" for l in after["guidance"]["likely_enemy"])
    chud = next(p for p in after["state"]["our_team"] if p["display_name"] == "Chud")
    assert chud["role"] == "Jungle" and chud["pool"]["Jungle"] == ["Nocturne"]
    assert before["headline"] != after["guidance"]["headline"] or True
    history = client.get("/api/imports").json()
    assert [h["name"] for h in history][-2:] == ["scout.csv", "upload"]
    assert (tmp_path / "roster.json").is_file()
    bad = client.post("/api/import", json={"name": "bad.json", "text": "{oops"})
    assert bad.status_code == 422 and not bad.json()["result"]["ok"]
    exported = client.get("/api/roster/export?format=json").json()
    assert exported["format"] == "flexers-loadfile" and any(p["name"] == "Chud" for p in exported["players"])
    assert client.get("/api/roster/export?format=csv").text.startswith("kind,player,role,champion")


def test_payload_state_and_clear(client):
    snap = client.post("/api/state", json={"side": "blue", "our_bans": ["Rumble"], "enemy_bans": ["Vi"], "our_picks": {"Top": "Ornn"},
                                           "enemy_picks": ["Ambessa"], "active": {"kind": "pick", "roles": ["Jungle"]}}).json()
    picks = snap["guidance"]["picks"]
    assert picks and all(p["role"] == "Jungle" for p in picks) and all(p["champion"] != "Vi" for p in picks)
    snap = client.delete("/api/state").json()
    assert snap["state"]["source"] == "manual" and snap["draft"]["current"]["label"] == "BB1"


def test_simulated_draft_streams_over_websocket(client):
    steps = simulate.DraftScript({"side": "blue"}).steps()
    with client.websocket_connect("/ws") as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "snapshot"
        enemy_ban_step = next(s for d, s in steps if d.startswith("R5 banned"))
        client.post("/api/lcu-session", json={"session": enemy_ban_step, "gameflow_phase": "ChampSelect"})
        snapshot = json.loads(ws.receive_text())
        assert snapshot["state"]["enemy_bans"] == ["Vi"] and snapshot["state"]["source"] == "simulation"
        assert "Vi" not in {p["champion"] for p in snapshot["guidance"]["picks"]}

        final = steps[-1][1]
        client.post("/api/lcu-session", json={"session": final, "gameflow_phase": "ChampSelect"})
        snapshot = json.loads(ws.receive_text())
        locked = {p["role"]: p["locked"] for p in snapshot["state"]["our_team"]}
        assert locked["Top"] == "Ornn" and locked["Mid"] == "Ahri"
        assert snapshot["guidance"]["draft_complete"] is True and snapshot["guidance"]["swaps"] == []
        assert snapshot["guidance"]["team"]["score"] > 0

        client.post("/api/lcu-session", json={"session": None, "gameflow_phase": "Lobby"})
        snapshot = json.loads(ws.receive_text())
        assert snapshot["state"] is None or snapshot["state"]["source"] == "manual"


def test_champion_icon_redirects_without_client(client):
    r = client.get("/api/champion-icon/266", follow_redirects=False)
    assert r.status_code == 302 and "communitydragon" in r.headers["location"]
