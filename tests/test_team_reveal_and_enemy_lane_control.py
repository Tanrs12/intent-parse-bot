"""Team-wide reveal order (picks can be traded) + captain enemy role edits."""
from __future__ import annotations
from types import SimpleNamespace
from pathlib import Path

import pytest

from draftcoach.hub import Hub, _strategic_team_pick
from draftcoach import champions as champions
from tools import intent_live_draft_bridge as bridge


def _team_state(*, ours=None, enemy=None, game="lobby-A"):
    ours = ours or {}
    enemy = enemy or {}
    return SimpleNamespace(
        our_locked=lambda: ours, enemy_locked=lambda: enemy,
        our_bans=[], enemy_bans=[], game_id=game,
    )


def _shared_plan():
    order = [
        {"slot":1, "role":"Jungle", "champion":"Lee Sin"},
        {"slot":2, "role":"Support", "champion":"Braum"},
        {"slot":3, "role":"ADC", "champion":"Jinx"},
        {"slot":4, "role":"Mid", "champion":"Orianna"},
        {"slot":5, "role":"Top", "champion":"Ornn"},
    ]
    return {"comps":[{"pick_order":order, "players":{
        "Jungle":"Kayla", "Support":"Jezz", "ADC":"Jack",
        "Mid":"Rat", "Top":"Cian"}}], "control":{"chosen":{"champions":{
            p["role"]:p["champion"] for p in order}}}}


def _session(*cells, hovers=None):
    hovers=hovers or {}
    return {"actions":[[{"type":"pick","isAllyAction":True,
                          "isInProgress":True,"completed":False,
                          "actorCellId":cell,
                          "championId":champions.id_for(hovers.get(cell)) or 0}
                         for cell in cells]]}


def test_top_player_first_pick_reveals_jungle_for_teammate():
    pick=_strategic_team_pick(
        _shared_plan(),_team_state(),_session(3),3,lambda c:True)
    assert pick["champion"]=="Lee Sin" and pick["role"]=="Jungle"
    assert pick["for_player"]=="Kayla"
    assert pick["source"]=="team-reveal-order"


def test_top_player_without_jungle_champion_picks_next_ownable_team_champion():
    allowed={"Braum","Ornn"}
    pick=_strategic_team_pick(
        _shared_plan(),_team_state(),_session(3),3,allowed.__contains__)
    assert pick["champion"]=="Braum"
    assert pick["role"]=="Support"
    assert "Lee Sin" not in allowed


def test_next_pick_advances_by_champion_not_by_physical_actor_role():
    pick=_strategic_team_pick(
        _shared_plan(),_team_state(ours={"Top":"Lee Sin"}),
        _session(4),4,lambda c:True)
    assert pick["champion"]=="Braum"
    assert pick["role"]=="Support"


def test_two_simultaneous_teammates_have_distinct_targets():
    a=_strategic_team_pick(_shared_plan(),_team_state(),
                           _session(3,4),3,lambda c:True)
    b=_strategic_team_pick(_shared_plan(),_team_state(),
                           _session(3,4),4,lambda c:True)
    assert a["champion"]=="Lee Sin"
    assert b["champion"]=="Braum"
    occupied=_strategic_team_pick(_shared_plan(),_team_state(),
                                  _session(3,4,hovers={3:"Lee Sin"}),
                                  4,lambda c:True)
    assert occupied["champion"]=="Braum"


def test_simultaneous_account_ownership_fallback_does_not_steal_other_turn():
    first=_strategic_team_pick(_shared_plan(),_team_state(),
                               _session(3,4),3,{"Jinx"}.__contains__)
    second=_strategic_team_pick(_shared_plan(),_team_state(),
                                _session(3,4),4,{"Braum"}.__contains__)
    assert first["champion"]=="Jinx"
    assert second["champion"]=="Braum"


def test_pickable_gate_rejects_all_unowned_team_champions():
    assert _strategic_team_pick(
        _shared_plan(),_team_state(),_session(3),3,lambda c:False) is None
    assert _strategic_team_pick(_shared_plan(),_team_state(),
                                _session(3),8,lambda c:True) is None


def test_selected_team_draft_disables_auto_pick_order_swapping():
    stub=SimpleNamespace(
        shared_guidance=lambda: _shared_plan(),
        guidance={"pick_order":[{"role":"Top"}]},
    )
    assert Hub.wanted_pick_slot(stub,"Top") is None


def test_premade_champion_pick_is_attributed_to_planned_lane_not_actor_lane():
    state={"our_team":[{"role":"Top","locked":"Lee Sin","cell_id":3},
                       {"role":"Jungle","locked":None,"cell_id":4}]}
    plan={"Top":"Ornn","Jungle":"Lee Sin"}
    assert bridge._locked_by_role(state,plan)=={"Jungle":"Lee Sin"}
    state["our_team"][1]["locked"]="Ornn"
    assert bridge._locked_by_role(state,plan)=={
        "Jungle":"Lee Sin","Top":"Ornn"}


def test_captain_manual_enemy_role_override_beats_imperfect_inference():
    state={"game_id":"game42","enemy_team":[
        {"locked":"Malphite","role":"Top"},
        {"locked":"Rakan","role":None},
        {"locked":None,"role":"Mid","hover":"Sylas"}]}
    manual=bridge._normalise_control({
        "enemy_role_overrides":{"game_id":"game42",
                                "assignments":{"Malphite":"Mid","Rakan":"Support"}}
    })
    result=bridge._locked_enemy_roles(state,manual)
    assert result=={"Mid":"Malphite","Support":"Rakan"}
    assert "Sylas" not in result.values()


def test_enemy_lane_overrides_never_follow_into_next_game_or_unlocked_hovers():
    state={"game_id":"game43","enemy_team":[
        {"locked":"Malphite","role":"Top"},{"locked":"Rakan","role":None}]}
    manual={"enemy_role_overrides":{
        "game_id":"game42","assignments":{"Malphite":"Mid","Rakan":"Jungle"}}}
    current=bridge._locked_enemy_roles(state,manual)
    assert current["Top"]=="Malphite"
    assert current.get("Mid")!="Malphite"
    assert current.get("Jungle")!="Rakan"


def test_normalization_rejects_duplicate_manual_lane_and_malformed_input():
    item=bridge._normalise_control({"enemy_role_overrides":{
        "game_id":"g", "assignments":{
            "Malphite":"Top", "Rakan":"Top", "Lee Sin":"Jungle",
            "X":"Unknown"}}})
    assert item["enemy_role_overrides"]=={
        "game_id":"g","assignments":{"Malphite":"Top","Lee Sin":"Jungle"}}
    empty=bridge._normalise_control({"enemy_role_overrides":"bad"})
    assert empty["enemy_role_overrides"]=={"game_id":"","assignments":{}}


def test_enemy_lanes_ui_and_shared_endpoint_are_present():
    src=Path(__file__).resolve().parents[1]
    js=(src/"draftcoach/static/enemy-roles.js").read_text(encoding="utf8")
    code=(src/"tools/intent_live_draft_bridge.py").read_text(encoding="utf8")
    for html in ("overlay.html","coach.html"):
        page=(src/"draftcoach/static"/html).read_text(encoding="utf8")
        assert 'id="enemy-role-editor"' in page
        assert "/static/enemy-roles.js" in page
        assert "D.EnemyRoles.render" in page
    assert "enemy_role_overrides:" in js
    assert "game_id: gameId" in js
    assert "Only the team captain can assign enemy lanes" in code
    assert 'guidance["enemy_roles_resolved"]' in code


def test_live_guidance_advances_team_reveal_when_top_holds_jungle_lock():
    comp=_shared_plan()["comps"][0]
    champions_by_role={p["role"]:p["champion"] for p in comp["pick_order"]}
    rec={
        "champions":champions_by_role,
        "players":{role:{"display_name":name,"user_id":400+i}
                   for i,(role,name) in enumerate(comp["players"].items())},
        "score":100.0,"rank_score":100.0,
        "pick_order":[{**p,"pick_group":p["slot"],
                       "pick_group_label":str(p["slot"])}
                      for p in comp["pick_order"]],
        "role_alternatives":{},"counter_bans":[],
    }
    state={
        "side":"blue","game_id":"abc","our_bans":[],"enemy_bans":[],
        "our_team":[{"role":"Top","locked":"Lee Sin","cell_id":3},
                    {"role":"Jungle","locked":None,"cell_id":4}],
        "enemy_team":[],"active":{"kind":"pick","ours":True}
    }
    control={"chosen":{"champions":champions_by_role}}
    result=bridge._guidance_from_result(
        {"recommendations":[rec], "side":"blue"},state,control)
    assert result["our_locked"]=={"Jungle":"Lee Sin"}
    assert result["picks"][0]["champion"]=="Braum"
    assert result["picks"][0]["role"]=="Support"
    assert result["pick_order"][0]["status"]=="locked"
    assert result["pick_order"][1]["status"]=="next"
    assert result["current_pick_group"]["members"][0]["role"]=="Support"
