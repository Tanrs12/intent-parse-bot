"""Two-champion adaptive completions: preserve skills, owners, frontline, locks."""
import copy

from adaptive_draft import select_adaptive_comp

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
ORIGINAL = {
    "Top": "Ornn", "Jungle": "Lee Sin", "Mid": "Orianna",
    "ADC": "Jinx", "Support": "Lulu",
}
IDS = {"Top": 139837324438536193, "Jungle": 13,
       "Mid": 14, "ADC": 15, "Support": 400745307467415553}
TRAITS = {
    "Ornn": {"roles": ["TANK", "VANGUARD"], "ratings": {"toughness": 3}},
    "Lee Sin": {"roles": ["FIGHTER", "DIVER"], "ratings": {"toughness": 2}},
    "Sylas": {"roles": ["SKIRMISHER", "MAGE"], "ratings": {"toughness": 2}},
    "Skarner": {"roles": ["TANK", "VANGUARD"], "ratings": {"toughness": 3}},
    "Elise": {"roles": ["ASSASSIN", "MAGE"], "ratings": {"toughness": 1}},
    "Vayne": {"roles": ["MARKSMAN"], "ratings": {"toughness": 1}},
}
RULES = {"Malphite": [{
    "champion": "Sylas", "score": 4.5,
    "reason": "Sylas steals Malphite R",
}]}


def row(champs, *, score=92.0, player_ids=None, team_plan_score=8.8,
        mid_jungle_score=7.2, bot_lane_score=8.8, edges=None):
    return {"champions": dict(champs), "score": score,
            "team_plan_score": team_plan_score,
            "bot_lane_score": bot_lane_score,
            "mid_jungle_score": mid_jungle_score, "identity": "dive",
            "player_ids": dict(player_ids or IDS),
            "live_lane_edges": list(edges or []), "rank_score": score}


def plan(options, **kw):
    return select_adaptive_comp(
        options, ORIGINAL, enemy_roles={"Top": "Malphite"},
        identity="dive", champion_traits=TRAITS,
        global_counter_rules=RULES, **kw)[0]


def test_sylas_top_and_tank_jungle_can_replace_tank_top_and_assassin_jungle():
    start = row(ORIGINAL)
    paired = row({**ORIGINAL, "Top": "Sylas", "Jungle": "Skarner"},
                 score=93.0, team_plan_score=8.6, mid_jungle_score=7.0)
    picked = plan([paired, start])
    assert picked["champions"]["Top"] == "Sylas"
    assert picked["champions"]["Jungle"] == "Skarner"
    assert "Malphite" in picked["adaptive_reasons"]["Top"]
    assert "frontline" not in picked["adaptive_reasons"]["Top"].lower() or "structure" in picked["adaptive_reasons"]["Top"]
    assert "compensating" in picked["adaptive_reasons"]["Jungle"]
    assert picked["player_ids"] == IDS


def test_two_seat_change_must_not_delete_the_last_frontline():
    start = row(ORIGINAL)
    bad = row({**ORIGINAL, "Top": "Sylas", "Jungle": "Elise"}, score=98.0)
    assert plan([bad, start])["champions"] == ORIGINAL


def test_wrong_player_owner_cannot_win_even_with_strong_matchup():
    start = row(ORIGINAL)
    wrong = row({**ORIGINAL, "Top": "Sylas", "Jungle": "Skarner"},
                score=98.0, player_ids={**IDS, "Top": IDS["Mid"]})
    assert plan([wrong, start])["champions"] == ORIGINAL


def test_pair_rejected_when_comp_is_worse():
    start = row(ORIGINAL)
    bad = row({**ORIGINAL, "Top": "Sylas", "Jungle": "Skarner"},
              score=87.0, team_plan_score=7.6, mid_jungle_score=6.2)
    assert plan([bad, start])["champions"] == ORIGINAL


def test_own_locked_jungler_or_top_blocks_compensating_switch():
    start = row(ORIGINAL)
    replacement = row({**ORIGINAL, "Top": "Sylas", "Jungle": "Skarner"},
                      score=95)
    assert plan([replacement, start],
                actual_locks={"Jungle": "Lee Sin"})["champions"] == ORIGINAL
    assert plan([replacement, start],
                actual_locks={"Top": "Ornn"})["champions"] == ORIGINAL


def test_global_exception_requires_confirmed_enemy_and_explicit_rule():
    start = row(ORIGINAL)
    pair = row({**ORIGINAL, "Top": "Sylas", "Jungle": "Skarner"},
               score=95)
    assert select_adaptive_comp(
        [pair, start], ORIGINAL, enemy_roles={},
        champion_traits=TRAITS, global_counter_rules=RULES)[0]["champions"] == ORIGINAL
    assert select_adaptive_comp(
        [pair, start], ORIGINAL, enemy_roles={"Top": "Malphite"},
        champion_traits=TRAITS, global_counter_rules={})[0]["champions"] == ORIGINAL


def test_vayne_top_requires_calibrated_lanematch_and_frontline_compensation():
    start = row(ORIGINAL)
    candidate = row({**ORIGINAL, "Top": "Vayne", "Jungle": "Skarner"},
                    score=94, edges=[{
                        "role": "Top", "enemy": "Malphite", "games": 3500,
                        "points": 2.2, "usable_for_ranking": True,
                    }])
    picked = plan([candidate, start])
    assert picked["champions"]["Top"] == "Vayne"
    broken = copy.deepcopy(candidate)
    broken["live_lane_edges"][0]["usable_for_ranking"] = False
    assert plan([broken, start])["champions"] == ORIGINAL
    broken = copy.deepcopy(candidate)
    broken["live_lane_edges"][0]["games"] = 10
    assert plan([broken, start])["champions"] == ORIGINAL



def _stat(champion, *, against, normal, games=3200, baseline_games=50000):
    return {
        "ours": champion, "enemy": "Malphite", "games": games,
        "head_to_head_win_rate": against,
        "role_baseline_win_rate": normal,
        "role_baseline_games": baseline_games,
        "source_patch": "16.21", "rank_filter": "Emerald+",
        "queue_filter": "Ranked Solo/Duo",
    }


def test_current_patch_baseline_adjusted_vayne_counter_can_trigger_compensated_swap():
    start = row(ORIGINAL)
    candidate = row({**ORIGINAL, "Top": "Vayne", "Jungle": "Skarner"},
                    score=94.0, team_plan_score=8.6, mid_jungle_score=7.0)
    evidence = {"Top": [
        _stat("Ornn", against=45, normal=51),
        _stat("Vayne", against=57, normal=51),
    ]}
    output = select_adaptive_comp(
        [candidate, start], ORIGINAL,
        enemy_roles={"Top": "Malphite"}, identity="dive",
        champion_traits=TRAITS,
        statistical_evidence=evidence,
    )[0]
    assert output["champions"]["Top"] == "Vayne"
    assert output["champions"]["Jungle"] == "Skarner"
    assert "adjusted counter" in output["adaptive_reasons"]["Top"]
    assert "compensating" in output["adaptive_reasons"]["Jungle"]


def test_baseline_adjustment_rejects_high_raw_winrate_without_counter_lift():
    start = row(ORIGINAL)
    candidate = row({**ORIGINAL, "Top": "Vayne", "Jungle": "Skarner"}, score=97)
    fake_good_raw = {"Top": [
        _stat("Ornn", against=52, normal=52),
        _stat("Vayne", against=57, normal=57),
    ]}
    output = select_adaptive_comp(
        [candidate, start], ORIGINAL, enemy_roles={"Top": "Malphite"},
        champion_traits=TRAITS, statistical_evidence=fake_good_raw,
    )
    assert output[0]["champions"] == ORIGINAL


def test_unqualified_patch_small_sample_or_no_baseline_never_autopicks():
    start = row(ORIGINAL)
    candidate = row({**ORIGINAL, "Top": "Vayne", "Jungle": "Skarner"}, score=95)
    good = [
        _stat("Ornn", against=42, normal=52),
        _stat("Vayne", against=59, normal=50),
    ]
    for corrupt in (
        [{**good[0], "source_patch": "16.20"}, good[1]],
        [good[0], {**good[1], "games": 100}],
        [good[0], {**good[1], "role_baseline_games": 100}],
        [good[0], {**good[1], "role_baseline_win_rate": None}],
    ):
        result = select_adaptive_comp(
            [candidate, start], ORIGINAL, enemy_roles={"Top": "Malphite"},
            champion_traits=TRAITS, statistical_evidence={"Top": corrupt},
        )
        assert result[0]["champions"] == ORIGINAL
