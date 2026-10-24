"""Late-pick solo lane counters must be exhaustive and evidence-weighted."""
import league_comp_engine as engine
from league_draft_backend import _counterpick_window


def test_red_last_pick_and_blue_second_rotation_are_correct():
    four_locked = {"Jungle": "Vi", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lulu"}
    assert _counterpick_window("red", four_locked, {"Top": "Shen"}) == {"Top": 2.8}
    assert _counterpick_window("blue", four_locked, {"Top": "Shen"}) == {}
    three_locked = {"Jungle": "Vi", "ADC": "Jinx", "Support": "Lulu"}
    assert _counterpick_window("blue", three_locked, {"Top": "Shen", "Mid": "Orianna"}) == {
        "Top": 1.85, "Mid": 1.6,
    }
    assert _counterpick_window("red", three_locked, {"Top": "Shen"}) == {}
    assert _counterpick_window("red", four_locked, {"Mid": "Orianna"}) == {}


def test_known_matchups_have_higher_impact_when_countering_r5():
    snap = {
        "source": {"patches_detected": ["26.21"]},
        "matchups": {"Top": {"Shen": {"responses": {
            "Camille": {"response_advantage": -3.5, "games": 1500},
            "Gwen": {"response_advantage": 3.5, "games": 1500},
        }}}},
    }
    camille = engine.live_lane_matchup_adjustment(
        {"Top": "Camille"}, {"Top": "Shen"}, snap,
    )
    gwen = engine.live_lane_matchup_adjustment(
        {"Top": "Gwen"}, {"Top": "Shen"}, snap,
    )
    camille_r5 = engine.live_lane_matchup_adjustment(
        {"Top": "Camille"}, {"Top": "Shen"}, snap, role_weights={"Top": 2.8},
    )
    gwen_r5 = engine.live_lane_matchup_adjustment(
        {"Top": "Gwen"}, {"Top": "Shen"}, snap, role_weights={"Top": 2.8},
    )
    assert gwen_r5["adjustment"] > gwen["adjustment"] > camille["adjustment"] > camille_r5["adjustment"]
    assert gwen_r5["edges"][0]["counterpick_weight"] == 2.8
    assert engine.live_lane_matchup_adjustment(
        {"Top": "Camille"}, {"Top": "Darius"}, snap, role_weights={"Top": 2.8},
    ) == {"adjustment": 0.0, "edges": []}


def test_late_top_pool_skips_truncation(monkeypatch):
    champions = [
        "Camille", "Jax", "Gwen", "Pantheon", "Kayle", "Sett", "Olaf", "Gnar",
        "Fiora", "Riven", "Irelia", "Kennen", "Kled", "Yorick", "Gangplank",
        "Aatrox", "Renekton", "Urgot", "Darius", "Sion", "Ornn", "Malphite",
    ]
    assignment = {
        role: {
            "user_id": i + 80000,
            "display_name": role,
            "roles": [role],
            "role_priority": {role: 1},
            "champions": {role: picks},
        } for i, (role, picks) in enumerate((
            ("Top", champions), ("Jungle", ["Vi"]), ("Mid", ["Ahri"]),
            ("ADC", ["Jinx"]), ("Support", ["Lulu"]),
        ))
    }
    seen = []
    def fake_bounded(pools, *_args, **_kwargs):
        seen.append(dict(pools))
        return []
    monkeypatch.setattr(engine, "_bounded_package_products", fake_bounded)
    rows, missing = engine.recommend_comps(
        [assignment], meta=engine.load_meta(), limit=20, pool_limit=10,
        live_enemy_roles={"Top": "Shen"}, full_counter_roles=frozenset({"Top"}),
        lane_leverage={"Top": 2.8},
    )
    assert not rows and not missing
    assert len(seen) == 1
    assert set(seen[0]["Top"]) == set(champions)
