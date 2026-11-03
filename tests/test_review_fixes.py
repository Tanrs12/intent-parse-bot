"""Fixes from the 3 November review of the Discord-decoupling work.

- the team room names a player before champion select (the signed-in League account, not the PC);
- the app that sent a draft puts it back when the relay restarts with an empty room;
- a signup row with only empty pools does not shadow the roster's pools;
- the server resolves the signed-in account to a roster player, with the id as a string.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import socket
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draftcoach import gameplan
from draftcoach.server import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
bridge = importlib.import_module("intent_live_draft_bridge")

ACCOUNT = {"riot_id": "JackJak#EUW", "user_id": "332490596004659201", "display_name": "Jack"}


def test_identity_falls_back_to_the_signed_in_account_before_champion_select(monkeypatch):
    monkeypatch.setattr(bridge, "_set_wanted_role", lambda *_: None)
    monkeypatch.setattr(bridge, "_get_wanted_role", lambda: None)
    # no seat yet, but the client knows who is signed in
    monkeypatch.setattr(bridge, "_json_get", lambda *a, **k: {"status": {"local_account": ACCOUNT}, "state": {"our_team": []}})
    identity = bridge._auto_identity()
    assert identity["player_id"] == 332490596004659201 and identity["player_name"] == "Jack"
    assert identity["in_champ_select"] is False
    # a real seat always wins over the signed-in account
    monkeypatch.setattr(bridge, "_json_get", lambda *a, **k: {"status": {"local_account": ACCOUNT, "lcu_in_champ_select": True},
                                                            "state": {"local_cell_id": 2, "our_team": [{"cell_id": 2, "user_id": 139837324438536193, "display_name": "Cian", "role": "Top"}]}})
    identity = bridge._auto_identity()
    assert identity["player_id"] == 139837324438536193 and identity["player_name"] == "Cian"
    # nothing known: the PC name, as before
    monkeypatch.setattr(bridge, "_json_get", lambda *a, **k: {"status": {}, "state": {"our_team": []}})
    identity = bridge._auto_identity()
    assert identity["player_id"] == 0 and identity["player_name"] == socket.gethostname()


def _chosen_control(label: str) -> dict:
    champions = {"Top": "Camille", "Jungle": "Amumu", "Mid": "Yasuo", "ADC": "Jinx", "Support": "Lulu"}
    return {"identity": None, "role_locks": {}, "champion_locks": {}, "shake": 0,
            "chosen": {"mode": "adaptive", "label": label, "source": "app", "plan": "front-to-back", "champions": champions, "players": {}}}


def test_the_app_that_sent_a_draft_restores_it_when_the_room_is_recreated(monkeypatch):
    posted = []

    def fake_preference(signature, control=None):
        posted.append((signature, control))
        return {"ok": True, "signature": signature, "control": control, "preference_version": 1, "room_epoch": 2.0}

    monkeypatch.setattr(bridge, "_cloud_preference", fake_preference)
    monkeypatch.setattr(bridge, "_last_sent", None)
    monkeypatch.setattr(bridge, "_cloud_room_epoch", 1.0)
    monkeypatch.setattr(bridge, "_cloud_pref_version", 5)
    bridge._set_control(_chosen_control("Draft 3"), replace=True)
    bridge._remember_sent("Camille|Amumu|Yasuo|Jinx|Lulu", _chosen_control("Draft 3"))

    # the bot redeployed: a fresh room, version 0, no control
    bridge._apply_cloud_control({"preference_version": 0, "room_epoch": 2.0, "control": None, "preference": None})
    assert len(posted) == 1 and posted[0][1]["chosen"]["label"] == "Draft 3"
    assert (bridge._get_control().get("chosen") or {}).get("label") == "Draft 3"
    assert bridge._cloud_pref_version == 1
    # the same empty room again (e.g. the next heartbeat before the relay caught up) is not re-posted twice
    bridge._apply_cloud_control({"preference_version": 0, "room_epoch": 2.0, "control": None, "preference": None})
    assert len(posted) == 1 and (bridge._get_control().get("chosen") or {}).get("label") == "Draft 3"
    # a release forgets the plan: a later fresh room stays empty
    bridge._remember_sent(None, {"identity": None, "role_locks": {}, "champion_locks": {}, "shake": 0, "chosen": None})
    bridge._apply_cloud_control({"preference_version": 0, "room_epoch": 3.0, "control": None, "preference": None})
    assert len(posted) == 1 and not bridge._get_control().get("chosen")
    # a plan older than the age limit is not restored either
    bridge._remember_sent("Camille|Amumu|Yasuo|Jinx|Lulu", _chosen_control("Draft 3"))
    bridge._last_sent["at"] = time.time() - bridge._LAST_SENT_MAX_AGE - 1
    bridge._apply_cloud_control({"preference_version": 0, "room_epoch": 4.0, "control": None, "preference": None})
    assert len(posted) == 1 and not bridge._get_control().get("chosen")


def test_a_signup_row_with_only_empty_pools_does_not_shadow_the_roster(tmp_path):
    cache = tmp_path / "signups.json"
    cache.write_text(json.dumps([{"user_id": 2, "display_name": "Jack (discord)", "roles": ["ADC"], "role_priority": {}, "champions": {"ADC": [], "Mid": []}}]), encoding="utf-8")

    class Entry:
        def __init__(self, champion):
            self.champion = champion

    class Player:
        def __init__(self, name, user_id, roles, pools):
            self.name, self.user_id, self.roles, self.pools = name, user_id, roles, pools

    class Roster:
        players = {"jack": Player("Jack", 2, ["ADC"], {"ADC": [Entry("Jinx")]})}

    rows = gameplan.load_signups(Roster(), cache)
    assert [r["display_name"] for r in rows] == ["Jack"] and rows[0]["champions"] == {"ADC": ["Jinx"]}


def test_the_server_resolves_the_signed_in_account_to_a_roster_player(tmp_path, monkeypatch):
    app = create_app({"auto_profiles": False, "debounce_ms": 0}, enable_lcu=False, roster_path=tmp_path / "roster.json")
    with TestClient(app) as client:
        local_account = client.app.state.local_account
        roster = client.app.state.roster

        class Rest:
            def get(self, path, **_):
                assert path == "/lol-summoner/v1/current-summoner"
                return {"gameName": "JackJak", "tagLine": "EUW"}

        class Player:
            name = "Jack"

        monkeypatch.setattr(roster, "profile_user_id_for", lambda riot_id: 332490596004659201 if riot_id == "JackJak#EUW" else None)
        monkeypatch.setattr(roster, "find_player", lambda **kw: Player() if kw.get("user_id") == 332490596004659201 else None)
        assert asyncio.run(local_account(Rest())) == {"riot_id": "JackJak#EUW", "user_id": "332490596004659201", "display_name": "Jack"}

        class Unknown(Rest):
            def get(self, path, **_):
                return {"gameName": "Someone", "tagLine": "ELSE"}

        assert asyncio.run(local_account(Unknown())) == {"riot_id": "Someone#ELSE", "user_id": None, "display_name": None}

        class Broken(Rest):
            def get(self, path, **_):
                raise OSError("client not ready")

        assert asyncio.run(local_account(Broken())) is None
