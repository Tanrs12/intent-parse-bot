"""Ten pre-game draft cards give both P1 midlaners meaningful games."""
from collections import Counter

from league_flex_core import ROLES, _select_diverse_recommendations

RAT = 270517092703272960
STE = 286592835850076162
CIAN = 139837324438536193
JACK = 332490596004659201
JEZZ = 400745307467415553


def _row(i, mid_owner, *, score):
    # All candidate players have submitted their assigned roles and champions.
    if mid_owner == STE:
        ids = (RAT, CIAN, STE, JACK, JEZZ)
    elif mid_owner == RAT:
        ids = (JACK, CIAN, RAT, STE, JEZZ)
    else:
        ids = (JACK, RAT, CIAN, STE, JEZZ)
    return {
        "score": score, "team_plan_score": 8.0,
        "bot_lane_score": 8.1, "mid_jungle_score": 7.8,
        "credible_mixed_damage": True,
        "champions": {role: f"{role}-Legal-{i}" for role in ROLES},
        "players": {
            role: {
                "user_id": ids[k], "roles": [role],
                "role_priority": {role: 1 if role == "Mid" else 2},
            }
            for k, role in enumerate(ROLES)
        },
        "carry_plan": {"role": "Mid"},
        "team_plan": "front-to-back", "archetypes": ["teamfight"],
    }


def test_rat_and_ste_each_receive_three_or_four_mids_when_viable():
    pool = (
        [_row(i, STE, score=100 - i * .22) for i in range(20)]
        + [_row(20 + i, RAT, score=95 - i * .13) for i in range(12)]
        + [_row(32 + i, CIAN, score=96 - i * .16) for i in range(12)]
    )
    pool.sort(key=lambda x: x["score"], reverse=True)
    for token in (None, "session-a", "session-b"):
        chosen = _select_diverse_recommendations(
            pool, limit=10, core_limit=4, role_mix_slots=6,
            rotation_token=token,
        )
        counts = Counter(int(r["players"]["Mid"]["user_id"]) for r in chosen)
        assert len(chosen) == 10
        assert 3 <= counts[RAT] <= 4, (token, counts)
        assert 3 <= counts[STE] <= 4, (token, counts)
        assert 2 <= counts[CIAN] <= 4, (token, counts)
        assert len({tuple(r["champions"][role] for role in ROLES)
                    for r in chosen}) == 10
        assert all(r["bot_lane_score"] >= 7 and r["mid_jungle_score"] >= 7
                   for r in chosen)


def test_mid_quota_never_forces_bad_team_or_missing_options():
    pool = [_row(i, STE, score=100 - i * .2) for i in range(15)]
    pool.append(_row(90, RAT, score=75))  # outside both quality and flex windows
    pool.append(_row(91, RAT, score=94))
    pool[-1]["bot_lane_score"] = 5.0
    chosen = _select_diverse_recommendations(
        pool, limit=10, core_limit=4, role_mix_slots=6,
    )
    assert len(chosen) == 10
    assert all(int(r["players"]["Mid"]["user_id"]) != RAT for r in chosen)
