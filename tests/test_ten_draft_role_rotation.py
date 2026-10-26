"""Prepared ten-draft portfolios should actually rotate legal player roles."""
import itertools

import league_comp_engine as eng
from league_flex_core import ROLES, _select_diverse_recommendations, generate_team_options


def _candidate(i, player_ids, *, score=96.0, bot=8.3, mid_jungle=8.2):
    return {
        "score": score,
        "team_plan_score": 8.0,
        "bot_lane_score": bot,
        "mid_jungle_score": mid_jungle,
        "team_plan": "front-to-back",
        "credible_mixed_damage": True,
        "champions": {
            role: f"{role}Champ{i}" for role in ROLES
        },
        "players": {
            role: {
                "user_id": player_ids[k],
                "roles": list(ROLES),
                "role_priority": {role: 2},
            }
            for k, role in enumerate(ROLES)
        },
        "carry_plan": {"role": "ADC"},
        "archetypes": ["engage"],
    }


def _signature(rec):
    return tuple(rec["players"][role]["user_id"] for role in ROLES)


def test_drafts_5_through_10_switch_role_assignments_not_just_champions():
    core = tuple(range(1, 6))
    pool = [_candidate(i, core, score=100 - i * .2) for i in range(12)]
    alternatives = [
        permutation for permutation in itertools.permutations(core)
        if sum(a != b for a, b in zip(permutation, core)) >= 2
    ]
    pool += [
        _candidate(i + 12, sig, score=95 - i * .10)
        for i, sig in enumerate(alternatives[:25])
    ]
    selected = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6
    )
    assert len(selected) == 10
    assert len({
        tuple(rec["champions"][role] for role in ROLES)
        for rec in selected
    }) == 10
    assert len({_signature(rec) for rec in selected[4:]}) >= 5
    assert all(
        sum(a != b for a, b in zip(_signature(rec), _signature(selected[0]))) >= 2
        for rec in selected[4:]
    )
    assert all(rec["bot_lane_score"] >= 7 and rec["mid_jungle_score"] >= 7 for rec in selected)


def test_cannot_force_role_swaps_when_all_alternatives_are_bad():
    core = (1, 2, 3, 4, 5)
    pool = [_candidate(i, core, score=100 - i*.1) for i in range(12)]
    pool += [
        _candidate(i + 12, (2, 1, 3, 4, 5), score=99, bot=4)
        for i in range(4)
    ]
    selected = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6
    )
    assert len(selected) == 10
    assert all(_signature(rec) == core for rec in selected)


def test_rat_submitted_mid_top_jungle_pools_get_wider_search():
    rat = {"user_id": 270517092703272960}
    for role in ("Mid", "Top", "Jungle"):
        assert eng._role_search_pool_limit(rat, role, 10) >= 16
    assert eng._role_search_pool_limit(rat, "ADC", 10) == 10


def test_cian_mid_rat_jungle_jack_top_ste_adc_legal_rotation_survives():
    # All five roles must be selected by the person. Never manufacture an
    # eligible role or champion based on this example.
    roster = [
        (139837324438536193, "Cian", ["Jungle", "Mid", "Top", "ADC"],
         {"Jungle": 1, "Mid": 1, "Top": 2, "ADC": 2}),
        (270517092703272960, "Rat", ["Mid", "Jungle", "Top"],
         {"Mid": 1, "Jungle": 2, "Top": 2}),
        (332490596004659201, "Jack", ["ADC", "Top"],
         {"ADC": 1, "Top": 2}),
        (286592835850076162, "Ste", ["Mid", "ADC"],
         {"Mid": 1, "ADC": 3}),
        (400745307467415553, "Jezz", ["Support"],
         {"Support": 1}),
    ]
    signups = [
        {
            "user_id": uid, "display_name": name, "roles": roles,
            "role_priority": priorities,
            "champions": {role: [f"{role}-{name}"] for role in roles},
        }
        for uid, name, roles, priorities in roster
    ]
    options = generate_team_options(signups, limit=16)
    signatures = {
        tuple(option[role]["user_id"] for role in ROLES)
        for option in options
    }
    target = (
        332490596004659201, 270517092703272960,
        139837324438536193, 286592835850076162,
        400745307467415553,
    )
    assert target in signatures
    assert all(role in option[role]["roles"] for option in options for role in ROLES)
