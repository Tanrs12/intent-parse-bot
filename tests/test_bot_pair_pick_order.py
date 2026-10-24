from __future__ import annotations

import pytest

import league_comp_engine as eng
import league_flex_core as core


def rows(*roles):
    champions = {"Top": "Ornn", "Jungle": "Vi", "Mid": "Orianna",
                 "ADC": "Aphelios", "Support": "Thresh"}
    return [{"role": role, "champion": champions[role]} for role in roles]


def ordered_roles(result):
    return [row["role"] for row in result]


@pytest.mark.parametrize(
    "side,original,expected",
    [
        ("blue", ("ADC", "Jungle", "Support", "Mid", "Top"),
         ("Support", "ADC", "Jungle", "Mid", "Top")),
        ("blue", ("Jungle", "ADC", "Mid", "Support", "Top"),
         ("Jungle", "ADC", "Support", "Mid", "Top")),
        ("blue", ("Jungle", "Mid", "ADC", "Top", "Support"),
         ("Jungle", "ADC", "Support", "Mid", "Top")),
        ("red", ("ADC", "Jungle", "Mid", "Support", "Top"),
         ("ADC", "Support", "Jungle", "Mid", "Top")),
        ("red", ("Jungle", "ADC", "Top", "Support", "Mid"),
         ("ADC", "Support", "Jungle", "Top", "Mid")),
        ("red", ("Jungle", "Top", "ADC", "Support", "Mid"),
         ("Jungle", "Top", "ADC", "Support", "Mid")),
    ],
)
def test_strong_pair_is_secured_before_enemy_intervening_picks(monkeypatch, side, original, expected):
    monkeypatch.setattr(core, "_bot_lane_duo_score", lambda *_: {"score": 9.3})
    assert ordered_roles(core._protect_linked_bot_pick_order(rows(*original), side, {})) == list(expected)


def test_weak_pair_does_not_distort_ordinary_order(monkeypatch):
    monkeypatch.setattr(core, "_bot_lane_duo_score", lambda *_: {"score": 6.5})
    original = rows("ADC", "Jungle", "Support", "Mid", "Top")
    assert core._protect_linked_bot_pick_order(original, "blue", {}) == original


def test_support_already_first_is_safe_even_if_rounds_differ(monkeypatch):
    monkeypatch.setattr(core, "_bot_lane_duo_score", lambda *_: {"score": 9.2})
    original = rows("Support", "Jungle", "ADC", "Top", "Mid")
    assert core._protect_linked_bot_pick_order(original, "blue", {}) == original


def test_adc_strength_uses_rank_adjusted_role_median(monkeypatch):
    index = {"ADC": {f"Other{i}": {"win_rate": 51.5 + i * .1, "weight": 7000}
                     for i in range(10)}}
    index["ADC"].update({
        "Aphelios": {"win_rate": 50.5, "weight": 8000},
        "Jinx": {"win_rate": 54.2, "weight": 8000},
    })
    monkeypatch.setattr(eng, "load_solo_queue_winrates", lambda: index)
    monkeypatch.setattr(eng, "load_lane_matchups", lambda: {"source": {"patches_detected": ["26.21"]}})
    eng._adc_field_strength_adjustment.cache_clear()
    assert eng._adc_field_strength_adjustment("Aphelios") < 0
    assert eng._adc_field_strength_adjustment("Jinx") > 0
    assert abs(eng._adc_field_strength_adjustment("Aphelios")) <= 1.35
    eng._adc_field_strength_adjustment.cache_clear()


def test_sparse_adc_evidence_is_neutral_not_negative(monkeypatch):
    monkeypatch.setattr(eng, "load_solo_queue_winrates", lambda: {
        "ADC": {"Aphelios": {"win_rate": 46.0, "weight": 300}}
    })
    eng._adc_field_strength_adjustment.cache_clear()
    assert eng._adc_field_strength_adjustment("Aphelios") == 0.0
    eng._adc_field_strength_adjustment.cache_clear()


def test_stale_d2_snapshot_does_not_override_current_patch(monkeypatch):
    monkeypatch.setattr(eng, "load_lane_matchups", lambda: {"source": {"patches_detected": ["16.20"]}})
    monkeypatch.setattr(eng, "load_solo_queue_winrates", lambda: {
        "ADC": {f"Other{i}": {"win_rate": 53.0, "weight": 8000} for i in range(10)}
               | {"Aphelios": {"win_rate": 48.0, "weight": 8000}}
    })
    eng._adc_field_strength_adjustment.cache_clear()
    if eng.live_patch_active():
        assert eng._adc_field_strength_adjustment("Aphelios") == 0.0
    eng._adc_field_strength_adjustment.cache_clear()


def test_aphelios_thresh_remains_legal_without_forced_shortlisting():
    assert ("Aphelios", "Thresh") in eng.BOT_DUO_PRIORS
    assert ("Aphelios", "Thresh") not in eng.EXPLICIT_BOT_PAIR_COVERAGE
