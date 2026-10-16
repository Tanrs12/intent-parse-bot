from tools import intent_live_draft_bridge as bridge


def test_mode_gate_disables_standard_aram(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_gameflow",
        lambda: {"gameData": {"queue": {"id": 450, "name": "ARAM", "gameMode": "ARAM"}}, "map": {"name": "Howling Abyss"}},
    )
    gate = bridge._mode_gate()
    assert gate["disabled"] is True
    assert gate["queue_id"] == 450


def test_mode_gate_disables_aram_mayhem(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_gameflow",
        lambda: {"gameData": {"queue": {"id": 2400, "name": "ARAM: Mayhem", "gameMode": "KIWI"}}, "map": {}},
    )
    gate = bridge._mode_gate()
    assert gate["disabled"] is True
    assert gate["queue_id"] == 2400


def test_mode_gate_allows_ranked_flex(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_gameflow",
        lambda: {"gameData": {"queue": {"id": 440, "name": "Ranked Flex", "gameMode": "CLASSIC"}}, "map": {"name": "Summoner's Rift"}},
    )
    gate = bridge._mode_gate()
    assert gate["disabled"] is False
    assert gate["queue_id"] == 440


def test_party_gate_requires_five_live_members(monkeypatch):
    bridge._party_size_cache = None
    bridge._party_size_at = 0.0
    monkeypatch.setattr(bridge, "_lcu_json", lambda path: {"members": [{}, {}, {}, {}, {}]} if path == "/lol-lobby/v2/lobby" else None)
    gate = bridge._party_gate()
    assert gate["party_confirmed"] is True
    assert gate["party_size"] == 5
    assert gate["party_cached"] is False


def test_party_gate_allows_four_man(monkeypatch):
    bridge._party_size_cache = None
    bridge._party_size_at = 0.0
    monkeypatch.setattr(bridge, "_lcu_json", lambda path: {"members": [{}, {}, {}, {}]} if path == "/lol-lobby/v2/lobby" else None)
    gate = bridge._party_gate()
    assert gate["party_confirmed"] is True
    assert gate["party_size"] == 4


def test_party_gate_allows_three_man(monkeypatch):
    bridge._party_size_cache = None
    bridge._party_size_at = 0.0
    monkeypatch.setattr(bridge, "_lcu_json", lambda path: {"members": [{}, {}, {}]} if path == "/lol-lobby/v2/lobby" else None)
    gate = bridge._party_gate()
    assert gate["party_confirmed"] is True
    assert gate["party_size"] == 3


def test_party_gate_still_disables_two_man(monkeypatch):
    bridge._party_size_cache = None
    bridge._party_size_at = 0.0
    monkeypatch.setattr(bridge, "_lcu_json", lambda path: {"members": [{}, {}]} if path == "/lol-lobby/v2/lobby" else None)
    gate = bridge._party_gate()
    assert gate["party_confirmed"] is False
    assert gate["party_size"] == 2


def test_party_gate_carries_five_man_confirmation_into_champ_select(monkeypatch):
    bridge._party_size_cache = 5
    bridge._party_size_at = 100.0
    monkeypatch.setattr(bridge.time, "time", lambda: 120.0)
    def unavailable(_path):
        raise RuntimeError("lobby endpoint unavailable in champion select")
    monkeypatch.setattr(bridge, "_lcu_json", unavailable)
    gate = bridge._party_gate()
    assert gate["party_confirmed"] is True
    assert gate["party_size"] == 5
    assert gate["party_cached"] is True

def test_mode_gate_marks_custom_summoners_rift(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_gameflow",
        lambda: {
            "gameData": {
                "isCustomGame": True,
                "queue": {"id": 3140, "name": "Multiplayer Practice Tool Custom", "gameMode": "PRACTICETOOL", "isCustom": True, "category": "Custom"},
            },
            "map": {"name": "Summoner's Rift", "gameMode": "PRACTICETOOL"},
        },
    )
    gate = bridge._mode_gate()
    assert gate["disabled"] is False
    assert gate["is_custom"] is True


def test_custom_lobby_bypasses_five_man_gate(monkeypatch):
    monkeypatch.setattr(bridge, "_mode_gate", lambda: {"disabled": False, "queue_id": 3140, "queue_name": "Custom", "is_custom": True})
    monkeypatch.setattr(bridge, "_party_gate", lambda: {"party_confirmed": False, "party_size": 1, "party_cached": False})
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("coach unavailable")
    monkeypatch.setattr(bridge, "_json_get", unavailable)
    result = bridge.build_response()
    assert result["reason"] == "Draft Coach server unavailable"
    assert result["party_size"] == 1

def test_mode_gate_marks_versus_ai_as_bot(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_gameflow",
        lambda: {
            "gameData": {"queue": {"id": 850, "name": "Intermediate", "category": "VersusAi", "gameMode": "CLASSIC", "type": "BOT"}},
            "map": {"name": "Summoner's Rift", "gameMode": "CLASSIC"},
        },
    )
    gate = bridge._mode_gate()
    assert gate["disabled"] is False
    assert gate["is_bot"] is True


def test_versus_ai_bypasses_five_man_gate(monkeypatch):
    monkeypatch.setattr(bridge, "_mode_gate", lambda: {"disabled": False, "queue_id": 850, "queue_name": "Intermediate", "is_custom": False, "is_bot": True})
    monkeypatch.setattr(bridge, "_party_gate", lambda: {"party_confirmed": False, "party_size": 1, "party_cached": False})
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("coach unavailable")
    monkeypatch.setattr(bridge, "_json_get", unavailable)
    result = bridge.build_response()
    assert result["reason"] == "Draft Coach server unavailable"
    assert result["party_size"] == 1


def test_live_guidance_uses_revealed_enemy_lane_matchup(monkeypatch):
    monkeypatch.setattr(bridge.league_comp_engine, "load_lane_matchups", lambda: {})
    def matchup(role, enemy, ours, _data):
        assert role == "Top" and enemy == "Sett"
        return ((-4.0, 1000) if ours == "Ornn" else (4.0, 1000))
    monkeypatch.setattr(bridge.league_comp_engine, "matchup_response", matchup)
    monkeypatch.setattr(
        bridge.league_comp_engine,
        "calibrated_matchup",
        lambda role, advantage, games, quality=1.0: (advantage, advantage * quality),
    )
    def rec(champ, score):
        champions = {"Top": champ, "Jungle": "Vi", "Mid": "Orianna", "ADC": "Ezreal", "Support": "Karma"}
        players = {role: {"display_name": role, "role_priority": {role: 1}} for role in bridge.ROLES}
        return {"score": score, "rank_score": score, "champions": champions, "players": players,
                "pick_order": [{"role": "Top", "champion": champ}]}
    result = {"recommendations": [rec("Ornn", 90.0), rec("Gwen", 89.0)]}
    state = {
        "our_team": [{"role": role, "locked": None} for role in bridge.ROLES],
        "enemy_team": [{"role": "Top", "locked": "Sett"}],
        "active": {"kind": "pick", "ours": True},
    }
    guidance = bridge._guidance_from_result(result, state)
    assert guidance["comps"][0]["champions"]["Top"] == "Gwen"
    assert guidance["picks"][0]["champion"] == "Gwen"
    assert guidance["picks"][0]["lane"][0]["enemy"] == "Sett"


def test_live_guidance_exposes_six_compositions(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    recs = []
    for index in range(6):
        champ = f"Top{index}"
        recs.append({
            "score": 100 - index,
            "champions": {"Top": champ, "Jungle": "Vi", "Mid": "Orianna", "ADC": "Ezreal", "Support": "Karma"},
            "players": {role: {"display_name": role, "role_priority": {role: 1}} for role in bridge.ROLES},
            "pick_order": [{"role": "Top", "champion": champ}],
        })
    state = {"our_team": [{"role": role, "locked": None} for role in bridge.ROLES], "enemy_team": [], "active": {}}
    guidance = bridge._guidance_from_result({"recommendations": recs}, state)
    assert len(guidance["comps"]) == 6


def test_current_five_fills_solo_bot_test_lineup(monkeypatch):
    rows = [
        {"user_id": uid, "display_name": f"P{i}", "roles": ["Top"], "role_priority": {"Top": 1}, "champions": {"Top": ["Gnar"]}}
        for i, uid in enumerate((101, 102, 103, 104, 105), start=1)
    ]
    monkeypatch.setattr(bridge, "_signups", rows)
    monkeypatch.setattr(bridge, "TEST_LINEUP_IDS", [101, 102, 103, 104, 105])
    state = {"our_team": [{"user_id": 101}]}
    assert bridge._current_five(state) == []
    filled = bridge._current_five(state, fill_for_test=True)
    assert [row["user_id"] for row in filled] == [101, 102, 103, 104, 105]


def test_queue_traits_recognises_bot_queue_from_coach_status():
    gate = bridge._queue_traits({"id": 850, "name": "Intermediate", "category": "VersusAi", "type": "BOT"})
    assert gate["is_bot"] is True
    assert gate["disabled"] is False


def test_bot_test_state_ignores_auto_selected_ally_bots(monkeypatch):
    monkeypatch.setattr(bridge, "_signups", [{"user_id": 101}])
    state = {
        "local_cell_id": 0,
        "our_team": [
            {"cell_id": 0, "user_id": 101, "role": "Mid", "locked": "LeBlanc"},
            {"cell_id": 1, "user_id": None, "role": "Jungle", "locked": "Master Yi"},
            {"cell_id": 2, "user_id": None, "role": "Top", "locked": "Fiddlesticks"},
        ],
        "enemy_team": [{"role": "Top", "locked": "Sett"}],
    }
    effective = bridge._state_for_test(state)
    assert effective["our_team"][0]["locked"] == "LeBlanc"
    assert effective["our_team"][1]["locked"] is None
    assert effective["our_team"][2]["locked"] is None
    assert effective["enemy_team"][0]["locked"] == "Sett"


def test_custom_test_overrides_apply_without_touching_original():
    bridge._set_test_overrides({"clear": True})
    bridge._set_test_overrides({"ban": "Camille", "enemy_pick": {"role": "Top", "champion": "Rumble"},
                                "our_pick": {"role": "Jungle", "champion": "Amumu"}})
    original = {"our_team": [{"role": "Jungle", "locked": None}], "enemy_team": [{"role": "Top", "locked": None}],
                "our_bans": [], "enemy_bans": []}
    effective = bridge._apply_test_overrides(original)
    assert original["enemy_bans"] == []
    assert effective["enemy_bans"] == ["Camille"]
    assert effective["enemy_team"][0]["locked"] == "Rumble"
    assert effective["our_team"][0]["locked"] == "Amumu"
    bridge._set_test_overrides({"clear": True})


def test_bridge_has_champion_ids_for_overlay_icons():
    assert bridge._champion_id("Camille") == 164
    assert bridge._champion_id("LeBlanc") == 7


def test_live_guidance_exposes_same_plan_role_options(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    rec = {
        "score": 91.0,
        "champions": {"Top": "Sion", "Jungle": "Vi", "Mid": "Orianna", "ADC": "Ezreal", "Support": "Karma"},
        "role_alternatives": {"Top": ["Shen", "Ornn"]},
        "players": {role: {"display_name": role, "role_priority": {role: 1}, "user_id": i + 1}
                    for i, role in enumerate(bridge.ROLES)},
        "pick_order": [{"role": "Top", "champion": "Sion"}],
    }
    state = {"our_team": [{"role": role, "locked": None} for role in bridge.ROLES], "enemy_team": [], "active": {}}
    comp = bridge._guidance_from_result({"recommendations": [rec]}, state)["comps"][0]
    assert comp["role_options"]["Top"] == ["Sion", "Shen", "Ornn"]
    assert comp["role_option_ids"]["Top"] == [14, 98, 516]


def test_manual_idle_state_never_keeps_live_helpers_active(monkeypatch):
    monkeypatch.setattr(bridge, "_mode_gate", lambda: {"disabled": False, "queue_id": 3100, "queue_name": "Custom", "is_custom": True, "is_bot": False})
    monkeypatch.setattr(bridge, "_party_gate", lambda: {"party_confirmed": False, "party_size": 1, "party_cached": False})
    monkeypatch.setattr(bridge, "_json_get", lambda *_args, **_kwargs: {
        "status": {"lcu_in_champ_select": False, "stale": False, "queue": None},
        "state": {"source": "manual", "phase": "BAN_PICK"},
    })
    result = bridge.build_response()
    assert result["active"] is False
    assert result["reason"] == "Waiting for champion select"


def test_each_comp_exposes_its_own_side_aware_bans(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    champions = {"Top": "Camille", "Jungle": "Vi", "Mid": "Ahri", "ADC": "Ezreal", "Support": "Karma"}
    players = {role: {"display_name": role, "role_priority": {role: 1}, "user_id": i + 1} for i, role in enumerate(bridge.ROLES)}
    result = {"side": "red", "recommendations": [{
        "score": 90.0, "champions": champions, "players": players,
        "pick_order": [{"slot": i + 1, "role": role, "champion": champions[role]} for i, role in enumerate(bridge.ROLES)],
        "counter_bans": [{"champion": "Jax", "score": 2.5}, {"champion": "Caitlyn", "score": 1.5}],
    }]}
    state = {"our_team": [{"role": role, "locked": None} for role in bridge.ROLES], "enemy_team": [], "active": {}}
    guidance = bridge._guidance_from_result(result, state)
    assert guidance["side"] == "red"
    assert [b["champion"] for b in guidance["comps"][0]["counter_bans"]] == ["Jax", "Caitlyn"]



def test_current_five_uses_actual_accounts_and_honest_pugs(monkeypatch):
    rows = [
        {"user_id": 101, "display_name": "Profile One", "roles": ["Top"], "role_priority": {"Top": 1}, "champions": {"Top": ["Camille"]}},
        {"user_id": 102, "display_name": "Profile Two", "roles": ["Jungle"], "role_priority": {"Jungle": 1}, "champions": {"Jungle": ["Vi"]}},
        {"user_id": 103, "display_name": "Profile Three", "roles": ["Mid"], "role_priority": {"Mid": 1}, "champions": {"Mid": ["Ahri"]}},
    ]
    monkeypatch.setattr(bridge, "_signups", rows)
    monkeypatch.setattr(bridge, "_pug_role_pool", lambda role, limit=3: {
        "ADC": ["Ezreal", "Jhin", "Kai'Sa"], "Support": ["Nautilus", "Braum", "Karma"]
    }[role][:limit])
    state = {"our_team": [
        {"cell_id": 0, "user_id": 101, "riot_id": "LiveTop#EUW", "display_name": "LiveTop", "role": "Top"},
        {"cell_id": 1, "user_id": 102, "riot_id": "LiveJg#EUW", "display_name": "LiveJg", "role": "Jungle"},
        {"cell_id": 2, "user_id": 103, "riot_id": "LiveMid#EUW", "display_name": "LiveMid", "role": "Mid"},
        {"cell_id": 3, "user_id": None, "riot_id": "RandomADC#EUW", "display_name": "RandomADC", "role": "ADC"},
        {"cell_id": 4, "user_id": None, "riot_id": "RandomSup#EUW", "display_name": "RandomSup", "role": "Support"},
    ]}
    five = bridge._current_five(state)
    assert len(five) == 5
    assert five[0]["display_name"] == "LiveTop#EUW"
    assert five[0]["_profile_display_name"] == "Profile One"
    assert five[3]["_uncontrolled"] is True
    assert five[3]["roles"] == ["ADC"]
    assert five[3]["display_name"] == "RandomADC#EUW"
    assert five[3]["champions"]["ADC"] == ["Ezreal", "Jhin", "Kai'Sa"]
    assert five[4]["user_id"] < 0


def test_pug_hover_replaces_placeholder_pool(monkeypatch):
    monkeypatch.setattr(bridge, "_signups", [])
    row = {"cell_id": 4, "user_id": None, "riot_id": "RandomADC#EUW", "display_name": "RandomADC",
           "role": "ADC", "hover": "Caitlyn", "locked": None}
    pug = bridge._live_pug_signup(row, 4)
    assert pug["champions"] == {"ADC": ["Caitlyn"]}
    assert pug["_live_champion_confirmed"] is True


def test_guidance_never_tells_uncontrolled_pug_what_to_pick(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    champions = {"Top": "Camille", "Jungle": "Vi", "Mid": "Ahri", "ADC": "Ezreal", "Support": "Nautilus"}
    players = {
        role: {"display_name": role, "role_priority": {role: 1}, "user_id": i + 1}
        for i, role in enumerate(bridge.ROLES)
    }
    players["Support"].update({"display_name": "RandomSup#EUW", "_uncontrolled": True,
                               "_live_account": "RandomSup#EUW", "_live_champion_confirmed": False})
    rec = {
        "score": 90.0, "champions": champions, "players": players,
        "pick_order": [
            {"slot": 1, "role": "Support", "champion": "Nautilus"},
            {"slot": 2, "role": "Top", "champion": "Camille"},
        ],
        "counter_bans": [],
    }
    state = {
        "our_team": [{"role": role, "locked": None, "hover": ("Braum" if role == "Support" else None)} for role in bridge.ROLES],
        "enemy_team": [], "active": {"kind": "pick", "ours": True},
    }
    guidance = bridge._guidance_from_result({"side": "blue", "recommendations": [rec]}, state)
    assert guidance["picks"][0]["role"] == "Top"
    comp = guidance["comps"][0]
    assert comp["uncontrolled_roles"] == ["Support"]
    assert comp["live_uncontrolled_champions"] == {"Support": "Braum"}
    assert comp["players"]["Support"] == "RandomSup#EUW"


def test_custom_visual_mode_fills_portraits_bans_and_pick(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    rec = {
        "score": 90.0,
        "champions": {"Top": "Camille", "Jungle": "Wukong", "Mid": "Orianna", "ADC": "Ezreal", "Support": "Nautilus"},
        "role_alternatives": {},
        "players": {role: {"display_name": role, "role_priority": {role: 1}, "user_id": i + 1, "champions": {role: []}}
                    for i, role in enumerate(bridge.ROLES)},
        "pick_order": [],
        "counter_bans": [],
    }
    state = {
        "local_cell_id": 1,
        "our_team": [
            {"cell_id": i, "role": role, "locked": None, "hover": None, "is_local": role == "Jungle"}
            for i, role in enumerate(bridge.ROLES)
        ],
        "enemy_team": [],
        "active": {"kind": "pick", "ours": True},
        "test_overrides": {"bans": [], "enemy": {}, "ours": {}},
    }
    guidance = bridge._guidance_from_result({"side": "blue", "recommendations": [rec]}, state)
    comp = guidance["comps"][0]
    assert guidance["visual_test"] is True
    assert len(comp["role_options"]["Top"]) == 3
    assert all(comp["role_option_ids"]["Top"])
    assert len(comp["counter_bans"]) == 5
    assert all(row["champion_id"] for row in comp["counter_bans"])
    assert len(guidance["bans"]) == 5
    assert guidance["picks"][0]["role"] == "Jungle"
    assert guidance["picks"][0]["champion_id"]


def test_current_pick_group_keeps_blue_pair_interchangeable(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    champions = {"Top": "Camille", "Jungle": "Vi", "Mid": "Orianna", "ADC": "KogMaw", "Support": "Nami"}
    players = {role: {"display_name": role, "role_priority": {role: 1}, "user_id": i + 1} for i, role in enumerate(bridge.ROLES)}
    rec = {
        "score": 90.0, "champions": champions, "players": players,
        "pick_order": [
            {"slot": 1, "role": "Top", "champion": "Camille"},
            {"slot": 2, "role": "Jungle", "champion": "Vi"},
            {"slot": 3, "role": "Support", "champion": "Nami"},
            {"slot": 4, "role": "ADC", "champion": "KogMaw"},
            {"slot": 5, "role": "Mid", "champion": "Orianna"},
        ],
        "counter_bans": [],
    }
    state = {
        "side": "blue",
        "our_team": [
            {"role": "Top", "locked": "Camille"},
            {"role": "Jungle", "locked": None},
            {"role": "Mid", "locked": None},
            {"role": "ADC", "locked": None},
            {"role": "Support", "locked": "Nami"},
        ],
        "enemy_team": [], "active": {"kind": "pick", "ours": True},
    }
    guidance = bridge._guidance_from_result({"side": "blue", "recommendations": [rec]}, state)
    group = guidance["current_pick_group"]
    assert group["label"] == "B2/B3"
    assert group["interchangeable"] is True
    assert {m["role"]: m["status"] for m in group["members"]} == {"Jungle": "remaining", "Support": "locked"}


def test_ally_hover_is_soft_intent_and_pick_group_marks_it(monkeypatch):
    monkeypatch.setattr(bridge, "_live_lane_adjustment", lambda rec, state: (0.0, []))
    champions = {"Top": "Ornn", "Jungle": "Vi", "Mid": "Sylas", "ADC": "Ezreal", "Support": "Nami"}
    players = {role: {"display_name": role, "role_priority": {role: 1}, "user_id": i + 1} for i, role in enumerate(bridge.ROLES)}
    result = {"side": "blue", "hovered_champions": {"Mid": "Sylas"}, "recommendations": [{
        "score": 90.0,
        "champions": champions,
        "players": players,
        "pick_order": [
            {"slot": 1, "role": "Jungle", "champion": "Vi"},
            {"slot": 2, "role": "Mid", "champion": "Sylas"},
            {"slot": 3, "role": "Support", "champion": "Nami"},
            {"slot": 4, "role": "ADC", "champion": "Ezreal"},
            {"slot": 5, "role": "Top", "champion": "Ornn"},
        ],
        "counter_bans": [],
    }]}
    state = {
        "our_team": [
            {"role": "Top", "locked": None, "hover": None},
            {"role": "Jungle", "locked": "Vi", "hover": None},
            {"role": "Mid", "locked": None, "hover": "Sylas"},
            {"role": "ADC", "locked": None, "hover": None},
            {"role": "Support", "locked": None, "hover": None},
        ],
        "enemy_team": [],
        "active": {"kind": "pick", "ours": True},
    }
    guidance = bridge._guidance_from_result(result, state)
    assert guidance["our_hovers"] == {"Mid": "Sylas"}
    assert guidance["current_pick_group"]["label"] == "B2/B3"
    statuses = {m["role"]: m["status"] for m in guidance["current_pick_group"]["members"]}
    assert statuses["Mid"] == "hovered"
    assert statuses["Support"] == "remaining"


def test_hover_collection_ignores_locks_and_ban_phase():
    state = {"active": {"kind": "pick"}, "our_team": [
        {"role": "Mid", "hover": "Sylas", "locked": None},
        {"role": "Top", "hover": "Gwen", "locked": "Ornn"},
    ]}
    assert bridge._hovered_by_role(state) == {"Mid": "Sylas"}
    state["active"] = {"kind": "ban"}
    assert bridge._hovered_by_role(state) == {}



def test_custom_test_roles_do_not_pin_synthetic_roster_seats():
    five = [
        {"user_id": 1}, {"user_id": 2}, {"user_id": 3}, {"user_id": 4}, {"user_id": 5},
    ]
    state = {
        "local_cell_id": 0,
        "our_team": [
            {"cell_id": 0, "user_id": 1, "role": "Top", "is_local": True, "puuid": None, "riot_id": None},
            {"cell_id": 1, "user_id": 2, "role": "Support", "puuid": None, "riot_id": None},
            {"cell_id": 2, "user_id": 3, "role": "Jungle", "puuid": None, "riot_id": None},
            {"cell_id": 3, "user_id": 4, "role": "Mid", "puuid": None, "riot_id": None},
            {"cell_id": 4, "user_id": 5, "role": "ADC", "puuid": None, "riot_id": None},
        ],
    }
    assert bridge._role_players_for_state(state, five, testing=True) == {"Top": 1}
    assert bridge._role_players_for_state(state, five, testing=False) == {
        "Top": 1, "Support": 2, "Jungle": 3, "Mid": 4, "ADC": 5,
    }


def test_custom_test_strips_bot_locks_even_when_placeholder_has_known_user_id():
    state = {
        "local_cell_id": 0,
        "our_team": [
            {"cell_id": 0, "user_id": 1, "role": "Top", "is_local": True, "locked": None, "hover": "Camille"},
            {"cell_id": 1, "user_id": 2, "role": "Support", "locked": "Morgana", "hover": "Morgana", "puuid": None, "riot_id": None},
            {"cell_id": 2, "user_id": 3, "role": "Jungle", "locked": "Amumu", "hover": "Amumu", "puuid": None, "riot_id": None},
            {"cell_id": 3, "user_id": 4, "role": "Mid", "locked": "Galio", "hover": "Galio", "puuid": None, "riot_id": None},
            {"cell_id": 4, "user_id": 5, "role": "ADC", "locked": "Ezreal", "hover": "Ezreal", "puuid": None, "riot_id": None},
        ],
    }
    cleaned = bridge._state_for_test(state)
    assert cleaned["our_team"][0]["hover"] == "Camille"
    assert all(row.get("locked") is None and row.get("hover") is None for row in cleaned["our_team"][1:])
