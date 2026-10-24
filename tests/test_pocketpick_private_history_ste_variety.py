"""Historical PocketPick aggregates are bounded priors; Ste's confirmed mids get coverage."""
import json
from pathlib import Path

import league_pocketpick_history as pocket
import league_comp_engine as engine
from league_flex_core import _select_diverse_recommendations

STE = 286592835850076162


def test_historical_pocketpick_retains_all_rows_but_never_scores_thin_samples(monkeypatch, tmp_path):
    root = tmp_path / "pocketpick"
    root.mkdir()
    metrics = {
        "patches": {"16.16": 30000}, "matches": 30000, "queue": 420, "region": "euw",
        "metrics": {"Gwen": {"TOP": {"games": 850, "wr": 0.50}}},
        "matchups": {
            "Gwen|TOP|Shen": {"n": 18, "wr": 0.62, "gd15": 550},
            "Gwen|TOP|Darius": {"n": 175, "wr": 0.59, "gd15": 400},
        },
    }
    gold = {"patch": {"16.16": 30000}, "matches": 30000,
            "data": {}, "pairRes": {"Gwen": {}}, "kitM": {"Gwen": {}}}
    (root / "matchv5-metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (root / "gd15-matchups.json").write_text(json.dumps(gold), encoding="utf-8")
    monkeypatch.setenv("DRAFTCOACH_POCKETPICK_DIR", str(root))
    shen = pocket.historical_matchup("Top", "Gwen", "Shen")
    assert shen["games"] == 18
    assert shen["draft_points"] == 0
    assert not shen["usable_for_ranking"]
    darius = pocket.historical_matchup("Top", "Gwen", "Darius")
    assert darius["draft_points"] > 0
    assert darius["confidence"] == "low / historical"
    assert darius["rank"] == "Silver-Platinum"
    assert 0 < darius["draft_points"] < 0.65
    scored = engine.live_lane_matchup_adjustment(
        {"Top": "Gwen"}, {"Top": "Darius"}, matchup_data={"matchups": {}},
        role_weights={"Top": 2.8},
    )
    assert scored["adjustment"] > 0
    assert scored["edges"][0]["historical"] is True
    assert scored["edges"][0]["source_patch"] == "16.16"
    assert scored["edges"][0]["source_tier"] == "Silver-Platinum"
    assert scored["edges"][0]["counterpick_weight"] == 2.8
    missing = engine.live_lane_matchup_adjustment(
        {"Top": "Camille"}, {"Top": "Darius"}, matchup_data={"matchups": {}},
    )
    assert missing == {"adjustment": 0.0, "edges": []}


def _sample(mid, index, score):
    champs = {
        "Top": ["Ornn", "Camille", "Gwen"][index % 3],
        "Jungle": ["Lee Sin", "Zac", "Vi"][index % 3],
        "Mid": mid,
        "ADC": ["Jinx", "Kalista", "Kai'Sa", "Yunara"][index % 4],
        "Support": ["Lulu", "Thresh", "Nautilus", "Rell"][index % 4],
    }
    players = {
        role: {"user_id": STE if role == "Mid" else 300 + i,
               "roles": [role], "role_priority": {role: 1}}
        for i, role in enumerate(("Top", "Jungle", "Mid", "ADC", "Support"))
    }
    return {
        "score": score, "team_plan_score": 8, "bot_lane_score": 8.1,
        "mid_jungle_score": 7.8, "team_plan": "dive / all-in",
        "credible_mixed_damage": True, "champions": champs,
        "players": players, "archetypes": ["dive", str(index % 3)],
        "carry_plan": {"role": "Mid"},
    }


def test_ste_confirmed_akali_and_vex_appear_among_ten_if_strong_and_legal():
    pool = [_sample("Orianna" if i % 2 else "Galio", i, 99.0 - i * 0.4)
            for i in range(15)]
    pool.extend([_sample("Akali", 16, 94.0), _sample("Vex", 17, 93.7)])
    pool.sort(key=lambda x: x["score"], reverse=True)
    chosen = _select_diverse_recommendations(pool, limit=10, rotation_token="one-game")
    mids = [row["champions"]["Mid"] for row in chosen]
    assert len(chosen) == 10
    assert "Akali" in mids and "Vex" in mids
    assert min(x["team_plan_score"] for x in chosen) >= 7


def test_never_force_weak_ste_akali_into_ten():
    pool = [_sample("Orianna" if i % 2 else "Galio", i, 99.0 - i * 0.4)
            for i in range(15)]
    pool += [_sample("Akali", 16, 60.0)]
    pool.sort(key=lambda x: x["score"], reverse=True)
    chosen = _select_diverse_recommendations(pool, limit=10, rotation_token="one-game")
    assert "Akali" not in [row["champions"]["Mid"] for row in chosen]
