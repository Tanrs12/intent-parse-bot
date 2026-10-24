"""Pro archive uses CC BY provenance, preserves patch and avoids causal counter claims."""
from __future__ import annotations
import json
import gzip
import csv

import league_chaincc_pro_history as pro
import league_comp_engine as engine
from tools.build_chaincc_matchup_index import build_index


def test_chaincc_index_does_not_infer_draft_pick_order_and_uses_both_sides(tmp_path, monkeypatch):
    def write(kind, year, rows):
        file = tmp_path / f"chaincc-{kind}-{year}.csv.gz"
        with gzip.open(file, "wt", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    for year in (2025, 2026):
        write("teams", year, [
            {"game_id": f"game-{year}", "side": side, "patch": "16.21",
             "pick1": "Gwen" if side == "Blue" else "Shen", "ban1": "Yone"}
            for side in ("Blue", "Red")
        ])
        write("players", year, [
            {"game_id": f"game-{year}", "side": side, "position": "top",
             "champion": "Gwen" if side == "Blue" else "Shen",
             "result": "TRUE" if side == "Blue" else "FALSE",
             "patch": "16.21", "golddiffat15": "430" if side == "Blue" else "-430"}
            for side in ("Blue", "Red")
        ])
    manifest = build_index(tmp_path, years=(2025, 2026), download=False)
    assert manifest["matched_lane_pairs"] == 2
    assert manifest["licence"] == "CC BY 4.0"
    monkeypatch.setenv("DRAFTCOACH_CHAINCC_DIR", str(tmp_path))
    pro._load.cache_clear()
    row = pro.pro_matchup("Top", "Gwen", "Shen", patch="26.21")
    assert row["exact_patch"]
    assert row["patch"] == "16.21"
    assert row["games"] == 1
    assert row["match_win_rate"] == 100
    assert row["avg_lane_gold_diff_15"] == 430
    assert row["draft_points"] == 0
    assert row["licence"] == "CC BY 4.0"
    assert not pro.pro_matchup("Top", "Camille", "Shen")
    pro._load.cache_clear()


def test_chaincc_pro_evidence_never_becomes_direct_lane_counter_bonus(monkeypatch):
    monkeypatch.setattr(engine, "load_lane_matchups", lambda: {"matchups": {}})
    monkeypatch.setattr(engine, "matchup_notes", lambda *_: [])
    monkeypatch.setattr(engine, "historical_matchup", lambda *_: {})
    monkeypatch.setattr(engine, "pro_matchup", lambda *_a, **_k: {
        "source": "ChainCC professional match archive", "games": 5, "win_rate": 80,
        "draft_points": 0, "confidence": "limited",
    })
    output = engine.live_lane_matchup_adjustment(
        {"Top": "Gwen"}, {"Top": "Shen"}, role_weights={"Top": 2.8},
    )
    assert output["adjustment"] == 0
    assert output["edges"][0]["professional_context"]["games"] == 5
    assert output["edges"][0]["points"] == 0
