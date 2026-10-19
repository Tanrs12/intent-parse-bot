from __future__ import annotations


def test_registered_bot_package_candidates_survive_large_roster_prune():
    from pathlib import Path
    import json
    import league_comp_engine as engine
    meta = engine.load_meta()
    signups = json.loads((Path(__file__).resolve().parents[1] / "data" / "live_flex_signups.json").read_text())
    adc = next(row for row in signups if "Chud" in row["display_name"])
    support = next(row for row in signups if "The Rock" in row["display_name"])
    adc_pool = adc["champions"]["ADC"]
    supp_pool = support["champions"]["Support"]
    # Do not discard a submitted viable pro partnership just because the ADC
    # has few recent matches on that champion.
    allowed = [
        champ for champ in adc_pool
        if engine._credible_broad_pool_pick(adc, "ADC", champ, meta)
        or any(engine._bot_lane_duo_score(champ, sup, meta)["score"] >= 8.0
               for sup in supp_pool)
    ]
    shortlist = engine._prune_pool(
        "ADC", allowed, meta, 10,
        user_id=adc["user_id"], linked_bot_pool=supp_pool)
    assert {"Aphelios", "Yunara", "Kalista"}.issubset(shortlist)


def test_yunara_lulu_is_established_pair_not_just_generic_peel():
    import league_comp_engine as engine
    meta = engine.load_meta()
    yunara = engine._bot_lane_duo_score("Yunara", "Lulu", meta)
    aphelios = engine._bot_lane_duo_score("Aphelios", "Lulu", meta)
    kalista = engine._bot_lane_duo_score("Kalista", "Renata Glasc", meta)
    assert yunara["grade"] in {"excellent", "strong"}
    assert yunara["score"] >= aphelios["score"]
    assert kalista["score"] >= 7.5


def test_discord_draft_card_preserves_linked_bot_packages():
    import json
    from pathlib import Path
    import league_comp_engine as engine
    from league_flex_core import _linked_bot_lane_packages
    meta = engine.load_meta()
    signups = json.loads((Path(__file__).resolve().parents[1] / "data" / "live_flex_signups.json").read_text())
    adc = next(row for row in signups if "Chud" in row["display_name"])
    support = next(row for row in signups if "The Rock" in row["display_name"])
    base = {"Top": "Urgot", "Jungle": "Amumu", "Mid": "Yasuo",
            "ADC": "Aphelios", "Support": "Renata Glasc"}
    scored = engine.score_comp(base, meta)
    record = {
        "champions": base,
        "submitted_pools": {
            "ADC": adc["champions"]["ADC"],
            "Support": support["champions"]["Support"],
        },
        "role_alternatives": {"Support": ["Thresh", "Lulu"]},
        "team_plan": scored["team_plan"],
        "team_plan_score": scored["team_plan_score"],
        "composition_score": scored["score"],
        "rank_score": scored["rank_score"],
        "credible_mixed_damage": scored["credible_mixed_damage"],
    }
    pairs = _linked_bot_lane_packages(record)
    actual = {(row["adc"], row["support"]) for row in pairs}
    assert {("Aphelios", "Thresh"), ("Yunara", "Lulu"),
            ("Kalista", "Renata Glasc")}.issubset(actual)
    assert next(row for row in pairs if row["adc"] == "Kalista")["plan_change"]
    assert not next(row for row in pairs if row["adc"] == "Yunara")["plan_change"]
    assert all(row["duo_score"] >= 7.4 for row in pairs)


def test_banned_or_unsubmitted_bot_picks_cannot_enter_package_options():
    from league_flex_core import _linked_bot_lane_packages
    rec = {
        "champions": {"Top": "Ornn", "Jungle": "Vi", "Mid": "Ahri",
                      "ADC": "Jinx", "Support": "Lulu"},
        "submitted_pools": {"ADC": ["Jinx"], "Support": ["Lulu"]},
        "team_plan": "scaling front-to-back",
        "composition_score": 80,
    }
    assert _linked_bot_lane_packages(rec) == []


def test_actual_five_candidate_search_retains_three_linked_bot_packages():
    import json
    from pathlib import Path
    import league_comp_engine as engine
    records=json.loads((Path(__file__).resolve().parents[1] /
                        "data/live_flex_signups.json").read_text(encoding="utf8"))
    def player(part): return next(p for p in records if part in p["display_name"])
    assignment={
        "Top":player("Cheeky"), "Jungle":player("JustKayla"),
        "Mid":player("Aristo"), "ADC":player("Chud"),
        "Support":player("The Rock"),
    }
    assert engine._verified_secondary_role_pick(assignment["ADC"],"ADC","Yunara",engine.load_meta())
    assert engine._verified_secondary_role_pick(assignment["ADC"],"ADC","Kalista",engine.load_meta())
    candidates,missing=engine.recommend_comps([assignment],limit=300,pool_limit=10)
    assert not missing
    pairs={(r["champions"]["ADC"],r["champions"]["Support"]) for r in candidates}
    assert {("Aphelios","Thresh"),("Yunara","Lulu"),
            ("Kalista","Renata Glasc")}.issubset(pairs)


def test_diversity_selection_prefers_new_real_bot_package_over_single_adc_repeat():
    from league_flex_core import _select_diverse_recommendations
    roles=("Top","Jungle","Mid","ADC","Support")
    players={r:{"user_id":i+1,"display_name":r,"roles":[r],"role_priority":{r:1}}
             for i,r in enumerate(roles)}
    base={"Top":"Urgot","Jungle":"Amumu","Mid":"Yasuo","ADC":"Aphelios","Support":"Thresh"}
    def item(adc,support,score):
        champs=dict(base,ADC=adc,Support=support)
        return {"score":score, "champions":champs,"players":players,
                "team_plan":"scaling front-to-back","team_plan_score":8.4,
                "archetypes":["front-to-back"],"credible_mixed_damage":True,
                "carry_plan":{"role":"ADC"}}
    cards=[item("Aphelios","Thresh",100),
           item("Aphelios","Lulu",99),
           item("Yunara","Lulu",98.5),
           item("Kalista","Renata Glasc",92)]
    selected=_select_diverse_recommendations(cards,limit=3)
    pairs={(r["champions"]["ADC"],r["champions"]["Support"]) for r in selected}
    assert ("Yunara","Lulu") in pairs
    assert len(pairs)>=2


def test_linked_support_pair_need_not_be_valid_one_role_swap():
    # Renata alongside Aphelios is not a good direct Support substitute, but
    # Kalista+Renata can be a valid complete package from the *submitted* pool.
    from league_flex_core import _linked_bot_lane_packages
    from pathlib import Path
    import json
    import league_comp_engine as engine
    rows=json.loads((Path(__file__).resolve().parents[1]/
                      "data/live_flex_signups.json").read_text())
    adc=next(x for x in rows if "Chud" in x["display_name"])
    sup=next(x for x in rows if "The Rock" in x["display_name"])
    champs={"Top":"Urgot","Jungle":"Amumu","Mid":"Yasuo",
            "ADC":"Aphelios","Support":"Thresh"}
    scored=engine.score_comp(champs)
    rec={
        "champions":champs,
        "submitted_pools":{"ADC":adc["champions"]["ADC"],
                           "Support":sup["champions"]["Support"]},
        "role_alternatives":{"Support":["Lulu"]},
        "team_plan":scored["team_plan"],
        "composition_score":scored["score"],
        "credible_mixed_damage":scored["credible_mixed_damage"],
    }
    assert "Renata Glasc" not in rec["role_alternatives"]["Support"]
    pairs=_linked_bot_lane_packages(rec)
    assert ("Kalista","Renata Glasc") in {(p["adc"],p["support"]) for p in pairs}
    assert ("Yunara","Lulu") in {(p["adc"],p["support"]) for p in pairs}
