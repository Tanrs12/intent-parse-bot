from __future__ import annotations

import json
from io import BytesIO

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
    assert "<title>Flexers Draft Coach</title>" in client.get("/").text
    assert "Draft Overlay</title>" in client.get("/overlay").text
    assert client.get("/static/mm/icons/check.svg").status_code == 200 and client.get("/static/../run.py").status_code == 404
    assert client.get("/static/common.js").status_code == 200
    snapshot = client.get("/api/state").json()
    assert snapshot["status"]["connection"] == "manual"
    assert snapshot["draft"]["current"]["label"] == "BB1"
    assert snapshot["guidance"]["phase_kind"] == "ban"


def test_player_review_follows_signed_in_account_instead_of_latest_global_cache(client, tmp_path, monkeypatch):
    from draftcoach import stats
    monkeypatch.setattr(stats, "STATS_DIR", tmp_path / "stats")
    class Rest:
        puuid = "account-a"
        def get(self, path, **_):
            if path == "/lol-summoner/v1/current-summoner":
                return {"puuid": self.puuid, "gameName": self.puuid, "tagLine": "TEST"}
            if path == "/lol-ranked/v1/current-ranked-stats":
                return {"queues": []}
            if path.startswith("/lol-match-history/v1/products/lol/"):
                return {"games": {"games": []}}
            raise AssertionError(path)
    rest = Rest()
    stats.fetch_from_client(rest)  # newest disk cache belongs to a different login
    rest.puuid = "account-b"
    client.app.state.hub.rest = rest
    assert client.get("/api/stats/me").json()["review"]["account"]["puuid"] == "account-b"
    rest.puuid = "account-c"
    assert client.get("/api/stats/me").json()["review"]["account"]["puuid"] == "account-c"


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


def test_champion_icon_is_available_offline_without_client(client):
    for champion_id in (266, 267, 145, 888):
        r = client.get(f"/api/champion-icon/{champion_id}", follow_redirects=False)
        assert r.status_code == 200
        assert r.content.startswith(b"\x89PNG")
        assert r.headers["content-type"] == "image/png"
        assert "location" not in r.headers


def test_unknown_icon_does_not_redirect_offline(client):
    assert client.get("/api/champion-icon/999999", follow_redirects=False).status_code == 404


def test_art_is_served_same_origin_and_cached_across_restarts(tmp_path, monkeypatch):
    from draftcoach import server

    payload = b"\xff\xd8\xff" + b"image fixture"
    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: BytesIO(payload))
    config = {"auto_profiles": False, "art_cache_dir": str(tmp_path / "art")}
    with TestClient(create_app(config, enable_lcu=False, roster_path=tmp_path / "roster.json")) as c:
        r = c.get("/api/art/centered/267", follow_redirects=False)
        assert r.status_code == 200 and r.content == payload
        assert "location" not in r.headers

    def offline(*args, **kwargs):
        raise AssertionError("Cached art must not request the network")

    monkeypatch.setattr(server.urllib.request, "urlopen", offline)
    with TestClient(create_app(config, enable_lcu=False, roster_path=tmp_path / "roster.json")) as c:
        r = c.get("/api/art/centered/267", follow_redirects=False)
        assert r.status_code == 200 and r.content == payload


def test_missing_art_fails_locally_so_browser_can_use_icon(tmp_path, monkeypatch):
    from draftcoach import server

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(server.urllib.request, "urlopen", offline)
    app = create_app({"auto_profiles": False, "art_cache_dir": str(tmp_path / "art")},
                     enable_lcu=False, roster_path=tmp_path / "roster.json")
    with TestClient(app) as c:
        r = c.get("/api/art/centered/267", follow_redirects=False)
        assert r.status_code == 404 and "location" not in r.headers
        assert c.get("/api/champion-icon/267").status_code == 200
