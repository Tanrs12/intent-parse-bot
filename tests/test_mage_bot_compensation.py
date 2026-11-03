"""Enemy ADC -> mage Bot, then restore physical carry in any open lane."""
from copy import deepcopy

import pytest

from adaptive_compensation_search import search_all_lane_companions
from adaptive_draft import select_adaptive_comp

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
BASE = {"Top": "Kennen", "Jungle": "Lillia", "Mid": "Orianna",
        "ADC": "Jinx", "Support": "Nautilus"}
OWNER_IDS = {role: i + 1 for i, role in enumerate(ROLES)}
TRAITS = {
    "Kennen": {"adaptive_type": "MAGIC_DAMAGE", "ratings": {"damage": 3, "toughness": 1}},
    "Lillia": {"adaptive_type": "MAGIC_DAMAGE", "ratings": {"damage": 3, "toughness": 1}},
    "Orianna": {"adaptive_type": "MAGIC_DAMAGE", "ratings": {"damage": 3, "toughness": 1}},
    "Jinx": {"adaptive_type": "PHYSICAL_DAMAGE", "roles": ["MARKSMAN"],
             "ratings": {"damage": 3, "toughness": 1}},
    "Nautilus": {"adaptive_type": "MAGIC_DAMAGE", "roles": ["TANK", "VANGUARD"],
                 "ratings": {"damage": 1, "toughness": 3}},
}
for champion in ("Swain", "Hwei", "Vel'Koz"):
    TRAITS[champion] = {
        "adaptive_type": "MAGIC_DAMAGE", "roles": ["MAGE"],
        "ratings": {"damage": 3, "toughness": 1},
    }
for champion in ("Tristana", "Vayne", "Graves", "Kindred"):
    TRAITS[champion] = {
        "adaptive_type": "PHYSICAL_DAMAGE", "roles": ["MARKSMAN"],
        "ratings": {"damage": 3, "toughness": 1},
    }


def draft(champions, *, score=92, bot_score=8.5, edge=None, owners=None):
    return {
        "champions": dict(champions), "player_ids": dict(owners or OWNER_IDS),
        "score": score, "rank_score": score, "identity": "dive",
        "team_plan_score": 8.3, "bot_lane_score": bot_score,
        "mid_jungle_score": 7.0, "live_lane_edges": [edge] if edge else [],
    }


@pytest.mark.parametrize(
    "mage,companion,physical",
    [
        ("Swain", "Mid", "Tristana"),
        ("Swain", "Top", "Vayne"),
        ("Hwei", "Jungle", "Graves"),
        ("Vel'Koz", "Jungle", "Kindred"),
    ],
)
def test_confirmed_enemy_adc_can_trigger_bot_mage_plus_physical_carry(
    mage, companion, physical
):
    original = draft(BASE)
    changed = dict(BASE, ADC=mage, **{companion: physical})
    counter = {"role": "ADC", "enemy": "EnemyADC",
               "games": 2400, "points": 2.7, "usable_for_ranking": True}
    proposed = draft(changed, score=95.0, bot_score=7.25, edge=counter)

    # As in the real draft backend, this only returns a legal player-owned
    # selection if both exact role pools include the new picks.
    assigned_pools = {role: [pick] for role, pick in BASE.items()}
    assigned_pools["ADC"].append(mage)
    assigned_pools[companion].append(physical)
    fetched = []
    def shared_backend(**kwargs):
        required = kwargs.get("required_champions") or {}
        fetched.append(dict(required))
        if any(proposed["champions"].get(role) != champion
               for role, champion in required.items()):
            return {"recommendations": []}
        if any(proposed["champions"][role] not in assigned_pools[role]
               for role in ROLES):
            return {"recommendations": []}
        return {"recommendations": [proposed]}

    output = search_all_lane_companions(
        {"recommendations": [original]}, chosen=BASE,
        known_enemy_lanes={"ADC": "EnemyADC"}, actual_locks={},
        hard_required={}, unavailable=set(),
        backend_kwargs={"identity": "dive"}, backend=shared_backend,
        champion_traits=TRAITS, global_counter_rules={},
    )
    winner = output["recommendations"][0]
    assert winner["champions"]["ADC"] == mage
    assert winner["champions"][companion] == physical
    assert "physical damage" in winner["adaptive_reasons"]["ADC"]
    assert winner["player_ids"] == OWNER_IDS
    assert output["compensation_search"]["selected_changes"] == [
        role for role in ROLES if role in {"ADC", companion}
    ]
    assert len(fetched) == 4


def test_only_real_player_submitted_bot_mages_and_companion_picks():
    original = draft(BASE)
    counter = {"role": "ADC", "enemy": "EnemyADC", "games": 1800,
               "points": 2.6, "usable_for_ranking": True}
    alt = draft({**BASE, "ADC": "Hwei", "Jungle": "Kindred"},
                score=96, bot_score=7.5, edge=counter)
    pools = {role: [champion] for role, champion in BASE.items()}
    pools["Jungle"].append("Kindred")
    # Hwei isn't in this ADC player's submitted role pool.
    def backend(**kwargs):
        if any(alt["champions"][role] not in pools[role] for role in ROLES):
            return {"recommendations": []}
        return {"recommendations": [alt]}
    result = search_all_lane_companions(
        {"recommendations": [original]}, chosen=BASE,
        known_enemy_lanes={"ADC": "EnemyADC"},
        actual_locks={}, hard_required={}, unavailable=set(),
        backend_kwargs={"identity": "dive"}, backend=backend,
        champion_traits=TRAITS, global_counter_rules={},
    )
    assert result["recommendations"][0]["champions"] == BASE


def test_own_adc_locked_cannot_be_moved_to_mage():
    original = draft(BASE)
    result = search_all_lane_companions(
        {"recommendations": [original]}, chosen=BASE,
        known_enemy_lanes={"ADC": "EnemyADC"},
        actual_locks={"ADC": "Jinx"}, hard_required={},
        unavailable=set(), backend_kwargs={},
        backend=lambda **kwargs: (_ for _ in ()).throw(AssertionError(
            "Locked ADC must not trigger another draft")),
        champion_traits=TRAITS, global_counter_rules={},
    )
    assert result["recommendations"][0]["champions"] == BASE


def test_mage_bot_counter_must_be_evidenced_not_assumed():
    base = draft(BASE)
    candidate = draft({**BASE, "ADC": "Swain", "Mid": "Tristana"},
                      score=97, bot_score=7.2,
                      edge={"role": "ADC", "enemy": "EnemyADC",
                            "games": 12, "points": 3.0,
                            "usable_for_ranking": False})
    out = select_adaptive_comp(
        [candidate, base], BASE, enemy_roles={"ADC": "EnemyADC"},
        champion_traits=TRAITS,
    )
    assert out[0]["champions"] == BASE


def test_incidental_tank_magic_does_not_count_as_primary_ap_damage():
    from adaptive_draft import _damage_roles
    assert _damage_roles(BASE, TRAITS, "MAGIC_DAMAGE") == {
        "Top", "Jungle", "Mid"
    }
    assert _damage_roles(BASE, TRAITS, "PHYSICAL_DAMAGE") == {"ADC"}



@pytest.mark.parametrize("mage", ["Swain", "Hwei", "Vel'Koz"])
def test_real_live_backend_allows_submitted_mage_bot_without_autofilling_pools(mage):
    from league_draft_backend import recommend_live_drafts
    roles = ("Top", "Jungle", "Mid", "ADC", "Support")
    planned = {"Top": "Kennen", "Jungle": "Lillia",
               "Mid": "Orianna", "ADC": mage, "Support": "Nautilus"}
    signups = [
        {
            "user_id": 100001 + i,
            "display_name": "MageBotTest" + role,
            "roles": [role],
            "role_priority": {role: 1},
            "champions": {role: [champ]},
        } for i, (role, champ) in enumerate(planned.items())
    ]
    result = recommend_live_drafts(
        signups,
        role_players={role: 100001+i for i, role in enumerate(roles)},
        required_champions={"ADC": mage},
        limit=1,
    )
    rows = result["recommendations"]
    assert rows, f"Submitted {mage} Bot was wrongly removed from legal pool"
    assert rows[0]["champions"]["ADC"] == mage
    assert all(rows[0]["champions"][role] == champion
               for role, champion in planned.items())
    assert rows[0]["players"]["ADC"]["user_id"] == 100004
