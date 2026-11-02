"""A draft chosen in Discord drives the five apps: pinned roles, locked five, re-solve one seat on a ban."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import bot
from league_draft_backend import recommend_live_drafts
from team_sync import (chosen_status, clean_chosen, clear_control_payload, comp_signature,
                       draft_control_payload, identity_for_plan)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
bridge = importlib.import_module("intent_live_draft_bridge")

REC = {
    "champions": {"Top": "Camille", "Jungle": "Zac", "Mid": "Ahri", "ADC": "Ashe", "Support": "Lulu"},
    "players": {"Top": {"user_id": 1, "display_name": "Cian"}, "Jungle": {"user_id": 2, "display_name": "Kayla"},
                "Mid": {"user_id": 3, "display_name": "Pete"}, "ADC": {"user_id": 4, "display_name": "Jack"},
                "Support": {"user_id": 5, "display_name": "Reform"}},
    "team_plan": "dive / all-in",
    "archetypes": ["dive"],
}


def _player(uid, name, roles, champions):
    return {"user_id": uid, "display_name": name, "roles": roles,
            "role_priority": {r: i + 1 for i, r in enumerate(roles[:3])}, "champions": champions}


FIVE = [
    _player(1, "Cian", ["Top"], {"Top": ["Camille", "Ambessa", "Gnar", "Ornn"]}),
    _player(2, "Kayla", ["Jungle"], {"Jungle": ["Zac", "Amumu", "Vi", "Sejuani"]}),
    _player(3, "Pete", ["Mid"], {"Mid": ["Ahri", "Yasuo", "Galio", "Orianna"]}),
    _player(4, "Jack", ["ADC"], {"ADC": ["Ashe", "Jinx", "Kai'Sa", "Caitlyn"]}),
    _player(5, "Reform", ["Support"], {"Support": ["Lulu", "Nautilus", "Thresh", "Leona"]}),
]


def test_discord_choice_becomes_pinned_roles_soft_five_and_a_style():
    payload = draft_control_payload(REC, label="Draft 3")
    assert payload["signature"] == "Camille|Zac|Ahri|Ashe|Lulu"
    control = payload["control"]
    assert control["role_locks"] == {"Top": 1, "Jungle": 2, "Mid": 3, "ADC": 4, "Support": 5}
    assert control["champion_locks"] == {}
    assert control["chosen"]["mode"] == "adaptive"
    assert control["identity"] == "dive"
    assert control["chosen"]["label"] == "Draft 3" and control["chosen"]["source"] == "discord"
    assert control["chosen"]["players"]["Mid"] == "Pete"
    assert clear_control_payload() == {"signature": None, "control": None}


def test_plan_labels_map_to_identity_families():
    assert identity_for_plan("scaling front-to-back") == "front-to-back"
    assert identity_for_plan("poke + side-lane") == "poke"
    assert identity_for_plan("balanced", ["protect-the-carry"]) == "protect"
    assert identity_for_plan("balanced", []) is None


def test_chosen_status_names_the_replaced_seat_and_why():
    chosen = clean_chosen(draft_control_payload(REC, label="Draft 3")["control"]["chosen"])
    lead = {"champions": {**REC["champions"], "Jungle": "Amumu", "Mid": "Galio"}}
    status = chosen_status(chosen, lead, our_bans=[], enemy_bans=["Zac"], enemy_locked=["Ahri"])
    assert status["replaced"] == {"Jungle": {"was": "Zac", "now": "Amumu", "why": "banned"},
                                  "Mid": {"was": "Ahri", "now": "Galio", "why": "taken by the enemy"}}
    assert status["intact"] is False
    assert chosen_status(chosen, {"champions": REC["champions"]}, our_bans=[], enemy_bans=[], enemy_locked=[])["intact"] is True
    assert chosen_status(None, lead, our_bans=[], enemy_bans=[], enemy_locked=[]) is None
    assert clean_chosen({"label": "x"}) is None


def test_engine_preserves_four_when_fifth_is_banned():
    payload = draft_control_payload(REC, label="Draft 1")
    control = payload["control"]
    unavailable = {"Zac"}
    result = recommend_live_drafts(FIVE, side="blue", limit=5, unavailable=unavailable,
                                   preferred_champions=REC["champions"], chosen_champions=REC["champions"],
                                   role_players=control["role_locks"], identity=control["identity"])
    lead = result["recommendations"][0]
    assert lead["champions"]["Top"] == "Camille" and lead["champions"]["Mid"] == "Ahri"
    assert lead["champions"]["ADC"] == "Ashe" and lead["champions"]["Support"] == "Lulu"
    assert lead["champions"]["Jungle"] != "Zac"
    assert {role: lead["players"][role]["user_id"] for role in bridge.ROLES} == control["role_locks"]


def test_bridge_control_carries_the_chosen_draft_through_the_relay():
    bridge._cloud_pref_version = 0
    bridge._set_control({"identity": None, "role_locks": {}, "champion_locks": {}, "shake": 0, "chosen": None, "signature": None}, replace=True)
    payload = draft_control_payload(REC, label="Draft 2")
    bridge._apply_cloud_control({"preference_version": 9, "preference": payload["signature"], "control": payload["control"]})
    control = bridge._get_control()
    assert control["chosen"]["label"] == "Draft 2" and control["champion_locks"] == {}
    assert control["chosen"]["mode"] == "adaptive"
    assert control["signature"] == payload["signature"]
    bridge._apply_cloud_control({"preference_version": 10, "preference": None, "control": None})
    assert bridge._get_control()["chosen"] is None and bridge._get_control()["signature"] is None
    bridge._set_control({"chosen": {"nonsense": True}})
    assert bridge._get_control()["chosen"] is None


def test_relay_stores_the_chosen_block_and_release_clears_it():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        app = web.Application()
        app.router.add_post("/draft-sync/{key}/preference", bot._relay_preference)
        app.router.add_post("/draft-sync/{key}/heartbeat", bot._relay_heartbeat)
        key = "k" * 24
        async with TestClient(TestServer(app)) as client:
            r = await client.post(f"/draft-sync/{key}/preference", json=draft_control_payload(REC, label="Draft 4"))
            body = await r.json()
            assert body["control"]["chosen"]["label"] == "Draft 4"
            assert body["control"]["chosen"]["mode"] == "adaptive"
            assert body["control"]["champion_locks"] == {}
            assert body["control"]["chosen"]["champions"]["Support"] == "Lulu"
            assert body["control"]["role_locks"]["ADC"] == 4
            r = await client.post(f"/draft-sync/{key}/heartbeat",
                                  json={"client_id": "client-123456", "player_id": 1, "player_name": "Cian", "priority": 0, "in_champ_select": True})
            beat = await r.json()
            assert beat["control"]["chosen"]["label"] == "Draft 4" and beat["preference"] == comp_signature(REC["champions"])
            r = await client.post(f"/draft-sync/{key}/preference", json=clear_control_payload())
            body = await r.json()
            assert body["control"] is None and body["signature"] is None
    asyncio.run(go())


def test_discord_live_mirror_text_follows_the_published_guidance():
    from cogs.league_flex import live_draft_text
    payload = {"active": True, "guidance": {
        "comps": [{"champions": {**REC["champions"], "Jungle": "Amumu"}}],
        "chosen": {"label": "Draft 3", "plan": "dive / all-in", "players": {r: REC["players"][r]["display_name"] for r in REC["players"]},
                   "replaced": {"Jungle": {"was": "Zac", "now": "Amumu", "why": "banned"}}},
        "our_locked": {"Top": "Camille"}, "our_bans": ["Yone"], "enemy_bans": ["Zac"], "enemy_locked": ["Syndra"],
        "picking_now": "Kayla picks now",
    }}
    text = live_draft_text(payload, "Draft 3")
    assert "Live draft · Draft 3" in text and "Enemy bans: Zac" in text and "Enemy picks: Syndra" in text
    assert "🔒 **Top** Cian · Camille" in text and "Kayla · Amumu  (was Zac, banned)" in text
    assert text.endswith("Kayla picks now")
    assert live_draft_text({"active": False}, "Draft 3") == ""
