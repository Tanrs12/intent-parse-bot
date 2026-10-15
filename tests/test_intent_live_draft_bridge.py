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


def test_party_gate_disables_four_man(monkeypatch):
    bridge._party_size_cache = None
    bridge._party_size_at = 0.0
    monkeypatch.setattr(bridge, "_lcu_json", lambda path: {"members": [{}, {}, {}, {}]} if path == "/lol-lobby/v2/lobby" else None)
    gate = bridge._party_gate()
    assert gate["party_confirmed"] is False
    assert gate["party_size"] == 4


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
