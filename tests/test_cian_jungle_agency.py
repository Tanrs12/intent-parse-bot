"""Cian Jungle playstyle should be taken into account without inventing picks."""
import league_comp_engine as engine

CIAN = 139837324438536193


def player(uid=CIAN, pool=None):
    return {
        "user_id": uid,
        "display_name": "Cian" if uid == CIAN else "Other player",
        "roles": ["Jungle"],
        "role_priority": {"Jungle": 1},
        "champions": {"Jungle": list(pool or ["Zac", "Diana", "Gragas", "Lee Sin"])},
    }


def test_playmakers_outscore_setup_in_cian_submitted_pool():
    meta = engine.load_meta()
    cian = player()
    zac, _ = engine._player_jungle_agency_adjustment(cian, "Jungle", "Zac", meta)
    diana, _ = engine._player_jungle_agency_adjustment(cian, "Jungle", "Diana", meta)
    gragas, _ = engine._player_jungle_agency_adjustment(cian, "Jungle", "Gragas", meta)
    lee, _ = engine._player_jungle_agency_adjustment(cian, "Jungle", "Lee Sin", meta)
    assert diana > zac + 2
    assert gragas > zac + 2
    assert lee > zac + 2
    assert -2.4 <= zac <= 1.65
    assert all(-2.4 <= value <= 1.65 for value in (diana, gragas, lee))


def test_no_unselected_picks_and_other_players_remain_neutral():
    meta = engine.load_meta()
    cian = player(pool=["Diana"])
    unselected_bonus, actual_demand = engine._player_jungle_agency_adjustment(
        cian, "Jungle", "Gragas", meta
    )
    assert unselected_bonus == 0
    assert actual_demand == engine._champion_carry_demand("Jungle", "Gragas", meta)
    for champion in ("Diana", "Zac", "Gragas"):
        other_bonus, _ = engine._player_jungle_agency_adjustment(
            player(uid=332490596004659201), "Jungle", champion, meta
        )
        assert other_bonus == 0
    other_role_bonus, _ = engine._player_jungle_agency_adjustment(
        cian, "Top", "Diana", meta
    )
    assert other_role_bonus == 0


def test_player_fit_reflects_selected_jungle_agency_without_overriding_all_rules(monkeypatch):
    meta = engine.load_meta()
    monkeypatch.setattr(engine, "load_player_score_index", lambda: {})
    # Remove account-history differences so this isolates the preference only.
    monkeypatch.setattr(engine, "_champion_comfort", lambda *_args: 5.0)
    monkeypatch.setattr(engine, "_champion_ownership", lambda *_args: 5.0)
    monkeypatch.setattr(engine, "_mastery_points_for", lambda *_args: 0)
    monkeypatch.setattr(engine, "_profile_for", lambda *_args: {})
    roles = engine.ROLES
    ids = {"Top": 10, "Jungle": CIAN, "Mid": 11, "ADC": 12, "Support": 13}
    assignment = {
        role: {"user_id": ids[role], "display_name": role,
               "roles": [role],
               "champions": {role: (["Zac", "Gragas", "Diana"] if role == "Jungle" else [role])}}
        for role in roles
    }
    base = {"Top": "Ornn", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lulu"}
    def fit(jungler):
        return engine._player_fit_adjustment(
            assignment, {**base, "Jungle": jungler}, [], meta
        )
    zac, gragas, diana = fit("Zac"), fit("Gragas"), fit("Diana")
    assert gragas["role_details"]["Jungle"]["adjustment"] > zac["role_details"]["Jungle"]["adjustment"] + 2
    assert diana["role_details"]["Jungle"]["adjustment"] > zac["role_details"]["Jungle"]["adjustment"] + 2
    assert all(abs(row["role_details"]["Jungle"]["agency_preference_adjustment"]) <= 2.4
               for row in (zac, gragas, diana))
