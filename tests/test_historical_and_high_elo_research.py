"""History-wide opponent scouting & attributed one-trick source separation."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

import league_pocketpick_history as pocket
from league_matchup_universe import scout_note, role_universe


def setup_fake_archive(tmp_path, monkeypatch):
    archive=tmp_path/"pocket"
    archive.mkdir()
    metrics={
        "patches":{"16.16":30000},"matches":30000,
        "queue":420,"region":"euw",
        "metrics":{
            "Camille":{"TOP":{"games":420,"wr":0.52}},
            "Gwen":{"TOP":{"games":500,"wr":0.49}},
            "Ornn":{"TOP":{"games":550,"wr":0.51}},
        },
        "matchups":{
            "Camille|TOP|Gwen":{"n":57,"wr":0.55,"gd15":112},
            "Ornn|TOP|Gwen":{"n":6,"wr":0.83,"gd15":222},
            "Camille|TOP|Camille":{"n":40,"wr":0.6},
            "Camille|MID|Gwen":{"n":200,"wr":0.8},
        },
    }
    (archive/"matchv5-metrics.json").write_text(json.dumps(metrics),encoding="utf8")
    (archive/"gd15-matchups.json").write_text(
        json.dumps({"matches":30000,"patch":{"16.16":30000}}),encoding="utf8")
    monkeypatch.setenv("DRAFTCOACH_POCKETPICK_DIR",str(archive))
    return archive


def test_full_historical_reference_preserves_thin_rows_without_scoring(tmp_path,monkeypatch):
    setup_fake_archive(tmp_path,monkeypatch)
    result=pocket.historical_for_enemy("Top","Gwen")
    assert result["status"]=="historical_only"
    assert result["patch"]=="16.16" and result["rank"]=="Silver-Platinum"
    rows={row["champion"]:row for row in result["rows"]}
    assert set(rows)=={"Camille","Ornn"}
    assert rows["Camille"]["games"]==57
    assert rows["Camille"]["adjusted_matchup_lift_pp"]==3.0
    assert rows["Camille"]["sample_grade"]=="moderate-historical"
    assert rows["Camille"]["usable_for_current_patch_autopick"] is False
    assert "draft_points" not in rows["Camille"]
    assert rows["Ornn"]["sample_grade"]=="thin"
    assert rows["Ornn"]["games"]==6
    assert pocket.historical_for_enemy("Top","Camille")["rows"]==[]


def test_corrupt_or_missing_archive_is_a_safe_empty_reference(tmp_path,monkeypatch):
    setup_fake_archive(tmp_path,monkeypatch)
    (tmp_path/"pocket"/"gd15-matchups.json").write_text("{}")
    assert pocket.historical_for_enemy("Top","Gwen")["rows"]==[]
    assert pocket.historical_for_enemy("Top","Gwen")["status"]=="historical_archive_missing"
    assert pocket.historical_for_enemy("Farming","Gwen")["rows"]==[]


def test_one_trick_references_are_qualitative_rather_than_autopick():
    qiyana=scout_note("Mid","Qiyana","Zed")
    assert qiyana["verdict"]=="favourable"
    assert qiyana["guide_tier"]=="C"
    assert qiyana["draft_points"]==0
    assert "onetricks.gg" in qiyana["sources"][0]["url"]
    vayne=scout_note("Top","Vayne","Malphite")
    assert vayne["verdict"]=="unfavourable"
    assert vayne["guide_tier"]=="S+"
    assert vayne["draft_points"]==0
    assert "Malphite" in vayne["enemy"]
    assert not scout_note("ADC","Vayne","Malphite")


def test_one_trick_review_count_source_links_and_champion_names():
    root=Path(__file__).resolve().parents[1]
    f=root/"data"/"league_onetricks_matchup_notes.json"
    data=json.loads(f.read_text(encoding="utf8"))
    assert data["schema_version"]==1
    assert len(data["entries"])==43
    pairs=set()
    for row in data["entries"]:
        assert row["role"] in ("Top","Jungle","Mid","ADC","Support")
        assert row["our"] in role_universe(row["role"])
        assert row["enemy"] in set(sum((list(role_universe(r)) for r in
                                      ("Top","Jungle","Mid","ADC","Support")),[]))
        key=(row["role"],row["our"],row["enemy"])
        assert key not in pairs
        pairs.add(key)
        assert row["draft_points"]==0
        assert row["guide_tier"] in ("S+","S","A","B","C","D","F")
        assert all(s["url"].startswith("https://www.onetricks.gg/guide/")
                   for s in row["sources"])


def test_scouting_ui_distinguishes_current_patch_from_low_rank_history():
    root=Path(__file__).resolve().parents[1]
    js=(root/"draftcoach"/"static"/"selected-comp.js").read_text(encoding="utf8")
    py=(root/"counterpick_advice.py").read_text(encoding="utf8")
    assert "historical_for_enemy(role, enemy)" in py
    assert "pocket_reference" in py
    assert "PocketPick 16.16 EUW Silver" in js
    assert "not current patch" in js
    assert "historicalEntry.type" in js
    assert "no automatic swap" in js
