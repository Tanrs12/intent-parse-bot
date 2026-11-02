"""Cian Jungle and Rat Mid is not an optional novelty swap."""
import pytest

from league_flex_core import generate_team_options
from league_player_identity import RAT_HISTORY_DISCORD_ID, RAT_CURRENT_DISCORD_ID

CIAN = 139837324438536193


def player(uid, name, roles, priority):
    return {
        "user_id": uid, "display_name": name, "roles": roles,
        "role_priority": priority,
        "champions": {role: ["Orianna" if role == "Mid" else "Lee Sin" if role == "Jungle"
                            else "Malphite" if role == "Top" else "Jinx" if role == "ADC"
                            else "Braum"] for role in roles},
    }


@pytest.mark.parametrize("rat_id", [RAT_HISTORY_DISCORD_ID, RAT_CURRENT_DISCORD_ID])
def test_never_swap_two_mains_when_legal_original_pair_exists(rat_id):
    team = [
        player(CIAN, "Cian", ["Jungle", "Mid"], {"Jungle": 1, "Mid": 3}),
        player(rat_id, "Rat", ["Mid", "Jungle"], {"Mid": 1, "Jungle": 3}),
        player(101, "Top", ["Top"], {"Top": 1}),
        player(102, "ADC", ["ADC"], {"ADC": 1}),
        player(103, "Support", ["Support"], {"Support": 1}),
    ]
    layouts = generate_team_options(team, limit=20, require_pools=True)
    assert layouts
    assert any(row["Jungle"]["user_id"] == CIAN and row["Mid"]["user_id"] == rat_id
               for row in layouts)
    assert not any(row["Jungle"]["user_id"] == rat_id and row["Mid"]["user_id"] == CIAN
                   for row in layouts)


def test_only_legally_possible_role_pair_is_preserved():
    team = [
        player(CIAN, "Cian", ["Mid"], {"Mid": 1}),
        player(RAT_HISTORY_DISCORD_ID, "Rat", ["Jungle"], {"Jungle": 1}),
        player(101, "Top", ["Top"], {"Top": 1}),
        player(102, "ADC", ["ADC"], {"ADC": 1}),
        player(103, "Support", ["Support"], {"Support": 1}),
    ]
    layouts = generate_team_options(team, limit=10, require_pools=True)
    assert len(layouts) == 1
    assert layouts[0]["Mid"]["user_id"] == CIAN
    assert layouts[0]["Jungle"]["user_id"] == RAT_HISTORY_DISCORD_ID
