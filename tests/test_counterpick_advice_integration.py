"""Player-aware counters never replace the original draft without valid evidence.

The new counter advice uses the existing scorer and only exposes off-pool picks
as learning suggestions, never as direct League champion actions.
"""
from __future__ import annotations

import copy
from pathlib import Path

import counterpick_advice as c
from league_comp_engine import matchup_response


def fixture_lead():
    return {
        "champions": {
            "Top": "Malphite", "Jungle": "Lee Sin", "Mid": "Ahri",
            "ADC": "Jinx", "Support": "Lulu",
        },
        "team_plan": "scaling front-to-back",
        "submitted_pools": {"Top": ["Malphite", "Aatrox", "Volibear"]},
        "players": {"Top": {"display_name": "Dyl", "user_id": 7}},
    }


def stub_fitting(champions, meta=None):
    return {
        "team_plan": "scaling front-to-back",
        "score": 88.0 if champions["Top"] == "Malphite" else 87.0,
        "team_plan_score": 8.5,
        "bot_lane_score": 8.0,
        "mid_jungle_score": 8.0,
    }


def stub_edge(champions, enemy_roles, matchup_data=None):
    points = {"Malphite": -0.4, "Aatrox": 1.2, "Dr. Mundo": 1.8,
              "Vayne": 2.4}
    name = next(iter(champions.values()))
    edge = {"role": "Top", "ours": name, "enemy": "Cho'Gath",
            "games": 300, "points": points.get(name, 0),
            "source": "Riot Match-V5", "source_patch": "16.21"}
    return {"edges": [edge], "adjustment": points.get(name, 0)}


def fake_meta():
    return {"role_stats": {"Top": {
        "Malphite": {"games": 100}, "Aatrox": {"games": 100},
        "Dr. Mundo": {"games": 100}, "Vayne": {"games": 100}
    }}}


def test_counter_respects_player_pool_and_labels_ideas_only(monkeypatch):
    monkeypatch.setattr(c, "score_comp", stub_fitting)
    monkeypatch.setattr(c, "live_lane_matchup_adjustment", stub_edge)
    monkeypatch.setattr(c, "load_solo_queue_winrates", lambda: {})
    result = c.counter_advice(fixture_lead(), {"Top": "Cho'Gath"},
                              matchup_data={"source": {}, "matchups": {}}, meta=fake_meta())
    lane = result["lanes"][0]
    assert lane["playable"][0]["champion"] == "Aatrox"
    assert lane["playable"][0]["type"] == "playable"
    assert [entry["champion"] for entry in lane["learn"]] == ["Vayne", "Dr. Mundo"]
    assert all(row["type"] == "learn" for row in lane["learn"])
    assert lane["enemy"] == "Cho'Gath"
    assert lane["playable"][0]["patch"] == "16.21"


def test_every_player_gets_their_own_counter_pool(monkeypatch):
    monkeypatch.setattr(c, "score_comp", stub_fitting)
    monkeypatch.setattr(c, "live_lane_matchup_adjustment", stub_edge)
    monkeypatch.setattr(c, "load_solo_queue_winrates", lambda: {})
    for who, submitted, selected in [
        ("Kayla", ["Malphite", "Dr. Mundo"], "Dr. Mundo"),
        ("Dyl", ["Malphite", "Aatrox"], "Aatrox"),
        ("Cian", ["Malphite", "Vayne"], "Vayne"),
    ]:
        lead = fixture_lead()
        lead["players"]["Top"]["display_name"] = who
        lead["submitted_pools"]["Top"] = submitted
        rows = c.counter_advice(lead, {"Top": "Cho'Gath"},
                                matchup_data={"matchups": {}}, meta=fake_meta())["lanes"]
        assert rows[0]["player"] == who
        assert rows[0]["playable"][0]["champion"] == selected


def test_counter_cannot_mutate_locked_role_or_invent_unknown_enemy_lane(monkeypatch):
    monkeypatch.setattr(c, "score_comp", stub_fitting)
    monkeypatch.setattr(c, "live_lane_matchup_adjustment", stub_edge)
    monkeypatch.setattr(c, "load_solo_queue_winrates", lambda: {})
    a = c.counter_advice(fixture_lead(), {"Top": "Cho'Gath"}, ally_locks={"Top":"Malphite"},
                         matchup_data={"matchups": {}}, meta=fake_meta())
    assert a["lanes"] == []
    b = c.counter_advice(fixture_lead(), None, matchup_data={"matchups": {}}, meta=fake_meta())
    assert b["lanes"] == [] and b["status"] == "waiting_for_confirmed_enemy_lane"


def test_qualitative_or_low_sample_is_not_counter_evidence(monkeypatch):
    monkeypatch.setattr(c, "score_comp", stub_fitting)
    monkeypatch.setattr(c, "load_solo_queue_winrates", lambda: {})
    monkeypatch.setattr(c, "live_lane_matchup_adjustment", lambda champions, enemies, matchup_data=None: {
        "edges": [{"role":"Top","enemy":"Cho'Gath","ours":next(iter(champions.values())),
                   "points":2.0,"games":0, "qualitative_only":True}]
    })
    lane = c.counter_advice(fixture_lead(), {"Top":"Cho'Gath"},
                            matchup_data={"matchups": {}}, meta=fake_meta())["lanes"][0]
    assert lane["coverage"] == "no_qualified_counter_data"
    assert lane["learn"] == lane["playable"] == []


def test_missing_matchup_never_creates_statistical_evidence():
    # A future authorised data export may add Cho'Gath coverage.
    empty = {"matchups": {"Top": {}}}
    assert matchup_response("Top", "Cho'Gath", "Dr. Mundo", empty) == (None, 0)


def test_gameplan_visuals_keep_rat_original_rendering_contract():
    html = (Path(__file__).resolve().parents[1] / "draftcoach/static/coach.html").read_text("utf-8")
    assert 'id="gameplan-build"' in html and 'id="gameplan-drafts"' in html
    assert "function renderGamePlanDrafts()" in html
    assert "D.portrait(candidate" in html
    assert 'for (const sideName of ["blue", "red"])' in html
    assert "gameplan-full-order" in html and "gameplan-ban-chip" in html
    assert '["B1", "R1", "R2", "B2", "B3", "R3", "R4", "B4", "B5", "R5"]' in html
    assert "const ourSlots = fullSequence.filter" in html
    assert '"Enemy pick"' not in html
    assert "accepted in the shared room" in html


def test_float_panel_scouting_never_creates_client_pick_action():
    path = Path(__file__).resolve().parents[1] / "draftcoach/static/selected-comp.js"
    source = path.read_text(encoding="utf-8")
    assert "counter_advice?.lanes" in source
    assert "LEARN" in source
    assert "no qualified counter data" in source
    assert "D.portrait" in source
    assert "api/client" not in source
