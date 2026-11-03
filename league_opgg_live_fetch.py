"""Non-blocking, bounded OP.GG lookup for *confirmed* enemy picks in champ select.

One opponent's public page yields a nearly complete list of lane matchups.
Fetching is strictly background, never blocks a draft, player sync or
Hover/Lock. Private local snapshots only. 15 new pages per UTC day per
client, five pages per lobby, at least four seconds between requests.
The user's OP.GG support reply authorises careful small-scale noncommercial
attributed use, not unrestricted bulk export. Stop on access restrictions.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from league_opgg_full_reference import _page_slug, _source_patch, folder
from tools.collect_opgg_full_web import (
    MIN_GAP_SECONDS, collect, page_file, read_page,
)

log = logging.getLogger(__name__)
ROLES = {"Top", "Jungle", "Mid", "ADC", "Support"}
MAX_REQUESTS_PER_DAY = 15
MAX_REQUESTS_PER_LOBBY = 5
DELAY_SECONDS = max(4.0, MIN_GAP_SECONDS)
PAGE_CACHE_SECONDS = 24 * 3600


class LiveCounterCollector:
    def __init__(self, *, root: Path | None = None,
                 fetcher: Callable = collect,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 on_update: Callable[[], None] | None = None):
        self.root = Path(root) if root is not None else folder()
        self.fetcher = fetcher
        self.clock = clock
        self.sleep = sleep
        self.on_update = on_update
        self._lock = threading.Lock()
        self._tasks: queue.Queue = queue.Queue()
        self._attempted: dict[tuple[str, str, str], float] = {}
        self._per_lobby: dict[str, int] = {}
        self._day = ""
        self._today_count = 0
        self._last_http_at = 0.0
        self._blocked = False
        self._worker: threading.Thread | None = None
        self._load_budget()

    def _date(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")

    def _load_budget(self):
        try:
            budget = json.loads((self.root / "live-budget.json").read_text(encoding="utf8"))
            if budget.get("utc_date") == self._date():
                self._day = budget["utc_date"]
                self._today_count = max(0, int(budget.get("attempts", 0)))
                self._blocked = bool(budget.get("blocked"))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _save_budget(self):
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / "live-budget.json"
        incoming = self.root / "live-budget.json.incoming"
        incoming.write_text(json.dumps({
            "utc_date": self._day, "attempts": self._today_count,
            "blocked": self._blocked,
        }), encoding="utf8")
        incoming.replace(target)

    def _current_day(self):
        today = self._date()
        if today != self._day:
            self._day = today
            self._today_count = 0
            self._blocked = False
            # Game keys can be reused after restarts and have no lasting value.
            self._per_lobby.clear()

    def _fresh_page(self, role: str, enemy: str, patch: str) -> bool:
        path = page_file(self.root, role, enemy)
        saved = read_page(path)
        if not saved:
            return False
        try:
            return (
                saved.get("source_patch") == patch
                and saved.get("rank_filter") == "Emerald+"
                and saved.get("queue_filter") == "Ranked Solo/Duo"
                and 0 <= self.clock() - float(saved["retrieved_epoch"]) <= PAGE_CACHE_SECONDS
            )
        except (TypeError, ValueError, KeyError):
            return False

    def schedule(self, lobby_id: str, confirmed: Mapping[str, str],
                 *, patch: str,
                 planned: Mapping[str, str] | None = None,
                 pools: Mapping[str, list[str]] | None = None) -> dict:
        """Only queue; never perform network I/O or wait for HTTP."""
        if os.environ.get("DRAFTCOACH_LIVE_OPGG", "1").strip() == "0":
            return {"state": "disabled", "queued": 0}
        source_patch = _source_patch(patch)
        if not source_patch or not confirmed:
            return {"state": "waiting_for_confirmed_enemy", "queued": 0}
        added = 0
        with self._lock:
            self._current_day()
            if self._blocked:
                return {"state": "access_restricted", "queued": 0,
                        "remaining_today": 0}
            # First fetch each revealed opponent's WHOLE counter table.
            # Optional follow-ups collect the actual owner's planned pick
            # and at most two of their declared pool choices, giving those
            # same-patch champion baselines for adjusted counter scoring.
            # This never broadens to every hero or every server tier.
            targets = [(role, enemy) for role, enemy in confirmed.items()]
            for role in confirmed:
                champ = (planned or {}).get(role)
                if champ:
                    targets.append((role, champ))
            for role in confirmed:
                chosen = (planned or {}).get(role)
                seen_pool = set()
                for champ in (pools or {}).get(role, []):
                    if not champ or champ == chosen or champ in seen_pool:
                        continue
                    seen_pool.add(champ)
                    targets.append((role, champ))
                    if len(seen_pool) >= 2:
                        break
            seen = set()
            for role, enemy in targets:
                if role not in ROLES or not isinstance(enemy, str):
                    continue
                champ = enemy.strip()
                if not champ or not _page_slug(champ):
                    continue
                key = (role, _page_slug(champ), source_patch)
                if key in seen:
                    continue
                seen.add(key)
                if self._fresh_page(role, champ, source_patch):
                    continue
                if self.clock() - self._attempted.get(key, -1e20) < PAGE_CACHE_SECONDS:
                    continue
                if self._per_lobby.get(lobby_id, 0) >= MAX_REQUESTS_PER_LOBBY:
                    break
                if self._today_count >= MAX_REQUESTS_PER_DAY:
                    break
                self._today_count += 1   # reserve BEFORE queueing, survive app restart
                self._per_lobby[lobby_id] = self._per_lobby.get(lobby_id, 0) + 1
                self._attempted[key] = self.clock()
                self._tasks.put((role, champ, source_patch))
                added += 1
            if added:
                self._save_budget()
                if self._worker is None or not self._worker.is_alive():
                    self._worker = threading.Thread(
                        target=self._run, name="DraftCoach-OPGG-SmallLiveFetch", daemon=True)
                    self._worker.start()
            return {
                "state": "fetching" if self._tasks.unfinished_tasks else (
                    "daily_limit" if self._today_count >= MAX_REQUESTS_PER_DAY else "cached"),
                "queued": self._tasks.unfinished_tasks,
                "newly_requested": added,
                "remaining_today": max(0, MAX_REQUESTS_PER_DAY - self._today_count),
                "daily_cap": MAX_REQUESTS_PER_DAY,
            }

    def _run(self):
        while True:
            try:
                role, champion, patch = self._tasks.get(timeout=2)
            except queue.Empty:
                return
            try:
                with self._lock:
                    self._current_day()
                    if self._blocked:
                        continue
                    wait = max(0.0, DELAY_SECONDS - (self.clock() - self._last_http_at))
                if wait:
                    self.sleep(wait)
                with self._lock:
                    self._last_http_at = self.clock()
                    if self._blocked:
                        continue
                result = self.fetcher(role, [champion], folder=self.root,
                                      max_new=1, delay=MIN_GAP_SECONDS,
                                      expected_patch=patch,
                                      allow_declared_offmeta=True,
                                      emit=lambda *_: None)
                if result.get("stopped"):
                    with self._lock:
                        self._blocked = True
                        self._save_budget()
                    log.warning("OP.GG access restricted; live fetch paused for UTC day")
                if result.get("new_pages") and self.on_update:
                    try:
                        self.on_update()
                    except Exception:
                        log.exception("Live reference update callback failed")
            except Exception:
                log.exception("Optional OP.GG live page fetch failed; draft unaffected")
            finally:
                self._tasks.task_done()


_collector: LiveCounterCollector | None = None
_collector_lock = threading.Lock()


def request_live_counters(lobby_id: str, confirmed: Mapping[str, str],
                          *, patch: str, on_update: Callable[[], None] | None = None,
                          planned: Mapping[str, str] | None = None,
                          pools: Mapping[str, list[str]] | None = None) -> dict:
    global _collector
    with _collector_lock:
        if _collector is None:
            _collector = LiveCounterCollector(on_update=on_update)
        else:
            _collector.on_update = on_update
        service = _collector
    return service.schedule(lobby_id, confirmed, patch=patch,
                            planned=planned, pools=pools)
