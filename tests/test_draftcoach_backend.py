"""Draft Coach ranks through the repository's /league game backend in every role-draft queue."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from draftcoach import advisor, backend
from draftcoach import loadfiles as lf
from draftcoach import state as ds
from draftcoach.hub import Hub
from draftcoach.roster import Roster

TEAM_FILE = Path(__file__).resolve().parent.parent / "loadfiles" / "flexers-team.json"


def session(ours: dict, theirs: dict, bans=(), their_bans=(), active_cells=(), kind="pick"):
    """Minimal champ-select session: five seats each side, ranked-flex style assigned positions."""
    from draftcoach import champions as champ_map
    positions = ["top", "jungle", "middle", "bottom", "utility"]
    my_team = [{"cellId": i, "championId": champ_map.id_for(ours.get(i)) or 0, "assignedPosition": positions[i],
                "gameName": f"Player{i + 1}", "tagLine": "EUW", "team": 1, "summonerId": 100 + i} for i in range(5)]
    their_team = [{"cellId": i, "championId": champ_map.id_for(theirs.get(i)) or 0, "assignedPosition": positions[i - 5],
                   "gameName": f"Enemy{i - 4}", "tagLine": "EUW", "team": 2} for i in range(5, 10)]
    actions = [[{"id": i, "actorCellId": i % 5 if i < 5 else i, "type": "ban", "championId": 0, "completed": True, "isInProgress": False, "isAllyAction": i < 5}
                for i in range(10)]]
    actions.append([{"id": 100 + i, "actorCellId": i, "type": kind, "championId": 0, "completed": False,
                     "isInProgress": i in active_cells, "isAllyAction": True} for i in range(5)])
    return {"myTeam": my_team, "theirTeam": their_team, "actions": actions, "localPlayerCellId": 0,
            "bans": {"myTeamBans": [champ_map.id_for(b) for b in bans], "theirTeamBans": [champ_map.id_for(b) for b in their_bans]},
            "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 25000}}


@pytest.fixture
def roster() -> Roster:
    r = Roster(auto_profiles=True)
    result = lf.import_path(r, TEAM_FILE)
    assert result.ok, result.errors
    return r


def test_backend_is_available_and_drives_the_ranking(roster):
    assert backend.available(), "league_draft_backend must import without discord"
    state = ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster)
    g = advisor.advise(state, roster)
    assert any(n.startswith("ranking: shared /league game backend") for n in g["notes"]), g["notes"]
    assert g["comps"] and g["picks"]
    # The leading composition's reveal sequence comes from the backend's roster-real pick order.
    assert g["pick_order_source"] == "backend" and len(g["pick_order"]) == 5


def test_backend_can_be_switched_off(roster):
    roster.settings["ranking"] = "engine"
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    assert not any(n.startswith("ranking: shared") for n in g["notes"]) and g["picks"]


def test_signup_shape_matches_the_discord_command(roster):
    state = ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster)
    slot = state.our_team[0]
    signup = backend.signup_for_seat(slot, "Top", ["Camille", "Gnar"], roster, 139837324438536193)
    assert signup["user_id"] == 139837324438536193 and signup["roles"][0] == "Top"
    assert signup["role_priority"]["Top"] == 1 and set(signup["role_priority"].values()) <= {1, 2, 3}
    assert signup["champions"]["Top"][:2] == ["Camille", "Gnar"]


def _ingest(hub, sess, queue):
    async def go():
        await hub.ingest_lcu_session(sess, "ChampSelect", queue)
        await hub.settle()
    asyncio.run(go())


def test_every_role_draft_queue_is_coached_and_aram_arena_stand_by(roster, monkeypatch):
    # Will, 2 Nov 2026: coach normals, solo queue and every queue except ARAM (Arena has no roles either).
    monkeypatch.setenv('INTENT_LIVE', '1')
    sess = session({}, {}, [], [], [0])
    coached = [{"id": 420, "name": "Ranked Solo/Duo", "type": "RANKED_SOLO_5x5", "gameMode": "CLASSIC", "mapId": 11},
               {"id": 440, "name": "Ranked Flex", "type": "RANKED_FLEX_SR", "gameMode": "CLASSIC", "mapId": 11},
               {"id": 400, "name": "Normal Draft", "type": "NORMAL", "gameMode": "CLASSIC", "mapId": 11},
               {"id": 430, "name": "Normal Blind", "type": "NORMAL", "gameMode": "CLASSIC", "mapId": 11},
               {"id": 490, "name": "Quickplay", "type": "NORMAL", "gameMode": "CLASSIC", "mapId": 11},
               {"id": 700, "name": "Clash", "type": "CLASH", "gameMode": "CLASSIC", "mapId": 11},
               {"id": 0, "name": "Custom", "type": "CUSTOM"},
               {"id": 3140, "name": "Multiplayer Practice Tool Custom", "type": "PRACTICETOOL"},
               {"id": 850, "name": "Intermediate", "type": "BOT", "category": "VersusAi"},
               None]                                      # unknown queue (REST failure) is not a reason to go silent
    for queue in coached:
        hub = Hub(roster)
        _ingest(hub, sess, queue)
        assert hub.state is not None and hub.state.source == "lcu", queue
    for queue, label in [({"id": 450, "name": "ARAM", "type": "ARAM_UNRANKED_5x5", "gameMode": "ARAM", "mapId": 12}, "ARAM"),
                         ({"id": 2400, "name": "ARAM: Mayhem", "gameMode": "KIWI", "mapId": 12}, "ARAM"),
                         ({"id": 1700, "name": "Arena", "gameMode": "CHERRY", "mapId": 30}, "Arena")]:
        hub = Hub(roster)
        _ingest(hub, sess, queue)
        assert hub.state is None and hub.status["detail"].startswith(f"Standing by: {label}"), queue
    hub_any = Hub(roster); hub_any.any_queue = True       # --any-queue never turns ARAM into a role draft
    _ingest(hub_any, sess, {"id": 450, "name": "ARAM", "gameMode": "ARAM", "mapId": 12})
    assert hub_any.state is None


def test_explicit_queue_list_still_filters(roster, monkeypatch):
    monkeypatch.setenv('INTENT_LIVE', '1')
    sess = session({}, {}, [], [], [0])
    roster.settings["queues"] = [440, 0]
    hub = Hub(roster)
    _ingest(hub, sess, {"id": 420, "name": "Ranked Solo/Duo", "type": "RANKED_SOLO_5x5"})
    assert hub.state is None and "settings.queues" in hub.status["detail"]
    _ingest(hub, sess, {"id": 440, "name": "Ranked Flex", "type": "RANKED_FLEX_SR"})
    assert hub.state is not None and hub.state.source == "lcu"


def test_team_loadfile_and_defaults_coach_every_queue(roster):
    assert roster.settings["queues"] == []
    assert Hub(roster).allowed_queues() is None


def test_short_lobby_is_padded_to_five_seats_from_the_lineup(roster):
    sess = session({}, {}, [], [], [0])
    sess["myTeam"] = sess["myTeam"][:1]                     # solo custom: one human on our side
    state = ds.state_from_lcu_session(sess, roster)
    assert len(state.our_team) == 5
    assert all(p.player_key for p in state.our_team[1:]) and len({p.player_key for p in state.our_team if p.player_key}) >= 4
    assert len({p.cell_id for p in state.our_team}) == 5


def test_signup_cache_is_seeded_from_the_roster(tmp_path, roster):
    cache = tmp_path / "data" / "live_flex_signups.json"
    assert backend.write_signup_cache(roster, cache)
    import json
    rows = json.loads(cache.read_text(encoding="utf-8"))
    assert len(rows) >= 5 and all({"user_id", "display_name", "roles", "role_priority", "champions"} <= set(r) for r in rows)
    assert not backend.write_signup_cache(roster, cache)   # unchanged roster: not rewritten
    cache.with_name(backend.SEED_MARKER).unlink()           # a bridge-owned file is never overwritten
    assert not backend.write_signup_cache(roster, cache)


def test_bridge_module_imports_without_discord():
    import importlib, sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    bridge = importlib.import_module("intent_live_draft_bridge")
    assert callable(bridge.build_response) and hasattr(bridge, "Handler")
