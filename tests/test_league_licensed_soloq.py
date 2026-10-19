import json
import pytest

import league_aggregate_sources as aggregate
import league_comp_engine as engine
from tools.import_licensed_soloq import clean

def _approved():
    return {
        "source": {"name": "Approved Example Vendor",
                   "permission_reference": "test-only fixture, not a real vendor approval"},
        "patch": "16.20", "queue": "RANKED_SOLO_5x5",
        "region": "EUW", "rank": "Emerald+",
        "champions": [{"role": "Mid", "champion": "Veigar", "win_rate": 53.2, "games": 8500}],
        "matchups": [{"role": "Mid", "enemy": "Ahri", "response": "Veigar",
                      "games": 3400, "response_advantage": 4.7}],
        "pairs": [{"role_a": "ADC", "champion_a": "Jinx",
                   "role_b": "Support", "champion_b": "Lulu",
                   "games": 2400, "lift_pp": 1.3}],
    }

def test_refuse_missing_permission():
    example = _approved()
    example["source"]["permission_reference"] = ""
    with pytest.raises(ValueError, match="permission"):
        clean(example)

def test_schema_normalizer_keeps_scope_and_provenance():
    result = clean(_approved())
    assert aggregate.validate_snapshot(result)
    assert result["patch"] == "16.20"
    assert result["sources"][0]["rank"] == "Emerald+"
    assert result["matchups"][0]["response_advantage"] == 4.7

def test_imported_current_patch_affects_lane_and_pair(monkeypatch, tmp_path):
    snap = clean(_approved())
    f=tmp_path/"approved.json"
    f.write_text(json.dumps(snap), encoding="utf8")
    monkeypatch.setattr(aggregate,"SOLOQ_PATH",f)
    aggregate._read.cache_clear()
    aggregate._indices.cache_clear()
    engine.load_lane_matchups.cache_clear()
    engine.load_solo_queue_winrates.cache_clear()
    try:
        result = engine.load_lane_matchups()
        actual=result["matchups"]["Mid"]["Ahri"]["responses"]["Veigar"]
        assert actual["games"] == 3400
        assert actual["response_advantage"] == 4.7
        assert actual["source"] == "Approved Example Vendor"
        wr=engine.load_solo_queue_winrates()["Mid"]["Veigar"]
        assert wr["win_rate"] == 53.2
        sig=aggregate.approved_pair_signal("ADC","Jinx","Support","Lulu","16.20")
        assert sig["games"] == 2400
        assert 0 < sig["adjustment"] < 0.3
    finally:
        engine.load_lane_matchups.cache_clear()
        engine.load_solo_queue_winrates.cache_clear()
        aggregate._read.cache_clear()
        aggregate._indices.cache_clear()

def test_stale_patch_never_overrides_current(monkeypatch,tmp_path):
    older = _approved()
    older["patch"] = "16.16"
    f=tmp_path/"stale.json"
    f.write_text(json.dumps(clean(older)),encoding="utf8")
    monkeypatch.setattr(aggregate,"SOLOQ_PATH",f)
    aggregate._read.cache_clear()
    aggregate._indices.cache_clear()
    engine.load_lane_matchups.cache_clear()
    engine.load_solo_queue_winrates.cache_clear()
    try:
        assert not aggregate.approved_matchups("16.20")
        result = engine.load_lane_matchups()
        entry=result.get("matchups",{}).get("Mid",{}).get("Ahri",{}).get("responses",{}).get("Veigar",{})
        assert entry.get("source") != "Approved Example Vendor"
        assert aggregate.approved_pair_signal("ADC","Jinx","Support","Lulu","16.20")["games"]==0
    finally:
        engine.load_lane_matchups.cache_clear()
        engine.load_solo_queue_winrates.cache_clear()
        aggregate._read.cache_clear()
        aggregate._indices.cache_clear()

def test_low_volume_source_does_not_steer_advice(tmp_path,monkeypatch):
    sample=_approved()
    sample["matchups"][0]["games"]=4
    sample["pairs"][0]["games"]=7
    sample["champions"][0]["games"]=15
    with pytest.raises(ValueError,match="no usable"):
        clean(sample)
