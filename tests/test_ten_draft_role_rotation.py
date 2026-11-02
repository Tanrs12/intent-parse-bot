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


def test_cian_jungle_rat_mid_jack_top_ste_adc_legal_rotation_survives():
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
        332490596004659201, 139837324438536193,
        270517092703272960, 286592835850076162,
        400745307467415553,
    )
    assert target in signatures
    # Experimental rotations cannot reverse the two specialists when the
    # main-role pairing is legal for the same five.
    assert not any(row["Mid"]["user_id"] == 139837324438536193
                   and row["Jungle"]["user_id"] == 270517092703272960 for row in options)
    assert all(role in option[role]["roles"] for option in options for role in ROLES)


def test_tail_flex_does_not_force_large_ranked_quality_sacrifice():
    # An experimental role rotation nearly 19 points weaker should not override
    # established carry positions simply to make the gallery more adventurous.
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
    assert all(_signature(item) != flex_roles for item in result)
    assert min(item["score"] for item in result) >= max(item["score"] for item in pool) - 14
    assert len({tuple(item["champions"][r] for r in ROLES) for item in result}) == 10


def test_rat_mid_is_visible_in_flex_gallery_when_strong_and_legal():
    # Preserve one P1 Rat-Mid option in the six flex cards even when many
    # higher-scored non-Rat-Mid layouts compete for those positions.
    cian, rat, jack, ste, jezz = (
        139837324438536193, 270517092703272960,
        332490596004659201, 286592835850076162,
        400745307467415553,
    )
    base = (rat, cian, ste, jack, jezz)
    rat_mid = (jack, cian, rat, ste, jezz)
    pool = [_candidate(i, base, score=100 - i * .15) for i in range(12)]
    competing = [
        sig for sig in itertools.permutations(base)
        if sig != base and sig != rat_mid and sig[2] != rat
    ]
    pool.extend(
        _candidate(12 + i, sig, score=98.0 - .08 * i)
        for i, sig in enumerate(competing[:35])
    )
    rat_mid_comp = _candidate(100, rat_mid, score=87.0, bot=8.2, mid_jungle=8.1)
    rat_mid_comp["players"]["Mid"]["role_priority"]["Mid"] = 1
    pool.append(rat_mid_comp)
    selected = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6
    )
    assert len(selected) == 10
    assert any(int(row["players"]["Mid"]["user_id"]) == rat for row in selected[4:])
    assert len({
        tuple(row["champions"][role] for role in ROLES)
        for row in selected
    }) == 10


def test_rat_mid_is_not_forced_when_bot_lane_is_bad():
    rat, cian, ste, jack, jezz = (
        270517092703272960, 139837324438536193,
        286592835850076162, 332490596004659201,
        400745307467415553,
    )
    base = (rat, cian, ste, jack, jezz)
    bad = _candidate(100, (jack, cian, rat, ste, jezz),
                     score=92.0, bot=5.0, mid_jungle=8.0)
    bad["players"]["Mid"]["role_priority"]["Mid"] = 1
    pool = [_candidate(i, base, score=100 - .2*i) for i in range(12)]
    pool.append(bad)
    selected = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6
    )
    assert len(selected) == 10
    assert all(int(row["players"]["Mid"]["user_id"]) != rat for row in selected)


def test_rat_mid_cannot_be_invented_without_ste_adc_pool():
    cian, rat, jack, ste, jezz = (
        139837324438536193, 270517092703272960,
        332490596004659201, 286592835850076162,
        400745307467415553,
    )
    roster = [
        (cian, "Cian", ["Jungle", "Mid"]),
        (rat, "Rat", ["Mid", "Top"]),
        (jack, "Jack", ["ADC", "Top"]),
        (ste, "Ste", ["Mid", "ADC"]),
        (jezz, "Jezz", ["Support"]),
    ]
    signups = [{
        "user_id": uid, "display_name": name, "roles": roles,
        "role_priority": {r: 1 for r in roles},
        "champions": {r: [f"{name}-{r}"] for r in roles
                      if not (name == "Ste" and r == "ADC")},
    } for uid, name, roles in roster]
    assignments = generate_team_options(signups, limit=24, require_pools=True)
    assert assignments
    assert all(int(a["Mid"]["user_id"]) != rat for a in assignments)


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
    target = (332490596004659201, 139837324438536193, 270517092703272960,
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
    # Strong first-role allocations take priority over putting Ste ADC merely
    # to hit a rotation quota; he can still appear there if he has a good plan.
    assert len({tuple(rec["champions"][role] for role in ROLES) for rec in selected}) == 10
    # Ranked efficiency now outranks cosmetic role permutations. At least one
    # alternative remains, without sacrificing carry priority just for novelty.
    assert len({
        tuple(rec["players"][role]["user_id"] for role in ROLES)
        for rec in selected
    }) >= 2
    assert all(int(rec["players"]["ADC"]["user_id"]) != 139837324438536193
               for rec in selected)
