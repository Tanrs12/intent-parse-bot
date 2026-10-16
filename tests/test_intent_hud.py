from draftcoach.hud_intent import decide


def test_human_hover_outranks_visual_preview_and_generated_pick():
    snapshot = {
        "status": {"connection": "connected"},
        "state": {
            "source": "lcu",
            "phase": "BAN_PICK",
            "active": {"kind": "pick", "ours": True},
            "our_team": [
                {"role": "Top", "locked": None},
                {"role": "Jungle", "locked": None},
                {"role": "Mid", "locked": None},
                {"role": "ADC", "locked": None},
                {"role": "Support", "locked": None},
            ],
            "enemy_team": [],
        },
        "guidance": {
            "visual_test": True,
            "visual_test_pick": {"role": "ADC", "champion": "Kog'Maw", "champion_id": 96},
            "picks": [{"role": "Mid", "champion": "Orianna", "champion_id": 61}],
            "bans": [],
        },
        "local": {
            "role": "Mid",
            "picking_role": "Mid",
            "can_act": True,
            "suggestion": {
                "kind": "pick",
                "role": "Mid",
                "champion": "Sylas",
                "champion_id": 517,
                "source": "human-hover",
            },
        },
    }
    decision, _ = decide(snapshot)
    assert decision["title"] == "Sylas"
    assert decision["meta"] == "YOUR HOVER · MID"
    assert decision["turn"] == "local"
    assert decision["hover_override"] is True


def test_local_hover_is_visible_even_before_our_turn():
    snapshot = {
        "status": {"connection": "connected"},
        "state": {
            "source": "lcu", "phase": "BAN_PICK",
            "active": {"kind": "pick", "ours": False},
            "our_team": [{"role": role, "locked": None} for role in ["Top", "Jungle", "Mid", "ADC", "Support"]],
            "enemy_team": [],
        },
        "guidance": {
            "picks": [{"role": "Mid", "champion": "Orianna", "champion_id": 61}],
            "bans": [],
            "our_hovers": {"Mid": "Sylas"},
            "our_hover_ids": {"Mid": 517},
        },
        "local": {"role": "Mid", "hover": "Sylas", "can_act": False, "suggestion": None},
    }
    decision, _ = decide(snapshot)
    assert decision["title"] == "Sylas"
    assert decision["meta"] == "YOUR HOVER · MID"
    assert decision["turn"] == ""
