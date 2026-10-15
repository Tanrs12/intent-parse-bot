from __future__ import annotations

import json
from pathlib import Path

import pytest

from draftcoach import advisor
from draftcoach import loadfiles as lf
from draftcoach import state as ds
from draftcoach.draft_order import ManualDraft
from draftcoach.roster import Roster

TEAM_FILE = Path(__file__).resolve().parent.parent / "loadfiles" / "flexers-team.json"


@pytest.fixture
def roster() -> Roster:
    r = Roster(auto_profiles=True)
    result = lf.import_path(r, TEAM_FILE)
    assert result.ok, result.errors
    return r


def test_team_loadfile_links_profiles_and_loads_the_playbook(roster):
    cian = roster.find_player(riot_id="Schyaitale#6131")
    assert cian is not None and cian.name == "Cian" and cian.user_id == 139837324438536193
    assert cian.priority("Jungle", "Sejuani") == 1 and cian.priority("Top", "Camille") == 1
    assert roster.find_player(riot_id="CHATBANCHARLIE#7782").name == "Aristo"
    assert len(roster.playbook) == 6 and {c["id"] for c in roster.playbook} >= {"original-five", "airborne", "sidelane"}
    plan = next(c for c in roster.playbook if c["id"] == "original-five")
    assert plan["picks"]["Mid"]["champion"] == "Yasuo" and plan["picks"]["Mid"]["player_name"] == "Aristo" and plan["picks"]["Mid"]["familiarity"] == "comfort"
    assert [roster.players[k].name for k in roster.lineup] == ["Cian", "Sasha", "Aristo", "Jack", "Jezz"]
    exported = lf.export_loadfile(roster)
    assert len(exported["playbook"]) == 6 and exported["playbook"][0]["picks"]["Top"]["champion"] == "Gnar"
    again = Roster(auto_profiles=False)
    assert lf.apply_loadfile(again, json.loads(json.dumps(exported)), "re").ok and len(again.playbook) == 6


def test_plans_track_progress_and_die_when_a_champion_is_gone(roster):
    draft = ManualDraft(side="blue")
    for c in ["Rumble", "Vi", "Zed", "Jinx", "Aurora", "Kalista"]:
        draft.act(c)
    draft.act("Yasuo", role="Mid")             # B1
    state = ds.state_from_manual_draft(draft, roster)
    g = advisor.advise(state, roster)
    by_id = {p["id"]: p for p in g["plans"]}
    assert by_id["original-five"]["viable"] and by_id["original-five"]["progress"] == 1
    assert by_id["airborne"]["viable"] and by_id["airborne"]["progress"] == 1
    assert not by_id["ste-refresh"]["viable"]            # wants Anivia mid, but we locked Yasuo
    assert all(p["score"] <= 3.0 for p in g["plans"])    # bounded preference
    # A plan pick in the plan's role gets the bonus and says so.
    draft.act("Lee Sin"); draft.act("Syndra")           # enemy R1 R2
    state = ds.state_from_manual_draft(draft, roster)
    g = advisor.advise(state, roster)
    ashe = next((p for p in g["picks"] if p["champion"] == "Ashe" and p["role"] == "ADC"), None)
    assert ashe is not None and ashe["plan"] and ashe["plan"]["bonus"] > 0 and "plan" in " ".join(ashe["why"])
    # Enemy locks Ashe: the plans that need her lose viability unless a substitution exists.
    draft.undo(); draft.act("Ashe")                      # R2 takes Ashe
    state = ds.state_from_manual_draft(draft, roster)
    g = advisor.advise(state, roster)
    by_id = {p["id"]: p for p in g["plans"]}
    assert not by_id["original-five"]["viable"] and any("Ashe" in b["reason"] for b in by_id["original-five"]["blocked"])
    assert by_id["airborne"]["viable"]                   # Aphelios bot is unaffected
    assert by_id["airborne"]["variants_open"] >= 1 and by_id["airborne"]["variant"] is not None
    assert by_id["airborne"]["reseat"] and by_id["airborne"]["score"] < by_id["original-five"]["score"]   # Jack top, Cian bot


def test_committing_to_a_plan_makes_only_that_plan_pull(roster):
    draft = ManualDraft(side="blue")
    for c in ["Rumble", "Vi", "Zed", "Jinx", "Aurora", "Kalista"]:
        draft.act(c)
    draft.act("Yasuo", role="Mid"); draft.act("Lee Sin"); draft.act("Syndra")
    roster.settings["committed_plan"] = "airborne"
    g = advisor.advise(ds.state_from_manual_draft(draft, roster), roster)
    assert g["committed_plan"] == {"id": "airborne", "name": next(p["name"] for p in roster.playbook if p["id"] == "airborne"), "viable": True}
    assert [p["id"] for p in g["plans"] if p["committed"]] == ["airborne"]
    boosted = {p["champion"] for p in g["picks"] if p["plan"]}
    assert boosted and all(p["plan"]["id"] == "airborne" for p in g["picks"] if p["plan"])
    assert "Ashe" not in boosted                              # original-five's bot pick no longer pulls
    roster.settings["committed_plan"] = "ste-refresh"         # wants Anivia mid, but Yasuo is locked
    g2 = advisor.advise(ds.state_from_manual_draft(draft, roster), roster)
    assert any("is blocked" in a["text"] for a in g2["alerts"]) and g2["committed_plan"]["viable"] is False
    assert any(p["plan"] for p in g2["picks"])                # fell back to every open plan
