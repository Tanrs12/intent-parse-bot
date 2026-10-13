from cogs.league_flex import _conditional_ban_branches
from league_comp_engine import load_meta


def test_alternate_top_pick_recalculates_protection_bans():
    rec = {
        "champions": {
            "Top": "Malphite",
            "Jungle": "Vi",
            "Mid": "Orianna",
            "ADC": "Kai'Sa",
            "Support": "Nautilus",
        },
        "role_alternatives": {"Top": ["Renekton"]},
        "learn_suggestions": {},
    }
    branches = _conditional_ban_branches(rec, load_meta(), "blue", set())
    assert len(branches) == 1
    branch = branches[0]
    assert branch["champion"] == "Renekton"
    assert "Sylas" in branch["removed"]
    assert "Sylas" not in branch["bans"]
