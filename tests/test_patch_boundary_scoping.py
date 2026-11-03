"""Patch 26.21 data must stop being treated as current at 26.22."""
from pathlib import Path

import counterpick_advice as advice
import league_draft_backend as backend
from league_patch_live import live_patch_active
from league_comp_engine import score_comp


def test_patch_expiration_is_exact_date():
    from datetime import date
    assert live_patch_active(date(2026,11,3))
    assert not live_patch_active(date(2026,11,4))
    assert not live_patch_active(date(2026,12,1))


def test_expired_patch_never_loads_old_current_opgg_into_advice(monkeypatch):
    monkeypatch.setattr(advice, "live_patch_active", lambda:False)
    def forbidden(*args,**kwargs):
        raise AssertionError("Expired OP.GG page wrongly loaded as current")
    monkeypatch.setattr(advice, "full_for_enemy",forbidden)
    champs={"Top":"Ornn","Jungle":"Lee Sin","Mid":"Lissandra",
            "ADC":"Jinx","Support":"Lulu"}
    lead={**score_comp(champs),"champions":champs,
          "submitted_pools":{"Top":["Ornn","Sylas"]},
          "players":{"Top":{"display_name":"Top player"}}}
    result=advice.counter_advice(lead,{"Top":"Malphite"})
    lane=result["lanes"][0]
    assert not lane["opgg_full_reference"]["planned"]
    assert lane["opgg_full_reference"]["alternatives"]==[]
    # Older PocketPick and qualitative references remain accessible but
    # only as independently labelled historical scouting.
    assert "pocket_reference" in lane
    assert "pro_order_reference" in lane


def test_expired_patch_cannot_be_loaded_into_auto_draft_selector(monkeypatch):
    monkeypatch.setattr(backend,"live_patch_active",lambda:False)
    def forbidden(*args,**kwargs):
        raise AssertionError("Expired patch called OP.GG scorer")
    monkeypatch.setattr(backend,"full_for_enemy",forbidden)
    roles=["Top","Jungle","Mid","ADC","Support"]
    champs={"Top":"Ornn","Jungle":"Lee Sin","Mid":"Orianna",
            "ADC":"Jinx","Support":"Lulu"}
    team=[{"user_id":300101+i,"display_name":"PatchTest"+role,
           "roles":[role],"role_priority":{role:1},
           "champions":{role:[champ]}}
          for i,(role,champ) in enumerate(champs.items())]
    results=backend.recommend_live_drafts(
        team,role_players={role:300101+i for i,role in enumerate(roles)},
        preferred_champions=champs,chosen_champions=champs,
        enemy_roles={"Top":"Malphite"},enemy_champions=["Malphite"],
        limit=2,
    )
    assert results["recommendations"]
    assert results["recommendations"][0]["champions"]==champs


def test_live_bridge_blocks_stale_patch_fetch_and_displays_notice():
    root=Path(__file__).resolve().parents[1]
    bridge=(root/"tools/intent_live_draft_bridge.py").read_text(encoding="utf8")
    ui=(root/"draftcoach/static/selected-comp.js").read_text(encoding="utf8")
    assert "and league_comp_engine.live_patch_active()" in bridge
    assert '"state": "patch_expired"' in bridge
    assert "if league_comp_engine.live_patch_active() else {}" in bridge
    assert "liveFetch?.state === \"patch_expired\"" in ui
    assert "Automatic current-patch matchup scoring paused" in ui
