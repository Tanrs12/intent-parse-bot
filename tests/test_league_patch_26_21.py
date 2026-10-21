from datetime import date

import league_comp_engine as engine
from league_patch_live import (
    live_patch_active,
    live_patch_role_adjustment,
    live_patch_draft_adjustment,
)


def test_patch_26_21_expiry_and_official_change_direction():
    assert not live_patch_active(date(2026, 10, 20))
    assert live_patch_active(date(2026, 10, 21))
    assert not live_patch_active(date(2026, 11, 4))
    assert live_patch_role_adjustment("Jungle", "Wukong", on_date=date(2026, 10, 22)) < 0
    assert live_patch_role_adjustment("Top", "Wukong", on_date=date(2026, 10, 22)) > 0
    assert live_patch_role_adjustment("Mid", "Orianna", on_date=date(2026, 10, 22)) > 0
    assert live_patch_role_adjustment("Mid", "Viktor", on_date=date(2026, 10, 22)) < 0
    assert live_patch_role_adjustment("ADC", "Yunara", on_date=date(2026, 10, 22)) == 0
    assert live_patch_role_adjustment("Mid", "Viktor", on_date=date(2026, 11, 4)) == 0


def test_patch_delta_is_small_and_never_claimed_as_winrate():
    comp = {"Top": "Sion", "Jungle": "Xin Zhao", "Mid": "Orianna",
            "ADC": "Miss Fortune", "Support": "Zyra"}
    assert live_patch_draft_adjustment(comp, on_date=date(2026, 10, 22)) == 1.6
    assert live_patch_draft_adjustment(comp, on_date=date(2026, 11, 4)) == 0.0
    assert abs(live_patch_draft_adjustment({
        "Top": "Yone", "Jungle": "Wukong", "Mid": "Viktor",
        "ADC": "Caitlyn", "Support": "Lulu",
    }, on_date=date(2026, 10, 22))) <= 1.6


def test_patch_affects_real_draft_score_without_killing_real_duos(monkeypatch):
    monkeypatch.setattr(engine, "live_patch_active", lambda: True)
    monkeypatch.setattr(engine, "live_patch_draft_adjustment",
                        lambda champions: live_patch_draft_adjustment(champions, on_date=date(2026, 10, 22)))
    comp = {"Top": "Ornn", "Jungle": "Jarvan IV", "Mid": "Orianna",
            "ADC": "Yunara", "Support": "Lulu"}
    result = engine.score_comp(comp)
    assert result["live_patch"] == "26.21"
    assert result["live_patch_adjustment"] == 0.4
    assert result["bot_lane_score"] >= 5.5
    nerfed = dict(comp, Mid="Viktor")
    assert engine.score_comp(nerfed)["live_patch_adjustment"] == -0.5


def test_old_patch_matchups_are_discounted_not_deleted():
    fixtures = {
        "source": {"patches_detected": ["16.20"]},
        "matchups": {"Top": {"Rumble": {"responses": {
            "Camille": {"response_advantage": 5, "games": 500}
        }}}},
    }
    old = engine.live_lane_matchup_adjustment(
        {"Top": "Camille"}, {"Top": "Rumble"}, fixtures)
    assert old["adjustment"] > 0
    assert old["adjustment"] <= engine.calibrated_matchup("Top", 5, 500)[1]
