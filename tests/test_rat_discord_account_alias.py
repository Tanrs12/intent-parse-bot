"""Ratsuo's new Discord login keeps his actual Mid priority and historic form."""
from collections import Counter

from league_player_identity import (
    RAT_CURRENT_DISCORD_ID,
    RAT_HISTORY_DISCORD_ID,
    canonical_player_id,
    is_rat,
)
from league_comp_engine import (
    _profile_for,
    _role_search_pool_limit,
    load_player_score_index,
    load_recent_match_index,
    role_experience_score,
)
from league_flex_core import (
    ROLES,
    _role_priority_level,
    _select_diverse_recommendations,
    generate_team_options,
)

DYL = 991234500000000001
CIAN = 139837324438536193
JACK = 332490596004659201
JEZZ = 400745307467415553
STE = 286592835850076162


def row(i, mid_id, score=97, *, good=True):
    rat_mid = mid_id in (RAT_CURRENT_DISCORD_ID, RAT_HISTORY_DISCORD_ID)
    if rat_mid:
        ids = (DYL, CIAN, mid_id, JACK, JEZZ)
    elif mid_id == STE:
        ids = (RAT_CURRENT_DISCORD_ID, CIAN, STE, JACK, JEZZ)
    else:
        ids = (RAT_CURRENT_DISCORD_ID, CIAN, DYL, JACK, JEZZ)
    players = {}
    for k, role in enumerate(ROLES):
        uid = ids[k]
        pref = 1 if role in ("Top", "Mid", "Support") else 2
        if uid == DYL:
            pref = 1 if role == "Top" else 2
        if is_rat(uid):
            pref = 1 if role == "Mid" else 2
        players[role] = {
            "user_id": uid, "roles": [role],
            "role_priority": {role: pref}
        }
    return {
        "score": score, "team_plan_score": 8,
        "bot_lane_score": 8.0 if good else 4.0,
        "mid_jungle_score": 8,
        "credible_mixed_damage": True,
        "champions": {role: f"{role}_{i}" for role in ROLES},
        "players": players, "team_plan": "skirmish",
        "carry_plan": {"role": "Mid"},
        "archetypes": ["dive"],
    }


def test_old_and_new_discord_accounts_keep_same_gameplay_identity():
    assert is_rat(RAT_HISTORY_DISCORD_ID) and is_rat(RAT_CURRENT_DISCORD_ID)
    assert canonical_player_id(RAT_CURRENT_DISCORD_ID) == RAT_HISTORY_DISCORD_ID
    assert _profile_for(RAT_CURRENT_DISCORD_ID) == _profile_for(RAT_HISTORY_DISCORD_ID)
    assert _profile_for(RAT_CURRENT_DISCORD_ID)
    assert load_player_score_index()[RAT_CURRENT_DISCORD_ID] == load_player_score_index()[RAT_HISTORY_DISCORD_ID]
    assert load_recent_match_index()[RAT_CURRENT_DISCORD_ID] == load_recent_match_index()[RAT_HISTORY_DISCORD_ID]
    assert role_experience_score(RAT_CURRENT_DISCORD_ID, "Mid") == role_experience_score(RAT_HISTORY_DISCORD_ID, "Mid")
    assert _role_search_pool_limit({"user_id": RAT_CURRENT_DISCORD_ID}, "Mid", 10) >= 16


def test_new_account_selected_mid_is_p1_even_with_old_or_missing_signup_priority():
    signup = {"user_id": RAT_CURRENT_DISCORD_ID, "roles": ["Mid", "Top", "Jungle"],
              "role_priority": {"Mid": 2, "Top": 1}}
    assert _role_priority_level(signup, "Mid") == 1
    assert _role_priority_level(signup, "Top") == 2
    assert _role_priority_level(signup, "Jungle") == 2


def test_new_discord_id_not_replaced_in_signups_or_mentions():
    signups = [
        {"user_id": RAT_CURRENT_DISCORD_ID, "display_name": "Ratsuo",
         "roles": ["Mid", "Top"], "champions": {"Mid": ["Yasuo"], "Top": ["Jax"]}},
        {"user_id": DYL, "display_name": "Dyl", "roles": ["Top", "Mid"],
         "role_priority": {"Top": 1, "Mid": 2},
         "champions": {"Top": ["Ornn"], "Mid": ["Ahri"]}},
        {"user_id": CIAN, "display_name": "Cian", "roles": ["Jungle"],
         "champions": {"Jungle": ["Lee Sin"]}},
        {"user_id": JACK, "display_name": "Jack", "roles": ["ADC"],
         "champions": {"ADC": ["Jinx"]}},
        {"user_id": JEZZ, "display_name": "Jezz", "roles": ["Support"],
         "champions": {"Support": ["Braum"]}},
    ]
    options = generate_team_options(signups, limit=12, require_pools=True)
    assert any(int(op["Mid"]["user_id"]) == RAT_CURRENT_DISCORD_ID and
               int(op["Top"]["user_id"]) == DYL for op in options)
    assert all(RAT_HISTORY_DISCORD_ID not in
               {int(p["user_id"]) for p in option.values()} for option in options)


def test_only_mid_main_rat_new_id_receives_majority_of_ten():
    pool = (
        [row(i, DYL, score=100 - i * .1) for i in range(18)]
        + [row(20+i, RAT_CURRENT_DISCORD_ID, score=96.5 - i * .15) for i in range(16)]
    )
    pool.sort(key=lambda x: x["score"], reverse=True)
    for token in ("first", "second", "third"):
        chosen = _select_diverse_recommendations(
            pool, limit=10, core_limit=4, role_mix_slots=6, rotation_token=token
        )
        counts = Counter(canonical_player_id(p["players"]["Mid"]["user_id"]) for p in chosen)
        assert len(chosen) == 10
        assert 6 <= counts[RAT_HISTORY_DISCORD_ID] <= 8, (token, counts)
        assert all(chosen[i]["players"]["Mid"]["user_id"] == RAT_CURRENT_DISCORD_ID
                   for i in range(min(1, len(chosen))))
        assert len({tuple(p["champions"][r] for r in ROLES) for p in chosen}) == 10


def test_both_mid_mains_new_rat_account_still_shares_with_ste():
    # The signed-in teams need five distinct players, so sample rows are
    # candidate portfolio records rather than copying Riot champion pools.
    pool = (
        [row(i, STE, score=100-i*.2) for i in range(15)]
        + [row(20+i, RAT_CURRENT_DISCORD_ID, score=96-i*.2) for i in range(15)]
    )
    pool.sort(key=lambda x: x["score"], reverse=True)
    chosen = _select_diverse_recommendations(pool, limit=10, core_limit=4, role_mix_slots=6)
    counts = Counter(canonical_player_id(p["players"]["Mid"]["user_id"]) for p in chosen)
    assert 3 <= counts[RAT_HISTORY_DISCORD_ID] <= 4, counts
    assert counts[STE] >= 3, counts  # no third submitted Mid owner in this pool
