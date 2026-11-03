"""Broad matchup candidate search and honest, source-labelled Shen scouting."""
from __future__ import annotations

import json
from pathlib import Path

from counterpick_advice import counter_advice
from league_comp_engine import load_lane_matchups, score_comp
from league_matchup_universe import role_universe, scout_note, matrix_coverage


def example_cian_top():
    five = {
        "Top": "Camille", "Jungle": "Lee Sin", "Mid": "Ahri",
        "ADC": "Jinx", "Support": "Lulu",
    }
    lead = score_comp(five)
    lead["champions"] = five
    lead["submitted_pools"] = {"Top": ["Camille", "Gwen"]}
    lead["players"] = {
        "Top": {"user_id": 139837324438536193, "display_name": "Cian"}
    }
    return lead


def test_every_position_can_search_full_champion_universe():
    roster = role_universe("Top")
    assert len(roster) >= 60
    for champion in ("Camille", "Shen", "Gwen", "Gnar", "Kayle"):
        assert champion in roster
    assert not role_universe("WrongRole")


def test_private_historical_coverage_is_explicit_and_sample_gated(tmp_path):
    from tools.matchup_coverage import historical_coverage
    metrics = {
        "matches": 30000,
        "patches": {"16.16": 30000},
        "matchups": {
            "Gwen|TOP|Shen": {"n": 16},
            "Camille|TOP|Shen": {"n": 101},
            "Shen|TOP|Shen": {"n": 999},
        },
    }
    (tmp_path / "matchv5-metrics.json").write_text(json.dumps(metrics), encoding="utf8")
    observed = historical_coverage(tmp_path)["Top"]
    assert observed == {1: 2, 40: 1, 100: 1}


def test_reports_actual_coverage_instead_of_claiming_full_data():
    summary = matrix_coverage(load_lane_matchups())
    assert sum(s["possible"] for s in summary.values()) >= 12_000
    assert summary["Top"]["possible"] > 3_000
    assert summary["Top"]["missing"] > 0
    assert 0 < summary["Top"]["coverage_percent"] < 100


def test_scout_sources_are_attributed_and_qualitative_not_fake_rates():
    for champion in ("Camille", "Gwen", "Gnar", "Kayle"):
        note = scout_note("Top", champion, "Shen")
        assert note.get("patch") == "26.21"
        assert len(note.get("sources", [])) >= 2
        assert "win_rate" not in note
        assert "draft_points" not in note
    assert scout_note("Top", "Gwen", "Shen")["verdict"] == "favourable"
    assert scout_note("Top", "Gnar", "Shen")["verdict"] == "mixed"
    assert scout_note("Top", "Kayle", "Shen")["verdict"] == "mixed"


def test_camille_shen_full_catalogue_looks_at_gwen_while_preserving_comp_tradeoff():
    result = counter_advice(example_cian_top(), {"Top": "Shen"})
    lane = result["lanes"][0]
    assert lane["candidates_searched"] >= len(role_universe("Top"))
    scouts = {row["champion"]: row for row in lane["scout"]}
    assert scouts["Gwen"]["type"] == "playable"
    assert scouts["Gwen"]["qualitative_only"]
    assert scouts["Gwen"]["team_fit"] == "weakens_comp"
    assert scouts["Gnar"]["type"] == "learn"
    assert scouts["Gnar"]["verdict"] == "mixed"
    assert not lane["playable"]  # No invented numeric evidence for Shen Top.


def test_scout_cannot_swap_in_locked_champion_or_use_unconfirmed_enemy_role():
    lead = example_cian_top()
    assert counter_advice(lead, {}, ally_locks={})["lanes"] == []
    assert counter_advice(lead, {"Top": "Shen"}, ally_locks={"Top": "Camille"})["lanes"] == []


def test_scout_only_in_visual_plan_not_client_lock_action():
    source = (Path(__file__).resolve().parents[1] / "draftcoach/static/selected-comp.js").read_text("utf8")
    assert 'const scout = (lane?.scout || []).slice(0, 2);' in source
    assert "scoutEntry.type === \"learn\"" in source
    assert "scoutEntry.team_fit" in source
    assert "api/client/lock" not in source
    assert "no qualified counter data" in source
