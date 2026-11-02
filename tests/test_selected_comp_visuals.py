"""Selected Discord cards must retain all visible alternative choices in the desktop UI."""
from __future__ import annotations

import asyncio
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot
from team_sync import draft_control_payload, clean_chosen

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
REC = {
    "champions": {"Top":"Malphite","Jungle":"Qiyana","Mid":"Ryze","ADC":"Twitch","Support":"Lulu"},
    "players": {
        "Top":{"user_id":548212317515677697,"display_name":"JustKayla"},
        "Jungle":{"user_id":139837324438536193,"display_name":"Cian"},
        "Mid":{"user_id":286592835850076162,"display_name":"Ste"},
        "ADC":{"user_id":332490596004659201,"display_name":"Jack"},
        "Support":{"user_id":265219035019477002,"display_name":"Jezz"},
    },
    "team_plan":"scaling front-to-back",
    "role_alternatives":{"Top":["Volibear","Nasus"],"Jungle":["Elise","Lee Sin"],"ADC":["Jinx","Kog'Maw"]},
    "learn_suggestions":{"Top":["Darius"],"Mid":["Galio"],"ADC":["Aphelios"]},
}


def test_discord_payload_mirrors_the_same_plan_multi_picks():
    chosen = draft_control_payload(REC,label="Draft 3")["control"]["chosen"]
    assert chosen["champions"]["ADC"] == "Twitch"
    assert chosen["options"]["ADC"][:3] == ["Twitch", "Jinx", "Kog'Maw"]
    assert chosen["options"]["Jungle"][:3] == ["Qiyana", "Elise", "Lee Sin"]
    assert chosen["options"]["Top"][:3] == ["Malphite", "Volibear", "Nasus"]
    assert "Galio" not in chosen["options"]["Mid"]  # Ste's unsubmitted learning slots never become playable.
    assert chosen["learn_options"]["Mid"] == []


def test_relay_heartbeat_and_bridge_keep_multi_picks():
    async def check():
        app = web.Application()
        app.router.add_post("/draft-sync/{key}/preference", bot._relay_preference)
        app.router.add_post("/draft-sync/{key}/heartbeat", bot._relay_heartbeat)
        async with TestClient(TestServer(app)) as client:
            key = "t" * 24
            payload = draft_control_payload(REC, label="Draft 3")
            response = await client.post(f"/draft-sync/{key}/preference",json=payload)
            assert response.status == 200
            data = await response.json()
            assert data["control"]["chosen"]["options"]["ADC"][:3] == ["Twitch","Jinx","Kog'Maw"]
            response = await client.post(f"/draft-sync/{key}/heartbeat",json={
                "client_id": "integration-test", "player_name": "Test", "player_id": 9, "in_champ_select": False
            })
            beat = await response.json()
            assert clean_chosen(beat["control"]["chosen"])["options"]["Top"][:3] == ["Malphite","Volibear","Nasus"]
    asyncio.run(check())


def test_legacy_comp_without_options_still_shows_its_primary():
    clean = clean_chosen({"champions":{"Mid":"Ryze"},"players":{"Mid":"Ste"}})
    assert clean["options"]["Mid"] == ["Ryze"]
    assert clean["learn_options"]["Mid"] == []


def test_mobile_and_wide_coach_load_the_same_visual_component():
    for page in ("overlay","coach"):
        html = (ROOT / f"draftcoach/static/{page}.html").read_text(encoding="utf-8")
        assert 'src="/static/selected-comp.js"' in html
        assert 'href="/static/mm/selected-comp.css"' in html
        assert 'id="selected-comp-board"' in html or 'preview.id = "selected-comp-board"' in html
        assert "D.SelectedComp.render" in html
    js = (ROOT / "draftcoach/static/selected-comp.js").read_text(encoding="utf-8")
    assert "chosen.options" in js
    assert "chosen.learn_options" in js
    assert "D.portrait" in js
    assert "ownLocks" in js
    assert "unavailable" in js
