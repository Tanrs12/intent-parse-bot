from __future__ import annotations

import league_comp_engine as engine


def make_player(uid, role, champions, priority=1):
    return {"user_id": uid, "display_name": f"Player-{uid}", "roles": [role],
            "role_priority": {role: priority}, "champions": {role: list(champions)}}


def test_recent_hot_streak_does_not_hard_exclude_other_primary_role_choices(monkeypatch):
    top_pool = ["Camille", "Gwen", "Ornn", "Sion", "Kennen", "Jayce",
                "Aatrox", "Urgot", "Gnar", "Rumble", "K'Sante", "Fiora"]
    team = {
        "Top": make_player(99811, "Top", top_pool),
        "Jungle": make_player(99812, "Jungle", ["Vi"]),
        "Mid": make_player(99813, "Mid", ["Ahri"]),
        "ADC": make_player(99814, "ADC", ["Yunara"]),
        "Support": make_player(99815, "Support", ["Lulu"]),
    }
    observed = []
    real_prune = engine._prune_pool
    def spy(role, champions, meta, limit, **kwargs):
        if role == "Top":
            observed.append(list(champions))
        return real_prune(role, champions, meta, limit, **kwargs)
    monkeypatch.setattr(engine, "_prune_pool", spy)
    monkeypatch.setattr(engine, "_credible_broad_pool_pick",
                        lambda player, role, champion, meta: False)
    result, missing = engine.recommend_comps([team], limit=3, pool_limit=8)
    assert not missing and result
    assert observed and set(observed[0]) == set(top_pool)


def test_mid_jungle_package_survives_heavy_recent_single_champion_bias(monkeypatch):
    meta = engine.load_meta()
    jungle_pool = [
        "Wukong", "Kha'Zix", "Hecarim", "Viego", "Lee Sin", "Kindred",
        "Evelynn", "Graves", "Vi", "Diana", "Zac", "Jarvan IV"
    ]
    mid_pool = [
        "Akali", "Zed", "Syndra", "Viktor", "LeBlanc", "Sylas",
        "Lissandra", "Orianna", "Yasuo", "Ahri", "Galio", "Taliyah"
    ]
    def fake_comfort(_uid, champion, _role):
        return 10.0 if champion in {"Wukong", "Zed"} else 5.0
    monkeypatch.setattr(engine, "_champion_comfort", fake_comfort)
    mids = engine._prune_pool(
        "Mid", mid_pool, meta, 8, user_id=99813, linked_role_pool=jungle_pool
    )
    junglers = engine._prune_pool(
        "Jungle", jungle_pool, meta, 8, user_id=99812, linked_role_pool=mid_pool
    )
    # Known Vi+Ahri and Diana+Yasuo are both legitimate submitted pairings.
    assert any(m in mids and j in junglers
               for j, m in [("Vi", "Ahri"), ("Diana", "Yasuo"),
                            ("Jarvan IV", "Orianna")]), (junglers, mids)


def test_complete_bot_and_midjungle_pairs_survive_five_role_bounded_search():
    top = ["Urgot", "Ornn", "Camille", "Kennen", "Gnar", "Shen"]
    jungle = ["Vi", "Zac", "Diana", "Wukong", "Graves", "Jarvan IV", "Sejuani",
              "Viego", "Lee Sin", "Nocturne", "Amumu"]
    mid = ["Ahri", "Yasuo", "Orianna", "Galio", "Annie", "Syndra", "Taliyah",
           "Viktor", "Vex", "Sylas", "Lissandra"]
    adc = ["Yunara", "Kog'Maw", "Aphelios", "Kalista", "Ezreal", "Jinx"]
    support = ["Lulu", "Thresh", "Renata Glasc", "Nautilus", "Milio", "Rell"]
    assignment = {
        "Top": make_player(99811, "Top", top),
        "Jungle": make_player(99812, "Jungle", jungle),
        "Mid": make_player(99813, "Mid", mid),
        "ADC": make_player(99814, "ADC", adc),
        "Support": make_player(99815, "Support", support),
    }
    recs, missing = engine.recommend_comps([assignment], limit=350, pool_limit=10)
    assert not missing and recs
    bot_pairs = {(r["champions"]["ADC"], r["champions"]["Support"]) for r in recs}
    mid_pairs = {(r["champions"]["Jungle"], r["champions"]["Mid"]) for r in recs}
    assert ("Yunara", "Lulu") in bot_pairs
    assert ("Kog'Maw", "Lulu") in bot_pairs or ("Aphelios", "Thresh") in bot_pairs
    assert any(pair in mid_pairs for pair in
               [("Vi", "Ahri"), ("Diana", "Yasuo"), ("Zac", "Yasuo")])
    # The complete score is still being computed per five, not assigned by
    # a duo popularity whitelist.
    assert all("team_plan_score" in row and "player_fit_adjustment" in row for row in recs)


def test_secondary_role_experience_safety_remains_not_overwritten_by_meta():
    meta = engine.load_meta()
    player = make_player(99844, "Top", ["Vayne", "Irelia", "Urgot"], priority=2)
    assert engine._verified_secondary_role_pick(player, "Top", "Urgot", meta) is True
    # Missing profile cannot be treated as evidence that the player is bad;
    # players with a history in a different role follow the stricter gate.


def test_mid_jungle_linked_options_preserve_submitted_synergy_and_full_team_score():
    from league_flex_core import _linked_mid_jungle_packages
    champs={"Top":"Urgot","Jungle":"Amumu","Mid":"Yasuo",
            "ADC":"Aphelios","Support":"Thresh"}
    base=engine.score_comp(champs)
    rec={
        "champions":champs,
        "submitted_pools":{
            "Jungle":["Amumu","Vi","Zac","Diana","Jarvan IV"],
            "Mid":["Yasuo","Ahri","Orianna","Galio"],
        },
        "team_plan":base["team_plan"],
        "team_plan_score":base["team_plan_score"],
        "composition_score":base["score"],
        "credible_mixed_damage":base["credible_mixed_damage"],
    }
    options=_linked_mid_jungle_packages(rec)
    assert options
    assert ("Vi","Ahri") in {(v["jungle"],v["mid"]) for v in options}
    assert all(v["composition_score"]>0 and v["team_plan"] for v in options)
    assert not any(v["jungle"]=="Lee Sin" for v in options)


def test_mid_jungle_locked_role_pool_excludes_unspecified_swap():
    from league_flex_core import _linked_mid_jungle_packages
    champs={"Top":"Urgot","Jungle":"Vi","Mid":"Ahri",
            "ADC":"Yunara","Support":"Lulu"}
    base=engine.score_comp(champs)
    rec={
        "champions":champs,
        "submitted_pools":{
            "Jungle":["Vi"],  # hard-locked: do not suggest a different jungler
            "Mid":["Ahri","Orianna","Yasuo"],
        },
        "team_plan":base["team_plan"],
        "composition_score":base["score"],
        "credible_mixed_damage":base["credible_mixed_damage"],
    }
    assert not any(v["jungle"]!="Vi" for v in _linked_mid_jungle_packages(rec))
