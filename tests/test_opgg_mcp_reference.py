"""Official, permissioned OP.GG MCP observations remain attributed references."""
import json
import time

import pytest

import league_opgg_mcp_reference as m


def payload():
    return {
        "position": "top", "my_champion": "Camille", "opponent_champion": "Shen",
        "data": {
            "counters": [{"champion_name": "Shen", "play": 1714, "win": 819}],
            "summary": {"positions": [
                {"name": "TOP", "stats": {"win_rate": .50754}}
            ]},
            "opponent_champion_tip": "Camille needs to respect Shen's taunt.",
        },
    }


def test_extracts_attributed_unversioned_observation_without_fake_patch():
    row = m.extract_record(payload(), "Top", "Camille", "Shen")
    assert row["games"] == 1714 and row["wins"] == 819
    assert row["head_to_head_win_rate"] == round(100 * 819 / 1714, 2)
    assert row["role_baseline_win_rate"] == 50.75
    assert row["source_patch"] is None
    assert row["rank_filter"] is None
    assert row["queue_filter"] is None
    assert "OP.GG" in row["source"]


def test_provider_champion_identifiers_are_normalised_correctly():
    assert m._api_name("Cho'Gath") == "CHOGATH"
    assert m._api_name("Dr. Mundo") == "DR_MUNDO"
    assert m._api_name("Kai'Sa") == "KAISA"
    assert m._api_name("Gnar") == "GNAR"


def test_cached_enemy_reader_never_makes_http_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "request_pair", lambda *args: (_ for _ in ()).throw(
        AssertionError("reader must not make remote calls")))
    for own in ("Gnar", "Gwen"):
        row = m.extract_record({
            "position": "top", "my_champion": own, "opponent_champion": "Shen",
            "data": {
                "counters": [{"champion_name": "Shen", "play": 800, "win": 418}],
                "summary": {"positions": [{"name": "TOP", "stats": {"win_rate": 0.50}}]},
            },
        }, "Top", own, "Shen")
        (tmp_path / m._filename("Top", own, "Shen")).write_text(
            json.dumps(row), encoding="utf8")
    found = m.cached_for_enemy("Top", "Shen", folder=tmp_path)
    assert set(found) == {"gnar", "gwen"}
    assert all(row["source_patch"] is None for row in found.values())
    assert m.cached_for_enemy("Top", "Cho'Gath", folder=tmp_path) == {}


def test_rejects_wrong_lane_or_invalid_aggregate():
    with pytest.raises(ValueError, match="different"):
        m.extract_record(payload(), "Mid", "Camille", "Shen")
    p = payload()
    p["data"]["counters"][0]["win"] = 2000
    with pytest.raises(ValueError, match="invalid"):
        m.extract_record(p, "Top", "Camille", "Shen")


def test_cache_used_without_extra_network_call(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "request_pair", lambda role, ours, enemy: m.extract_record(
        payload(), role, ours, enemy))
    row = m.fetch_cached("Top", "Camille", "Shen", folder=tmp_path)
    monkeypatch.setattr(m, "request_pair", lambda *a: (_ for _ in ()).throw(
        AssertionError("should have used a cache")))
    assert m.fetch_cached("Top", "Camille", "Shen", folder=tmp_path) == row
    assert m.get_cached("Top", "Camille", "Gwen", folder=tmp_path) == {}
    assert m.get_cached("Top", "Camille", "Shen", folder=tmp_path, max_age=-1) == {}
