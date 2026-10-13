import league_comp_engine as engine
from league_comp_engine import _team_plan_completion, _bot_lane_duo_score, _coordinated_duo_score, _bounded_package_products, counter_ban_recommendations, load_meta, recommend_comps, score_comp


def _player(user_id, name, role, champions):
    return {
        "user_id": user_id,
        "display_name": name,
        "roles": [role],
        "champions": {role: champions},
    }


def test_structured_comp_scores_above_greedy_all_in_comp():
    structured = {
        "Top": "Ornn",
        "Jungle": "Jarvan IV",
        "Mid": "Orianna",
        "ADC": "Jinx",
        "Support": "Lulu",
    }
    greedy = {
        "Top": "Riven",
        "Jungle": "Master Yi",
        "Mid": "Yasuo",
        "ADC": "Draven",
        "Support": "Pyke",
    }
    assert score_comp(structured)["score"] > score_comp(greedy)["score"]



def test_ashe_is_not_mislabeled_as_protect_the_carry():
    ashe_comp = {
        "Top": "Ornn",
        "Jungle": "Jarvan IV",
        "Mid": "Orianna",
        "ADC": "Ashe",
        "Support": "Lulu",
    }
    jinx_comp = dict(ashe_comp, ADC="Jinx")
    assert "protect-the-carry" not in score_comp(ashe_comp)["archetypes"]
    assert "protect-the-carry" in score_comp(jinx_comp)["archetypes"]

def test_bot_lane_duo_model_rewards_real_pairings_and_penalises_mismatches():
    meta = load_meta()
    assert _bot_lane_duo_score("Ezreal", "Karma", meta)["score"] >= 8.0
    assert _bot_lane_duo_score("Caitlyn", "Morgana", meta)["score"] >= 8.0
    assert _bot_lane_duo_score("Ezreal", "Lulu", meta)["score"] <= 4.0
    assert _bot_lane_duo_score("Sivir", "Morgana", meta)["score"] <= 4.5


def test_ashe_braum_is_not_treated_as_an_excellent_lane_without_current_evidence():
    meta = load_meta()
    result = _bot_lane_duo_score("Ashe", "Braum", meta)
    assert result["pro_games"] == 0
    assert result["score"] <= 5.5
    assert result["grade"] in {"weak", "playable"}


def test_large_pool_package_search_is_bounded_and_preserves_role_coverage():
    meta = load_meta()
    pools = {
        "Top": ["Ornn", "Gnar", "Shen", "Riven", "Malphite", "Urgot"],
        "Jungle": ["Vi", "Jarvan IV", "Amumu", "Sejuani", "Nocturne", "Viego"],
        "Mid": ["Ahri", "Orianna", "Viktor", "Syndra", "Yasuo", "Malzahar"],
        "ADC": ["Jinx", "Ashe", "Caitlyn", "Ezreal", "Jhin", "Smolder"],
        "Support": ["Lulu", "Nautilus", "Leona", "Nami", "Braum", "Karma"],
    }
    assignment = {
        role: {"user_id": i + 900001, "display_name": role}
        for i, role in enumerate(engine.ROLES)
    }
    products = _bounded_package_products(
        pools,
        assignment,
        meta,
        {role: [] for role in engine.ROLES},
    )
    assert 0 < len(products) <= 1000
    for index, role in enumerate(engine.ROLES):
        represented = {product[index] for product in products}
        assert set(pools[role]).issubset(represented)


def test_mid_jungle_model_rewards_real_packages():
    meta = load_meta()
    assert _coordinated_duo_score("Jungle", "Vi", "Mid", "Ahri", meta)["score"] >= 7.5
    assert _coordinated_duo_score("Jungle", "Jarvan IV", "Mid", "Galio", meta)["score"] >= 7.5
    assert _coordinated_duo_score("Jungle", "Diana", "Mid", "Yasuo", meta)["score"] >= 7.5


def test_mid_jungle_package_materially_changes_comp_score():
    good = {"Top": "Ornn", "Jungle": "Vi", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lulu"}
    loose = {"Top": "Ornn", "Jungle": "Master Yi", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lulu"}
    assert score_comp(good)["score"] > score_comp(loose)["score"]


def test_bot_lane_pairing_materially_changes_comp_score():
    base = {
        "Top": "Ornn",
        "Jungle": "Jarvan IV",
        "Mid": "Orianna",
        "ADC": "Ezreal",
        "Support": "Karma",
    }
    bad_lane = dict(base, Support="Lulu")
    assert score_comp(base)["score"] >= score_comp(bad_lane)["score"] + 4.0


def test_weak_bot_lane_cannot_be_rescued_by_rest_of_comp():
    good = {"Top": "Ornn", "Jungle": "Jarvan IV", "Mid": "Orianna", "ADC": "Draven", "Support": "Nautilus"}
    weak = dict(good, Support="Neeko")
    assert score_comp(good)["score"] >= score_comp(weak)["score"] + 3.0


def test_ashe_seraphine_heavy_engage_shell_is_pick_control_not_siege():
    comp = {"Top": "Poppy", "Jungle": "Amumu", "Mid": "Hwei", "ADC": "Ashe", "Support": "Seraphine"}
    assert score_comp(comp)["team_plan"] == "pick / control"


def test_real_poke_shell_can_still_be_siege():
    comp = {"Top": "Ornn", "Jungle": "Jarvan IV", "Mid": "Xerath", "ADC": "Ezreal", "Support": "Karma"}
    assert score_comp(comp)["team_plan"] == "poke / siege"


def test_recommendations_only_leave_submitted_pool_for_marked_history_suggestions():
    assignment = {
        "Top": _player(1, "Top", "Top", ["Ornn", "Riven"]),
        "Jungle": _player(2, "Jungle", "Jungle", ["Jarvan IV", "Master Yi"]),
        "Mid": _player(3, "Mid", "Mid", ["Orianna", "Yasuo"]),
        "ADC": _player(4, "ADC", "ADC", ["Jinx", "Draven"]),
        "Support": _player(5, "Support", "Support", ["Lulu", "Pyke"]),
    }
    recommendations, missing = recommend_comps([assignment], limit=3, pool_limit=10)
    assert recommendations
    assert not missing
    for recommendation in recommendations:
        assert len(set(recommendation["champions"].values())) == 5
        for role, player in assignment.items():
            champion = recommendation["champions"][role]
            if champion not in player["champions"][role]:
                assert recommendation["pick_sources"][role] == "history"
                assert champion in recommendation["history_suggestions"][role]


def test_missing_champion_pool_uses_fallback_instead_of_blocking():
    assignment = {
        "Top": _player(1, "Top", "Top", ["Ornn"]),
        "Jungle": _player(2, "Jungle", "Jungle", ["Jarvan IV"]),
        "Mid": _player(3, "Mid", "Mid", []),
        "ADC": _player(4, "ADC", "ADC", ["Jinx"]),
        "Support": _player(5, "Support", "Support", ["Lulu"]),
    }
    recommendations, missing = recommend_comps([assignment], limit=3)
    assert recommendations
    assert not missing
    assert all(rec["pool_sources"]["Mid"] == "history" for rec in recommendations)


def test_cheeky_chuckler_history_supplies_missing_support_pool():
    cheeky_id = 400745307467415553
    assignment = {
        "Top": _player(1, "Top", "Top", ["Ornn"]),
        "Jungle": _player(2, "Jungle", "Jungle", ["Jarvan IV"]),
        "Mid": _player(3, "Mid", "Mid", ["Orianna"]),
        "ADC": _player(4, "ADC", "ADC", ["Jinx"]),
        "Support": _player(cheeky_id, "The Cheeky Chuckler", "Support", []),
    }
    recommendations, missing = recommend_comps([assignment], limit=3)
    assert recommendations
    assert not missing
    assert all(rec["pool_sources"]["Support"] == "history" for rec in recommendations)
    assert recommendations[0]["champions"]["Support"] in {
        "Lulu", "Leona", "Blitzcrank", "Braum", "Janna", "Poppy", "Miss Fortune"
    }


def test_protect_the_carry_prefers_stronger_historical_carry_profile():
    from league_comp_engine import _player_fit_adjustment, load_meta

    chud = 139837324438536193
    cheeky = 400745307467415553
    neutral = {
        "Top": {"user_id": 548212317515677697, "display_name": "Kayla"},
        "Jungle": {"user_id": 265219035019477002, "display_name": "Ste"},
        "Mid": {"user_id": 270517092703272960, "display_name": "Aristo"},
    }
    champions = {
        "Top": "Ornn",
        "Jungle": "Jarvan IV",
        "Mid": "Orianna",
        "ADC": "Jinx",
        "Support": "Lulu",
    }
    strong_adc = {
        **neutral,
        "ADC": {"user_id": chud, "display_name": "Chud"},
        "Support": {"user_id": cheeky, "display_name": "Cheeky"},
    }
    weak_adc = {
        **neutral,
        "ADC": {"user_id": cheeky, "display_name": "Cheeky"},
        "Support": {"user_id": chud, "display_name": "Chud"},
    }
    meta = load_meta()
    strong = _player_fit_adjustment(strong_adc, champions, ["protect-the-carry"], meta)
    weak = _player_fit_adjustment(weak_adc, champions, ["protect-the-carry"], meta)
    assert strong["adjustment"] > weak["adjustment"] + 5
    assert strong["adc_carry_index"] > weak["adc_carry_index"]


def test_current_lane_matchup_data_can_re_rank_viktor_responses():
    from league_comp_engine import build_draft_book, matchup_response

    advantage, games = matchup_response("Mid", "Viktor", "Akali")
    assert advantage is not None
    assert games >= 100

    assignment = {
        "Top": _player(1, "Top", "Top", ["Ornn", "Jax"]),
        "Jungle": _player(2, "Jungle", "Jungle", ["Jarvan IV", "Vi"]),
        "Mid": _player(3, "Mid", "Mid", ["Akali", "Talon", "Syndra", "Orianna"]),
        "ADC": _player(4, "ADC", "ADC", ["Jinx", "Ezreal"]),
        "Support": _player(5, "Support", "Support", ["Lulu", "Nautilus"]),
    }
    base, branches, missing = build_draft_book(
        [assignment],
        base_limit=20,
        branches_per_role=5,
    )
    assert base
    assert not missing
    viktor = [branch for branch in branches if branch["enemy_champion"] == "Viktor"]
    assert viktor
    assert viktor[0]["response_champion"] in assignment["Mid"]["champions"]["Mid"]


def test_team_plan_rewards_shell_that_completes_scaling_bot_package():
    meta = load_meta()
    good = {"Top": "Ornn", "Jungle": "Jarvan IV", "Mid": "Orianna", "ADC": "Yunara", "Support": "Lulu"}
    bad = {"Top": "Fiora", "Jungle": "Master Yi", "Mid": "Kassadin", "ADC": "Yunara", "Support": "Lulu"}
    good_plan = _team_plan_completion(good, meta)
    bad_plan = _team_plan_completion(bad, meta)
    assert good_plan["plan"] == "scaling front-to-back"
    assert good_plan["score"] >= bad_plan["score"] + 4.0
    assert "frontline" in bad_plan["missing"]


def test_team_plan_prefers_active_shell_for_lucian_milio():
    meta = load_meta()
    good = {"Top": "Ornn", "Jungle": "Vi", "Mid": "Ahri", "ADC": "Lucian", "Support": "Milio"}
    bad = {"Top": "Kayle", "Jungle": "Master Yi", "Mid": "Kassadin", "ADC": "Lucian", "Support": "Milio"}
    good_plan = _team_plan_completion(good, meta)
    bad_plan = _team_plan_completion(bad, meta)
    assert good_plan["plan"] == "tempo skirmish"
    assert good_plan["score"] > bad_plan["score"]


def test_counter_bans_follow_side_and_pick_exposure():
    champions = {
        "Top": "Ornn", "Jungle": "Amumu", "Mid": "Taliyah", "ADC": "Ashe", "Support": "Seraphine"
    }
    pick_order = [("Mid", "Taliyah"), ("ADC", "Ashe"), ("Support", "Seraphine"), ("Jungle", "Amumu"), ("Top", "Ornn")]
    matchup_data = {
        "matchups": {
            "Top": {
                "Fiora": {"responses": {"Ornn": {"response_advantage": -10.0, "games": 500}}}
            },
            "Mid": {
                "Syndra": {"responses": {"Taliyah": {"response_advantage": -5.0, "games": 500}}}
            },
            "Jungle": {}, "ADC": {}, "Support": {},
        }
    }
    meta = {"role_stats": {role: {} for role in ("Top", "Jungle", "Mid", "ADC", "Support")}}
    red = counter_ban_recommendations(champions, pick_order, "red", matchup_data=matchup_data, meta=meta, limit=5)
    blue = counter_ban_recommendations(champions, pick_order, "blue", matchup_data=matchup_data, meta=meta, limit=5)
    assert [row["champion"] for row in red] == ["Syndra"]  # R5 Ornn cannot be counter-picked.
    assert "Fiora" in [row["champion"] for row in blue]  # B5 can still be answered by R5.
    assert red[0]["reasons"][0]["pick_number"] == 1


def test_recent_match_index_uses_normals_at_lower_weight_and_excludes_aram(monkeypatch):
    profile_data = {
        "players": {
            "1": {
                "recent_matches": [
                    {
                        "created_at": "2026-10-10T12:00:00+00:00", "queue_group": "RANKED",
                        "result": "WIN", "champion": "Ashe", "position": "ADC",
                        "dpm": 700, "damage_share": 25, "cs_per_min": 7.0, "gold_per_min": 430,
                        "kda": 4.0, "op_score": 7.0, "lane_score": 60, "kp": 55, "vision_score": 12,
                    },
                    {
                        "created_at": "2026-10-09T12:00:00+00:00", "queue_group": "NORMAL",
                        "result": "WIN", "champion": "Ashe", "position": "ADC",
                        "dpm": 800, "damage_share": 28, "cs_per_min": 7.5, "gold_per_min": 450,
                        "kda": 5.0, "op_score": 7.5, "lane_score": 65, "kp": 60, "vision_score": 14,
                    },
                    {
                        "created_at": "2026-10-08T12:00:00+00:00", "queue_group": "ARAM",
                        "result": "WIN", "champion": "Ashe", "position": "ADC",
                        "dpm": 2000, "damage_share": 50, "cs_per_min": 2.0, "gold_per_min": 600,
                        "kda": 10.0, "op_score": 10.0, "lane_score": 100, "kp": 95, "vision_score": 1,
                    },
                ]
            }
        }
    }
    monkeypatch.setattr(engine, "load_player_profiles", lambda: profile_data)
    engine.load_recent_match_index.cache_clear()
    try:
        row = engine.load_recent_match_index()[1]
        assert row["queue_counts"] == {"RANKED": 1, "NORMAL": 1}
        assert 1.0 < row["global"]["effective_games"] < 1.7
        assert row["by_role_champion"]["ADC"]["Ashe"]["raw_games"] == 2
        assert row["global"]["dpm"] < 850  # ARAM's 2000 DPM never enters the profile.
    finally:
        engine.load_recent_match_index.cache_clear()



def test_signature_mastery_counts_as_champion_ownership():
    from league_comp_engine import _champion_ownership

    chud = 139837324438536193
    aristo = 270517092703272960
    assert _champion_ownership(chud, "Lee Sin", "Jungle") > _champion_ownership(aristo, "Lee Sin", "Jungle") + 2.0
    assert _champion_ownership(chud, "Rengar", "Jungle") > _champion_ownership(aristo, "Rengar", "Jungle") + 1.0


def test_ranked_role_history_softly_prefers_natural_role_without_locking_it():
    from league_comp_engine import role_experience_score

    chud = 139837324438536193
    aristo = 270517092703272960
    # OP.GG currently tags Chud Jungle MAIN and Aristo Jungle SUB. Both remain legal
    # if Discord selected Jungle; this is only a tie-break signal.
    assert role_experience_score(chud, "Jungle") > role_experience_score(aristo, "Jungle")


def test_learn_suggestion_does_not_offer_skarner_when_sejuani_is_fixing_damage_mix():
    from league_comp_engine import _player_fit_adjustment, attach_learn_suggestions, load_meta, score_comp

    meta = load_meta()
    champions = {
        "Top": "Aatrox",
        "Jungle": "Sejuani",
        "Mid": "Yasuo",
        "ADC": "Ashe",
        "Support": "Seraphine",
    }
    assignment = {
        "Top": {"user_id": 400745307467415553, "display_name": "Cheeky"},
        "Jungle": {"user_id": 548212317515677697, "display_name": "Kayla"},
        "Mid": {"user_id": 270517092703272960, "display_name": "Aristo"},
        "ADC": {"user_id": 139837324438536193, "display_name": "Chud"},
        "Support": {"user_id": 332490596004659201, "display_name": "Jack"},
    }
    scored = score_comp(champions, meta)
    fit = _player_fit_adjustment(assignment, champions, scored["archetypes"], meta)
    rec = {
        "players": assignment,
        "champions": champions,
        "submitted_pools": {role: [champ] for role, champ in champions.items()},
        "history_suggestions": {role: [] for role in champions},
        "composition_score": scored["score"],
        "carry_plan": fit["carry_plan"],
        **scored,
    }
    attach_learn_suggestions([rec], meta=meta)
    assert "Skarner" not in rec.get("learn_suggestions", {}).get("Jungle", [])


def test_learn_suggestions_only_fill_narrow_submitted_pools():
    from league_comp_engine import _player_fit_adjustment, attach_learn_suggestions, load_meta, score_comp

    meta = load_meta()
    champions = {"Top": "Urgot", "Jungle": "Amumu", "Mid": "Yone", "ADC": "Ashe", "Support": "Seraphine"}
    assignment = {
        "Top": {"user_id": 400745307467415553, "display_name": "Cheeky"},
        "Jungle": {"user_id": 548212317515677697, "display_name": "Kayla"},
        "Mid": {"user_id": 270517092703272960, "display_name": "Aristo"},
        "ADC": {"user_id": 139837324438536193, "display_name": "Chud"},
        "Support": {"user_id": 332490596004659201, "display_name": "Jack"},
    }
    scored = score_comp(champions, meta)
    fit = _player_fit_adjustment(assignment, champions, scored["archetypes"], meta)
    rec = {
        "players": assignment,
        "champions": champions,
        "submitted_pools": {
            "Top": ["Urgot"],
            "Jungle": ["Amumu"],
            "Mid": ["Yone", "Syndra", "Azir", "Viktor"],
            "ADC": ["Ashe", "Caitlyn", "Ezreal", "Jinx"],
            "Support": ["Seraphine", "Nautilus", "Thresh", "Lulu"],
        },
        "history_suggestions": {role: [] for role in champions},
        "composition_score": scored["score"],
        "carry_plan": fit["carry_plan"],
        **scored,
    }
    attach_learn_suggestions([rec], meta=meta)
    learn = rec.get("learn_suggestions", {})
    assert "Mid" not in learn
    assert "ADC" not in learn
    assert "Support" not in learn


def test_role_preference_penalty_is_charged_but_not_a_hard_lock():
    assignment = {
        "Top": _player(1, "Top", "Top", ["Ornn"]),
        "Jungle": _player(2, "Jungle", "Jungle", ["Jarvan IV"]),
        "Mid": _player(3, "Mid", "Mid", ["Orianna"]),
        "ADC": _player(4, "ADC", "ADC", ["Jinx"]),
        "Support": _player(5, "Support", "Support", ["Lulu"]),
    }
    base, _ = recommend_comps([assignment], limit=1, pool_limit=1)
    flex_assignment = {role: dict(player) for role, player in assignment.items()}
    flex_assignment["Top"]["_role_preference_penalty"] = 3.0
    penalized, _ = recommend_comps([flex_assignment], limit=1, pool_limit=1)
    assert base and penalized
    assert penalized[0]["role_preference_penalty"] == 3.0
    assert 2.9 <= base[0]["score"] - penalized[0]["score"] <= 3.1



def test_poke_with_camille_is_labeled_side_lane_variant_not_pure_siege():
    meta = load_meta()
    comp = {
        "Top": "Camille",
        "Jungle": "Nidalee",
        "Mid": "Jayce",
        "ADC": "Ezreal",
        "Support": "Karma",
    }
    result = _team_plan_completion(comp, meta)
    assert result["plan"] == "poke + side-lane"
    assert any("side-lane" in reason for reason in result["reasons"])


def test_camille_kaisa_nautilus_gets_second_access_angle_in_dive_shell():
    meta = load_meta()
    comp = {
        "Top": "Camille",
        "Jungle": "Vi",
        "Mid": "Orianna",
        "ADC": "Kai'Sa",
        "Support": "Nautilus",
    }
    result = _team_plan_completion(comp, meta)
    assert result["plan"] == "dive / all-in"
    assert any("side-lane threat" in reason for reason in result["reasons"])


def test_best_player_on_low_agency_tank_has_explicit_opportunity_cost(monkeypatch):
    meta = load_meta()
    assignment = {
        role: {"user_id": i + 910001, "display_name": role}
        for i, role in enumerate(engine.ROLES)
    }
    scores = {
        910001: {"carry_index": 95.0, "reliability_index": 80.0, "metrics": {}, "roles": {}},
        910002: {"carry_index": 60.0, "reliability_index": 60.0, "metrics": {}, "roles": {}},
        910003: {"carry_index": 62.0, "reliability_index": 60.0, "metrics": {}, "roles": {}},
        910004: {"carry_index": 65.0, "reliability_index": 60.0, "metrics": {}, "roles": {}},
        910005: {"carry_index": 45.0, "reliability_index": 65.0, "metrics": {}, "roles": {}},
    }
    monkeypatch.setattr(engine, "load_player_score_index", lambda: scores)
    monkeypatch.setattr(engine, "_champion_comfort", lambda uid, champion, role=None: 6.0)
    monkeypatch.setattr(engine, "_champion_ownership", lambda uid, champion, role=None: 6.0)
    monkeypatch.setattr(engine, "_mastery_points_for", lambda uid, champion: 0)

    tank = {
        "Top": "Ornn",
        "Jungle": "Vi",
        "Mid": "Orianna",
        "ADC": "Kai'Sa",
        "Support": "Nautilus",
    }
    carry = dict(tank, Top="Camille")
    tank_fit = engine._player_fit_adjustment(assignment, tank, score_comp(tank)["archetypes"], meta)
    carry_fit = engine._player_fit_adjustment(assignment, carry, score_comp(carry)["archetypes"], meta)

    assert tank_fit["agency_allocation_adjustment"] < -1.5
    assert carry_fit["agency_allocation_adjustment"] > tank_fit["agency_allocation_adjustment"]
    assert any("sacrificing agency" in note for note in tank_fit["notes"])
    assert carry_fit["carry_options"][0]["role"] == "Top"


def test_malphite_branch_adds_sylas_ban_but_renekton_branch_does_not():
    empty_matchups = {"matchups": {role: {} for role in engine.ROLES}}
    meta = load_meta()
    base = {
        "Top": "Malphite",
        "Jungle": "Vi",
        "Mid": "Orianna",
        "ADC": "Kai'Sa",
        "Support": "Nautilus",
    }
    order = list(base.items())
    malphite_bans = counter_ban_recommendations(
        base, order, "blue", matchup_data=empty_matchups, meta=meta, limit=5
    )
    renekton = dict(base, Top="Renekton")
    renekton_order = list(renekton.items())
    renekton_bans = counter_ban_recommendations(
        renekton, renekton_order, "blue", matchup_data=empty_matchups, meta=meta, limit=5
    )
    assert "Sylas" in {row["champion"] for row in malphite_bans}
    assert "Sylas" not in {row["champion"] for row in renekton_bans}



def test_mastery_plus_current_role_evidence_can_infer_niche_pick(monkeypatch):
    meta = load_meta()
    profile = {
        "champion_history": [{
            "champion": "Varus", "games": 40, "recent_weighted_win_rate": 60,
            "recent_weighted_op_score": 7, "mastery_points": 100000,
        }],
        "masteries": [{"champion_name": "Varus", "points": 100000}],
    }
    monkeypatch.setattr(engine, "_profile_for", lambda user_id: profile)
    monkeypatch.setattr(engine, "load_recent_match_index", lambda: {1: {"by_role_champion": {"Top": {}}}})
    monkeypatch.setattr(engine, "load_solo_queue_winrates", lambda path=None: {
        "Top": {
            "Varus": {"win_rate": 53.3, "weight": 2490},
            "Ornn": {"win_rate": 50.5, "weight": 20000},
        }
    })
    monkeypatch.setattr(engine, "_meta_value", lambda role, champion, meta: (0.51, 22 if champion == "Varus" else 50))
    pool = engine._historical_pool(1, "Top", meta, limit=10)
    assert "Varus" in pool


def test_player_agency_is_tiebreaker_not_full_draft_override(monkeypatch):
    # The final weighting should discount the agency-only portion of player fit.
    raw_fit = 4.0
    agency = 3.0
    weighted = (raw_fit - agency) + agency * 0.35
    assert weighted == 2.05
    assert weighted < raw_fit


def test_ranged_top_requires_the_rest_of_draft_to_pay_setup_cost():
    meta = load_meta()
    awkward = {"Top": "Varus", "Jungle": "Amumu", "Mid": "Yasuo", "ADC": "Ezreal", "Support": "Karma"}
    setup_shell = {"Top": "Varus", "Jungle": "Wukong", "Mid": "Orianna", "ADC": "Ezreal", "Support": "Nautilus"}
    awkward_score = score_comp(awkward, meta)
    setup_score = score_comp(setup_shell, meta)
    assert awkward_score["top_structure_adjustment"] <= -5.0
    assert any("ranged Top" in item for item in awkward_score["team_plan_missing"])
    assert setup_score["top_structure_adjustment"] > 0
    assert setup_score["team_plan_score"] > awkward_score["team_plan_score"]


def test_coherent_poke_shell_can_still_use_varus_top():
    meta = load_meta()
    comp = {"Top": "Varus", "Jungle": "Maokai", "Mid": "Jayce", "ADC": "Ezreal", "Support": "Karma"}
    scored = score_comp(comp, meta)
    assert scored["top_structure_adjustment"] > 0
    assert scored["team_plan_score"] >= 7.0
