"""Licensed professional duo snapshots are read-only, patch-aware and safe."""
import csv
import gzip
import json

from tools.build_chaincc_duo_index import build_duo_index
from league_chaincc_pro_synergy import pro_duo, pro_duo_summary


def test_two_pro_games_produce_patch_specific_mid_jungle_and_bot_duos(tmp_path, monkeypatch):
    fields = ["game_id", "side", "position", "champion", "result", "patch"]
    rows = []
    team = {
        "blue": {"mid": "Sylas", "jng": "Lee Sin", "bot": "Yunara", "sup": "Lulu", "top": "Gnar"},
        "red": {"mid": "Galio", "jng": "Sejuani", "bot": "Jinx", "sup": "Braum", "top": "Ornn"},
    }
    for side in ("blue", "red"):
        for position, champion in team[side].items():
            rows.append({"game_id": "101", "side": side,
                         "position": position, "champion": champion,
                         "result": "TRUE" if side == "blue" else "FALSE",
                         "patch": "16.21"})
    path = tmp_path / "chaincc-players-2026.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = build_duo_index(tmp_path, years=(2026,), download=False)
    assert summary["team_sides"] == 2
    assert summary["games_per_year"]["2026"] == 1
    assert summary["duo_observations"] == 4
    assert summary["duo_records"] == 8
    monkeypatch.setenv("DRAFTCOACH_CHAINCC_DIR", str(tmp_path))
    exact = pro_duo("Mid+Jungle", "Sylas", "Lee Sin", patch="26.21")
    assert exact["exact_patch"] is True
    assert exact["games"] == 1 and exact["wins"] == 1
    assert exact["draft_points"] == 0
    assert exact["licence"] == "CC BY 4.0"
    fallback = pro_duo("ADC+Support", "Yunara", "Lulu", patch="26.22")
    assert fallback["exact_patch"] is False
    assert fallback["patch"] == "2026 season aggregate"
    assert pro_duo("ADC+Support", "Jinx", "Braum", patch="26.21")["wins"] == 0
    assert pro_duo("ADC+Support", "Yunara", "Jinx") == {}
    assert pro_duo_summary()["duo_observations"] == 4


def test_duplicate_or_mixed_patch_team_does_not_create_fake_duo(tmp_path):
    fields = ["game_id", "side", "position", "champion", "result", "patch"]
    rows = [
        ["101", "blue", "mid", "Sylas", "TRUE", "16.21"],
        ["101", "blue", "mid", "Ahri", "TRUE", "16.21"],  # invalid repeated role
        ["101", "blue", "jng", "Lee Sin", "TRUE", "16.21"],
        ["102", "red", "bot", "Jinx", "FALSE", "16.20"],
        ["102", "red", "sup", "Braum", "FALSE", "16.21"],  # inconsistent patch
    ]
    path = tmp_path / "chaincc-players-2026.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        writer.writerows(rows)
    summary = build_duo_index(tmp_path, years=(2026,), download=False)
    assert summary["duo_observations"] == 0
    assert summary["duo_records"] == 0
    data = json.loads((tmp_path / "duo-index.json").read_text(encoding="utf-8"))
    assert data["duos"] == {}
