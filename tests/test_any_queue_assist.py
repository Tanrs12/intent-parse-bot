"""Any-queue champion select (Will, 2 Nov 2026): solo/duo and normals with strangers on both teams.

Written from scratch against the requirements, not the old Ranked-Flex-only contract:
queue policy, enemy prediction from bans, comfort-first picks, role / pick-order swaps (plan and
automatic execution), one-click lock that refuses stale clicks, anonymous seats, and the bridge's
solo path.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from draftcoach import assist, client_actions, queues
from draftcoach import champions as champ_map
from draftcoach import loadfiles as lf
from draftcoach import state as ds
from draftcoach.hub import Hub
from draftcoach.roster import Roster

TEAM_FILE = Path(__file__).resolve().parent.parent / "loadfiles" / "flexers-team.json"
POSITIONS = ["top", "jungle", "middle", "bottom", "utility"]

TIER = {"patch": "16.21", "roles": {
    "Top": {"Garen": {"win": 52.9, "pick": 6.3}, "Darius": {"win": 52.0, "pick": 7.6}, "Yone": {"win": 50.9, "pick": 8.1},
            "Urgot": {"win": 51.5, "pick": 2.0}, "Teemo": {"win": 50.0, "pick": 0.2}, "Kayle": {"win": 49.0, "pick": 1.4}},
    "Jungle": {"Lee Sin": {"win": 48.5, "pick": 10.0}, "Vi": {"win": 51.0, "pick": 5.0}},
    "Mid": {"Ahri": {"win": 51.0, "pick": 9.0}, "Yone": {"win": 49.5, "pick": 4.0}},
    "ADC": {"Jinx": {"win": 51.5, "pick": 12.0, "games": 120000}, "Ezreal": {"win": 49.0, "pick": 11.0}},
    "Support": {"Thresh": {"win": 50.2, "pick": 9.0}, "Lulu": {"win": 51.0, "pick": 6.0}},
}}
# Kayle answers Yone in top (Yone's lane counter). A ban of Kayle hints that someone plays Yone.
MATCHUPS = {"matchups": {"Top": {"Yone": {"responses": {"Kayle": {"response_advantage": 4.0}}},
                                 "Darius": {"responses": {"Urgot": {"response_advantage": 3.0}}}}}}


def _session(*, names=True, local=0, enemy_locks=None, our_locks=None, enemy_bans=(), active=None, kind="pick",
             position_swaps=None, pick_swaps=None, positions=True, game_id=4242):
    our_locks, enemy_locks = our_locks or {}, enemy_locks or {}
    my = [{"cellId": i, "championId": champ_map.id_for(our_locks.get(i)) or 0,
           "assignedPosition": POSITIONS[i] if positions else "", "team": 1,
           "gameName": f"Stranger{i}" if names else "", "tagLine": "EUW" if names else "", "puuid": f"p{i}"} for i in range(5)]
    their = [{"cellId": i, "championId": champ_map.id_for(enemy_locks.get(i)) or 0, "assignedPosition": "", "team": 2,
              "gameName": "", "tagLine": ""} for i in range(5, 10)]
    bans = [{"id": 900 + n, "actorCellId": 5 + n, "type": "ban", "championId": champ_map.id_for(b), "completed": True,
             "isInProgress": False, "isAllyAction": False} for n, b in enumerate(enemy_bans)]
    picks = [{"id": 100 + i, "actorCellId": i, "type": kind, "championId": champ_map.id_for(our_locks.get(i)) or 0,
              "completed": i in our_locks, "isInProgress": i == active, "isAllyAction": True} for i in range(5)]
    enemy_picks = [{"id": 200 + i, "actorCellId": i, "type": "pick", "championId": champ_map.id_for(enemy_locks.get(i)) or 0,
                    "completed": i in enemy_locks, "isInProgress": False, "isAllyAction": False} for i in range(5, 10)]
    out = {"myTeam": my, "theirTeam": their, "actions": [bans, picks, enemy_picks], "localPlayerCellId": local,
           "bans": {"myTeamBans": [], "theirTeamBans": [champ_map.id_for(b) for b in enemy_bans]},
           "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 25000}, "gameId": game_id}
    if position_swaps is not None:
        out["positionSwaps"] = position_swaps
    if pick_swaps is not None:
        out["pickOrderSwaps"] = pick_swaps
    return out


@pytest.fixture
def roster() -> Roster:
    r = Roster(auto_profiles=True)
    assert lf.import_path(r, TEAM_FILE).ok
    return r


# --------------------------------------------------------------------------- queue policy

@pytest.mark.parametrize("queue,expected", [
    ({"id": 420, "gameMode": "CLASSIC", "mapId": 11}, None),
    ({"id": 400, "gameMode": "CLASSIC", "mapId": 11}, None),
    ({"id": 430, "gameMode": "CLASSIC", "mapId": 11}, None),
    ({"id": 490, "gameMode": "CLASSIC", "mapId": 11}, None),
    ({"id": 0, "name": "Custom"}, None),
    (None, None),
    ({"id": 450, "gameMode": "ARAM", "mapId": 12}, "ARAM"),
    ({"id": 450}, "ARAM"),                                  # lobby fallback without a game mode
    ({"id": 9999, "name": "ARAM Clash"}, "ARAM"),
    ({"id": 2400, "gameMode": "KIWI", "mapId": 12}, "ARAM"),
    ({"id": 1700, "gameMode": "CHERRY", "mapId": 30}, "Arena"),
    ({"id": "bad", "mapId": "x"}, None),                    # malformed fields never raise
])
def test_queue_policy(queue, expected):
    assert queues.unsupported_mode(queue) == expected


# --------------------------------------------------------------------------- enemy prediction

def _state(session, roster=None, seat_fallback=False):
    return ds.state_from_lcu_session(session, roster, "ChampSelect", seat_fallback=seat_fallback)


def test_enemy_prediction_covers_open_roles_from_the_meta():
    rows = assist.predict_enemy(_state(_session()), TIER, MATCHUPS)
    assert {r["role"] for r in rows} == set(assist.ROLES)
    top = [r for r in rows if r["role"] == "Top"]
    assert [r["champion"] for r in top] == ["Yone", "Darius", "Garen"]          # by pick rate, Teemo too rare
    assert abs(sum(r["share"] for r in rows if r["role"] == "ADC") - 1.0) < 1e-6


def test_enemy_ban_of_a_lane_counter_lifts_the_champion_it_protects():
    plain = {r["champion"]: r for r in assist.predict_enemy(_state(_session()), TIER, MATCHUPS) if r["role"] == "Top"}
    hinted = {r["champion"]: r for r in assist.predict_enemy(_state(_session(enemy_bans=["Urgot"])), TIER, MATCHUPS)
              if r["role"] == "Top"}
    assert hinted["Darius"]["ban_hints"] == ["Urgot"] and "protects it" in hinted["Darius"]["why"]
    assert hinted["Darius"]["share"] > plain["Darius"]["share"]
    assert not hinted["Yone"]["ban_hints"]


def test_ban_signal_is_capped():
    huge = {"matchups": {"Top": {"Garen": {"responses": {"Urgot": {"response_advantage": 500.0}}}}}}
    rows = {r["champion"]: r for r in assist.predict_enemy(_state(_session(enemy_bans=["Urgot"])), TIER, huge) if r["role"] == "Top"}
    # Garen (6.3%) may rise by at most 60%: 10.08 < Yone's 8.1? no - but never beyond pick * 1.6.
    assert rows["Garen"]["share"] <= 6.3 * (1 + assist.BAN_SIGNAL_CAP) / (6.3 * 1.6 + 8.1 + 7.6) + 1e-6


def test_banned_picked_and_locked_role_champions_are_excluded():
    sess = _session(enemy_bans=["Yone"], our_locks={2: "Ahri"}, enemy_locks={5: "Jinx"})
    st = _state(sess)
    rows = assist.predict_enemy(st, TIER, MATCHUPS)
    names = {r["champion"] for r in rows}
    assert "Yone" not in names and "Ahri" not in names and "Jinx" not in names
    enemy_jinx = next(p for p in st.enemy_team if p.locked == "Jinx")
    assert enemy_jinx.role == "ADC"                                           # Jinx's lane is certain enough
    assert not any(r["role"] == "ADC" for r in rows)


def test_missing_tierlist_degrades_to_no_prediction(tmp_path):
    empty = assist.load_tierlist(tmp_path / "absent.json")
    assert empty["roles"] == {} and assist.predict_enemy(_state(_session()), empty, MATCHUPS) == []


def test_bundled_tierlist_is_current_and_complete():
    tier = assist.load_tierlist()
    assert tier["patch"] and set(tier["roles"]) == set(assist.ROLES)
    assert all(len(rows) >= 100 for rows in tier["roles"].values())


# --------------------------------------------------------------------------- comfort-first picks

def test_mastery_curve_saturates_for_mains():
    rows = [{"championId": champ_map.id_for("Urgot"), "championPoints": 250_000, "lastPlayTime": 0},
            {"championId": champ_map.id_for("Garen"), "championPoints": 1_000, "lastPlayTime": 40 * 86_400_000},
            {"championId": 0, "championPoints": 5}, "junk", {"championId": "x"}]
    comfort = assist.comfort_from_mastery(rows, now_ms=41 * 86_400_000)
    assert comfort["Urgot"]["comfort"] == 1.0 and not comfort["Urgot"]["recent"]
    assert abs(comfort["Garen"]["comfort"] - 1 / 3) < 0.01 and comfort["Garen"]["recent"]
    assert set(comfort) == {"Urgot", "Garen"}


def test_comfort_outweighs_a_small_win_rate_edge():
    comfort = assist.comfort_from_mastery([{"championId": champ_map.id_for("Urgot"), "championPoints": 300_000}], now_ms=0)
    picks = assist.comfort_picks("Top", set(), TIER, MATCHUPS, comfort=comfort)
    assert picks[0]["champion"] == "Urgot"                                    # 51.5% main beats 52.9% Garen
    assert any("mastery" in w for w in picks[0]["why"])


def test_without_history_the_meta_win_rate_leads_and_pool_bonus_applies():
    picks = assist.comfort_picks("Top", set(), TIER, MATCHUPS)
    assert picks[0]["champion"] == "Garen"
    pooled = assist.comfort_picks("Top", set(), TIER, MATCHUPS, pool=["Kayle"])
    assert pooled[0]["champion"] == "Kayle" and pooled[0]["in_pool"]


def test_unavailable_and_avoided_champions_are_never_offered():
    picks = assist.comfort_picks("Top", {"Garen"}, TIER, MATCHUPS, avoid={"Darius"})
    assert not {"Garen", "Darius"} & {p["champion"] for p in picks}


def test_mastery_champion_not_played_in_the_role_is_not_offered():
    comfort = assist.comfort_from_mastery([{"championId": champ_map.id_for("Jinx"), "championPoints": 900_000}], now_ms=0)
    assert "Jinx" not in {p["champion"] for p in assist.comfort_picks("Top", set(), TIER, MATCHUPS, comfort=comfort)}


def test_lane_counter_moves_a_pick_up():
    picks = {p["champion"]: p for p in assist.comfort_picks("Top", set(), TIER, MATCHUPS, enemy_laner="Darius")}
    assert picks["Urgot"]["lane_edge"] == 3.0 and any("vs Darius" in w for w in picks["Urgot"]["why"])


# --------------------------------------------------------------------------- swap plan

def test_position_swap_requested_towards_the_wanted_role():
    sess = _session(local=4, position_swaps=[{"id": 11, "cellId": 0, "state": "AVAILABLE"},
                                             {"id": 12, "cellId": 2, "state": "AVAILABLE"}])
    plan = assist.swap_plan(sess, "Top", None)
    assert plan["current_role"] == "Support"
    assert plan["position"] == {"swap_id": 11, "with_cell": 0, "state": "AVAILABLE", "from_role": "Support",
                                "to_role": "Top", "family": "position-swaps"}
    assert plan["pick_order"] is None and plan["accept"] == []


def test_pick_order_swap_moves_us_to_the_planned_slot():
    sess = _session(local=0, pick_swaps=[{"id": 21, "cellId": 4, "state": "AVAILABLE"}])
    plan = assist.swap_plan(sess, "Top", 4)
    assert plan["position"] is None                                          # already Top
    assert plan["pick_order"]["swap_id"] == 21 and plan["pick_order"]["to_slot"] == 4


def test_incoming_offers_are_accepted_only_when_they_move_us_to_the_plan():
    sess = _session(local=4, position_swaps=[{"id": 31, "cellId": 0, "state": "RECEIVED"},
                                             {"id": 32, "cellId": 1, "state": "RECEIVED"}])
    plan = assist.swap_plan(sess, "Top", None)
    assert [a["swap_id"] for a in plan["accept"]] == [31]
    assert [d["swap_id"] for d in plan["decline"]] == [32]
    no_target = assist.swap_plan(sess, None, None)
    assert no_target["accept"] == [] and no_target["decline"] == []           # strangers' offers left to the player


def test_swap_plan_survives_malformed_sessions():
    assert assist.swap_plan({}, "Top", 0)["position"] is None
    assert assist.swap_plan({"localPlayerCellId": "x"}, "Top", 0)["pick_order"] is None
    bad = {"localPlayerCellId": 0, "myTeam": [None, {"cellId": "x"}], "actions": [None, [None]], "positionSwaps": [None]}
    assert assist.swap_plan(bad, "Top", 0)["position"] is None


# --------------------------------------------------------------------------- live seats

def test_anonymous_solo_queue_teammates_are_not_given_our_lineup(roster):
    sess = _session(names=False)
    stranger = ds.state_from_lcu_session(sess, roster, "ChampSelect", seat_fallback=False)
    assert all(p.player_key is None for p in stranger.our_team)
    custom = ds.state_from_lcu_session(sess, roster, "ChampSelect", seat_fallback=True)
    assert any(p.player_key for p in custom.our_team)                         # test lobbies still borrow it


def _run(coro):
    return asyncio.run(coro)


def test_hub_uses_lineup_fallback_only_in_test_lobbies(roster, monkeypatch):
    monkeypatch.setenv("INTENT_LIVE", "1")
    sess = _session(names=False)

    async def go(queue):
        hub = Hub(roster)
        await hub.ingest_lcu_session(sess, "ChampSelect", queue)
        await hub.settle()
        return hub

    solo = _run(go({"id": 420, "name": "Ranked Solo/Duo", "type": "RANKED_SOLO_5x5"}))
    assert solo.state is not None and all(p.player_key is None for p in solo.state.our_team)
    custom = _run(go({"id": 0, "name": "Custom", "type": "CUSTOM"}))
    assert any(p.player_key for p in custom.state.our_team)


class FakeRest:
    def __init__(self, offers=None, mastery=None):
        self.calls: list[tuple] = []
        self.offers = offers or {}
        self.mastery = mastery or []

    def get(self, path, **_):
        self.calls.append(("GET", path))
        if path.endswith("/champion-mastery"):
            return self.mastery
        for family in client_actions.SWAP_FAMILIES:
            if path.endswith("/" + family):
                return self.offers.get(family, [])
        return None

    def request(self, method, path, body=None, **_):
        self.calls.append((method, path, body))
        return None


def _live_hub(roster, rest, monkeypatch, tmp_path, role="Top"):
    monkeypatch.setenv("INTENT_LIVE", "1")
    monkeypatch.setattr("draftcoach.hub.VAR_DIR", tmp_path)
    (tmp_path / "my-role.json").write_text(json.dumps({"role": role}), encoding="utf-8")
    hub = Hub(roster)
    hub.rest = rest
    hub.status["connection"] = "connected"          # the client watcher reports this before any session
    return hub


def test_auto_swap_requests_once_and_accepts_wanted_offers(roster, monkeypatch, tmp_path):
    rest = FakeRest(offers={"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"},
                                               {"id": 12, "cellId": 3, "state": "RECEIVED"}],
                            "pick-order-swaps": []})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)
    sess = _session(local=4)
    queue = {"id": 420, "name": "Ranked Solo/Duo", "type": "RANKED_SOLO_5x5"}

    async def go():
        for _ in range(3):                                                   # repeated session events
            await hub.ingest_lcu_session(sess, "ChampSelect", queue)
            await hub.settle()
            if hub._swap_task:
                await hub._swap_task
    _run(go())
    posts = [c for c in rest.calls if c[0] == "POST"]
    assert posts == [("POST", "/lol-champ-select/v1/session/position-swaps/11/request", None)]
    # An offer that does not move us to Top is neither accepted nor declined automatically.
    assert not any("/12/" in c[1] for c in posts)

    rest.offers["position-swaps"] = [{"id": 13, "cellId": 0, "state": "RECEIVED"}]
    _run(go())
    assert ("POST", "/lol-champ-select/v1/session/position-swaps/13/accept", None) in rest.calls


def test_auto_swap_off_and_no_target_never_touch_the_client(roster, monkeypatch, tmp_path):
    rest = FakeRest(offers={"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"}]})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path, role=None)

    async def go():
        await hub.ingest_lcu_session(_session(local=4), "ChampSelect", {"id": 420})
        await hub.settle()
        if hub._swap_task:
            await hub._swap_task
    _run(go())
    roster.settings["auto_swap"] = False
    (tmp_path / "my-role.json").write_text(json.dumps({"role": "Top"}), encoding="utf-8")
    _run(go())
    assert not [c for c in rest.calls if c[0] == "POST"]


def test_assist_view_ranks_this_seat_with_its_mastery(roster, monkeypatch, tmp_path):
    rest = FakeRest(mastery=[{"championId": champ_map.id_for("Urgot"), "championPoints": 400_000, "lastPlayTime": 0}])
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)
    hub._tierlist = TIER

    async def go():
        await hub.ingest_lcu_session(_session(local=0, active=0), "ChampSelect", {"id": 420})
        await hub.settle()
        await asyncio.sleep(0.05)                                            # mastery task
        return hub.snapshot()
    snap = _run(go())
    a = snap["assist"]
    assert a["role"] == "Top" and a["comfort_known"] and a["picks"][0]["champion"] == "Urgot"
    assert {r["role"] for r in a["enemy"]} == set(assist.ROLES)
    assert a["swaps"]["target_role"] == "Top" and a["swaps"]["position"] is None


def test_stale_lock_click_is_refused(roster, monkeypatch, tmp_path):
    rest = FakeRest()
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=0, active=0), "ChampSelect", {"id": 420})
        await hub.settle()
        with pytest.raises(RuntimeError, match="out of date"):
            await hub.client_act("lock", "Garen", action_id=999, kind="pick", game_id=4242)
        with pytest.raises(RuntimeError, match="older screen"):
            await hub.client_act("lock", "Garen")                             # tokenless live clicks are refused
        with pytest.raises(RuntimeError, match="older screen"):
            await hub.client_act("lock", "Garen", action_id=100, kind="pick")             # no game id
        with pytest.raises(RuntimeError, match="out of date"):
            await hub.client_act("lock", "Garen", action_id=100, kind="pick", game_id=1)   # another game's id
        await hub.client_act("lock", "Garen", action_id=100, kind="pick", game_id=4242)
    _run(go())
    patches = [c for c in rest.calls if c[0] == "PATCH"]
    assert patches[-1] == ("PATCH", "/lol-champ-select/v1/session/actions/100",
                           {"championId": champ_map.id_for("Garen"), "completed": True})


def test_seat_swap_endpoint_validates_its_arguments(roster, monkeypatch, tmp_path):
    rest = FakeRest()
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=0), "ChampSelect", {"id": 420})
        await hub.settle()
        with pytest.raises(RuntimeError):
            await hub.client_act("seat_swap", family="trades", swap_id=1, verb="request")
        await hub.client_act("seat_swap", family="pick-order-swaps", swap_id=7, verb="accept")
    _run(go())
    assert ("POST", "/lol-champ-select/v1/session/pick-order-swaps/7/accept", None) in rest.calls


# --------------------------------------------------------------------------- bridge solo path + UI

def test_bridge_coaches_a_solo_player(monkeypatch):
    from tools import intent_live_draft_bridge as bridge

    monkeypatch.setattr(bridge, "_mode_gate", lambda: {"disabled": False, "queue_id": 420, "queue_name": "Ranked Solo/Duo",
                                                       "is_custom": False, "is_bot": False})
    monkeypatch.setattr(bridge, "_party_gate", lambda: {"party_confirmed": False, "party_size": 1, "party_cached": False})
    monkeypatch.setattr(bridge, "_json_get", lambda *_a, **_k: {
        "status": {"lcu_in_champ_select": True, "stale": False, "queue": {"id": 420}},
        "state": {"source": "lcu", "our_team": [], "phase": "BAN_PICK"}})
    result = bridge.build_response()
    assert "premade" not in str(result.get("reason")) and not result.get("disabled")


def test_bridge_keeps_an_unsigned_local_seat_controlled(monkeypatch):
    from tools import intent_live_draft_bridge as bridge

    monkeypatch.setattr(bridge, "_signups", [])
    team = [{"cell_id": i, "role": r, "riot_id": f"S{i}#EUW", "is_local": i == 2,
             "pool": {"Mid": ["Ahri"]} if i == 2 else {}} for i, r in enumerate(assist.ROLES)]
    five = bridge._current_five({"our_team": team})
    assert len(five) == 5
    local = next(p for p in five if p.get("_local_unsigned"))
    assert local["_uncontrolled"] is False and local["champions"]["Mid"][0] == "Ahri"
    assert all(p["_uncontrolled"] for p in five if p is not local)


@pytest.mark.parametrize("queue", [{"id": 1700, "name": "Arena", "gameMode": "CHERRY"}, {"id": 450, "name": "ARAM"}])
def test_bridge_stands_by_in_aram_and_arena(queue):
    from tools import intent_live_draft_bridge as bridge

    assert bridge._queue_traits(queue)["disabled"] is True
    assert bridge._queue_traits({"id": 420, "name": "Ranked Solo/Duo"})["disabled"] is False


def test_selected_comp_renderer_is_exported_where_the_pages_call_it():
    static = Path(__file__).resolve().parent.parent / "draftcoach" / "static"
    js = (static / "selected-comp.js").read_text(encoding="utf-8")
    assert "D.SelectedComp =" in js
    for page in ("coach.html", "overlay.html"):
        assert "D.SelectedComp.render" in (static / page).read_text(encoding="utf-8")


def test_coach_page_renders_the_seat_assist_panel():
    html = (Path(__file__).resolve().parent.parent / "draftcoach" / "static" / "coach.html").read_text(encoding="utf-8")
    assert 'id="assist-section"' in html and "renderAssist(snapshot);" in html
    assert 'clientAct("lock", { champion: r.champion, action_id: local.action_id, kind: "pick", game_id: local.game_id })' in html


# --------------------------------------------------------------------------- review regressions

def _simultaneous_bans(local):
    """Ranked simultaneous ban phase: all ten seats in progress, blue (cells 0-4) listed first."""
    sess = _session(local=local)
    for member in sess["myTeam"]:
        member["cellId"] += 5 if local >= 5 else 0
    if local >= 5:
        for member in sess["theirTeam"]:
            member["cellId"] -= 5
    ours = {m["cellId"] for m in sess["myTeam"]}
    sess["actions"] = [[{"id": 500 + c, "actorCellId": c, "type": "ban", "championId": 0, "completed": False,
                         "isInProgress": True, "isAllyAction": c in ours} for c in range(10)]]
    sess["hasSimultaneousBans"] = True
    return sess


@pytest.mark.parametrize("local", [0, 7])
def test_simultaneous_bans_are_our_turn_on_both_sides(local):
    st = _state(_simultaneous_bans(local))
    assert st.active.kind == "ban" and st.active.ours
    assert local in st.active.cell_ids and all(c in {p.cell_id for p in st.our_team} for c in st.active.cell_ids)
    assert st.active.action_ids[local] == 500 + local


def test_ban_recovery_fills_each_side_independently():
    sess = _session(enemy_bans=["Yone"])
    sess["bans"] = {"myTeamBans": [], "theirTeamBans": [champ_map.id_for("Darius")]}
    sess["actions"][0].append({"id": 990, "actorCellId": 1, "type": "ban", "championId": champ_map.id_for("Ahri"),
                               "completed": True, "isInProgress": False, "isAllyAction": True})
    st = _state(sess)
    assert st.our_bans == ["Ahri"] and st.enemy_bans == ["Darius"]


def test_malformed_session_never_raises_out_of_the_hub(roster, monkeypatch):
    monkeypatch.setenv("INTENT_LIVE", "1")
    hub = Hub(roster)

    async def go():
        await hub.ingest_lcu_session({"myTeam": [None], "actions": [[None, {"type": "pick", "actorCellId": "x"}]]},
                                     "ChampSelect", {"id": None})
    _run(go())
    assert "Could not read" in hub.status["detail"]


def test_saved_flex_only_queue_list_is_migrated(tmp_path):
    for legacy in ([440, 0], [440, 0, 830, 840, 850, 870]):
        data = Roster().to_dict() if hasattr(Roster(), "to_dict") else {}
        data.setdefault("settings", {})["queues"] = legacy
        r = Roster()
        r.load_dict(data)
        assert r.settings["queues"] == []
    r = Roster()
    r.load_dict({"settings": {"queues": [420]}})
    assert r.settings["queues"] == [420]                                      # a deliberate list is kept


def test_live_strangers_never_match_by_bare_name(roster):
    player = next(p for p in roster.players.values() if p.riot_ids)
    game_name, _, tag = player.riot_ids[0].partition("#")
    sess = _session()
    sess["myTeam"][1].update({"gameName": game_name, "tagLine": "ZZZ9", "puuid": "stranger"})
    live = ds.state_from_lcu_session(sess, roster, "ChampSelect", seat_fallback=False)
    assert live.our_team[1].player_key is None
    sess["myTeam"][1]["tagLine"] = tag
    exact = ds.state_from_lcu_session(sess, roster, "ChampSelect", seat_fallback=False)
    assert exact.our_team[1].player_key == player.key


def test_blind_pick_puts_our_seat_in_the_wanted_role(roster, monkeypatch, tmp_path):
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path, role="Mid")

    async def go():
        await hub.ingest_lcu_session(_session(local=0, positions=False), "ChampSelect", {"id": 430, "name": "Normal Blind"})
        await hub.settle()
    _run(go())
    me = next(p for p in hub.state.our_team if p.cell_id == 0)
    assert me.role == "Mid" and me.role_source == "wanted"
    assert sorted(p.role for p in hub.state.our_team) == sorted(assist.ROLES)
    assert all(p.role_source == "guessed" for p in hub.state.our_team if p is not me)


def test_guessed_roles_are_never_pinned_by_the_bridge():
    from tools import intent_live_draft_bridge as bridge

    state = {"our_team": [{"role": "Top", "user_id": 1, "role_source": "guessed", "cell_id": 0},
                          {"role": "Mid", "user_id": 2, "role_source": "lcu", "cell_id": 1}]}
    pins = bridge._role_players_for_state(state, [{"user_id": 1}, {"user_id": 2}], testing=False)
    assert pins == {"Mid": 2}


def test_discord_choice_does_not_steer_a_solo_game():
    from tools import intent_live_draft_bridge as bridge

    control = {"chosen": {"label": "Draft 1"}, "champion_locks": {"Top": "Camille"}, "identity": None,
               "role_locks": {"Top": 11, "Jungle": 12, "Mid": 13}, "shake": 0}
    solo = [{"user_id": 11, "_uncontrolled": False}] + [{"user_id": -100 - i, "_uncontrolled": True} for i in range(4)]
    out = bridge._control_for_lobby(control, solo, {11} | {-100 - i for i in range(4)})
    assert out["chosen"] is None and out["champion_locks"] == {} and out["team_absent"]
    team = [{"user_id": u, "_uncontrolled": False} for u in (11, 12, 13)] + solo[1:3]
    assert bridge._control_for_lobby(control, team, {11, 12, 13, -100, -101})["chosen"] == {"label": "Draft 1"}


def test_browser_rounded_discord_id_still_pins_the_player():
    from tools import intent_live_draft_bridge as bridge

    real, rounded = 332490596004659201, int(float(332490596004659201))
    assert rounded != real
    out = bridge._apply_role_locks({"Top": 7, "Jungle": real}, {"Top": rounded}, {7, real})
    assert out == {"Top": real, "Jungle": 7}


def test_roster_drawer_rows_are_appended_to_the_table():
    html = (Path(__file__).resolve().parent.parent / "draftcoach" / "static" / "coach.html").read_text(encoding="utf-8")
    assert 'append(D.el("tr")).append(' not in html


def test_disconnect_during_scoring_stays_stale(roster, monkeypatch, tmp_path):
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(), "ChampSelect", {"id": 420})
        await hub.set_status("waiting", "client websocket closed")
        await hub.settle()
        await hub.ingest_lcu_session(_session(), "ChampSelect", {"id": 420})   # buffered event lands late
        await hub.settle()
    _run(go())
    assert hub.status["stale"] is True


# --------------------------------------------------------------------------- acceptance-review regressions (Codex 363)

class SlowOffers(FakeRest):
    """The draft changes underneath the pending auto-swap task while its offers are being fetched."""
    def __init__(self, hub_box, new_session, **kw):
        super().__init__(**kw)
        self.hub_box, self.new_session = hub_box, new_session

    def get(self, path, **kw):
        if path.endswith("/position-swaps") and self.new_session is not None:
            self.hub_box[0].last_session, self.new_session = self.new_session, None   # a new game arrived
        return super().get(path, **kw)


def test_auto_swap_is_cancelled_when_the_game_changes_mid_flight(roster, monkeypatch, tmp_path):
    box = [None]
    rest = SlowOffers(box, _session(local=4, game_id=9999),
                      offers={"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"}]})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)
    box[0] = hub

    async def go():
        await hub.ingest_lcu_session(_session(local=4), "ChampSelect", {"id": 420})
        await hub._swap_task
    _run(go())
    assert not [c for c in rest.calls if c[0] == "POST"]


def test_wanted_role_never_follows_a_display_name(roster, monkeypatch, tmp_path):
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path, role=None)
    st = _state(_session(local=0))
    st.our_team[0].display_name = "Alice"
    hub.intent_guidance = {"chosen": {"players": {"Mid": "Alice"}}, "control": {"role_locks": {}}}
    assert hub.wanted_role(st) is None
    player = next(p for p in roster.players.values() if p.user_id)
    st.our_team[0].user_id, st.our_team[0].player_key = player.user_id, player.key
    hub.intent_guidance = {"control": {"role_locks": {"Mid": float(player.user_id)}}}   # browser-rounded id
    assert hub.wanted_role(st) == "Mid"


def test_click_is_checked_against_the_session_it_executes_on(roster, monkeypatch, tmp_path):
    rest = FakeRest()
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=0, active=0), "ChampSelect", {"id": 420})
        await hub.settle()
        hub.last_session = _session(local=4, active=4)          # the raw session moved on before publishing
        with pytest.raises(RuntimeError, match="out of date"):
            await hub.client_act("lock", "Garen", action_id=100, kind="pick", game_id=4242)
        with pytest.raises(RuntimeError, match="out of date"):
            await hub.client_act("lock", "Garen", action_id=104, kind="ban", game_id=4242)       # a ban button on a pick
        await hub.client_act("lock", "Garen", action_id=104, kind="pick", game_id=4242)
    _run(go())
    patches = [c for c in rest.calls if c[0] == "PATCH"]
    assert patches and all(c[1].endswith("/actions/104") for c in patches)


def test_no_second_request_while_one_is_pending(roster, monkeypatch, tmp_path):
    rest = FakeRest(offers={"position-swaps": [{"id": 11, "cellId": 0, "state": "SENT"}],
                            "pick-order-swaps": [{"id": 22, "cellId": 0, "state": "AVAILABLE"}]})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)
    hub.intent_guidance = {"comps": [{"pick_order": [{"role": r} for r in ("Support", "Top", "Jungle", "Mid", "ADC")]}]}

    async def go():
        await hub.ingest_lcu_session(_session(local=4), "ChampSelect", {"id": 420})
        await hub._swap_task
    _run(go())
    assert not [c for c in rest.calls if c[0] == "POST"]


def test_a_tried_request_does_not_block_the_other_family(roster, monkeypatch, tmp_path):
    rest = FakeRest(offers={"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"}],
                            "pick-order-swaps": [{"id": 22, "cellId": 0, "state": "AVAILABLE"}]})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)
    # Seat 4 wants Top (held by seat 0) and the first pick slot (also seat 0's).
    hub.intent_guidance = {"comps": [{"pick_order": [{"role": r} for r in ("Top", "Jungle", "Mid", "ADC", "Support")]}]}
    hub._comfort_game = (4242, "p4")                                         # same game: keep the record
    hub._swaps_done.add(("position-swaps", 11, "request"))                  # already asked once, refused

    async def go():
        await hub.ingest_lcu_session(_session(local=4), "ChampSelect", {"id": 420})
        await hub._swap_task
    _run(go())
    posts = [c for c in rest.calls if c[0] == "POST"]
    assert posts == [("POST", "/lol-champ-select/v1/session/pick-order-swaps/22/request", None)]


def test_one_manual_role_lock_does_not_hide_a_present_five_stack():
    from tools import intent_live_draft_bridge as bridge

    control = {"chosen": {"label": "Draft 2"}, "champion_locks": {}, "identity": None, "role_locks": {"Top": 11},
               "signature": "A|B|C|D|E", "shake": 0}
    five = [{"user_id": u, "_uncontrolled": False} for u in (11, 12, 13, 14, 15)]
    assert bridge._control_for_lobby(control, five, {11, 12, 13, 14, 15})["chosen"] == {"label": "Draft 2"}
    solo = [five[0]] + [{"user_id": -100 - i, "_uncontrolled": True} for i in range(4)]
    out = bridge._control_for_lobby(control, solo, {11})
    assert out["signature"] is None and out["chosen"] is None


def test_live_enemy_scouting_needs_the_full_riot_id(roster):
    opp = roster.ensure_opponent("Jack", "Rivals")
    opp.riot_ids = ["Jack#EUW"]
    assert roster.find_opponent(riot_id="Jack#ZZZ9", strict=True) is None
    assert roster.find_opponent(riot_id="Jack#ZZZ9") is opp
    assert roster.find_opponent(riot_id="jack#euw", strict=True) is opp


def test_an_inferred_flex_lane_stays_open_for_prediction():
    tier = {"roles": {"Top": {"Gragas": {"pick": 2.0, "win": 50, "games": 2000}, "Garen": {"pick": 6.0, "win": 52, "games": 60000}},
                      "Jungle": {"Gragas": {"pick": 3.0, "win": 50, "games": 3000}, "Garen": {"pick": 0.1, "win": 45, "games": 100}},
                      "Mid": {"Gragas": {"pick": 1.0, "win": 50, "games": 1000}, "Ahri": {"pick": 9, "win": 51}},
                      "ADC": {"Jinx": {"pick": 12, "win": 51}}, "Support": {"Lulu": {"pick": 6, "win": 51}}}}
    flex = ds.PlayerSlot(cell_id=5, role="Top", role_source="inferred", locked="Gragas")
    main = ds.PlayerSlot(cell_id=6, role="Top", role_source="inferred", locked="Garen")
    assert not assist.confident_lane(flex, tier) and assist.confident_lane(main, tier)
    assert assist.confident_lane(ds.PlayerSlot(cell_id=7, role="Mid", role_source="lcu"), tier)
    st = ds.DraftState(enemy_team=[flex])
    assert any(r["role"] == "Top" for r in assist.predict_enemy(st, tier, {}))


def test_rejected_update_after_a_good_one_disables_actions(roster, monkeypatch, tmp_path):
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path)
    pushed = []

    async def broadcast(snapshot):
        pushed.append(snapshot)
    hub.broadcast = broadcast

    async def go():
        await hub.ingest_lcu_session(_session(local=0, active=0), "ChampSelect", {"id": 420})
        await hub.settle()
        await hub.ingest_lcu_session({"myTeam": [None], "localPlayerCellId": 0, "actions": [[None]]}, "ChampSelect", {"id": None})
        with pytest.raises(RuntimeError):
            await hub.client_act("lock", "Garen")
    _run(go())
    assert hub.last_session is None and pushed and "Could not read" in pushed[-1]["status"]["detail"]


@pytest.mark.parametrize("rel", ["draftcoach/static/coach.html", "draftcoach/static/overlay.html", "draftcoach/static/hud.html",
                                 "draftcoach/hud.py", "draftcoach/hud_intent.py"])
def test_every_lock_caller_sends_the_action_it_rendered(rel):
    text = (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")
    assert "action_id" in text



# --------------------------------------------------------------------------- Codex re-check regressions (364)

def test_lane_confidence_uses_games_not_pick_rate_shares():
    # 90% of the pick-rate percentage points but only 10% of the games are in Top: not confident.
    tier = {"roles": {"Top": {"Zed": {"pick": 9.0, "games": 1000}}, "Mid": {"Zed": {"pick": 1.0, "games": 9000}}}}
    assert not assist.confident_lane(ds.PlayerSlot(cell_id=5, role="Top", role_source="inferred", locked="Zed"), tier)
    assert assist.confident_lane(ds.PlayerSlot(cell_id=5, role="Mid", role_source="inferred", locked="Zed"), tier)
    assert not assist.confident_lane(ds.PlayerSlot(cell_id=5, role="Mid", role_source="inferred", locked="Zed"),
                                     {"roles": {"Mid": {"Zed": {"pick": 5.0}}}})        # no counts: not confident


class ChangingOffers(FakeRest):
    """The second fetch of the offers (the pre-POST re-check) sees a different world."""
    def __init__(self, first, second, on_second=None):
        super().__init__()
        self.rounds = [first, second]
        self.on_second = on_second
        self.fetches = 0

    def get(self, path, **kw):
        if path.endswith("/position-swaps"):
            self.fetches += 1
            if self.fetches == 2 and self.on_second:
                self.on_second()
        if path.endswith("/position-swaps") or path.endswith("/pick-order-swaps"):
            self.calls.append(("GET", path))
            world = self.rounds[0] if self.fetches <= 1 else self.rounds[1]
            return world.get(path.rsplit("/", 1)[1], [])
        return super().get(path, **kw)


def test_request_is_dropped_when_one_of_ours_went_out_meanwhile(roster, monkeypatch, tmp_path):
    rest = ChangingOffers({"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"}]},
                          {"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"}],
                           "pick-order-swaps": [{"id": 22, "cellId": 1, "state": "SENT"}]})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=4), "ChampSelect", {"id": 420})
        await hub._swap_task
    _run(go())
    assert not [c for c in rest.calls if c[0] == "POST"]


def test_request_is_dropped_when_the_wanted_role_changes_meanwhile(roster, monkeypatch, tmp_path):
    def change_mind():
        (tmp_path / "my-role.json").write_text(json.dumps({"role": "Jungle"}), encoding="utf-8")
    world = {"position-swaps": [{"id": 11, "cellId": 0, "state": "AVAILABLE"}]}
    rest = ChangingOffers(world, world, on_second=change_mind)
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=4), "ChampSelect", {"id": 420})
        await hub.settle()
        await hub._swap_task
    _run(go())
    assert ("POST", "/lol-champ-select/v1/session/position-swaps/11/request", None) not in rest.calls


@pytest.mark.parametrize("rel", ["draftcoach/hud.py", "draftcoach/hud_intent.py"])
def test_native_hud_clicks_carry_the_token_they_were_drawn_with(rel):
    text = (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")
    assert "(bx, by, bw, bh, what, champion, dict(self._drawn_token))" in text
    assert "self._act(hit[4], hit[5], hit[6] if len(hit) > 6 else None)" in text
    assert "(self.snapshot or {}).get(\"local\")" not in text.split("def _act", 1)[1].split("def ", 1)[0]
