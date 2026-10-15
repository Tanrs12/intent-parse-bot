from __future__ import annotations

import json

import pytest

from draftcoach import advisor
from draftcoach import loadfiles as lf
from draftcoach import state as ds
from draftcoach.draft_order import ManualDraft
from draftcoach.roster import Roster
from tests.test_state import POOLS, session


@pytest.fixture
def roster() -> Roster:
    r = Roster(auto_profiles=False)
    lf.apply_loadfile(r, {"default_pools": POOLS}, "pools")
    return r


def test_recommendations_exclude_bans_and_locks_and_rank_roles_that_are_up(roster):
    state = ds.state_from_lcu_session(session({0: "Ornn"}, {5: "Ambessa", 6: "Lee Sin"}, ["Rumble", "Yone", "Aurora"], ["Vi", "Jinx", "Orianna"], [1, 2]), roster)
    g = advisor.advise(state, roster)
    assert g["picking_now"] is True and set(g["roles_now"]) == {"Jungle", "Mid"}
    champs = {p["champion"] for p in g["picks"]}
    assert champs and not champs & {"Vi", "Orianna", "Ornn", "Ambessa"}
    assert all(p["role"] in {"Jungle", "Mid"} for p in g["picks"])
    assert g["picks"] == sorted(g["picks"], key=lambda p: (p["source"] != "exhausted", p["rank"], p["flex"]), reverse=True)
    assert all(c["champions"]["Top"] == "Ornn" for c in g["comps"])
    assert not {b["champion"] for b in g["bans"]} & state.unavailable()
    lee = next(c for c in g["counters"] if c["enemy"] == "Lee Sin")
    assert lee["role"] == "Jungle" and all(r["champion"] in POOLS["Jungle"] for r in lee["responses"])
    assert g["headline"].startswith("Pick now:")
    json.dumps(g)


def test_enemy_ban_changes_recommendation_immediately(roster):
    before = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    top = before["picks"][0]["champion"]
    after = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [top], [0]), roster), roster)
    assert top not in {p["champion"] for p in after["picks"]} and after["picks"]


def test_priorities_avoid_and_never_pick_shape_the_ranking():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {"default_pools": POOLS}, "pools")
    base = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [1]), roster), roster)
    jungle_order = [p["champion"] for p in base["picks"] if p["role"] == "Jungle"]
    last = jungle_order[-1]
    lf.apply_loadfile(roster, {"players": [{"name": "Chud", "riot_ids": ["Player1#EUW"], "roles": ["Jungle"],
                                            "pools": {"Jungle": [{"champion": last, "priority": 1}] + [c for c in POOLS["Jungle"] if c != last]},
                                            "avoid": [jungle_order[0]]}],
                               "never_pick": [jungle_order[1]]}, "prio")
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [1]), roster), roster)
    champs = [p["champion"] for p in g["picks"] if p["role"] == "Jungle"]
    assert jungle_order[0] not in champs and jungle_order[1] not in champs
    favourite = next(p for p in g["picks"] if p["champion"] == last)
    assert favourite["priority"] == 1 and favourite["priority_adjustment"] > 0 and "preferred pick" in favourite["why"]
    assert "comfort pick" not in favourite["why"]


def test_scouting_adds_ban_targets_likely_picks_and_counter_awareness():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {
        "default_pools": POOLS,
        "opponents": [{"name": "RivalTop", "riot_ids": ["Player5#EUW"], "roles": ["Top"],
                       "pools": {"Top": [{"champion": "Rumble", "priority": 1}, "Jax"]}}],
        "ban_priority": [{"champion": "Yone", "weight": 2, "note": "policy"}],
    }, "scout")
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    bans = {b["champion"]: b for b in g["bans"]}
    assert "Rumble" in bans and "scouted" in bans["Rumble"]["tags"]
    assert "Yone" in bans and "policy" in bans["Yone"]["tags"]
    assert {(l["champion"], l["role"]) for l in g["likely_enemy"]} >= {("Rumble", "Top"), ("Jax", "Top")}
    assert g["likely_enemy"][0]["champion"] == "Rumble"
    # Once the scouted player locks, their champions stop being "likely".
    g2 = advisor.advise(ds.state_from_lcu_session(session({}, {5: "Jax"}, [], [], [0]), roster), roster)
    assert not any(l["player"] == "RivalTop" for l in g2["likely_enemy"])


def test_flex_picks_and_full_lock_in(roster):
    lf.apply_loadfile(roster, {"mode": "replace", "default_pools": {"Top": ["Gragas", "Ornn"], "Jungle": ["Gragas", "Vi"], "Mid": ["Gragas", "Ahri"]}}, "flex")
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    assert any(f["champion"] == "Gragas" and set(f["roles"]) >= {"Top", "Jungle", "Mid"} for f in g["flex"])
    ours = dict(zip(range(5), ["Ornn", "Vi", "Ahri", "Kai'Sa", "Nautilus"]))
    theirs = dict(zip(range(5, 10), ["Ambessa", "Lee Sin", "Syndra", "Ezreal", "Karma"]))
    done = advisor.advise(ds.state_from_lcu_session(session(ours, theirs, [], [], []), roster), roster)
    assert done["draft_complete"] and done["team"]["score"] > 0 and done["enemy_team_score"]["score"] > 0
    assert done["counters"] == [] and done["phase_kind"] == "complete"


def test_pool_exhausted_falls_back_with_an_alert():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {"default_pools": {**POOLS, "Mid": ["Ahri"]}}, "pools")
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, ["Ahri"], [], [2]), roster), roster)
    assert any("Mid" in a["text"] for a in g["alerts"])
    assert g["picks"] and all(p["champion"] != "Ahri" for p in g["picks"])


def test_exhausted_submitted_pool_is_an_emergency_not_a_primary():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {"default_pools": POOLS, "players": [{"name": "Jezz", "riot_ids": ["Player4#EUW"], "roles": ["Support"],
                                                                     "pools": {"Support": [{"champion": "Lulu", "familiarity": "developing"}]}}]}, "p")
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, ["Lulu"], [], [4]), roster), roster)
    assert any("No prepared Support pick remains" in a["text"] for a in g["alerts"])
    support = [p for p in g["picks"] if p["role"] == "Support"]
    assert support and all(p["source"] == "exhausted" and "emergency pick" in p["why"][0] for p in support)


def test_familiarity_drives_the_score_more_than_preference():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {"default_pools": POOLS, "players": [{"name": "Cian", "riot_ids": ["Player1#EUW"], "roles": ["Jungle"],
        "pools": {"Jungle": [{"champion": "Vi", "familiarity": "developing", "priority": 1}, {"champion": "Sejuani", "familiarity": "comfort"}, "Nocturne"]}}]}, "p")
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [1]), roster), roster)
    by = {p["champion"]: p for p in g["picks"] if p["role"] == "Jungle"}
    assert by["Sejuani"]["familiarity_adjustment"] == 2.0 and "confirmed comfort" in by["Sejuani"]["why"]
    assert by["Vi"]["familiarity_adjustment"] == -3.0 and by["Vi"]["priority_adjustment"] == 0.7
    assert by["Nocturne"]["familiarity_adjustment"] == 0.0
    roster.settings["mode"] = "practice"
    g2 = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [1]), roster), roster)
    assert next(p for p in g2["picks"] if p["champion"] == "Vi")["familiarity_adjustment"] == -1.0


def test_matchup_wording_is_calibrated_and_coverage_aware(roster):
    g = advisor.advise(ds.state_from_lcu_session(session({}, {5: "Rumble"}, [], [], [0]), roster), roster)
    camille = next(p for p in g["picks"] if p["champion"] == "Camille")
    edge = camille["lane"][0]
    assert edge["enemy"] == "Rumble" and 1.9 < edge["adjusted"] < 2.1 and abs(camille["lane_adjustment"]) <= 4.0
    assert any(w.startswith("favourable matchup signal vs locked Rumble") for w in camille["why"])
    assert not any("beats" in w for w in camille["why"])


def test_bans_never_recommend_our_own_prepared_picks(roster):
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    ours = {p["champion"] for p in g["picks"][:2]}
    assert not any(b["champion"] in ours and "ours" not in b["tags"] for b in g["bans"])
    assert all("probability" in b for b in g["bans"])


def test_manual_board_ban_phase_headlines(roster):
    draft = ManualDraft(side="blue")
    g = advisor.advise(ds.state_from_manual_draft(draft, roster), roster)
    assert g["phase_kind"] == "ban" and g["headline"].startswith("Ban now")
    draft.act("Rumble")
    g = advisor.advise(ds.state_from_manual_draft(draft, roster), roster)
    assert g["phase_kind"] == "wait" and "Enemy banning" in g["headline"]


def test_draft_order_changes_how_much_counter_risk_matters(roster):
    # Blue B1: five enemy answers follow, so risk weighs fully and nothing is a counter slot.
    first = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    assert all(p["exposure"] == 1.0 and not p["counter_slot"] for p in first["picks"])
    # Red R5 analogue: everything else locked, enemy has no picks left -> exposure 0, counter slot.
    ours = dict(zip(range(4), ["Ornn", "Vi", "Ahri", "Kai'Sa"]))
    theirs = dict(zip(range(5, 10), ["Ambessa", "Lee Sin", "Syndra", "Ezreal", "Karma"]))
    last = advisor.advise(ds.state_from_lcu_session(session(ours, theirs, [], [], [4]), roster), roster)
    assert last["picks"] and all(p["exposure"] == 0.0 and p["counter_slot"] and p["counter_risk"] == 0.0 for p in last["picks"])


def test_bans_outside_known_enemy_pools_are_marked_unlikely():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {"default_pools": POOLS, "opponents": [
        {"name": "RivalTop", "riot_ids": ["Player5#EUW"], "roles": ["Top"], "pools": {"Top": ["Rumble", "Jax"]}},
        {"name": "RivalMid", "riot_ids": ["Player7#EUW"], "roles": ["Mid"], "pools": {"Mid": ["Yone", "Sylas"]}}]}, "scout")
    roster.settings["bans_shown"] = 12
    g = advisor.advise(ds.state_from_lcu_session(session({0: "Ornn", 2: "Ahri"}, {}, [], [], [1]), roster), roster)
    unlikely = [b for b in g["bans"] if "unlikely" in b["tags"]]
    known = [b for b in g["bans"] if b["champion"] in {"Rumble", "Jax", "Yone", "Sylas"}]
    assert unlikely and all("not seen in their known pools" in b["reasons"] for b in unlikely)
    assert not any("unlikely" in b["tags"] for b in known)


def test_pick_order_exposes_real_flexes_and_safe_blinds_before_counter_prone_solo_lanes():
    import league_comp_engine as engine
    champions = {"Top": "Camille", "Jungle": "Zac", "Mid": "Orianna", "ADC": "Ezreal", "Support": "Zyra"}
    pools = {"Top": ["Camille"], "Jungle": ["Zac"], "Mid": ["Orianna"], "ADC": ["Ezreal"], "Support": ["Zyra"]}
    order = advisor.plan_pick_order(champions, "blue", engine.load_meta(), engine.load_lane_matchups(), pools)
    roles = [r["role"] for r in order]
    assert roles.index("Jungle") < roles.index("Top") and roles.index("Support") < roles.index("Top")
    assert [r["slot"] for r in order] == ["B1", "B2", "B3", "B4", "B5"] and order[0]["status"] == "next"
    red = advisor.plan_pick_order(champions, "red", engine.load_meta(), engine.load_lane_matchups(), pools)
    assert red[-1]["role"] in ("Top", "Mid") and "R5 counter slot" in red[-1]["tags"] and red[-1]["slot"] == "R5"
    # A champion two players can play is a real flex; one player's champion is not.
    assert advisor.real_flex_value("Zac", {"Jungle": ["Zac"], "Top": ["Zac"]}) == 0.5
    assert advisor.real_flex_value("Zac", {"Jungle": ["Zac"]}) == 0.0
    # Locked roles stay at the front in the order they were revealed.
    locked = advisor.plan_pick_order(champions, "blue", engine.load_meta(), engine.load_lane_matchups(), pools, {"Top": "Camille"})
    assert locked[0]["role"] == "Top" and locked[0]["status"] == "locked" and locked[1]["status"] == "next"


def test_guidance_carries_a_pick_order_for_the_target_five(roster):
    g = advisor.advise(ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster), roster)
    assert len(g["pick_order"]) == 5 and g["pick_order_source"] in ("comp",) or g["pick_order_source"].startswith("plan:")
    assert g["pick_order"][0]["status"] == "next" and all("order_adjustment" in p for p in g["picks"])
