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


def test_tail_flex_replaces_ste_mid_and_jack_top_even_if_base_has_higher_score():
    # This is the specific complaint: first four drafts can be strong defaults;
    # drafts 5-10 must offer real Cian Mid / Rat Jungle / Jack Top / Ste ADC.
    cian, rat, jack, ste, jezz = (
        139837324438536193, 270517092703272960,
        332490596004659201, 286592835850076162,
        400745307467415553,
    )
    fixed_roles = (rat, cian, ste, jack, jezz)
    flex_roles = (jack, rat, cian, ste, jezz)
    pool = [_candidate(i, fixed_roles, score=99.8 - .2*i) for i in range(18)]
    pool.append(_candidate(18, flex_roles, score=81.2))
    result = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6
    )
    assert len(result) == 10
    assert any(_signature(item) == flex_roles for item in result[4:])
    assert len({tuple(item["champions"][r] for r in ROLES) for item in result}) == 10


def test_role_rotations_do_not_invent_missing_submitted_champion_pools():
    players = [
        {"user_id": i, "display_name": str(i), "roles": list(ROLES),
         "role_priority": {r: 2 for r in ROLES},
         "champions": {r: [f"{r}-{i}"] for r in ROLES}}
        for i in range(5)
    ]
    players[3]["champions"]["ADC"] = []
    options = generate_team_options(players, limit=24, require_pools=True)
    # Other players may rotate through ADC; none can place player 3 there.
    assert options
    assert all(int(option["ADC"]["user_id"]) != 3 for option in options)
    assert all(
        option[role].get("champions", {}).get(role)
        for option in options for role in ROLES
    )


def test_role_gallery_eligibility_preserves_ste_adc_when_confirmed():
    roster = [
        (139837324438536193, ["Jungle", "Mid", "Top", "ADC"]),
        (270517092703272960, ["Mid", "Jungle", "Top"]),
        (332490596004659201, ["ADC", "Top"]),
        (286592835850076162, ["Mid", "ADC"]),
        (400745307467415553, ["Support"]),
    ]
    signups = [
        {"user_id": uid, "display_name": str(uid), "roles": roles,
         "role_priority": {role: (1 if k == 0 else 3) for k, role in enumerate(roles)},
         "champions": {role: [f"{uid}-{role}"] for role in roles}}
        for uid, roles in roster
    ]
    layouts = generate_team_options(signups, limit=24, require_pools=True)
    target = (332490596004659201, 270517092703272960, 139837324438536193,
              286592835850076162, 400745307467415553)
    assert target in {
        tuple(row[role]["user_id"] for role in ROLES) for row in layouts
    }


def test_bundled_five_player_flex_gallery_end_to_end():
    # Regression for the real failure: champion/role rotation looked legal
    # but secondary-role gating erased Ste bot BEFORE the top-300 stage.
    # Additional champions below are deliberately test-only, not Discord edits.
    import json
    from pathlib import Path
    saved = json.loads(
        (Path(__file__).resolve().parents[1] / "loadfiles" / "flexers-team.json")
        .read_text(encoding="utf-8")
    )
    who = {"Cian", "Aristo", "Jack", "Ste", "Jezz"}
    squad = []
    for record in saved["players"]:
        if record.get("rename") not in who:
            continue
        pools = {
            role: [row["champion"] for row in entries]
            for role, entries in record.get("pools", {}).items()
        }
        if record["rename"] == "Cian":
            pools["Mid"] = ["Sylas"]
        if record["rename"] == "Ste":
            pools["ADC"] = ["Jinx", "Ashe"]
        player = {
            "user_id": record["user_id"],
            "display_name": record["rename"],
            "roles": list(record["roles"]),
            "role_priority": {
                role: (1 if index == 0 else 2 if index == 1 else 3)
                for index, role in enumerate(record["roles"])
            },
            "champions": pools,
        }
        squad.append(player)
    assignments = generate_team_options(squad, limit=24, require_pools=True)
    candidates, missing = eng.recommend_comps(
        assignments, limit=300, pool_limit=10, prepared_flex_gallery=True
    )
    assert not missing
    assert len({
        tuple(c["players"][role]["user_id"] for role in ROLES)
        for c in candidates
    }) >= 4
    selected = _select_diverse_recommendations(
        candidates, limit=10, core_limit=4, role_mix_slots=6
    )
    assert len(selected) == 10
    assert any(int(rec["players"]["ADC"]["user_id"]) == 286592835850076162
               for rec in selected[4:])
    assert len({
        tuple(rec["players"][role]["user_id"] for role in ROLES)
        for rec in selected
    }) >= 4
