"""Portable bot-package regression tests; no private local signup cache required."""
from __future__ import annotations

import league_comp_engine as engine
from league_flex_core import _linked_bot_lane_packages, _select_diverse_recommendations

CIAN = 139837324438536193

ADC_POOL = [
    "Aphelios", "Yunara", "Kalista", "Ezreal", "Ashe", "Jinx",
    "Caitlyn", "Kai'Sa", "Lucian", "Xayah", "Smolder", "Sivir",
    "Varus", "Zeri",
]
SUPPORT_POOL = ["Renata Glasc", "Thresh", "Lulu", "Rell", "Seraphine", "Alistar"]


def _base():
    return {"Top": "Urgot", "Jungle": "Amumu", "Mid": "Yasuo",
            "ADC": "Aphelios", "Support": "Renata Glasc"}


def _player(uid, name, role, pool, priority=1):
    return {"user_id": uid, "display_name": name, "roles": [role],
            "role_priority": {role: priority},
            "champions": {role: list(pool)}}


def _rec(champions=None, support_alternatives=("Thresh", "Lulu")):
    champions = dict(champions or _base())
    score = engine.score_comp(champions)
    return {
        "champions": champions,
        "submitted_pools": {"ADC": ADC_POOL, "Support": SUPPORT_POOL},
        "role_alternatives": {"Support": list(support_alternatives)},
        "team_plan": score["team_plan"],
        "team_plan_score": score["team_plan_score"],
        "composition_score": score["score"],
        "rank_score": score["rank_score"],
        "credible_mixed_damage": score["credible_mixed_damage"],
    }


def test_confirmed_flex_adc_and_registered_packages_survive_shortlist():
    meta = engine.load_meta()
    cian = _player(CIAN, "Cian", "ADC", ADC_POOL, priority=3)
    for champion in ("Yunara", "Kalista"):
        assert engine._verified_secondary_role_pick(cian, "ADC", champion, meta)
    credible = [
        champ for champ in ADC_POOL
        if engine._credible_broad_pool_pick(cian, "ADC", champ, meta)
        or any(engine._bot_lane_duo_score(champ, sup, meta)["score"] >= 8
               for sup in SUPPORT_POOL)
    ]
    pruned = engine._prune_pool(
        "ADC", credible, meta, 10, user_id=CIAN,
        linked_bot_pool=SUPPORT_POOL,
    )
    assert {"Aphelios", "Yunara", "Kalista"}.issubset(pruned)


def test_yunara_lulu_and_kalista_renata_are_established_packages():
    meta = engine.load_meta()
    yunara = engine._bot_lane_duo_score("Yunara", "Lulu", meta)
    aphelios = engine._bot_lane_duo_score("Aphelios", "Lulu", meta)
    kalista = engine._bot_lane_duo_score("Kalista", "Renata Glasc", meta)
    assert yunara["grade"] in {"excellent", "strong"}
    assert yunara["score"] >= aphelios["score"]
    assert kalista["score"] >= 7.5


def test_draft_four_exposes_complete_linked_pairs_and_plan_changes():
    packages = _linked_bot_lane_packages(_rec())
    names = {(row["adc"], row["support"]) for row in packages}
    assert {("Aphelios", "Thresh"), ("Yunara", "Lulu"),
            ("Kalista", "Renata Glasc")}.issubset(names)
    assert next(row for row in packages if row["adc"] == "Kalista")["plan_change"]
    assert not next(row for row in packages if row["adc"] == "Yunara")["plan_change"]


def test_real_role_logic_retains_named_pairs_without_ignored_signups_file():
    assignments = [{
        "Top": _player(987001, "Top", "Top", ["Urgot"]),
        "Jungle": _player(987002, "Jungle", "Jungle", ["Amumu"]),
        "Mid": _player(987003, "Mid", "Mid", ["Yasuo"]),
        "ADC": _player(CIAN, "Cian", "ADC", ADC_POOL, priority=3),
        "Support": _player(987004, "Support", "Support", SUPPORT_POOL),
    }]
    recs, missing = engine.recommend_comps(assignments, limit=300, pool_limit=8)
    assert not missing
    pairs = {(r["champions"]["ADC"], r["champions"]["Support"]) for r in recs}
    assert {("Aphelios", "Thresh"), ("Yunara", "Lulu"),
            ("Kalista", "Renata Glasc")}.issubset(pairs)


def test_unavailable_or_unsubmitted_picks_cannot_enter_package_options():
    rec = {
        "champions": {"Top": "Ornn", "Jungle": "Vi", "Mid": "Ahri",
                      "ADC": "Jinx", "Support": "Lulu"},
        "submitted_pools": {"ADC": ["Jinx"], "Support": ["Lulu"]},
        "team_plan": "scaling front-to-back",
        "composition_score": 80,
    }
    assert _linked_bot_lane_packages(rec) == []


def test_portfolio_values_new_bot_lane_unit():
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    players = {r: {"user_id": i+1, "display_name": r, "roles": [r],
                   "role_priority": {r: 1}} for i, r in enumerate(roles)}
    def rec(adc, support, score):
        return {
            "score": score,
            "champions": dict(_base(), ADC=adc, Support=support),
            "players": players,
            "team_plan": "scaling front-to-back",
            "team_plan_score": 8.4,
            "archetypes": ["front-to-back"],
            "credible_mixed_damage": True,
            "carry_plan": {"role": "ADC"},
        }
    selected = _select_diverse_recommendations([
        rec("Aphelios", "Thresh", 100),
        rec("Aphelios", "Lulu", 99),
        rec("Yunara", "Lulu", 98.5),
        rec("Kalista", "Renata Glasc", 92),
    ], limit=3)
    pairs = {(r["champions"]["ADC"], r["champions"]["Support"]) for r in selected}
    assert ("Yunara", "Lulu") in pairs
    assert len(pairs) >= 2


def test_linked_partner_not_required_to_be_standalone_swap():
    # Renata may be poor beside Aphelios but right beside Kalista.
    rec = _rec(
        {"Top": "Urgot", "Jungle": "Amumu", "Mid": "Yasuo",
         "ADC": "Aphelios", "Support": "Thresh"},
        support_alternatives=("Lulu",),
    )
    assert "Renata Glasc" not in rec["role_alternatives"]["Support"]
    packages = _linked_bot_lane_packages(rec)
    pairs = {(p["adc"], p["support"]) for p in packages}
    assert ("Kalista", "Renata Glasc") in pairs
    assert ("Yunara", "Lulu") in pairs
