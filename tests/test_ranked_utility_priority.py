"""Ranked playbook: captain agency and lower-execution teammate picks."""
import league_comp_engine as engine
import league_flex_core as flex

CIAN = 139837324438536193
STE = 286592835850076162
CHEEKY = 400745307467415553


def _player(uid, roles, priorities=None):
    return {
        "user_id": uid, "roles": roles,
        "role_priority": priorities or {role: 1 for role in roles},
        "champions": {role: [] for role in roles},
    }


def test_cian_order_overrides_stale_signup_and_respects_selected_roles():
    captain = _player(CIAN, ["Jungle", "Top", "Mid", "ADC"],
                      {"Top": 1, "Jungle": 1, "Mid": 1, "ADC": 2})
    assert [flex._role_priority_level(captain, role) for role in
            ("Jungle", "Top", "Mid", "ADC")] == [1, 2, 3, 3]
    costs = [flex._role_preference_penalty(captain, role)
             for role in ("Jungle", "Top", "Mid", "ADC")]
    assert costs == sorted(costs)
    assert len(set(costs)) == 4
    assert flex._role_priority_level(_player(CIAN, ["Jungle"]), "Top") is None
    jack = _player(332490596004659201, ["ADC", "Support", "Top"], {})
    assert [flex._role_priority_level(jack, role) for role in
            ("ADC", "Support", "Top")] == [1, 2, 3]


def test_known_bad_player_picks_are_not_suggested_but_live_locks_are_valid():
    ste = _player(STE, ["ADC"])
    cheeky = _player(CHEEKY, ["Support"])
    assert not engine._ranked_player_pick_allowed(ste, "ADC", "Caitlyn")
    assert not engine._ranked_player_pick_allowed(cheeky, "Support", "Morgana")
    assert engine._ranked_player_pick_allowed(ste, "ADC", "Miss Fortune")
    assert engine._ranked_player_pick_allowed(cheeky, "Support", "Braum")
    assert engine._ranked_player_pick_allowed(
        dict(ste, _forced_live_lock=True), "ADC", "Caitlyn")
    assert engine._ranked_player_pick_allowed(
        cheeky, "Support", "Morgana", preferred="Morgana")


def test_utility_champions_outscore_high_lane_burden_for_secondary_adc():
    def adj(uid, role, champ):
        return engine._ranked_utility_adjustment(uid, role, champ, 40, 40)
    assert adj(STE, "ADC", "Miss Fortune") > adj(STE, "ADC", "Corki") + 2
    assert adj(STE, "ADC", "Jhin") > adj(STE, "ADC", "Caitlyn") + 2
    assert adj(CHEEKY, "Support", "Alistar") > adj(CHEEKY, "Support", "Yuumi")
    assert adj(CHEEKY, "Support", "Braum") > adj(CHEEKY, "Support", "Morgana")
    assert adj(CIAN, "Jungle", "Diana") == 0
