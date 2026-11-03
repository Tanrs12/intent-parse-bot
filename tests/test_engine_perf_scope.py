"""One draft board stats each source file once, and memoised patch signals follow the data."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

import league_comp_engine as engine
from league_draft_backend import plan_game
from league_source_stats import source_mtime_ns, source_stat_scope

SIGNUPS = Path(__file__).resolve().parent.parent / "data" / "live_flex_signups.json"


def _five_players() -> list[dict]:
    try:
        rows = json.loads(SIGNUPS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    if len(rows) < 5:
        pytest.skip("live_flex_signups.json does not hold five signed-up players")
    return rows[:5]


def test_plan_game_stats_each_source_file_at_most_once(monkeypatch):
    five = _five_players()
    plan_game(five, rotation_token="warm")  # fill the lru-cached loaders first

    counts: Counter[str] = Counter()
    original = Path.stat

    def counted(self, *args, **kwargs):
        counts[str(self)] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted)
    plan_game(five, rotation_token="counted")

    assert counts, "the calculation consults source files through Path.stat"
    worst = max(counts.values())
    assert worst <= 2, {path: n for path, n in counts.items() if n > 2}


def test_missing_optional_source_is_statted_once_per_scope(tmp_path, monkeypatch):
    missing = tmp_path / "absent.json"
    calls = Counter()
    original = Path.stat

    def counted(self, *args, **kwargs):
        calls[str(self)] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted)

    @source_stat_scope
    def evaluate():
        for _ in range(50):
            with pytest.raises(OSError):
                source_mtime_ns(missing)

    evaluate()
    assert calls[str(missing)] == 1
    # Outside a scope every check is immediate again.
    with pytest.raises(OSError):
        source_mtime_ns(missing)
    assert calls[str(missing)] == 2


def test_worlds_bridge_memo_follows_reloaded_recent_pro_cache():
    before = engine._worlds_patch_bridge_factor()
    minor_before = engine._recent_pro_patch_minor()
    first = engine.load_recent_pro()
    assert engine._worlds_patch_bridge_factor() == before  # memo hit on the same object

    saved = engine._RECENT_PRO_CACHE
    try:
        # A reset cache (what the live bridge does after a refresh) reloads the file
        # into a different dict; the memo must recompute from it, not serve stale data.
        engine._RECENT_PRO_CACHE = (None, {})
        reloaded = engine.load_recent_pro()
        assert reloaded is not first
        assert engine._worlds_patch_bridge_factor() == before
        assert engine._recent_pro_patch_minor() == minor_before
        assert engine._WORLDS_BRIDGE_MEMO[0] is reloaded
    finally:
        engine._RECENT_PRO_CACHE = saved


def test_worlds_bridge_memo_invalidates_when_the_dataset_changes(monkeypatch):
    def dataset(worlds_games: int) -> dict:
        return {
            "latest_patch": "15.20",
            "games": [{"tournament": "Worlds 2025", "patch": "15.20"}] * worlds_games,
        }

    few = dataset(5)
    many = dataset(20)
    monkeypatch.setattr(engine, "load_recent_pro", lambda: few)
    assert abs(engine._worlds_patch_bridge_factor() - 0.75) < 1e-9
    assert abs(engine._worlds_patch_bridge_factor() - 0.75) < 1e-9
    monkeypatch.setattr(engine, "load_recent_pro", lambda: many)
    assert engine._worlds_patch_bridge_factor() == 0.0
    monkeypatch.setattr(engine, "load_recent_pro", lambda: {"latest_patch": "15.19", "games": []})
    assert engine._worlds_patch_bridge_factor() == 1.0
    assert engine._recent_pro_patch_minor() == 19
