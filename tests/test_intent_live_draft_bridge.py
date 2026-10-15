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
