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
