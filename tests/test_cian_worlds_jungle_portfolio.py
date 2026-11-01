from collections import Counter

from league_comp_engine import (
    CIAN_JUNGLE_AGENCY_FLOORS, _player_jungle_agency_adjustment, load_meta
)
from league_flex_core import ROLES, _CURATED_ROLE_CHAMPIONS, _select_diverse_recommendations

CIAN = 139837324438536193


def test_qiyana_is_a_permanent_jungle_signup_option():
    assert "Qiyana" in _CURATED_ROLE_CHAMPIONS["Jungle"]


def test_cian_qiyana_and_lee_have_playmaker_agency_if_submitted():
    player = {
        "user_id": CIAN,
        "champions": {"Jungle": ["Qiyana", "Lee Sin", "Sejuani"]},
    }
    meta = load_meta()
    results = {
        champion: _player_jungle_agency_adjustment(player, "Jungle", champion, meta)[0]
        for champion in player["champions"]["Jungle"]
    }
    assert CIAN_JUNGLE_AGENCY_FLOORS["Qiyana"] >= .75
    assert results["Qiyana"] > results["Sejuani"] + 2
    assert results["Lee Sin"] > results["Sejuani"] + 2


def test_ten_drafts_give_cian_real_jungle_carries_when_viable():
    people = dict(zip(ROLES, (10, CIAN, 11, 12, 13)))
    candidates = []
    for i in range(13):
        jungle = (
            "Lee Sin" if i == 11 else
            "Qiyana" if i == 12 else "Sejuani"
        )
        candidates.append({
            "players": {
                role: {"user_id": people[role], "roles": [role],
                       "role_priority": {role: 1}}
                for role in ROLES
            },
            "champions": dict(zip(ROLES, (
                ["Ornn", "Camille", "Shen"][i % 3],
                jungle,
                "Ahri" if i % 2 else "Orianna",
                f"ADC-{i}", f"Support-{i}",
            ))),
            "score": 100 - i * .25,
            "team_plan_score": 8.2,
            "bot_lane_score": 8.0,
            "mid_jungle_score": 8.1,
            "team_plan": ["dive / all-in", "pick / control", "scaling front-to-back"][i % 3],
            "carry_plan": {"role": "Jungle"},
            "credible_mixed_damage": True,
            "archetypes": ["engage"],
        })
    chosen = _select_diverse_recommendations(candidates, limit=10, core_limit=4, role_mix_slots=6)
    assert len(chosen) == 10
    playmakers = Counter(row["champions"]["Jungle"] for row in chosen)
    assert playmakers["Lee Sin"] >= 1, playmakers
    assert playmakers["Qiyana"] >= 1, playmakers
    assert all(row["bot_lane_score"] >= 7 and row["mid_jungle_score"] >= 7 for row in chosen)
    assert len({tuple(row["champions"][role] for role in ROLES) for row in chosen}) == 10
