"""Shared draft control: identity, hard locks, shake-it-up, relay sync."""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import bot
from league_draft_backend import IDENTITIES, draft_identity, recommend_live_drafts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
bridge = importlib.import_module("intent_live_draft_bridge")


def _player(uid, name, roles, champions, priority=None):
    priority = priority or {r: i + 1 for i, r in enumerate(roles[:3])}
    return {"user_id": uid, "display_name": name, "roles": roles, "role_priority": priority, "champions": champions}


FIVE = [
    _player(1, "Cian", ["Top"], {"Top": ["Camille", "Ambessa", "Gnar", "Ornn"]}),
    _player(2, "Kayla", ["Jungle"], {"Jungle": ["Amumu", "Zac", "Vi", "Sejuani"]}),
    _player(3, "Pete", ["Mid"], {"Mid": ["Yasuo", "Ahri", "Galio", "Orianna"]}),
    _player(4, "Jack", ["ADC"], {"ADC": ["Ashe", "Jinx", "Kai'Sa", "Caitlyn"]}),
    _player(5, "Reform", ["Support"], {"Support": ["Lulu", "Nautilus", "Thresh", "Leona"]}),
]


def test_identity_families_cover_every_engine_plan_label():
    assert draft_identity({"team_plan": "dive / all-in", "archetypes": []}) == "dive"
    assert draft_identity({"team_plan": "scaling front-to-back", "archetypes": []}) == "front-to-back"
    assert draft_identity({"team_plan": "balanced", "archetypes": ["protect-the-carry"]}) == "protect"
    assert draft_identity({"team_plan": "balanced", "archetypes": []}) == "balanced"
    assert set(IDENTITIES) >= {"dive", "poke", "front-to-back", "protect", "pick", "tempo", "split"}


def test_required_champion_appears_in_every_draft():
    result = recommend_live_drafts(FIVE, side="blue", limit=6, required_champions={"Jungle": "Zac"})
    assert result["recommendations"]
    assert all(rec["champions"]["Jungle"] == "Zac" for rec in result["recommendations"])
    assert all("identity" in rec for rec in result["recommendations"])


def test_identity_preference_leads_when_a_matching_draft_is_close():
    base = recommend_live_drafts(FIVE, side="blue", limit=10)
    families = {rec["identity"] for rec in base["recommendations"]}
    assert families, "no drafts"
    for family in families:
        if family == "balanced":
            continue
        kept = recommend_live_drafts(FIVE, side="blue", limit=10, identity=family)
        assert kept["identity"] == family
        assert kept["recommendations"][0]["identity"] == family
        assert kept["identities"][family] >= 1
        break


def test_shake_retires_the_drafts_already_seen():
    first = recommend_live_drafts(FIVE, side="blue", limit=3, rotation_token="g1")
    top = "|".join(first["recommendations"][0]["champions"][r] for r in bridge.ROLES)
    shaken = recommend_live_drafts(FIVE, side="blue", limit=3, rotation_token="g1#shake1", exclude_signatures=[top])
    assert "|".join(shaken["recommendations"][0]["champions"][r] for r in bridge.ROLES) != top


def test_bridge_control_merges_normalises_and_shake_retires_the_best():
    bridge._set_control({"identity": None, "role_locks": {}, "champion_locks": {}, "shake": 0, "signature": None}, replace=True)
    bridge._last_best_signature = "Camille|Zac|Ahri|Ashe|Lulu"
    bridge._last_game_id = "game-7"
    bridge._shaken_signatures.clear()
    current = bridge._set_control({"identity": "DIVE", "role_locks": {"Top": "1", "Bogus": 2}, "champion_locks": {"Jungle": "Zac"}})
    assert current["identity"] == "dive" and current["role_locks"] == {"Top": 1} and current["champion_locks"] == {"Jungle": "Zac"}
    assert bridge._set_control({"identity": "not-a-thing"})["identity"] is None
    bridge._set_control({"shake": 1})
    assert bridge._shaken_signatures["game-7"] == ["Camille|Zac|Ahri|Ashe|Lulu"]
    assert bridge._get_control()["signature"] is None
    bridge._set_control({"signature": "A|B|C|D|E"})
    assert bridge._get_control()["signature"] == "A|B|C|D|E" and bridge._get_control()["identity"] is None


def test_relay_applies_a_versioned_control_from_the_captain():
    bridge._cloud_pref_version = 0
    bridge._set_control({"identity": None, "role_locks": {}, "champion_locks": {}, "shake": 0, "signature": None}, replace=True)
    bridge._apply_cloud_control({"preference_version": 3, "preference": "A|B|C|D|E",
                                 "control": {"identity": "poke", "role_locks": {"Mid": 3}, "champion_locks": {}, "shake": 2}})
    control = bridge._get_control()
    assert control["identity"] == "poke" and control["role_locks"] == {"Mid": 3} and control["shake"] == 2 and control["signature"] == "A|B|C|D|E"
    # an older version never overrides
    bridge._apply_cloud_control({"preference_version": 2, "preference": None, "control": {"identity": "dive"}})
    assert bridge._get_control()["identity"] == "poke"


def test_relay_endpoint_stores_and_returns_control():
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        app = web.Application()
        app.router.add_post("/draft-sync/{key}/preference", bot._relay_preference)
        app.router.add_post("/draft-sync/{key}/heartbeat", bot._relay_heartbeat)
        key = "k" * 24
        async with TestClient(TestServer(app)) as client:
            r = await client.post(f"/draft-sync/{key}/preference",
                                  json={"signature": "A|B|C|D|E", "control": {"identity": "dive", "role_locks": {"Top": "1"}, "champion_locks": {"Mid": "Ahri"}, "shake": "1"}})
            body = await r.json()
            assert body["control"] == {"signature": "A|B|C|D|E", "identity": "dive", "role_locks": {"Top": 1}, "champion_locks": {"Mid": "Ahri"}, "shake": 1}
            r = await client.post(f"/draft-sync/{key}/heartbeat", json={"client_id": "client-123456", "player_id": 1, "player_name": "Cian", "priority": 0, "in_champ_select": True})
            beat = await r.json()
            assert beat["control"]["identity"] == "dive" and beat["preference_version"] == body["preference_version"]
    asyncio.run(go())
