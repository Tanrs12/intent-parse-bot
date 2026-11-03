"""One-sided enemy damage rewards our durable frontline; the app's own ranking sees enemy locks."""
from __future__ import annotations

import pytest

from league_comp_engine import enemy_damage_lean, live_enemy_draft_adjustment, load_meta

ALL_PHYSICAL = ["Jayce", "Kha'Zix", "Zed", "Caitlyn", "Pyke"]
MIXED = ["Jayce", "Lillia", "Syndra", "Caitlyn", "Lulu"]
ALL_MAGIC = ["Gwen", "Lillia", "Syndra", "Ziggs", "Lulu"]
OURS = {"Top": "Malphite", "Jungle": "Sejuani", "Mid": "Ahri", "ADC": "Jinx", "Support": "Leona"}


@pytest.fixture(scope="module")
def meta():
    return load_meta()


def test_lean_needs_three_known_locks(meta):
    assert enemy_damage_lean(["Zed", "Caitlyn"], meta)["type"] is None
    three = enemy_damage_lean(["Zed", "Caitlyn", "Pyke"], meta)
    assert three["type"] == "physical" and three["count"] == 3 and three["locked"] == 3
    assert three["strength"] == pytest.approx(0.6)          # 3 of 5 seats revealed


def test_lean_thresholds_and_scaling(meta):
    full = enemy_damage_lean(ALL_PHYSICAL, meta)
    assert full == {"type": "physical", "count": 5, "locked": 5, "strength": 1.0}
    four = enemy_damage_lean(["Jayce", "Kha'Zix", "Zed", "Caitlyn", "Lulu"], meta)
    assert four["type"] == "physical" and four["strength"] == pytest.approx(0.5)
    assert enemy_damage_lean(["Jayce", "Kha'Zix", "Zed", "Syndra", "Lulu"], meta)["type"] is None   # 3 v 2 is not a lean
    assert enemy_damage_lean(MIXED, meta)["type"] is None
    assert enemy_damage_lean(ALL_MAGIC, meta)["type"] == "magic"


def test_hybrids_and_duplicates_do_not_inflate_the_lean(meta):
    # Kai'Sa is half of each type; a repeated name is one champion.
    lean = enemy_damage_lean(["Zed", "Zed", "Caitlyn", "Kai'Sa", "Pyke"], meta)
    assert lean["locked"] == 4 and lean["count"] == 3.5


def test_physical_enemy_rewards_our_tanks_with_armour_reason(meta):
    into_ad = live_enemy_draft_adjustment(OURS, ALL_PHYSICAL, meta)
    into_mixed = live_enemy_draft_adjustment(OURS, MIXED, meta)
    assert into_ad["adjustment"] > into_mixed["adjustment"]
    assert any("5/5 physical" in r and "armour" in r and "Malphite" in r for r in into_ad["reasons"])
    assert not any("stack" in r for r in into_mixed["reasons"])


def test_magic_enemy_names_magic_resist(meta):
    response = live_enemy_draft_adjustment(OURS, ALL_MAGIC, meta)
    assert any("magic resist" in r for r in response["reasons"])


def test_no_tank_no_bonus_and_bonus_is_bounded(meta):
    squishy = {"Top": "Jayce", "Jungle": "Kha'Zix", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lux"}
    assert live_enemy_draft_adjustment(squishy, ALL_PHYSICAL, meta)["adjustment"] == 0.0
    five_tanks = {"Top": "Malphite", "Jungle": "Sejuani", "Mid": "Galio", "ADC": "Jinx", "Support": "Leona"}
    gain = live_enemy_draft_adjustment(five_tanks, ALL_PHYSICAL, meta)["adjustment"]
    assert 0 < gain <= 3.0 * (1.0 + 0.7 + 0.4) + 1e-9      # only three frontliners count, with diminishing weight


def test_a_tank_beats_a_non_tank_in_the_same_seat(meta):
    with_tank = live_enemy_draft_adjustment(OURS, ALL_PHYSICAL, meta)["adjustment"]
    without = live_enemy_draft_adjustment(dict(OURS, Top="Fiora"), ALL_PHYSICAL, meta)["adjustment"]
    assert with_tank > without


def test_advisor_passes_enemy_locks_to_backend_without_roles(monkeypatch):
    from draftcoach import advisor, backend
    from draftcoach.roster import Roster
    from draftcoach.state import ActiveAction, DraftState, PlayerSlot

    seen = {}

    def fake_recommend(signups, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(backend, "recommend", fake_recommend)
    roles = ["Top", "Jungle", "Mid", "ADC", "Support"]
    ours = [PlayerSlot(cell_id=i, role=r, role_source="lcu") for i, r in enumerate(roles)]
    enemy = [PlayerSlot(cell_id=5 + i, role=r, locked=c) for i, (r, c) in enumerate(zip(roles, ALL_PHYSICAL))]
    state = DraftState(our_team=ours, enemy_team=enemy, active=ActiveAction(kind="pick", ours=True, cell_ids=[0]))
    advisor.advise(state, Roster())
    assert sorted(seen["enemy_champions"]) == sorted(ALL_PHYSICAL)
    assert "enemy_roles" not in seen                          # lane counters stay with the advisor's own edges


def test_why_lists_the_enemy_draft_reason():
    from draftcoach import advisor

    best = {"live_enemy_reasons": ["enemy damage is 5/5 physical: Malphite can stack armour"], "strengths": [], "gaps": []}
    why = advisor._why("Malphite", best, [], 0.0, [], "submitted", 0, [])
    assert "enemy damage is 5/5 physical: Malphite can stack armour" in why


def test_one_tank_gains_less_than_the_strongest_top_lane_signal(meta):
    """The damage lean is a tie-breaker: a single extra tank never outweighs calibrated Top lane evidence."""
    import league_comp_engine as engine
    _, worst = engine.calibrated_matchup("Top", -30.0, 1_000_000)
    _, best = engine.calibrated_matchup("Top", 30.0, 1_000_000)
    squishy = {"Top": "Fiora", "Jungle": "Kha'Zix", "Mid": "Ahri", "ADC": "Jinx", "Support": "Lux"}
    marginal = (live_enemy_draft_adjustment(dict(squishy, Top="Malphite"), ALL_PHYSICAL, meta)["adjustment"]
                - live_enemy_draft_adjustment(squishy, ALL_PHYSICAL, meta)["adjustment"])
    assert 0 < marginal < min(abs(worst), best)
