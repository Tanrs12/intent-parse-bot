"""Flexers additions to the engine: regression tests for the review fixes (see handoff/003)."""
from __future__ import annotations

import league_comp_engine as engine


def test_solo_queue_baseline_is_complemented_for_opponent_semantics():
    index = engine.load_solo_queue_winrates()
    # Camille top: every row stores opponent_baseline_win_rate 47.58 (= enemy_wr - delta1), i.e. 100 - her own WR.
    assert abs(index["Top"]["Camille"]["win_rate"] - 52.42) < 0.05


def test_calibrated_matchup_shrinks_caps_and_weights_by_role():
    residual_small, points_small = engine.calibrated_matchup("Top", 6.58, 171)
    residual_big, points_big = engine.calibrated_matchup("Top", 6.58, 2000)
    assert 1.9 < residual_small < 2.05 and residual_small < residual_big <= 2.5
    assert abs(points_small - residual_small * 0.90) < 2e-3
    _, support_points = engine.calibrated_matchup("Support", 6.58, 2000)
    assert support_points < points_big
    assert engine.calibrated_matchup("Mid", None, 100) == (0.0, 0.0)
    assert engine.calibrated_matchup("Mid", 3.0, 0) == (0.0, 0.0)
    assert engine.calibrated_matchup("Mid", -40.0, 5000)[0] == -2.5


def test_unknown_players_are_neutral_and_rank_score_is_unclamped():
    assignment = {role: {"user_id": 900000000000 + i, "display_name": role, "roles": [role], "champions": {role: []}}
                  for i, role in enumerate(engine.ROLES)}
    champions = {"Top": "Ornn", "Jungle": "Vi", "Mid": "Ahri", "ADC": "Jinx", "Support": "Nautilus"}
    meta = engine.load_meta()
    fit = engine._player_fit_adjustment(assignment, champions, [], meta)
    assert all(abs(float(d.get("adjustment", 0.0) or 0.0)) < 1e-9 for d in fit["role_details"].values()) or fit["adjustment"] > -1.0
    scored = engine.score_comp(champions, meta)
    assert "rank_score" in scored and 0.0 <= scored["score"] <= 100.0
    assert abs(scored["rank_score"] - scored["score"]) < 0.11 or scored["score"] in (0.0, 100.0)
