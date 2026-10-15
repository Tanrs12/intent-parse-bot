from cogs.league_flex import _conditional_ban_branches
from league_comp_engine import load_meta


def test_alternate_top_pick_recalculates_protection_bans():
    rec = {
        "champions": {
            "Top": "Malphite",
            "Jungle": "Vi",
            "Mid": "Orianna",
            "ADC": "Kai'Sa",
            "Support": "Nautilus",
        },
        "role_alternatives": {"Top": ["Renekton"]},
        "learn_suggestions": {},
    }
    branches = _conditional_ban_branches(rec, load_meta(), "blue", set())
    assert len(branches) == 1
    branch = branches[0]
    assert branch["champion"] == "Renekton"
    assert "Sylas" in branch["removed"]
    assert "Sylas" not in branch["bans"]



def test_conditional_ban_annotations_put_replacement_beside_primary():
    from cogs.league_flex import _conditional_ban_annotations
    base = [{"champion": "Syndra"}, {"champion": "Rumble"}]
    branches = [{
        "role": "Mid", "champion": "Yasuo",
        "added": ["Viktor"], "removed": ["Syndra"],
    }]
    notes = _conditional_ban_annotations(base, branches)
    assert notes["Syndra"] == [{"condition": "Yasuo", "ban": "Viktor"}]


def test_recommendation_role_guard_rejects_unselected_role():
    from cogs.league_flex import _recommendation_respects_selected_roles
    rec = {"players": {}}
    for role in ("Top", "Jungle", "Mid", "ADC", "Support"):
        rec["players"][role] = {"user_id": role, "display_name": role, "roles": [role]}
    assert _recommendation_respects_selected_roles(rec)
    rec["players"]["ADC"]["roles"] = ["Top", "Mid"]
    assert not _recommendation_respects_selected_roles(rec)


def test_diversity_does_not_choose_bad_team_plan_just_for_novelty():
    from cogs.league_flex import _select_diverse_recommendations
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    def rec(idx, score, plan_score, champs):
        return {
            "score": score, "team_plan_score": plan_score, "team_plan": f"plan-{idx}",
            "carry_plan": {"role": "Top"}, "archetypes": [f"a{idx}"],
            "champions": dict(zip(roles, champs)),
            "players": {role: {"user_id": i + 1, "display_name": role, "roles": [role]} for i, role in enumerate(roles)},
        }
    strong = [
        rec(1, 90, 9.0, ["Ornn", "Vi", "Orianna", "Jinx", "Lulu"]),
        rec(2, 88, 8.5, ["Renekton", "Wukong", "Ahri", "Kai'Sa", "Nautilus"]),
        rec(3, 87, 8.0, ["Camille", "Sejuani", "Syndra", "Ezreal", "Karma"]),
        rec(4, 86, 7.8, ["K'Sante", "Jarvan IV", "Viktor", "Jhin", "Bard"]),
    ]
    flashy_bad = rec(5, 89, 5.8, ["Varus", "Amumu", "Yasuo", "Ezreal", "Karma"])
    chosen = _select_diverse_recommendations([strong[0], flashy_bad, *strong[1:]], limit=4, core_limit=4, role_mix_slots=0)
    assert flashy_bad not in chosen



def test_role_alternative_cannot_break_ranged_top_structure():
    from cogs.league_flex import _attach_role_alternatives
    roles=("Top","Jungle","Mid","ADC","Support")
    players={role:{"user_id":i+1,"display_name":role,"roles":[role]} for i,role in enumerate(roles)}
    base={
        "players":players,
        "champions":{"Top":"Varus","Jungle":"Skarner","Mid":"Galio","ADC":"Ezreal","Support":"Karma"},
        "team_plan":"poke / siege","score":90,"team_plan_score":8.5,"top_structure_adjustment":1.6,
        "bot_lane_score":8.0,"mid_jungle_score":7.0,
    }
    bad_mid={
        **base,
        "champions":dict(base["champions"],Mid="Yasuo"),
        "score":89,"team_plan_score":8.0,"top_structure_adjustment":-7.5,
    }
    _attach_role_alternatives([base],[base,bad_mid])
    assert "Yasuo" not in base.get("role_alternatives",{}).get("Mid",[])


def test_draft_portfolio_uses_distinct_shells_instead_of_one_pick_clones():
    from cogs.league_flex import _select_diverse_recommendations
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    players = {role: {"user_id": i + 1, "display_name": role, "roles": [role]} for i, role in enumerate(roles)}

    def rec(champs, score, plan, mixed=True):
        return {
            "score": score,
            "team_plan_score": 8.0,
            "team_plan": plan,
            "carry_plan": {"role": "Mid"},
            "archetypes": [plan],
            "credible_mixed_damage": mixed,
            "champions": dict(zip(roles, champs)),
            "players": players,
        }

    base = rec(["Ornn", "Zac", "Yasuo", "Jhin", "Rakan"], 100, "engage")
    clone = rec(["Sion", "Zac", "Yasuo", "Jhin", "Rakan"], 99, "engage")
    shell2 = rec(["Renekton", "Vi", "Ahri", "Ashe", "Nautilus"], 96, "pick")
    shell3 = rec(["K'Sante", "Diana", "Yasuo", "Ezreal", "Karma"], 94, "dive")
    chosen = _select_diverse_recommendations([base, clone, shell2, shell3], limit=3)
    assert base in chosen
    assert clone not in chosen
    assert shell2 in chosen and shell3 in chosen


def test_draft_portfolio_prefers_credible_mixed_damage_when_enough_exist():
    from cogs.league_flex import _select_diverse_recommendations
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    players = {role: {"user_id": i + 1, "display_name": role, "roles": [role]} for i, role in enumerate(roles)}

    def rec(tag, score, mixed):
        return {
            "score": score,
            "team_plan_score": 8.0,
            "team_plan": tag,
            "carry_plan": {"role": "Mid"},
            "archetypes": [tag],
            "credible_mixed_damage": mixed,
            "champions": {role: f"{role}-{tag}" for role in roles},
            "players": players,
        }

    rows = [rec("physical-only", 100, False)] + [rec(f"mixed-{i}", 96 - i, True) for i in range(5)]
    chosen = _select_diverse_recommendations(rows, limit=4)
    assert len(chosen) == 4
    assert all(row["credible_mixed_damage"] for row in chosen)


def test_varus_is_not_offered_in_top_signup_pool():
    from cogs.league_flex import ROLE_CHAMPIONS
    assert "Varus" not in ROLE_CHAMPIONS["Top"]


def test_fresh_draft_rotation_can_change_the_opening_shell_without_dropping_quality():
    from cogs.league_flex import _select_diverse_recommendations
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")

    def rec(idx, score, champs, players):
        return {
            "score": score,
            "team_plan_score": 8.0,
            "team_plan": f"plan-{idx}",
            "carry_plan": {"role": "Mid"},
            "archetypes": [f"plan-{idx}"],
            "credible_mixed_damage": True,
            "champions": dict(zip(roles, champs)),
            "players": {
                role: {"user_id": players[i], "display_name": role, "roles": [role]}
                for i, role in enumerate(roles)
            },
        }

    rows = [
        rec(1, 100.0, ["Ornn", "Zac", "Yasuo", "Jhin", "Rakan"], [1,2,3,4,5]),
        rec(2, 99.2, ["Renekton", "Vi", "Ahri", "Kai'Sa", "Nautilus"], [2,1,3,4,5]),
        rec(3, 98.5, ["K'Sante", "Jarvan IV", "Orianna", "Ashe", "Braum"], [1,3,2,4,5]),
    ]
    first_signatures = set()
    for token in map(str, range(20)):
        out = _select_diverse_recommendations(rows, limit=3, rotation_token=token)
        first_signatures.add(tuple(out[0]["champions"][role] for role in roles))
        assert out[0]["score"] >= 97.0
    assert len(first_signatures) > 1


def test_portfolio_rewards_bot_lane_style_diversity_when_quality_is_close():
    from cogs.league_flex import _select_diverse_recommendations
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    players = {role: {"user_id": i + 1, "display_name": role, "roles": [role]} for i, role in enumerate(roles)}

    def rec(champs, score, plan):
        return {
            "score": score,
            "team_plan_score": 8.0,
            "team_plan": plan,
            "carry_plan": {"role": "Mid"},
            "archetypes": [plan],
            "credible_mixed_damage": True,
            "champions": dict(zip(roles, champs)),
            "players": players,
        }

    enchanter1 = rec(["Ornn", "Zac", "Yasuo", "Jinx", "Lulu"], 100, "scaling front-to-back")
    enchanter2 = rec(["Renekton", "Vi", "Ahri", "Lucian", "Milio"], 99.8, "tempo skirmish")
    engage = rec(["K'Sante", "Jarvan IV", "Orianna", "Kai'Sa", "Nautilus"], 99.5, "dive / all-in")
    chosen = _select_diverse_recommendations([enchanter1, enchanter2, engage], limit=2)
    assert enchanter1 in chosen
    assert engage in chosen



def test_team_option_search_preserves_selected_roles_for_top_carry_flex_player(monkeypatch):
    import cogs.league_flex as flex

    cian = {
        "user_id": 1, "display_name": "Cian",
        "roles": ["Top", "Jungle", "Mid"],
        "role_priority": {"Top": 1, "Jungle": 1, "Mid": 1},
    }
    signups = [
        cian,
        {"user_id": 2, "display_name": "A", "roles": ["Top", "Jungle"], "role_priority": {"Top": 1, "Jungle": 1}},
        {"user_id": 3, "display_name": "B", "roles": ["Jungle", "Mid"], "role_priority": {"Jungle": 1, "Mid": 1}},
        {"user_id": 4, "display_name": "C", "roles": ["ADC"], "role_priority": {"ADC": 1}},
        {"user_id": 5, "display_name": "D", "roles": ["Support"], "role_priority": {"Support": 1}},
    ]
    monkeypatch.setattr(flex, "load_player_score_index", lambda: {
        1: {"carry_index": 100.0}, 2: {"carry_index": 60.0}, 3: {"carry_index": 55.0},
        4: {"carry_index": 50.0}, 5: {"carry_index": 40.0},
    })
    monkeypatch.setattr(
        flex,
        "role_experience_score",
        lambda uid, role: {("1", "Jungle"): 7.0, ("1", "Mid"): 5.0, ("1", "Top"): 4.0}.get((str(uid), role), 3.0),
    )
    options = flex.generate_team_options(signups, limit=8)
    cian_roles = {
        role
        for option in options
        for role in flex.ROLES
        if int(option[role]["user_id"]) == 1
    }
    assert {"Top", "Jungle", "Mid"}.issubset(cian_roles)


def test_role_priority_penalties_make_secondary_and_fill_true_fallbacks():
    import cogs.league_flex as flex

    base = {
        "user_id": 1,
        "display_name": "Flex",
        "roles": ["Top", "Mid", "ADC"],
        "role_priority": {"Top": 1, "Mid": 2, "ADC": 3},
    }
    assert flex._role_preference_penalty(base, "Top") == 0.0
    assert flex._role_preference_penalty(base, "Mid") == 1.4
    assert flex._role_preference_penalty(base, "ADC") == 3.75



def test_team_option_search_preserves_each_selected_adc_holder(monkeypatch):
    import cogs.league_flex as flex

    signups = [
        {"user_id": 1, "display_name": "Cian", "roles": ["Top", "Jungle", "Mid", "ADC"], "role_priority": {"Top": 1, "Jungle": 1, "Mid": 1, "ADC": 2}},
        {"user_id": 2, "display_name": "Rat", "roles": ["Top", "Jungle", "Mid", "ADC", "Support"], "role_priority": {"Top": 2, "Jungle": 2, "Mid": 1, "ADC": 3, "Support": 3}},
        {"user_id": 3, "display_name": "Ste", "roles": ["Mid", "ADC", "Support"], "role_priority": {"Mid": 1, "ADC": 3, "Support": 3}},
        {"user_id": 4, "display_name": "Kayla", "roles": ["Top", "Jungle", "Mid"], "role_priority": {"Top": 3, "Jungle": 1, "Mid": 2}},
        {"user_id": 5, "display_name": "Sasha", "roles": ["Top", "Jungle", "Mid", "Support"], "role_priority": {"Top": 2, "Jungle": 1, "Mid": 2, "Support": 1}},
    ]
    monkeypatch.setattr(flex, "load_player_score_index", lambda: {uid: {"carry_index": 50.0} for uid in range(1, 6)})
    monkeypatch.setattr(flex, "role_experience_score", lambda uid, role: 3.0)
    options = flex.generate_team_options(signups, limit=8)
    assert {int(option["ADC"]["user_id"]) for option in options} == {1, 2, 3}


def test_draft_portfolio_balances_adc_between_viable_holders():
    from cogs.league_flex import _select_diverse_recommendations
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")

    def rec(idx, adc_uid):
        player_ids = [10 + idx, 20 + idx, 30 + idx, adc_uid, 50 + idx]
        return {
            "score": 100.0 - idx * 0.5,
            "team_plan_score": 8.0,
            "team_plan": f"plan-{idx}",
            "carry_plan": {"role": "Mid"},
            "archetypes": [f"a-{idx}"],
            "credible_mixed_damage": True,
            "champions": {role: f"{role}-{idx}" for role in roles},
            "players": {
                role: {"user_id": player_ids[i], "display_name": str(player_ids[i]), "roles": list(roles)}
                for i, role in enumerate(roles)
            },
        }

    rows = [
        rec(0, 101), rec(1, 101),
        rec(2, 202), rec(3, 202),
        rec(4, 303), rec(5, 303),
    ]
    chosen = _select_diverse_recommendations(rows, limit=6)
    counts = {}
    for row in chosen:
        uid = int(row["players"]["ADC"]["user_id"])
        counts[uid] = counts.get(uid, 0) + 1
    assert counts == {101: 2, 202: 2, 303: 2}
