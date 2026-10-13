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



def test_conditional_ban_annotations_put_replacement_beside_primary():
    from cogs.league_flex import _conditional_ban_annotations
    base = [{"champion": "Syndra"}, {"champion": "Rumble"}]
    branches = [{
        "role": "Mid", "champion": "Yasuo",
        "added": ["Viktor"], "removed": ["Syndra"],
    }]
    notes = _conditional_ban_annotations(base, branches)
    assert notes["Syndra"] == [{"condition": "Yasuo", "ban": "Viktor"}]


def test_recommendation_role_guard_rejects_unselected_role():
    from cogs.league_flex import _recommendation_respects_selected_roles
    rec = {"players": {}}
    for role in ("Top", "Jungle", "Mid", "ADC", "Support"):
        rec["players"][role] = {"user_id": role, "display_name": role, "roles": [role]}
    assert _recommendation_respects_selected_roles(rec)
    rec["players"]["ADC"]["roles"] = ["Top", "Mid"]
    assert not _recommendation_respects_selected_roles(rec)
