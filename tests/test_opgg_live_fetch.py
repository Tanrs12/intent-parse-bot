"""Live enemy counter fetching is bounded, persisted and never synchronous."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import league_opgg_live_fetch as live
from league_opgg_live_fetch import LiveCounterCollector

ROOT = Path(__file__).resolve().parents[1]


def test_on_demand_fetch_is_nonblocking_and_deduped(tmp_path):
    ready = threading.Event()
    release = threading.Event()
    updated = []
    calls = []

    def fake_fetch(role, champs, **kwargs):
        calls.append((role, champs[0], kwargs["expected_patch"]))
        ready.set()
        assert release.wait(timeout=5), "network stub was never released"
        return {"new_pages": 1, "stopped": 0}

    collector = LiveCounterCollector(
        root=tmp_path, fetcher=fake_fetch,
        on_update=lambda: updated.append(1))
    started = time.perf_counter()
    status = collector.schedule(
        "game-1", {"Top": "Malphite", "Mid": "Sylas"}, patch="26.21")
    assert time.perf_counter() - started < 0.4
    assert status["queued"] == 2
    assert status["remaining_today"] == live.MAX_REQUESTS_PER_DAY - 2
    assert ready.wait(timeout=5)
    again = collector.schedule(
        "game-1", {"Top": "Malphite", "Mid": "Sylas"}, patch="26.21")
    assert again["newly_requested"] == 0
    assert again["queued"] == 2
    release.set()
    collector._tasks.join()
    assert sorted(calls) == sorted([("Top", "Malphite", "16.21"),
                                   ("Mid", "Sylas", "16.21")])
    assert len(updated) == 2
    budget = json.loads((tmp_path / "live-budget.json").read_text(encoding="utf8"))
    assert budget["attempts"] == 2


def test_daily_cap_survives_restart_and_no_network_is_ever_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "MAX_REQUESTS_PER_DAY", 2)
    called = []
    def fake_fetch(role, champs, **kwargs):
        called.append((role, champs[0]))
        return {"new_pages": 0}
    a = LiveCounterCollector(root=tmp_path, fetcher=fake_fetch, sleep=lambda s: None)
    value = a.schedule("g1", {"Top": "Gwen", "Jungle": "Elise",
                              "Mid": "Akali"}, patch="26.21")
    assert value["newly_requested"] == 2
    a._tasks.join()
    other = LiveCounterCollector(root=tmp_path, fetcher=fake_fetch)
    no_more = other.schedule("g2", {"ADC": "Jinx"}, patch="26.21")
    assert no_more["newly_requested"] == 0
    assert no_more["state"] == "daily_limit"
    assert len(called) == 2


def test_denied_access_circuit_breaker(tmp_path):
    def denied(*args, **kwargs):
        return {"stopped": 1, "new_pages": 0}
    c = LiveCounterCollector(root=tmp_path, fetcher=denied)
    assert c.schedule("g3", {"Top": "Shen"}, patch="26.21")["newly_requested"] == 1
    c._tasks.join()
    row = c.schedule("g3", {"Mid": "Vex"}, patch="26.21")
    assert row["state"] == "access_restricted"
    assert row["newly_requested"] if "newly_requested" in row else 0 == 0
    assert json.loads((tmp_path / "live-budget.json").read_text(encoding="utf8"))["blocked"] is True


def test_live_fetch_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAFTCOACH_LIVE_OPGG", "0")
    c = LiveCounterCollector(root=tmp_path, fetcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    result = c.schedule("g4", {"Top": "Malphite"}, patch="26.21")
    assert result["state"] == "disabled"
    assert not (tmp_path / "live-budget.json").exists()


def test_bridge_only_queues_confirmed_live_lanes():
    s = (ROOT / "tools" / "intent_live_draft_bridge.py").read_text(encoding="utf8")
    assert "not testing and known_enemy_lanes" in s
    assert "or _auto_is_host" in s
    assert "if can_fetch:" in s
    assert "request_live_counters(" in s
    assert "on_update=_invalidate_opgg_guidance" in s
    assert "with _compute_lock:" in s


def test_packaged_build_includes_new_source_and_historic_deps():
    s = (ROOT / "build_exe.py").read_text(encoding="utf8")
    for p in ("league_opgg_full_reference.py",
              "league_opgg_live_fetch.py",
              "league_chaincc_pro_synergy.py"):
        assert "ROOT / '" + p + "'" in s


def test_new_stats_display_loading_without_replacing_pick_system():
    s = (ROOT / "draftcoach" / "static" / "selected-comp.js").read_text(encoding="utf8")
    assert '"fetching"' in s
    assert '"access_restricted"' in s
    assert "liveFetch.queued" in s
    assert "picks and locks stay responsive" in s.lower()



def test_live_fetch_prefers_enemy_page_then_actual_players_declared_role_pool(tmp_path):
    called = []
    def fake_fetch(role, champions, **kwargs):
        called.append((role, champions[0]))
        return {"new_pages": 0, "stopped": 0}
    service = LiveCounterCollector(root=tmp_path, fetcher=fake_fetch, sleep=lambda _: None)
    response = service.schedule(
        "confirmed-game", {"Top": "Malphite"}, patch="26.21",
        planned={"Top": "Ornn"},
        pools={"Top": ["Ornn", "Sylas", "Vayne", "Teemo"]},
    )
    assert response["newly_requested"] == 4
    service._tasks.join()
    assert called == [("Top", "Malphite"), ("Top", "Ornn"),
                      ("Top", "Sylas"), ("Top", "Vayne")]
    # The cap remains strict even with a very broad reported pool.
    assert service._per_lobby["confirmed-game"] <= live.MAX_REQUESTS_PER_LOBBY
