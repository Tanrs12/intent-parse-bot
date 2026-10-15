from __future__ import annotations

import json

import pytest

from draftcoach import champions as champ_map
from draftcoach import loadfiles as lf
from draftcoach.roster import Roster


@pytest.fixture
def roster() -> Roster:
    return Roster(auto_profiles=False)


def test_champion_resolution_is_forgiving_but_honest():
    assert champ_map.resolve("jarvan iv") == "Jarvan IV"
    assert champ_map.resolve("j4") == "Jarvan IV"
    assert champ_map.resolve("ksante") == "K'Sante"
    assert champ_map.resolve("kaisa") == "Kai'Sa"
    assert champ_map.resolve("Nunu") == "Nunu & Willump"
    assert champ_map.resolve("leesin") == "Lee Sin"
    assert champ_map.resolve("definitely-not-a-champion") is None
    assert champ_map.canonical_name("definitely-not-a-champion") == "definitely-not-a-champion"


def test_json_loadfile_merges_pools_and_reports_changes(roster):
    result = lf.apply_loadfile(roster, {
        "players": [{"name": "Chud", "roles": ["Jungle"], "pools": {"Jungle": ["Lee Sin", {"champion": "Vi", "priority": 1}, "Nocturn"]}}],
        "default_pools": {"Top": ["Ornn", "Gnar"]},
        "ban_priority": ["Rumble", {"champion": "Yone", "weight": 2}],
        "never_pick": ["Teemo"],
    }, "week1.json")
    assert result.ok and not result.warnings
    chud = roster.find_player(name="chud")
    assert chud is not None
    assert chud.champions("Jungle") == ["Vi", "Lee Sin", "Nocturne"]   # priority 1 sorts first, prefix match resolved
    assert chud.priority("Jungle", "Vi") == 1
    assert [e.champion for e in roster.default_pools["Top"]] == ["Ornn", "Gnar"]
    assert {b["champion"]: b["weight"] for b in roster.ban_priority} == {"Rumble": 1.0, "Yone": 2.0}
    assert roster.never_pick == ["Teemo"]
    assert any("added player Chud" in a for a in result.applied)
    assert roster.history[-1]["name"] == "week1.json"

    # Second merge: priority update, removal, unknown champion warning.
    second = lf.apply_loadfile(roster, {
        "players": [{"name": "chud", "pools": {"Jungle": [{"champion": "Lee Sin", "priority": 2}, "Zzzzzz"]},
                     "remove": {"Jungle": ["Nocturne"]}}],
    }, "week2.json")
    assert second.ok
    assert any("unknown champion 'Zzzzzz'" in w for w in second.warnings)
    assert chud.champions("Jungle") == ["Vi", "Lee Sin"]
    assert chud.priority("Jungle", "Lee Sin") == 2


def test_replace_mode_only_touches_listed_roles(roster):
    lf.apply_loadfile(roster, {"players": [{"name": "Atomic", "roles": ["Top", "Mid"],
                                            "pools": {"Top": ["Ornn", "Gnar"], "Mid": ["Ahri"]}}]}, "a.json")
    lf.apply_loadfile(roster, {"players": [{"name": "Atomic", "mode": "replace", "pools": {"Top": ["Jax"]}}]}, "b.json")
    atomic = roster.find_player(name="Atomic")
    assert atomic.champions("Top") == ["Jax"]
    assert atomic.champions("Mid") == ["Ahri"]


def test_csv_loadfile_covers_pools_scouting_bans_and_avoid(roster):
    text = (
        "kind,player,role,champion,priority,action,note\n"
        "pool,Chud,Jungle,Lee Sin,1,add,signature\n"
        "pool,Chud,Jungle,Vi,,add,\n"
        "pool,,ADC,Jinx,,add,\n"
        "scout,RivalMid,Mid,Yone,1,add,one-trick\n"
        "ban,,,Aurora,3,add,\n"
        "avoid,,,Teemo,,add,\n"
        "avoid,Chud,,Yuumi,,add,\n"
    )
    result = lf.import_text(roster, text, "week.csv")
    assert result.ok, result.errors
    chud = roster.find_player(name="Chud")
    assert chud.champions("Jungle") == ["Lee Sin", "Vi"] and chud.avoid == ["Yuumi"]
    assert [e.champion for e in roster.default_pools["ADC"]] == ["Jinx"]
    rival = roster.find_opponent(name="RivalMid")
    assert rival.champions("Mid") == ["Yone"] and rival.roles == ["Mid"]
    assert roster.ban_priority[0] == {"champion": "Aurora", "weight": 3.0, "note": ""}
    assert roster.never_pick == ["Teemo"]

    removal = lf.import_text(roster, "kind,player,role,champion,action\npool,Chud,Jungle,Vi,remove\n", "rm.csv")
    assert removal.ok and chud.champions("Jungle") == ["Lee Sin"]


def test_unparseable_and_unknown_documents_fail_cleanly(roster):
    bad = lf.import_text(roster, "{not json", "bad.json")
    assert not bad.ok and "could not parse" in bad.errors[0]
    unknown = lf.import_document(roster, {"foo": 1}, "x.json")
    assert not unknown.ok and unknown.kind == "unknown"
    assert roster.history[-1]["ok"] is False


def test_export_round_trips_through_import(roster):
    lf.apply_loadfile(roster, {
        "players": [{"name": "Chud", "roles": ["Jungle"], "pools": {"Jungle": [{"champion": "Lee Sin", "priority": 1}, "Vi"]}, "avoid": ["Teemo"]}],
        "opponents": [{"name": "Rival", "team": "Rivals", "pools": {"Mid": ["Yone"]}}],
        "ban_priority": ["Rumble"], "never_pick": ["Teemo"], "lineup": ["Chud"], "enemy_lineup": ["Rival"],
    }, "src.json")
    exported = lf.export_loadfile(roster)
    fresh = Roster(auto_profiles=False)
    result = lf.apply_loadfile(fresh, json.loads(json.dumps(exported)), "exported.json")
    assert result.ok
    assert fresh.find_player(name="Chud").champions("Jungle") == ["Lee Sin", "Vi"]
    assert fresh.find_opponent(name="Rival").champions("Mid") == ["Yone"]
    assert fresh.lineup[0] == "chud" and fresh.enemy_lineup[0] == "rival"
    csv_text = lf.export_csv(roster)
    again = Roster(auto_profiles=False)
    assert lf.import_text(again, csv_text, "roster.csv").ok
    assert again.find_player(name="Chud").priority("Jungle", "Lee Sin") == 1


def test_roster_persistence_round_trip(tmp_path, roster):
    lf.apply_loadfile(roster, {"players": [{"name": "Chud", "roles": ["Mid"], "pools": {"Mid": ["Ahri"]}}]}, "p.json")
    path = tmp_path / "roster.json"
    roster.save(path)
    loaded = Roster.load(path, auto_profiles=False)
    assert loaded.find_player(name="Chud").champions("Mid") == ["Ahri"]
    assert loaded.history and loaded.history[-1]["name"] == "p.json"


def test_engine_data_snapshot_is_detected_and_installed(tmp_path, roster):
    engine_dir = tmp_path / "engine"
    (engine_dir / "data").mkdir(parents=True)
    (engine_dir / "data" / "league_lane_matchups.json").write_text('{"schema_version": 1, "matchups": {}}', encoding="utf-8")
    snapshot = {"schema_version": 1, "generated_at": "2026-10-15", "source": {}, "common_enemies": {}, "matchups": {"Top": {}}}
    kind, filename = lf.detect_kind(snapshot)
    assert (kind, filename) == ("engine-data", "league_lane_matchups.json")
    result = lf.import_document(roster, snapshot, "fresh.json", engine_dir=engine_dir, backup_dir=tmp_path / "backups")
    assert result.ok and result.data_changed
    assert json.loads((engine_dir / "data" / "league_lane_matchups.json").read_text(encoding="utf-8"))["matchups"] == {"Top": {}}
    assert list((tmp_path / "backups").glob("league_lane_matchups.*.json"))


def test_watcher_only_reports_new_or_changed_files(tmp_path):
    folder = tmp_path / "loadfiles"
    folder.mkdir()
    (folder / "a.json").write_text("{}", encoding="utf-8")
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    watcher = lf.LoadfileWatcher(folder, tmp_path / "seen.json")
    pending = watcher.pending()
    assert [p.name for p in pending] == ["a.json"]
    watcher.mark(pending[0])
    assert watcher.pending() == []
    (folder / "a.json").write_text('{"players": []}', encoding="utf-8")
    assert [p.name for p in watcher.pending()] == ["a.json"]


def test_familiarity_words_are_kept_separately_from_preference(roster):
    result = lf.apply_loadfile(roster, {"players": [{"name": "Cian", "roles": ["Jungle"], "pools": {"Jungle": [
        {"champion": "Sejuani", "familiarity": "comfort"}, {"champion": "Vi", "familiarity": "refresh"},
        {"champion": "Amumu", "familiarity": "developing"}, {"champion": "Olaf", "familiarity": "whatever"}]}}]}, "fam.json")
    assert result.ok and any("unknown familiarity" in w for w in result.warnings)
    cian = roster.find_player(name="Cian")
    assert [cian.familiarity("Jungle", c) for c in ("Sejuani", "Vi", "Amumu", "Olaf")] == ["comfort", "refresh", "developing", ""]
    assert all(cian.priority("Jungle", c) == 0 for c in ("Sejuani", "Vi", "Amumu", "Olaf"))
