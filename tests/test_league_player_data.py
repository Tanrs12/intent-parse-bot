import json

import league_player_data as player_data


def _match(game_id: int, queue: str, created_at: str, champion: str = "Ahri") -> dict:
    return {
        "id": game_id,
        "account": "Example-EUW",
        "created_at": created_at,
        "queue": queue,
        "queue_group": queue,
        "map": "SUMMONERS_RIFT",
        "result": "WIN",
        "champion": champion,
        "position": "MID",
        "kda": 3.0,
        "kp": 55.0,
        "op_score": 6.0,
        "lane_score": 52.0,
        "cs_per_min": 7.0,
        "gold_per_min": 400.0,
        "dpm": 600.0,
        "damage_share": 25.0,
        "vision_score": 15.0,
    }


def test_merge_recent_matches_prefers_fresh_and_caps_queues():
    old = [_match(1, "RANKED", "2026-10-01T00:00:00Z", "Lux")]
    fresh = [_match(1, "RANKED", "2026-12-01T00:00:00Z", "Ahri")]
    fresh.extend(
        _match(1000 + i, "RANKED", f"2026-10-{(i % 28) + 1:02d}T12:{i % 60:02d}:00Z")
        for i in range(player_data.MAX_STORED_MATCHES["RANKED"] + 5)
    )
    fresh.extend(
        _match(3000 + i, "NORMAL", f"2026-09-{(i % 28) + 1:02d}T12:{i % 60:02d}:00Z")
        for i in range(player_data.MAX_STORED_MATCHES["NORMAL"] + 5)
    )

    merged = player_data._merge_recent_matches(old, fresh)
    ranked = [row for row in merged if row["queue_group"] == "RANKED"]
    normals = [row for row in merged if row["queue_group"] == "NORMAL"]
    assert len(ranked) == player_data.MAX_STORED_MATCHES["RANKED"]
    assert len(normals) == player_data.MAX_STORED_MATCHES["NORMAL"]
    assert next(row for row in merged if row["id"] == 1)["champion"] == "Ahri"


def test_refresh_recent_player_data_merges_without_rebuilding_history(tmp_path, monkeypatch):
    target = tmp_path / "profiles.json"
    profile = {
        "schema_version": 2,
        "source": {"site": "OP.GG"},
        "players": {
            "123": {
                "discord_name": "Example",
                "opgg_urls": ["https://op.gg/lol/summoners/euw/Example%20Name-EUW"],
                "champion_history": [{"champion": "Orianna", "games": 99}],
                "masteries": [{"champion_name": "Orianna", "points": 123456}],
                "summary": {"ranked_games_observed": 99},
                "recent_matches": [_match(1, "RANKED", "2026-10-10T00:00:00Z")],
            }
        },
    }
    target.write_text(json.dumps(profile), encoding="utf-8")

    monkeypatch.setattr(player_data, "fetch", lambda url, retries=3: "html")
    monkeypatch.setattr(player_data, "extract_puuid", lambda html: "puuid")

    def fake_history(url, html, account, queue, max_games):
        if queue == "RANKED":
            return [_match(2, "RANKED", "2026-10-13T08:00:00Z", "Syndra")]
        return [_match(3, "NORMAL", "2026-10-13T07:00:00Z", "Viktor")]

    monkeypatch.setattr(player_data, "fetch_match_history", fake_history)
    monkeypatch.setattr(player_data.time, "sleep", lambda seconds: None)

    result = player_data.refresh_recent_player_data(target, {"RANKED": 1, "NORMAL": 1})
    updated = json.loads(target.read_text(encoding="utf-8"))
    player = updated["players"]["123"]

    assert result["players"] == 1
    assert result["accounts"] == 1
    assert result["new_games"] == 2
    assert player["champion_history"] == profile["players"]["123"]["champion_history"]
    assert player["masteries"] == profile["players"]["123"]["masteries"]
    assert {row["id"] for row in player["recent_matches"]} == {1, 2, 3}
    assert player["recent_match_summary"]["queues"]["RANKED"]["games"] == 2
    assert player["recent_match_summary"]["queues"]["NORMAL"]["games"] == 1
    assert updated["source"]["recent_match_auto_refresh"]["match_limits_per_account"] == {"RANKED": 1, "NORMAL": 1}
