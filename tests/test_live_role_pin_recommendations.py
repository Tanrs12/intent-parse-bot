"""Live League role ownership cannot be pruned by pre-game role ranking."""
from league_draft_backend import recommend_live_drafts
from league_comp_engine import _ranked_player_pick_allowed
import league_draft_backend as backend

CIAN = 139837324438536193


def team():
    players = [
        (101, "Top", "Ornn"),
        (102, "Jungle", "Wukong"),
        (CIAN, "Mid", "Orianna"),
        (103, "ADC", "Kog'Maw"),
        (104, "Support", "Lulu"),
    ]
    return [
        {
            "user_id": uid, "display_name": role,
            "roles": [role], "role_priority": {role: 1},
            "champions": {role: [name, "Annie"] if role == "Mid" else [name]},
        }
        for uid, role, name in players
    ]


def test_exact_live_five_must_not_depend_on_top_eight_role_assignments(monkeypatch):
    # A real lobby can assign players to a low-ranked but legal five-role
    # combination. The previous backend discarded it if the top-eight
    # generator omitted that combination and showed no advice.
    def no_candidates(*args, **kwargs):
        raise AssertionError("A fully known live lineup must bypass pregame role permutations")
    monkeypatch.setattr(backend, "generate_team_options", no_candidates)

    fixed = {"Top": 101, "Jungle": 102, "Mid": CIAN,
             "ADC": 103, "Support": 104}
    result = recommend_live_drafts(
        team(), role_players=fixed,
        locked_champions={"Jungle": "Wukong", "ADC": "Kog'Maw", "Support": "Lulu"},
        enemy_champions=["Zilean", "Yunara"], limit=10,
    )
    rows = result["recommendations"]
    assert rows
    assert all(
        all(int(row["players"][role]["user_id"]) == uid for role, uid in fixed.items())
        for row in rows
    )


def test_cian_mid_annie_yasuo_only_when_player_intentionally_locks():
    assert not _ranked_player_pick_allowed({"user_id": CIAN}, "Mid", "Annie")
    assert not _ranked_player_pick_allowed({"user_id": CIAN}, "Mid", "Yasuo")
    assert _ranked_player_pick_allowed({"user_id": CIAN}, "Mid", "Orianna")
    assert _ranked_player_pick_allowed(
        {"user_id": CIAN, "_forced_live_lock": True}, "Mid", "Annie",
    )
