"""Official Riot Match-V5 ranked baseline: Gold-first, patch-scoped, no leaked IDs."""
from __future__ import annotations

import json
import sqlite3

import league_riot_ranked_sample as sample
import league_comp_engine as engine
from tools.collect_riot_ranked_matchups import database, extract_lanes, record_match, build_export


ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def fake_match(match_id="EUW1_100", patch="16.21", queue=420):
    first = ("Gwen", "Gragas", "Orianna", "Jinx", "Lulu")
    second = ("Shen", "LeeSin", "Syndra", "KaiSa", "Thresh")
    return {
        "metadata": {"matchId": match_id},
        "info": {"queueId": queue, "gameVersion": patch+".123.456",
                 "participants": [
                     {"teamId": team, "teamPosition": role,
                      "championName": champs[i], "win": (team == 100)}
                     for team, champs in ((100, first), (200, second))
                     for i, role in enumerate(ROLES)
                 ]},
    }


def test_ranked_collector_validates_queue_patch_roles_and_deduplication(tmp_path):
    conn = database(tmp_path / "sample.sqlite3")
    assert not record_match(conn, fake_match("EUW1_1", queue=450), "GOLD", "16.21")
    assert not record_match(conn, fake_match("EUW1_2", patch="16.20"), "GOLD", "16.21")
    assert record_match(conn, fake_match("EUW1_3"), "GOLD", "16.21")
    assert not record_match(conn, fake_match("EUW1_3"), "PLATINUM", "16.21")
    assert len(extract_lanes(fake_match("EUW1_4"))) == 5
    out = tmp_path / "ranked-matchups.json"
    metadata = build_export(conn, out)
    assert metadata["sampled_matches"] == {"GOLD|16.21": 1}
    row = json.loads(out.read_text())["matchups"]["GOLD|16.21|Top|Gwen|Shen"]
    assert row["games"] == 1 and row["wins"] == 1
    assert "puuid" not in out.read_text().lower()
    assert "summoner" not in out.read_text().lower()
    conn.close()


def test_ranked_matchups_prefer_gold_and_exclude_old_patch(tmp_path, monkeypatch):
    data = {
        "schema_version": 1, "provider": "Riot Match-V5",
        "region": "EUW", "queue": 420, "matchups": {
            "GOLD|16.21|Top|Gwen|Shen": {"games": 140, "overall_games": 300,
                                          "residual_pp": 6.0},
            "PLATINUM|16.21|Top|Gwen|Shen": {"games": 75, "overall_games": 240,
                                              "residual_pp": 3.0},
            "EMERALD|16.21|Top|Gwen|Shen": {"games": 30, "overall_games": 90,
                                             "residual_pp": -5.0},
            "DIAMOND|16.21|Top|Gwen|Shen": {"games": 12, "overall_games": 200,
                                             "residual_pp": -8.0},
            "GOLD|16.20|Top|Gwen|Shen": {"games": 2000, "overall_games": 3000,
                                          "residual_pp": -8.0},
        },
    }
    folder = tmp_path / "riot-rank-samples"
    folder.mkdir()
    (folder / "ranked-matchups.json").write_text(json.dumps(data))
    monkeypatch.setenv("DRAFTCOACH_RIOT_SAMPLE_DIR", str(folder))
    found = sample.ranked_matchup("Top", "Gwen", "Shen", patch="26.21")
    assert found["patch"] == "16.21"
    assert [tier["tier"] for tier in found["tiers"]] == ["GOLD", "PLATINUM", "EMERALD"]
    assert found["draft_points"] > 0
    assert not sample.ranked_matchup("Top", "Gwen", "Shen", patch="26.22")
    assert not sample.ranked_matchup("Top", "Camille", "Shen", patch="26.21")
    assert sample.TIER_WEIGHTS["GOLD"] > sample.TIER_WEIGHTS["DIAMOND"]


def test_new_ranked_matchup_takes_priority_over_historical_when_available(monkeypatch):
    monkeypatch.setattr(engine, "ranked_matchup", lambda *_a, **_k: {
        "source": "Riot Match-V5 private EUW ranked sample",
        "url": "https://developer.riotgames.com/apis#match-v5",
        "patch": "16.21", "tiers": [{"tier": "GOLD", "games": 200}],
        "games": 200, "adjusted_matchup_pp": 3.5, "draft_points": 0.7,
    })
    result = engine.live_lane_matchup_adjustment(
        {"Top": "Gwen"}, {"Top": "Shen"}, role_weights={"Top": 2.8},
    )
    assert result["adjustment"] > 1.5
    assert result["edges"][0]["sampled_ranked"]
    assert result["edges"][0]["source_tiers"][0]["tier"] == "GOLD"
    assert result["edges"][0]["counterpick_weight"] == 2.8
