from __future__ import annotations

import io
import json
from pathlib import Path

from league_shared_data import sync_shared_league_data


class FakeReply:
    def __init__(self, payload: bytes):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, length: int):
        return self.payload[:length]


def test_one_canonical_snapshot_source_updates_both_fields(monkeypatch, tmp_path):
    profile = {"players": {"42": {"discord_name": "Flexer"}}}
    matchup = {"matchups": {"Mid": {"Ahri": {"responses": {"Veigar": {"games": 1200}}}}}}
    payloads = {
        "league_player_profiles.json": json.dumps(profile).encode(),
        "league_lane_matchups.json": json.dumps(matchup).encode(),
        "league_open_pro_pairs.json": json.dumps({
            "pairs": [{"role_a": "ADC", "champion_a": "Jinx",
                       "role_b": "Support", "champion_b": "Lulu",
                       "games": 15, "shrunken_lift": 0.02}],
            "source": {"name": "ChainCC", "license": "CC BY 4.0"},
        }).encode(),
    }
    def fake_download(request, timeout):
        return FakeReply(payloads[request.full_url.rsplit("/", 1)[-1]])
    monkeypatch.setattr("league_shared_data.urllib.request.urlopen", fake_download)
    first = sync_shared_league_data(tmp_path, base_url="https://example.com/data")
    assert first["changed"] == list(payloads)
    assert first["errors"] == {}
    again = sync_shared_league_data(tmp_path, base_url="https://example.com/data")
    assert again["changed"] == []
    assert again["errors"] == {}
    assert json.loads((tmp_path / "league_lane_matchups.json").read_text()) == matchup


def test_bad_snapshots_do_not_erase_existing_files(monkeypatch, tmp_path):
    original = {"players": {"42": {"discord_name": "Flexer"}}}
    path = tmp_path / "league_player_profiles.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    def bad_source(*_args, **_kwargs):
        return FakeReply(b'{"players":{}}')
    monkeypatch.setattr("league_shared_data.urllib.request.urlopen", bad_source)
    result = sync_shared_league_data(tmp_path)
    assert result["changed"] == []
    assert "league_player_profiles.json" in result["errors"]
    assert json.loads(path.read_text()) == original


def test_network_outage_preserves_previous_downloads(monkeypatch, tmp_path):
    path = tmp_path / "league_player_profiles.json"
    path.write_text('{"players":{"a":{"name":"original"}}}', encoding="utf-8")
    def offline(*_args, **_kwargs):
        raise ConnectionError("offline")
    monkeypatch.setattr("league_shared_data.urllib.request.urlopen", offline)
    result = sync_shared_league_data(tmp_path)
    assert result["changed"] == []
    assert path.read_text(encoding="utf-8") == '{"players":{"a":{"name":"original"}}}'
