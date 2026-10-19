from league_draft_backend import _constrain_assignments, _pick_group_meta, recommend_live_drafts


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


def test_live_constraint_accepts_off_pool_lock_as_hard_fact():
    assignment = {
        "Top": _player(1, "Top", "Top", "Camille"),
        "Jungle": _player(2, "Jungle", "Jungle", "Zac"),
        "Mid": _player(3, "Mid", "Mid", "Yasuo"),
        "ADC": _player(4, "ADC", "ADC", "Caitlyn"),
        "Support": _player(5, "Support", "Support", "Zyra"),
    }
    constrained = _constrain_assignments(
        [assignment], locked_champions={"ADC": "KogMaw"}, role_players={"ADC": 4}
    )
    assert len(constrained) == 1
    assert constrained[0]["ADC"]["champions"]["ADC"] == ["KogMaw"]
    assert constrained[0]["ADC"]["_forced_live_lock"] is True


def test_pick_groups_model_simultaneous_blue_and_red_pairs():
    assert [_pick_group_meta("blue", i)["pick_group_label"] for i in range(1, 6)] == [
        "B1", "B2/B3", "B2/B3", "B4/B5", "B4/B5"
    ]
    assert [_pick_group_meta("red", i)["pick_group_label"] for i in range(1, 6)] == [
        "R1/R2", "R1/R2", "R3/R4", "R3/R4", "R5"
    ]


def test_hover_outside_pool_does_not_hide_other_options_and_lock_wins():
    assignment = {
        "Top": _player(1, "Top", "Top", "Camille"),
        "Jungle": _player(2, "Jungle", "Jungle", "Zac"),
        "Mid": _player(3, "Mid", "Mid", "Orianna"),
        "ADC": _player(4, "ADC", "ADC", "Caitlyn"),
        "Support": _player(5, "Support", "Support", "Nautilus"),
    }
    hovered = _constrain_assignments([assignment], hovered_champions={"Mid": "Sylas"})
    assert hovered[0]["Mid"]["champions"]["Mid"] == ["Sylas", "Orianna"]
    assert hovered[0]["Mid"]["_forced_live_hover"] is True
    locked = _constrain_assignments(
        [assignment],
        locked_champions={"Mid": "Orianna"},
        hovered_champions={"Mid": "Sylas"},
    )
    assert locked[0]["Mid"]["champions"]["Mid"] == ["Orianna"]
    assert locked[0]["Mid"]["_forced_live_hover"] is False


def test_enemy_reveals_reward_cross_role_sylas_and_explain_ulti_value():
    from league_comp_engine import load_meta, live_enemy_draft_adjustment
    meta = load_meta()
    champs = {"Top": "Gnar", "Jungle": "Sylas", "Mid": "Yasuo", "ADC": "Jinx", "Support": "Nautilus"}
    response = live_enemy_draft_adjustment(champs, ["Malphite", "Ornn", "Rell"], meta)
    assert response["adjustment"] > 0
    assert any("Sylas" in reason and "ultimates" in reason for reason in response["reasons"])


def test_enemy_triple_tank_punishes_magic_stacking_and_zed_mid():
    from league_comp_engine import load_meta, live_enemy_draft_adjustment
    meta = load_meta()
    ap_mid = {"Top": "Gwen", "Jungle": "Sylas", "Mid": "Orianna", "ADC": "Kog'Maw", "Support": "Karma"}
    physical_mid = dict(ap_mid, Mid="Tristana")
    zed_mid = dict(ap_mid, Mid="Zed")
    enemies = ["Malphite", "Ornn", "Rell"]
    ap = live_enemy_draft_adjustment(ap_mid, enemies, meta)
    balanced = live_enemy_draft_adjustment(physical_mid, enemies, meta)
    zed = live_enemy_draft_adjustment(zed_mid, enemies, meta)
    assert balanced["adjustment"] > ap["adjustment"]
    assert balanced["adjustment"] > zed["adjustment"]
    assert any("resistances" in reason for reason in ap["reasons"])
    assert any("burst-assassin" in reason for reason in zed["reasons"])


def test_live_draft_keeps_mid_alternatives_when_hover_is_bad():
    signups = [
        {**_player(1, "Top", "Top", "Gnar")},
        {**_player(2, "Jungle", "Jungle", "Sylas")},
        {**_player(3, "Mid", "Mid", "Tristana")},
        {**_player(4, "ADC", "ADC", "Kog'Maw")},
        {**_player(5, "Support", "Support", "Nautilus")},
    ]
    signups[2]["champions"]["Mid"] = ["Tristana", "Orianna", "Zed"]
    result = recommend_live_drafts(
        signups,
        locked_champions={"Jungle": "Sylas"},
        hovered_champions={"Mid": "Zed"},
        enemy_champions=["Malphite", "Ornn", "Rell"],
        limit=6,
    )
    mids = {rec["champions"]["Mid"] for rec in result["recommendations"]}
    assert "Tristana" in mids
    assert mids != {"Zed"}


def test_sylas_is_first_recommendation_when_malphite_ornn_are_revealed():
    def multi(uid, role, champions):
        person = _player(uid, role, role, champions[0])
        person["champions"][role] = champions
        return person

    signups = [
        multi(1, "Top", ["Gnar"]),
        multi(2, "Jungle", ["Vi", "Sylas"]),
        multi(3, "Mid", ["Tristana", "Orianna", "Zed"]),
        multi(4, "ADC", ["Kog'Maw"]),
        multi(5, "Support", ["Nautilus"]),
    ]
    result = recommend_live_drafts(
        signups,
        hovered_champions={"Mid": "Zed"},
        enemy_champions=["Malphite", "Ornn"],
        limit=6,
    )
    assert result["recommendations"][0]["champions"]["Jungle"] == "Sylas"
    assert result["recommendations"][0]["champions"]["Mid"] == "Tristana"
