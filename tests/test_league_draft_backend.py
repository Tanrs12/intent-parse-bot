from league_draft_backend import _constrain_assignments, recommend_live_drafts


def _player(uid, name, role, champion, priority=1):
    return {
        "user_id": uid,
        "display_name": name,
        "roles": [role],
        "role_priority": {role: priority},
        "champions": {role: [champion]},
    }


def test_live_backend_uses_same_core_and_exposes_pick_order():
    signups = [
        _player(139837324438536193, "Cian", "Top", "Camille"),
        _player(548212317515677697, "Kayla", "Jungle", "Zac"),
        _player(270517092703272960, "Rat", "Mid", "Yasuo"),
        _player(286592835850076162, "Ste", "ADC", "Caitlyn"),
        _player(265219035019477002, "Sasha", "Support", "Zyra"),
    ]

    result = recommend_live_drafts(signups, side="blue", limit=1)

    assert result["recommendations"]
    rec = result["recommendations"][0]
    assert rec["champions"] == {
        "Top": "Camille",
        "Jungle": "Zac",
        "Mid": "Yasuo",
        "ADC": "Caitlyn",
        "Support": "Zyra",
    }
    assert [row["slot"] for row in rec["pick_order"]] == [1, 2, 3, 4, 5]
    assert {row["role"] for row in rec["pick_order"]} == {"Top", "Jungle", "Mid", "ADC", "Support"}


def test_live_constraints_pin_player_and_locked_champion():
    assignment = {
        "Top": _player(1, "Top", "Top", "Camille"),
        "Jungle": _player(2, "Jungle", "Jungle", "Zac"),
        "Mid": _player(3, "Mid", "Mid", "Yasuo"),
        "ADC": _player(4, "ADC", "ADC", "Caitlyn"),
        "Support": _player(5, "Support", "Support", "Zyra"),
    }

    constrained = _constrain_assignments(
        [assignment],
        locked_champions={"Top": "Camille"},
        role_players={"Top": 1},
    )

    assert len(constrained) == 1
    assert constrained[0]["Top"]["champions"]["Top"] == ["Camille"]
    assert _constrain_assignments([assignment], role_players={"Top": 999}) == []
