"""Regression: broad ADC players retain their legal carry options in ten good drafts."""
from __future__ import annotations

import league_comp_engine as eng
import league_flex_core as core
from tools.intent_comp_overlay import _paged_draft_cards, MAX_DRAFT_CARDS, VISIBLE_DRAFT_CARDS

CIAN = 139837324438536193


def test_confirmed_cian_adcs_pass_secondary_role_gate_without_score_bonus():
    meta = eng.load_meta()
    player = {"user_id": CIAN, "role_priority": {"ADC": 2}}
    for champion in ("Kai'Sa", "Jinx", "Kog'Maw", "Yunara", "Kalista"):
        assert eng._verified_secondary_role_pick(player, "ADC", champion, meta)
    assert eng._role_search_pool_limit(player, "ADC", 10) >= 14


def test_cian_adcs_survive_large_pool_candidate_pruning():
    meta = eng.load_meta()
    adc_pool = [
        "Aphelios", "Ashe", "Caitlyn", "Ezreal", "Draven", "Jhin",
        "Twitch", "Yunara", "Kalista", "Sivir", "Varus", "Zeri",
        "Xayah", "Kai'Sa", "Jinx", "Kog'Maw", "Smolder", "Miss Fortune",
    ]
    pruned = eng._prune_pool(
        "ADC", adc_pool, meta, limit=eng._role_search_pool_limit({"user_id": CIAN}, "ADC", 10),
        user_id=CIAN, linked_bot_pool=["Lulu", "Thresh", "Nautilus", "Rell", "Milio"],
    )
    assert {"Kai'Sa", "Jinx", "Kog'Maw", "Yunara", "Kalista"}.issubset(pruned)
    assert len(pruned) <= 14


def _rec(index: int, adc: str, score: float, bot: float = 8.4, mj: float = 8.2):
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    champions = {
        "Top": ["Ornn", "Renekton", "Gwen"][index % 3],
        "Jungle": ["Vi", "Jarvan IV", "Zac"][index % 3],
        "Mid": ["Ahri", "Orianna", "Yasuo"][index % 3],
        "ADC": adc,
        "Support": ["Nautilus", "Lulu", "Thresh", "Rell"][index % 4],
    }
    return {
        "score": score, "team_plan_score": 8.3, "bot_lane_score": bot,
        "mid_jungle_score": mj, "team_plan": ["dive / all-in", "front-to-back", "skirmish"][index % 3],
        "carry_plan": {"role": "ADC"}, "archetypes": [str(index % 3)],
        "credible_mixed_damage": True, "champions": champions,
        "players": {
            role: {
                "user_id": CIAN if role == "ADC" else (100 + i),
                "roles": [role], "role_priority": {role: 2},
            }
            for i, role in enumerate(roles)
        },
    }


def test_ten_plan_portfolio_retains_adc_variety_and_essential_package_quality():
    pool = [_rec(i, "Aphelios", 100.0 - i * .20) for i in range(8)]
    pool += [
        _rec(8 + i, adc, 98.0 - i * .5)
        for i, adc in enumerate(("Kai'Sa", "Jinx", "Kog'Maw", "Yunara", "Kalista", "Xayah"))
    ]
    result = core._select_diverse_recommendations(pool, limit=10)
    assert len(result) == 10
    assert result[0]["score"] == 100.0
    assert len({row["champions"]["ADC"] for row in result}) >= 5
    assert all(row["bot_lane_score"] >= 7 and row["mid_jungle_score"] >= 7 for row in result)


def test_extended_portfolio_refuses_weak_bot_or_mid_jungle_even_for_new_adc():
    pool = [_rec(i, "Aphelios", 100 - i * .4) for i in range(10)]
    pool.append(_rec(11, "Kog'Maw", 99, bot=4.0))
    pool.append(_rec(12, "Jinx", 99, mj=4.0))
    result = core._select_diverse_recommendations(pool, limit=10)
    assert all(row["bot_lane_score"] >= 7 and row["mid_jungle_score"] >= 7 for row in result)


def test_overlay_has_two_readable_pages_of_five():
    cards = [{"id": i} for i in range(10)]
    assert MAX_DRAFT_CARDS == 10 and VISIBLE_DRAFT_CARDS == 5
    assert [row["id"] for row in _paged_draft_cards(cards, 0)] == list(range(5))
    assert [row["id"] for row in _paged_draft_cards(cards, 1)] == list(range(5, 10))
    assert [row["id"] for row in _paged_draft_cards(cards, 99)] == list(range(5, 10))
