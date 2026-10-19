"""Regression checks for newly joined players with sparse or missing history."""
from __future__ import annotations

import copy

import league_comp_engine as engine


TOM_ID = 1051188024668602440


def test_tom_registered_with_five_accounts_but_no_assumed_roles():
    profiles = engine.load_player_profiles()["players"]
    tom = profiles[str(TOM_ID)]
    assert tom["discord_name"] == "Tom"
    assert len(tom["opgg_urls"]) == 5
    assert len(profiles) == 8
    assert len({u for prof in profiles.values() for u in prof["opgg_urls"]}) == 28

    from pathlib import Path
    import json
    roster = json.loads((Path(__file__).resolve().parents[1] /
                         "loadfiles" / "flexers-team.json").read_text(encoding="utf-8"))
    rows = [p for p in roster["players"] if int(p["user_id"]) == TOM_ID]
    assert len(rows) == 1
    assert rows[0]["roles"] == []
    assert rows[0]["pools"] == {}
    assert set(rows[0]["riot_ids"]) == {
        "Conor McGregor#HERO", "Mid Iane banter#EUW",
        "ndnuh#EUW", "TSM TheOddOne#God", "yNrZora#EUW",
    }


def test_no_match_history_has_neutral_skill_and_does_not_shift_teammates(monkeypatch):
    base = engine.load_player_profiles()
    full = copy.deepcopy(base)
    excluded = copy.deepcopy(base)
    excluded["players"].pop(str(TOM_ID))
    monkeypatch.setattr(engine, "load_player_profiles", lambda: excluded)
    engine.load_player_score_index.cache_clear()
    old = engine.load_player_score_index()
    monkeypatch.setattr(engine, "load_player_profiles", lambda: full)
    engine.load_player_score_index.cache_clear()
    updated = engine.load_player_score_index()
    try:
        assert updated[TOM_ID]["data_quality"] == "insufficient_history"
        assert updated[TOM_ID]["carry_index"] == 50.0
        assert updated[TOM_ID]["reliability_index"] == 50.0
        assert updated[TOM_ID]["roles"] == {}
        for uid in old:
            assert updated[uid] == old[uid], (uid, updated[uid], old[uid])
    finally:
        engine.load_player_score_index.cache_clear()
