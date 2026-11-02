"""Live captain Jungle pool must survive both the desktop and engine cutoffs."""
from types import SimpleNamespace

from draftcoach.advisor import _pool_for
from league_comp_engine import _prune_pool, load_meta
from league_flex_core import _select_diverse_recommendations, ROLES

CIAN = 139837324438536193
FOCUS = ("Lee Sin", "Qiyana", "Elise", "Taliyah", "Talon")


def test_desktop_passes_captain_full_submitted_jungle_pool_to_engine():
    pool = [f"Candidate {i}" for i in range(38)] + list(FOCUS)
    slot = SimpleNamespace(
        user_id=CIAN, avoid=[], priorities={}, pool={"Jungle": pool},
        pool_source="player",
    )
    result, source = _pool_for(slot, "Jungle", set(), load_meta(), 8, None, [])
    assert result == pool
    assert source == "player"
    slot.user_id = 10000001
    other, _ = _pool_for(slot, "Jungle", set(), load_meta(), 8, None, [])
    assert len(other) == 8


def test_engine_retains_five_requested_jungle_picks_after_18_slot_cutoff():
    champions = [f"Unrelated {i}" for i in range(40)] + list(FOCUS)
    result = _prune_pool(
        "Jungle", champions, load_meta(), limit=18, user_id=CIAN,
        rotation_token="ranked-example",
    )
    assert len(result) == 18
    assert all(champ in result for champ in FOCUS)


def _rec(i, champ, score):
    champs = {
        "Top": f"Top{i}", "Jungle": champ, "Mid": f"Mid{i}",
        "ADC": f"ADC{i}", "Support": f"Support{i}",
    }
    return {
        "score": score, "rank_score": score, "champions": champs,
        "players": {role: {"user_id": CIAN if role == "Jungle" else 100 + k,
                            "roles": [role], "display_name": role,
                            "role_priority": {role: 1}} for k, role in enumerate(ROLES)},
        "team_plan_score": 8.2, "mid_jungle_score": 7.3,
        "bot_lane_score": 8.1, "credible_mixed_damage": True,
        "team_plan": "tempo skirmish", "archetypes": ["skirmish"],
        "carry_plan": {"role": "Jungle"},
    }


def test_competitive_carry_gallery_prioritizes_lee_and_varies_requested_junglers():
    pool = [_rec(i, "Vi", 100 - 0.15 * i) for i in range(25)]
    pool += [_rec(30, "Lee Sin", 99.5), _rec(31, "Qiyana", 97.4),
             _rec(32, "Elise", 98.5), _rec(33, "Taliyah", 97.6),
             _rec(34, "Talon", 93.3)]
    pool.sort(key=lambda row: row["score"], reverse=True)
    selected = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6, rotation_token="session-1",
    )
    champs = [row["champions"]["Jungle"] for row in selected]
    assert len(selected) == 10
    assert champs[0] == "Lee Sin"
    assert set(FOCUS).issubset(champs)
    assert len({tuple(row["champions"][r] for r in ROLES) for row in selected}) == 10
