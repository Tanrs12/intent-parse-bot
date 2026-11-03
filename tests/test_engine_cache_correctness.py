"""The installed score cache must never change a result (Codex review, section B).

Reproduces the two failures the reviewer demonstrated (a stale display name
served from the fit cache; a mutated returned list contaminating the next hit)
and the key-completeness gaps (champion insertion order, default-meta identity,
submitted-pool membership, ADC priority, data-file replacement, the date gate).
"""
from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

import league_comp_engine as engine
from draftcoach import engine_cache

CIAN = 139837324438536193
COMP = {"Top": "Gnar", "Jungle": "Sejuani", "Mid": "Orianna", "ADC": "Jinx", "Support": "Nautilus"}
SIGNUPS = Path(__file__).resolve().parent.parent / "data" / "live_flex_signups.json"


def _player(uid: int, name: str, role: str, pool: list[str], priority: int = 1) -> dict:
    return {
        "user_id": uid,
        "display_name": name,
        "roles": [role],
        "role_priority": {role: priority},
        "champions": {role: list(pool)},
    }


def _assignment(adc_priority: int = 1) -> dict:
    return {
        "Top": _player(9001, "Alice", "Top", ["Gnar"]),
        "Jungle": _player(9002, "Bob", "Jungle", ["Sejuani"]),
        "Mid": _player(9003, "Cara", "Mid", ["Orianna"]),
        "ADC": _player(9004, "Dana", "ADC", ["Jinx"], priority=adc_priority),
        "Support": _player(9005, "Eve", "Support", ["Nautilus"]),
    }


@pytest.fixture
def installed():
    engine_cache.install()
    yield engine_cache.stats


@pytest.fixture
def meta():
    return engine.load_meta()


def _archetypes(meta: dict) -> list:
    return engine_cache._original_score_comp(COMP, meta)["archetypes"]


# (i) the reviewer's first reproduction: a display-name-only change returned the stale name.
def test_display_name_change_is_not_served_from_cache(installed, meta):
    archetypes = _archetypes(meta)
    first = engine._player_fit_adjustment(_assignment(), COMP, archetypes, meta)
    role = first["carry_plan"]["role"]
    renamed = _assignment()
    renamed[role]["display_name"] = "Renamed Player"

    second = engine._player_fit_adjustment(renamed, COMP, archetypes, meta)

    assert second["carry_plan"]["display_name"] == "Renamed Player"
    assert first["carry_plan"]["display_name"] != "Renamed Player"
    assert second == engine_cache._original_player_fit(renamed, COMP, archetypes, meta)
    assert installed["fit_misses"] == 2 and installed["fit_hits"] == 0


# (ii) the reviewer's second reproduction: appending to a returned list leaked into the next hit.
def test_mutating_a_returned_score_does_not_contaminate_the_next_hit(installed, meta):
    first = engine.score_comp(COMP, meta)
    first["strengths"].append("poisoned")
    first["damage_profile"]["physical_sources"].append("Poison")
    first["bot_lane_reasons"] = ["rewritten"]

    second = engine.score_comp(COMP, meta)

    assert installed["score_hits"] == 1
    assert "poisoned" not in second["strengths"]
    assert "Poison" not in second["damage_profile"]["physical_sources"]
    assert second == engine_cache._original_score_comp(COMP, meta)


def test_mutating_a_returned_fit_does_not_contaminate_the_next_hit(installed, meta):
    archetypes = _archetypes(meta)
    first = engine._player_fit_adjustment(_assignment(), COMP, archetypes, meta)
    first["notes"].append("poisoned")
    first["role_details"]["Top"]["carry_index"] = -1.0
    first["carry_options"].clear()

    second = engine._player_fit_adjustment(_assignment(), COMP, archetypes, meta)

    assert installed["fit_hits"] == 1
    assert "poisoned" not in second["notes"]
    assert second == engine_cache._original_player_fit(_assignment(), COMP, archetypes, meta)


# (iii) champion insertion order is part of the key because the engine's output depends on it.
def test_champion_insertion_order_is_part_of_the_key(installed, meta):
    reversed_comp = dict(reversed(list(COMP.items())))
    forward = engine.score_comp(COMP, meta)
    backward = engine.score_comp(reversed_comp, meta)

    assert installed["score_misses"] == 2 and installed["score_hits"] == 0
    assert forward == engine_cache._original_score_comp(COMP, meta)
    assert backward == engine_cache._original_score_comp(reversed_comp, meta)
    # The uncached engine really does differ by traversal order (source lists),
    # so sharing one entry would have served the wrong list to one of them.
    assert forward["damage_profile"]["physical_sources"] != backward["damage_profile"]["physical_sources"]
    assert forward["score"] == backward["score"]


# (iv) a replaced default meta object must not be served the old cached result.
def test_replaced_default_meta_is_not_served_from_cache(installed, meta, monkeypatch):
    before = engine.score_comp(COMP)  # meta=None -> load_meta()
    replaced = deepcopy(meta)
    for champion in COMP.values():
        traits = replaced.setdefault("champion_traits", {}).setdefault(champion, {})
        traits.setdefault("ratings", {})["control"] = 0
    monkeypatch.setattr(engine, "load_meta", lambda path=None: replaced)

    after = engine.score_comp(COMP)

    assert installed["score_misses"] == 2 and installed["score_hits"] == 0
    assert after == engine_cache._original_score_comp(COMP, replaced)
    assert after != before
    assert after["solo_queue_score"] == before["solo_queue_score"]  # same data otherwise


def test_identity_key_is_not_fooled_by_an_equal_but_different_object():
    a, b = {"x": 1}, {"x": 1}
    assert engine_cache._IdentityKey(a) == engine_cache._IdentityKey(a)
    assert engine_cache._IdentityKey(a) != engine_cache._IdentityKey(b)
    assert hash(engine_cache._IdentityKey(a)) == hash(engine_cache._IdentityKey(a))


# The fit key must carry every assignment field the scorer reads, not just user ids.
def test_fit_key_covers_submitted_pool_membership(installed, meta):
    archetypes = _archetypes(meta)
    in_pool = _assignment()
    in_pool["Jungle"] = _player(CIAN, "Cian", "Jungle", ["Sejuani", "Lee Sin"])
    out_of_pool = deepcopy(in_pool)
    out_of_pool["Jungle"]["champions"]["Jungle"] = ["Lee Sin"]

    first = engine._player_fit_adjustment(in_pool, COMP, archetypes, meta)
    second = engine._player_fit_adjustment(out_of_pool, COMP, archetypes, meta)

    assert installed["fit_misses"] == 2 and installed["fit_hits"] == 0
    assert first == engine_cache._original_player_fit(in_pool, COMP, archetypes, meta)
    assert second == engine_cache._original_player_fit(out_of_pool, COMP, archetypes, meta)
    assert (first["role_details"]["Jungle"]["agency_preference_adjustment"]
            != second["role_details"]["Jungle"]["agency_preference_adjustment"])


def test_fit_key_covers_effective_adc_priority(installed, meta):
    archetypes = _archetypes(meta)
    engine._player_fit_adjustment(_assignment(adc_priority=1), COMP, archetypes, meta)
    engine._player_fit_adjustment(_assignment(adc_priority=2), COMP, archetypes, meta)
    engine._player_fit_adjustment(_assignment(adc_priority=3), COMP, archetypes, meta)
    assert installed["fit_misses"] == 2 and installed["fit_hits"] == 1  # P2 and P3 share one bucket
    flagged = _assignment(adc_priority=1)
    flagged["ADC"]["_role_priority_level"] = 2
    engine._player_fit_adjustment(flagged, COMP, archetypes, meta)
    assert installed["fit_misses"] == 2 and installed["fit_hits"] == 2


def test_fit_input_key_matches_scorer_reads():
    key = engine_cache._fit_input_key(_assignment(adc_priority=2), COMP)
    assert key == (
        (
            ("Top", 9001, "Alice", True),
            ("Jungle", 9002, "Bob", True),
            ("Mid", 9003, "Cara", True),
            ("ADC", 9004, "Dana", True),
            ("Support", 9005, "Eve", True),
        ),
        True,
    )


def test_clear_drops_entries_and_bumps_generation(installed, meta):
    engine.score_comp(COMP, meta)
    generation = installed["generation"]
    engine_cache.clear()
    assert installed["generation"] == generation + 1
    engine.score_comp(COMP, meta)
    assert installed["score_misses"] == 1 and installed["score_hits"] == 0


def test_replaced_data_file_invalidates_entries(installed, meta, tmp_path, monkeypatch):
    recent = tmp_path / "league_recent_pro.json"
    shutil.copy2(engine.RECENT_PRO_PATH, recent)
    monkeypatch.setattr(engine, "RECENT_PRO_PATH", recent)

    engine.score_comp(COMP, meta)
    engine.score_comp(COMP, meta)
    assert installed["score_misses"] == 1 and installed["score_hits"] == 1

    stat = recent.stat()
    os.utime(recent, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    engine.score_comp(COMP, meta)
    assert installed["score_misses"] == 2 and installed["score_hits"] == 1


def test_new_day_invalidates_entries(installed, meta, monkeypatch):
    class Day1(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 3)

    class Day2(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 4)

    monkeypatch.setattr(engine_cache, "date", Day1)
    engine.score_comp(COMP, meta)
    engine.score_comp(COMP, meta)
    monkeypatch.setattr(engine_cache, "date", Day2)
    engine.score_comp(COMP, meta)
    assert installed["score_misses"] == 2 and installed["score_hits"] == 1


def test_load_recent_pro_reuses_one_bound_cache_tuple():
    first = engine.load_recent_pro()
    second = engine.load_recent_pro()
    assert first is second
    assert engine._RECENT_PRO_CACHE[1] is first


def test_installed_cache_changes_no_live_draft_result():
    try:
        rows = json.loads(SIGNUPS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    if len(rows) < 5:
        pytest.skip("live_flex_signups.json does not hold five signed-up players")
    from league_draft_backend import recommend_live_drafts

    five = rows[:5]
    kwargs = dict(side="blue", rotation_token="cache-equivalence", enemy_champions=["Ahri"],
                  enemy_roles={"Mid": "Ahri"}, unavailable={"Zac"})
    uncached = recommend_live_drafts(five, **kwargs)
    engine_cache.install()
    cold = recommend_live_drafts(five, **kwargs)
    warm = recommend_live_drafts(five, **kwargs)
    assert engine_cache.stats["score_hits"] > 0 and engine_cache.stats["fit_hits"] > 0
    assert json.dumps(cold, sort_keys=True, default=sorted) == json.dumps(uncached, sort_keys=True, default=sorted)
    assert json.dumps(warm, sort_keys=True, default=sorted) == json.dumps(uncached, sort_keys=True, default=sorted)
