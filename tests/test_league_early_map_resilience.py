"""Draft-only early pressure and roaming response, never fabricated match outcomes."""
from league_comp_engine import _early_map_resilience


def lineup(*, top="Ambessa", jungle="Vi", mid="Ahri", adc="Yunara", support="Lulu"):
    return {
        "Top": top, "Jungle": jungle, "Mid": mid,
        "ADC": adc, "Support": support,
    }


def test_revealed_roaming_support_makes_bot_wave_answer_material():
    passive = _early_map_resilience(
        lineup(adc="Ezreal", support="Yuumi"),
        enemy_roles={"Support": "Leona"},
    )
    push = _early_map_resilience(
        lineup(adc="Caitlyn", support="Karma"),
        enemy_roles={"Support": "Leona"},
    )
    assert passive["enemy_roam_threat"] == "Leona"
    assert passive["adjustment"] < push["adjustment"] - 1.0
    assert any("punish" in text for text in passive["reasons"])


def test_unrevealed_support_is_not_claimed_to_be_roaming():
    match = _early_map_resilience(lineup(adc="Ezreal", support="Yuumi"))
    assert match["enemy_roam_threat"] is None
    assert not any("can roam" in text for text in match["reasons"])


def test_jungle_early_access_matters_when_multiple_lanes_cede_wave():
    low = _early_map_resilience(
        lineup(top="Nasus", mid="Kassadin", jungle="Vi",
               adc="Ezreal", support="Yuumi"),
    )
    control = _early_map_resilience(
        lineup(top="Renekton", mid="Orianna", jungle="Vi",
               adc="Caitlyn", support="Karma"),
    )
    assert low["adjustment"] <= control["adjustment"] - 2.0
    assert any("jungle skirmishes" in text for text in low["reasons"])


def test_low_support_reliability_reduces_usable_pressure_not_pool():
    champs = lineup(adc="Varus", support="Nami")
    normal = _early_map_resilience(champs)
    vulnerable = _early_map_resilience(
        champs, player_fit={"role_details": {
            "ADC": {"reliability_index": 65},
            "Support": {"reliability_index": 20},
        }},
    )
    assert vulnerable["bot_priority"] < normal["bot_priority"]
    assert vulnerable["adjustment"] <= normal["adjustment"]


def test_scaling_yunara_lulu_is_not_inherently_forbidden():
    result = _early_map_resilience(lineup(adc="Yunara", support="Lulu"))
    assert result["adjustment"] >= -5.8
    assert result["enemy_roam_threat"] is None
    assert result["bot_priority"] >= .4


def test_carry_is_role_dynamic_not_player_id_specific():
    champs = lineup(mid="Kassadin", adc="Ezreal", support="Yuumi")
    top_star = _early_map_resilience(
        champs, player_fit={"carry_options": [{"role": "Top"}]},
    )
    support_star = _early_map_resilience(
        champs, player_fit={"carry_options": [{"role": "Support"}]},
    )
    assert top_star["adjustment"] < support_star["adjustment"]
