"""Discord comp choice is soft until League actually locks a champion."""
import asyncio
from adaptive_draft import select_adaptive_comp
from team_sync import draft_control_payload, clean_chosen, chosen_status
from league_draft_backend import recommend_live_drafts

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
ORIGINAL = {"Top": "Camille", "Jungle": "Lee Sin", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lulu"}


def rec(mid, score, mid_edge=0, games=1500, *, plan=8.0):
    champs = dict(ORIGINAL, Mid=mid)
    return {
        "champions": champs, "score": score, "rank_score": score,
        "identity": "dive", "team_plan_score": plan,
        "bot_lane_score": 8.4, "mid_jungle_score": 8.1,
        "live_lane_edges": [{
            "role": "Mid", "enemy": "Galio", "ours": mid,
            "points": mid_edge, "games": games
        }],
    }


def test_current_matchup_data_supports_pool_legal_lane_switch():
    from league_comp_engine import live_lane_matchup_adjustment
    # Rumble Top is confirmed. Both Volibear and Mordekaiser are in Kayla's
    # submitted Top pool, and the matchup advantage is better for Mordekaiser.
    start = dict(ORIGINAL, Top="Volibear")
    alt = dict(ORIGINAL, Top="Mordekaiser")
    first = live_lane_matchup_adjustment(start, {"Top": "Rumble"})
    second = live_lane_matchup_adjustment(alt, {"Top": "Rumble"})
    assert second["edges"][0]["games"] >= 100
    assert second["edges"][0]["points"] - first["edges"][0]["points"] >= .75
    options = [
        {**rec("Ahri", 91.6), "champions": alt,
         "live_lane_edges": second["edges"]},
        {**rec("Ahri", 92.2), "champions": start,
         "live_lane_edges": first["edges"]},
    ]
    picked = select_adaptive_comp(
        options, start, enemy_roles={"Top": "Rumble"}, identity="dive")
    assert picked[0]["champions"]["Top"] == "Mordekaiser"
    assert picked[0]["champions"]["Jungle"] == start["Jungle"]


def test_no_enemies_keep_selected_comp_even_if_other_scored_higher():
    baseline, counter = rec("Ahri", 91.4, 0.0), rec("Sylas", 95.1, 2.6)
    out = select_adaptive_comp([counter, baseline], ORIGINAL, identity="dive")
    assert out[0]["champions"] == ORIGINAL
    assert out[0]["adaptive_selected"]


def test_known_enemy_mid_permits_verified_counter_from_scored_pool():
    baseline, counter = rec("Ahri", 91.4, 0.0), rec("Sylas", 95.1, 2.6)
    out = select_adaptive_comp(
        [counter, baseline], ORIGINAL, enemy_roles={"Mid": "Galio"}, identity="dive")
    assert out[0]["champions"]["Mid"] == "Sylas"
    assert out[0]["champions"]["Top"] == "Camille"
    assert "counter into Galio" in out[0]["adaptive_reasons"]["Mid"]
    status = chosen_status(
        {"champions": ORIGINAL, "label": "Draft 2"}, out[0],
        our_bans=[], enemy_bans=[], enemy_locked=["Galio"])
    assert "counter into Galio" in status["replaced"]["Mid"]["why"]


def test_unknown_enemy_role_and_unverified_sample_never_force_counter():
    baseline, counter = rec("Ahri", 91.4, 0.0), rec("Sylas", 95.1, 2.6, games=15)
    assert select_adaptive_comp([counter, baseline], ORIGINAL, enemy_roles={}, identity="dive")[0]["champions"] == ORIGINAL
    assert select_adaptive_comp([counter, baseline], ORIGINAL, enemy_roles={"Mid":"Galio"}, identity="dive")[0]["champions"] == ORIGINAL


def test_already_locked_champion_is_immutable():
    baseline, counter = rec("Ahri", 91.4, 0), rec("Sylas", 95.1, 2.6)
    out = select_adaptive_comp(
        [counter, baseline], ORIGINAL, enemy_roles={"Mid":"Galio"},
        actual_locks={"Mid":"Ahri"}, identity="dive")
    assert out[0]["champions"]["Mid"] == "Ahri"


def test_discord_selected_comp_pins_roles_but_not_unpicked_champions():
    chosen = {"players": {r: {"user_id":i+1,"display_name":r} for i,r in enumerate(ROLES)},
              "champions": ORIGINAL, "team_plan":"dive / all-in"}
    payload = draft_control_payload(chosen, label="Draft 7")
    c = payload["control"]
    assert c["chosen"]["mode"] == "adaptive"
    assert c["champion_locks"] == {}
    assert set(c["role_locks"]) == set(ROLES)
    assert clean_chosen(c["chosen"])["mode"] == "adaptive"


def test_real_live_engine_never_invents_counter_outside_player_pool():
    team = [
        {"user_id":i+1,"display_name":role,"roles":[role],
         "role_priority":{role:1},
         "champions":{role: ([champ, "Sylas"] if role == "Mid" else [champ])}}
        for i,(role,champ) in enumerate(ORIGINAL.items())
    ]
    roles = {role:i+1 for i,role in enumerate(ROLES)}
    out = recommend_live_drafts(team,limit=5,role_players=roles,
        preferred_champions=ORIGINAL,chosen_champions=ORIGINAL,
        enemy_roles={"Mid":"Galio"},enemy_champions=["Galio"],
        identity="dive")
    assert out["recommendations"]
    for row in out["recommendations"]:
        assert all(row["champions"][role] in team[i]["champions"][role]
                   for i,role in enumerate(ROLES))
        assert all(row["players"][role]["user_id"]==roles[role] for role in ROLES)
