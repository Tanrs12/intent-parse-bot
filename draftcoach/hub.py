"""Central hub: ingests draft updates, recomputes guidance, broadcasts snapshots.

Inputs arrive from the League client watcher, the manual board, the REST API
or the simulator. Each replaces the current draft state. Recomputation runs
in a worker thread and is coalesced: if several events arrive while one
computation is in flight, only the newest state is scored next.

The hub also owns the roster (loadfile imports re-score the current draft and
persist to ``var/roster.json``), the draft type, the fearless series memory,
the lobby view (which seat is which player, and whether their pool is known)
and a one-line explanation of what the last event changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import VAR_DIR, advisor, assist, loadfiles
from . import champions as champ_map
from . import client_actions, queues
from . import state as ds
from .draft_order import DRAFT_TYPES, ManualDraft, normalise_type
from .roster import Roster

import league_comp_engine as engine  # type: ignore  # noqa: E402

log = logging.getLogger("draftcoach.hub")
MISSING = object()   # a client action request that did not say which game its button was drawn for
Broadcast = Callable[[dict], Awaitable[None]]
ROLE_SHORT = {"Top": "Top", "Jungle": "Jungle", "Mid": "Mid", "ADC": "Bot", "Support": "Support"}


class Hub:
    def __init__(self, roster: Roster, draft: ManualDraft | None = None, broadcast: Broadcast | None = None,
                 debounce_ms: int = 30, prewarm: int = 3, roster_path: Path | None = None):
        self.roster = roster
        self.draft = draft or ManualDraft()
        self.draft.draft_type = normalise_type(roster.settings.get("draft_type"))
        self.broadcast = broadcast
        self.debounce = debounce_ms / 1000.0
        self.prewarm = prewarm
        self.roster_path = roster_path
        self.state: ds.DraftState | None = None
        self.guidance: dict | None = None
        self.intent_guidance: dict | None = None     # FLEXERS: the shared backend's latest picks/bans (server poller)
        self.status: dict[str, Any] = {"connection": "waiting", "detail": "starting", "source": None, "updated_at": None,
                                       "compute_ms": None, "events": 0, "stale": False, "lcu_in_champ_select": False}
        self.events: deque[dict] = deque(maxlen=60)
        self.last_change: dict | None = None
        self.meta: dict = engine.load_meta()
        self.matchups: dict = engine.load_lane_matchups()
        self._pending: ds.DraftState | None = None
        self._task: asyncio.Task | None = None
        self._last_input: tuple | None = None
        self.last_session: dict | None = None
        self._prev_state: ds.DraftState | None = None
        self._prev_guidance: dict | None = None
        self._submitted_seq = 0
        self._scored_seq = 0
        self._generation = 0
        self.rest = None                 # LcuRest of the connected client (set by the server)
        self.any_queue = False           # --any-queue: coach customs/normals too (testing)
        self.lockfile: str | None = None # explicit lockfile path, if the server was given one
        self.lcu_enabled = False         # the server runs a client watcher (lockfile fallback is allowed)
        # Any-queue assistance: this seat's mastery (fetched once per game) and the swap offers this
        # client already acted on, so a refused or pending request is never repeated.
        self.comfort: dict[str, dict] = {}
        self._comfort_game: object = None
        self._swaps_done: set[tuple] = set()
        self._chase_game: tuple | None = None   # (game id, puuid) the one-button swap was pressed for
        self._swap_task: asyncio.Task | None = None
        self._tierlist: dict | None = None
        from .profile_sync import ProfileStore
        from . import identity as _identity

        self.profiles = ProfileStore(roster_path.parent / "profiles.json" if roster_path else None, _identity.device_id())
        self.profile_relay = None
        self._published_at = 0.0
        # The client's current role / pick-order swap offers for the overlay's swap buttons.
        self._offers: dict = {}
        self._offers_epoch: tuple | None = None
        self._offers_task: asyncio.Task | None = None
        self._offers_at = 0.0
        self._offers_again = False
        self._offers_poll: asyncio.Task | None = None
        self._pickable: set[int] | None = None   # champion ids this account may pick in this champ select
        self._pickable_epoch: tuple | None = None
        self._sync_series()

    # ---- draft type / fearless series -------------------------------------------
    @property
    def draft_type(self) -> str:
        return self.draft.draft_type

    def _sync_series(self) -> None:
        fearless = DRAFT_TYPES[self.draft_type]["fearless"]
        self.draft.series_unavailable = self.roster.series_champions() if fearless else set()

    async def set_draft_type(self, draft_type: str) -> None:
        new_type = normalise_type(draft_type)
        if new_type == self.draft_type:
            return
        self.draft.set_type(new_type)
        self.roster.settings["draft_type"] = new_type
        self._sync_series()
        self.log_event(f"draft type: {DRAFT_TYPES[new_type]['label']}", "info")
        self.persist()
        await self.rescore()

    async def series_record(self, state: ds.DraftState | None = None) -> dict | None:
        """Store the current (or given) draft's picks as the next game of the series."""
        state = state or self.state
        if state is None:
            return None
        ours = state.our_locked()
        theirs = state.enemy_locked()
        if not ours and not theirs:
            return None
        game = {"game": len(self.roster.series) + 1, "ours": ours, "theirs": theirs, "side": state.side, "at": time.time()}
        self.roster.series.append(game)
        self.log_event(f"series: game {game['game']} recorded ({len(ours)} + {len(theirs)} champions removed)", "info")
        self._sync_series()
        self.persist()
        return game

    async def series_next_game(self) -> dict | None:
        game = await self.series_record()
        self.draft.reset()
        self._prev_state = None
        await self.ingest_manual()
        return game

    async def series_clear(self) -> None:
        self.roster.series = []
        self._sync_series()
        self.log_event("series cleared", "info")
        self.persist()
        await self.rescore()

    def _apply_series(self, state: ds.DraftState) -> None:
        if DRAFT_TYPES[self.draft_type]["fearless"]:
            state.series_unavailable = sorted(self.roster.series_champions())
        if state.source != "manual" and state.draft_type == "ranked" and self.draft_type != "ranked":
            state.notes.append("client reports simultaneous bans (ranked draft)")

    # ---- inputs ------------------------------------------------------------
    def allowed_queues(self) -> list[int] | None:
        """Queue ids the live coach reacts to; None means every role-draft queue (ARAM/Arena never)."""
        if self.any_queue:
            return None
        raw = self.roster.settings.get("queues", [])
        try:
            ids = [int(x) for x in (raw or [])]
        except (TypeError, ValueError):
            ids = []
        return ids or None

    async def ingest_lcu_session(self, session: dict | None, gameflow_phase: str | None, queue: dict | None = None,
                                 source: str = "lcu") -> None:
        """Never raises: a malformed session (new client field, odd queue id) must not tear down the
        client watcher, which would reconnect and fail again for the rest of champion select."""
        try:
            await self._ingest_lcu_session(session, gameflow_phase, queue, source)
        except Exception as exc:  # noqa: BLE001
            log.exception("champion select session could not be read")
            self.last_session = None          # no client action may run against the rejected update
            self.status["detail"] = f"Could not read this champion select update: {exc}"
            try:
                await self._push()
            except Exception:  # noqa: BLE001
                log.exception("could not publish after a rejected update")

    async def _ingest_lcu_session(self, session: dict | None, gameflow_phase: str | None, queue: dict | None,
                                  source: str) -> None:
        allowed = self.allowed_queues() if source == "lcu" else None
        self.status["queue"] = queue
        qtext = " ".join(str((queue or {}).get(k) or "") for k in ("name", "shortName", "description", "type", "category")).casefold()
        is_custom = queue is not None and (int(queue.get("id", -1)) == 0 or "custom" in qtext or "practice tool" in qtext)
        category = str((queue or {}).get("category") or "").casefold()
        qtype = str((queue or {}).get("type") or "").casefold()
        is_bot = category == "versusai" or "bot" in qtype or int((queue or {}).get("id", -1)) in {830, 840, 850}
        mode = queues.unsupported_mode(queue) if session and source == "lcu" else None
        filtered = (allowed is not None and queue is not None and int(queue.get("id", -1)) not in allowed
                    and not (is_custom and 0 in allowed) and not is_bot)
        if session and (mode or filtered):
            # ARAM / Arena have no role draft; other queues are skipped only when settings.queues lists queues.
            name = queues.queue_label(queue)
            detail = (f"Standing by: {mode} has no role draft." if mode
                      else f"Standing by: {name} is not in this coach's queue list (settings.queues).")
            if self.state is not None and self.state.source == "lcu":
                self.log_event(f"standing by: {mode or name}", "phase")
            self.last_session = None
            self.status["lcu_in_champ_select"] = False
            self._last_input = None
            await self.clear(detail=detail, source=source)
            return
        self.last_session = session
        if not session:
            self._chase_game = None          # the one-button swap covered that champion select only
            self._swaps_done.clear()         # a re-entered lobby can reuse the game id: its swaps start fresh
            self.status["lcu_in_champ_select"] = False
            live_input = self._last_input is not None and self._last_input[0] == "lcu"
            if self.state is not None and self.state.source in ("lcu", "simulation"):
                live_input = True
                if DRAFT_TYPES[self.draft_type]["fearless"] and len(self.state.our_locked()) == 5:
                    await self.series_record(self.state)
                self.log_event("left champion select", "phase")
            if live_input:
                self._last_input = None
                await self.clear(detail=f"not in champ select (phase {gameflow_phase or 'None'})", source=source)
            return
        seat_fallback = source == "simulation" or is_custom or is_bot
        if source == "lcu":
            self._link_local_account(session)
        state = ds.state_from_lcu_session(session, self.roster, gameflow_phase, seat_fallback=seat_fallback)
        self._apply_wanted_role(state)
        state.source = source
        self.status["lcu_in_champ_select"] = source == "lcu"
        self._last_input = ("lcu", session, gameflow_phase, source, seat_fallback)
        if source == "lcu":
            self._after_live_session(session, state)
        self._apply_series(state)
        await self.submit(state)

    async def ingest_manual(self) -> None:
        self._last_input = ("manual",)
        self._sync_series()
        state = ds.state_from_manual_draft(self.draft, self.roster)
        await self.submit(state)

    async def ingest_payload(self, payload: dict) -> None:
        self._last_input = ("payload", payload)
        state = ds.state_from_payload(payload, self.roster)
        self._apply_series(state)
        await self.submit(state)

    async def rescore(self) -> None:
        """Re-run the current input (after a roster, data or draft-type change)."""
        if self._last_input is None:
            await self._push()
            return
        kind = self._last_input[0]
        if kind == "lcu":
            _, session, phase, source, seat_fallback = self._last_input
            state = ds.state_from_lcu_session(session, self.roster, phase, seat_fallback=seat_fallback)
            self._apply_wanted_role(state)
            state.source = source
            self._apply_series(state)
        elif kind == "manual":
            self._sync_series()
            state = ds.state_from_manual_draft(self.draft, self.roster)
        else:
            state = ds.state_from_payload(self._last_input[1], self.roster)
            self._apply_series(state)
        await self.submit(state)

    async def set_status(self, connection: str, detail: str = "") -> None:
        self.status.update({"connection": connection, "detail": detail})
        if connection != "connected" and self.state is not None and self.state.source == "lcu":
            self.status["stale"] = True
        await self._push()

    async def clear(self, detail: str = "", source: str | None = None) -> None:
        self._generation += 1
        self._pending = None
        self.state = None
        self.guidance = None
        self.intent_guidance = None
        self._prev_state = None
        self._prev_guidance = None
        self.last_change = None
        self.status.update({"detail": detail, "source": source, "updated_at": time.time(), "stale": False})
        await self._push()

    async def submit(self, state: ds.DraftState) -> None:
        self.status["events"] += 1
        self._submitted_seq += 1
        state.deadline = (time.time() + state.time_left_ms / 1000.0) if state.time_left_ms else None
        self._pending = state
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())

    async def settle(self, timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._scored_seq >= self._submitted_seq or self._task is None or self._task.done():
                return
            await asyncio.sleep(0.01)

    async def _worker(self) -> None:
        try:
            while self._pending is not None:
                if self.debounce:
                    await asyncio.sleep(self.debounce)
                state = self._pending
                if state is None:
                    continue
                generation = self._generation
                seq = self._submitted_seq
                self._pending = None
                started = time.perf_counter()
                try:
                    if os.environ.get("INTENT_LIVE") == "1" and self.roster.settings.get("draft_source") == "discord":
                        # In INTent live mode this server is the lightweight LCU/state/action layer.
                        # The shared backend on :8766 owns composition scoring, matchup reactions,
                        # bans and pick order, so running Rat's full advisor here would duplicate the
                        # expensive search on every hover/pick/ban for no visible benefit.
                        active_cells = set(state.active.cell_ids)
                        guidance = {
                            "headline": "INTent live backend",
                            "phase_kind": state.active.kind,
                            "picks": [], "bans": [], "counters": [], "comps": [], "notes": [], "alerts": [],
                            "likely_enemy": [], "flex": [], "swaps": [],
                            "roles_now": [p.role for p in state.our_team if p.cell_id in active_cells and p.role],
                            "picking_now": bool(state.active.kind == "pick" and state.active.ours),
                            "draft_complete": len(state.our_locked()) == 5,
                            "our_locked": state.our_locked(),
                            "enemy_locked": state.enemy_locked(),
                        }
                    else:
                        guidance = await asyncio.to_thread(advisor.advise, state, self.roster, meta=self.meta, matchups=self.matchups)
                except Exception as exc:  # noqa: BLE001
                    log.exception("advice failed")
                    guidance = {"error": str(exc), "headline": "Scoring failed", "phase_kind": "error", "picks": [], "bans": [],
                                "counters": [], "comps": [], "notes": [str(exc)], "alerts": [{"level": "error", "text": str(exc)}],
                                "likely_enemy": [], "flex": [], "roles_now": [], "picking_now": False, "draft_complete": False,
                                "our_locked": {}, "enemy_locked": {}, "swaps": []}
                if generation != self._generation:
                    continue  # A dodge/queue change revoked this computation while it ran.
                if seq != self._submitted_seq and self._pending is not None and time.time() - self._published_at < 1.0:
                    continue  # A newer session is queued; publish it instead (at least once a second).
                elapsed = (time.perf_counter() - started) * 1000
                self._diff_events(self._prev_state, state)
                change = self._explain_change(self._prev_state, self._prev_guidance, state, guidance)
                if change:
                    self.last_change = change
                guidance["last_change"] = self.last_change
                self._prev_state, self._prev_guidance = state, guidance
                self.state = state
                self.guidance = guidance
                # Scoring finishing is not proof the client is back: a disconnect during scoring stays stale.
                stale = state.source == "lcu" and self.status.get("connection") not in ("connected", None)
                self.status.update({"source": state.source, "updated_at": time.time(), "compute_ms": round(elapsed, 1), "stale": stale})
                self._scored_seq = seq
                self._published_at = time.time()
                await self._push()
                if self._pending is None and self.prewarm:
                    await self._prewarm(state, guidance)
        finally:
            self._scored_seq = self._submitted_seq

    async def _prewarm(self, state: ds.DraftState, guidance: dict) -> None:
        if not guidance.get("picking_now"):
            return
        candidates: list[tuple[str, str]] = []
        for slot in state.our_team:
            if slot.cell_id in state.active.cell_ids and slot.hover and slot.role and not slot.locked:
                candidates.append((slot.role, slot.hover))
        for pick in guidance.get("picks", [])[: self.prewarm]:
            if (pick["role"], pick["champion"]) not in candidates:
                candidates.append((pick["role"], pick["champion"]))
        for role, champion in candidates[: self.prewarm + 2]:
            if self._pending is not None:
                return
            hypothetical = advisor.hypothetical_lock(state, role, champion)
            try:
                await asyncio.to_thread(advisor.advise, hypothetical, self.roster, meta=self.meta, matchups=self.matchups)
            except Exception:  # noqa: BLE001
                log.debug("prewarm failed", exc_info=True)

    # ---- roster / loadfiles --------------------------------------------------------
    async def import_text(self, text: str, name: str) -> loadfiles.ImportResult:
        result = loadfiles.import_text(self.roster, text, name)
        await self._after_import(result)
        return result

    async def import_path(self, path: Path) -> loadfiles.ImportResult:
        result = loadfiles.import_path(self.roster, path)
        await self._after_import(result)
        return result

    async def import_document(self, data: dict, name: str) -> loadfiles.ImportResult:
        result = loadfiles.import_document(self.roster, data, name)
        await self._after_import(result)
        return result

    async def _after_import(self, result: loadfiles.ImportResult) -> None:
        if result.data_changed:
            self.meta = engine.load_meta()
            self.matchups = engine.load_lane_matchups()
            ds.reset_role_cache()
        if normalise_type(self.roster.settings.get("draft_type")) != self.draft_type:
            self.draft.set_type(self.roster.settings.get("draft_type"))
            self._sync_series()
        if result.applied:
            self.profiles.apply_to(self.roster)   # loadfiles, LAN sync and Discord exports carry no versions
        level = "error" if result.errors else ("warn" if result.warnings else "import")
        summary = f"{result.name}: {len(result.applied)} change(s)"
        if result.errors:
            summary = f"{result.name}: failed - {result.errors[0]}"
        elif result.warnings:
            summary += f", {len(result.warnings)} warning(s)"
        self.log_event(summary, level)
        self.persist()
        await self.rescore()

    async def roster_changed(self, what: str = "roster updated") -> None:
        self.roster.touch()
        self.log_event(what, "roster")
        self.persist()
        await self.rescore()

    async def link_seat(self, riot_id: str, player_key: str | None, name: str | None = None) -> dict:
        """Attach a lobby seat's Riot id to a roster player (creating one if asked)."""
        player = self.roster.players.get(player_key) if player_key else None
        if player is None and name:
            player = self.roster.ensure_player(name, source="lobby")
        if player is None:
            raise ValueError("no such player")
        for other in self.roster.players.values():
            other.riot_ids = [r for r in other.riot_ids if self.roster.riot_key(r) != self.roster.riot_key(riot_id)]
        player.riot_ids.append(riot_id)
        if player.user_id is None:
            player.user_id = self.roster.profile_user_id_for(riot_id)
        await self.roster_changed(f"{riot_id} linked to {player.name}")
        return player.to_dict()

    def persist(self) -> None:
        if self.roster_path is None:
            return
        try:
            self.roster.save(self.roster_path)
        except OSError as exc:
            log.warning("could not save roster: %s", exc)

    # ---- events ----------------------------------------------------------------------
    def log_event(self, text: str, kind: str = "info") -> None:
        self.events.append({"t": time.time(), "text": text, "kind": kind})

    def _diff_events(self, before: ds.DraftState | None, after: ds.DraftState) -> None:
        if before is None:
            self.log_event(f"draft started ({after.source}, {after.side} side, {DRAFT_TYPES[self.draft_type]['label']})", "phase")
            before = ds.DraftState()
        for name in after.our_bans[len(before.our_bans):]:
            self.log_event(f"we banned {name}", "ban")
        for name in after.enemy_bans[len(before.enemy_bans):]:
            self.log_event(f"enemy banned {name}", "ban")
        prev_ours = {p.cell_id: p.locked for p in before.our_team}
        prev_theirs = {p.cell_id: p.locked for p in before.enemy_team}
        for p in after.our_team:
            if p.locked and prev_ours.get(p.cell_id) != p.locked:
                self.log_event(f"{p.display_name} locked {p.locked}" + (f" ({p.role})" if p.role else ""), "lock")
        for p in after.enemy_team:
            if p.locked and prev_theirs.get(p.cell_id) != p.locked:
                self.log_event(f"enemy locked {p.locked}" + (f" ({p.role})" if p.role else ""), "enemy")
        if before.phase != after.phase and after.phase not in ("IDLE",):
            self.log_event(f"phase {after.phase.lower().replace('_', ' ')}", "phase")

    def _explain_change(self, before: ds.DraftState | None, prev_guidance: dict | None,
                        after: ds.DraftState, guidance: dict) -> dict | None:
        """One line saying what the enemy just did and how the suggestion moved."""
        if before is None or prev_guidance is None:
            return None
        prev_picks = prev_guidance.get("picks") or []
        new_picks = guidance.get("picks") or []
        prev_top = prev_picks[0]["champion"] if prev_picks else None
        new_top = new_picks[0]["champion"] if new_picks else None
        events: list[str] = []
        for name in after.enemy_bans[len(before.enemy_bans):]:
            events.append(f"Enemy banned {name}")
        for name in after.our_bans[len(before.our_bans):]:
            events.append(f"We banned {name}")
        prev_theirs = {p.cell_id: p.locked for p in before.enemy_team}
        for p in after.enemy_team:
            if p.locked and prev_theirs.get(p.cell_id) != p.locked:
                events.append(f"Enemy locked {p.locked}" + (f" ({ROLE_SHORT.get(p.role, p.role)})" if p.role else ""))
        prev_ours = {p.cell_id: p.locked for p in before.our_team}
        for p in after.our_team:
            if p.locked and prev_ours.get(p.cell_id) != p.locked:
                events.append(f"{p.display_name} locked {p.locked}")
        if not events:
            return None
        text = "; ".join(events)
        removed = [p["champion"] for p in prev_picks[:5] if p["champion"] in (set(after.enemy_bans) | set(after.our_bans)
                                                                               | set(after.unavailable())) and p["champion"] != prev_top]
        effect = ""
        if prev_top and prev_top != new_top:
            if prev_top in after.unavailable():
                effect = f"{prev_top} is gone, {new_top} is the pick now" if new_top else f"{prev_top} is gone"
            elif new_top:
                effect = f"{new_top} is the pick now, ahead of {prev_top}"
        elif removed:
            effect = f"{', '.join(removed)} gone, {new_top} is still the pick"
        elif new_top and prev_top == new_top:
            effect = f"{new_top} is still the pick"
        return {"event": text, "effect": effect, "at": time.time()}

    # ---- lobby view --------------------------------------------------------------------
    def lobby(self) -> list[dict]:
        """Who is in our five seats and whether we know their pool."""
        if self.state is None:
            return []
        out = []
        for slot in sorted(self.state.our_team, key=lambda p: p.cell_id):
            if slot.pool_source == "player" and slot.role and not slot.pool.get(slot.role):
                status = "partial"
                detail = f"no {slot.role} pool (knows " + ", ".join(r for r, v in slot.pool.items() if v) + ")"
            elif slot.pool_source == "player":
                status = "pool"
                detail = f"{len(slot.pool.get(slot.role, [])) if slot.role else sum(len(v) for v in slot.pool.values())} champions"
            elif slot.pool_source == "history":
                status = "history"
                n = len(slot.pool.get(slot.role, [])) if slot.role else sum(len(v) for v in slot.pool.values())
                detail = f"account history ({n} champions), no submitted pool yet"
            elif slot.pool_source in ("default", "payload"):
                status = "default"
                detail = "default pool"
            else:
                status = "none"
                detail = "no pool: using role meta"
            out.append({"cell_id": slot.cell_id, "name": slot.display_name, "riot_id": slot.riot_id, "role": slot.role,
                        "player_key": slot.player_key, "matched": slot.player_key is not None, "pool_status": status,
                        "detail": detail, "profile": slot.user_id is not None, "locked": slot.locked, "hover": slot.hover,
                        "is_local": slot.is_local})
        return out

    # ---- this machine's seat -----------------------------------------------------------------
    def client_rest(self):
        """The connected client's REST handle, or one built from the lockfile right now.

        The watcher hands over its handle on the "connected" status; between reconnects (the client
        websocket drops and comes back) that handle is cleared, but the lockfile is still valid, so
        an action must not fail just because the status flipped a moment ago."""
        if self.rest is not None:
            return self.rest
        if not self.lcu_enabled:
            return None
        try:
            from . import lcu

            info = lcu.find_lockfile(self.lockfile)
            return lcu.LcuRest(info) if info else None
        except Exception:  # noqa: BLE001
            return None

    def local_view(self) -> dict | None:
        """What this machine's player should do, and whether the client can do it for them."""
        state, g = self.state, self.guidance
        if state is None or state.source not in ("lcu", "simulation") or state.local_cell_id is None or g is None:
            return None
        slot = next((p for p in state.our_team if p.cell_id == state.local_cell_id), None)
        if slot is None:
            return None
        shared = self.shared_guidance()
        if isinstance(shared, dict):
            # Following the team's shared drafts: its picks and bans drive this seat (what the page shows is
            # what Lock does); the app's own lists only fill in when the shared ones are empty.
            g = {**g, "picks": list(shared.get("picks") or []) or list(g.get("picks") or []),
                 "bans": list(shared.get("bans") or []) or list(g.get("bans") or []),
                 "swaps": list(g.get("swaps") or shared.get("swaps") or [])}
        # FLEXERS: the scripted simulation stands in for the League client, so the panel's Hover / Lock
        # buttons can be tried in demo mode; the action lands on the simulated session (see client_act).
        simulated = state.source == "simulation"
        view: dict = {"cell_id": slot.cell_id, "game_id": state.game_id, "draft_token": self._draft_token(), "name": slot.display_name, "role": slot.role, "locked": slot.locked,
                      "hover": slot.hover, "kind": None, "can_act": False, "suggestion": None, "swap": None,
                      "incoming_swap": None, "client": simulated or (not self.status.get("stale") and self.client_rest() is not None),
                      "simulated": simulated}
        session = self.last_session or {}
        action = client_actions.active_action(session, slot.cell_id)
        if action is not None and state.active.ours:
            view["kind"] = action.get("type")
            view["action_id"] = int(action["id"])
            view["can_act"] = view["client"]
            if action.get("type") == "ban":
                # FLEXERS: never suggest a champion that is already banned or picked (the shared ranking can
                # lag a ban that landed a moment ago).
                gone = set(state.our_bans) | set(state.enemy_bans) | set(state.our_locked().values()) | set(state.enemy_locked().values())
                # Simultaneous bans (ranked / normal draft): every teammate's app ranks the same list, so each
                # seat takes the next one down in seat order, skipping what a teammate already hovered or
                # locked; otherwise five clicks ban one champion and waste four bans.
                ranked = [b for b in g.get("bans") or [] if b.get("champion") not in gone]
                seats = sorted((a for grp in session.get("actions") or [] for a in grp or []
                                if isinstance(a, dict) and a.get("type") == "ban" and a.get("isAllyAction")
                                and a.get("isInProgress") and not a.get("completed")),
                               key=lambda a: int(a.get("actorCellId", 99)))
                mine = next((a for a in seats if a.get("actorCellId") == slot.cell_id), None)
                hovered = champ_map.name_for(int((mine or {}).get("championId") or 0))
                if len(seats) > 1:
                    # One allocation every seat's app computes alike: a hover reserves its ban for the lowest
                    # seat hovering it; every other seat (no hover, or a duplicate) takes the next free ban
                    # in seat order. Nobody is offered a ban another seat holds.
                    owner: dict[str, int] = {}
                    for a in seats:
                        name = champ_map.name_for(int(a.get("championId") or 0))
                        if name and name not in gone and name not in owner:
                            owner[name] = int(a.get("actorCellId", 99))
                    if hovered and owner.get(hovered) == slot.cell_id:
                        best = next((b for b in ranked if b.get("champion") == hovered), None) \
                            or {"champion": hovered, "champion_id": champ_map.id_for(hovered), "reasons": []}
                    else:
                        free = [b for b in ranked if b.get("champion") not in owner]
                        undecided = [int(a.get("actorCellId", 99)) for a in seats if int(a.get("actorCellId", 99)) not in owner.values()]
                        k = undecided.index(slot.cell_id) if slot.cell_id in undecided else len(free)
                        best = free[k] if k < len(free) else None
                else:
                    best = ranked[0] if ranked else None
                if best:
                    reason = (best.get("reasons") or [""])[0]
                    if isinstance(reason, dict):        # the shared backend's reasons are objects, the advisor's text
                        edge = reason.get("counter_edge")
                        reason = (f"beats our {reason.get('our_champion')} {reason.get('role')}"
                                  + (f" by {float(edge):+.1f}" if isinstance(edge, (int, float)) else "")
                                  if reason.get("our_champion") else str(reason.get("reason") or ""))
                    view["suggestion"] = {"kind": "ban", "champion": best["champion"], "champion_id": best.get("champion_id"),
                                          "reason": reason}
            else:
                # A teammate may have locked a champion for this seat's role; then this seat picks the vacated role.
                picking_role = slot.role
                for sw in g.get("swaps") or []:
                    if sw["to_cell"] == slot.cell_id:
                        picking_role = sw["from_role"]
                view["picking_role"] = picking_role
                # FLEXERS: never offer a champion another seat already took or a ban removed, and when the
                # ranked picks carry nothing for this seat's role, fall back to the leading draft's champion
                # for that role rather than the top pick of a different role.
                taken = set(state.our_locked().values()) | set(state.enemy_locked().values()) | set(state.our_bans) | set(state.enemy_bans)
                mine = [p for p in g.get("picks") or [] if p["role"] == picking_role and p["champion"] not in taken
                        and self.can_pick(p["champion"])]
                if not mine:
                    for comp in (shared or {}).get("comps") or g.get("comps") or []:
                        planned = (comp.get("champions") or {}).get(picking_role)
                        if planned and planned not in taken and self.can_pick(planned):
                            mine = [{"champion": planned, "champion_id": comp.get("champion_ids", {}).get(picking_role) or champ_map.id_for(planned),
                                     "role": picking_role, "why": ["from the leading draft"]}]
                            break
                if not mine and picking_role:
                    # Nothing planned for this seat (a practice lobby, a stranger's role): this player's own
                    # best comfort pick for the role, never another seat's champion.
                    comfort = assist.comfort_picks(picking_role, taken | set(state.never_pick), self.tierlist(), self.matchups,
                                                   comfort=self.comfort, pool=slot.pool.get(picking_role, []),
                                                   avoid=set(slot.avoid), limit=12)
                    mine = [{**p, "why": ["your comfort pick for " + picking_role]} for p in comfort if self.can_pick(p["champion"])][:1]
                if mine:
                    best = mine[0]
                    view["suggestion"] = {"kind": "pick", "champion": best["champion"], "champion_id": best.get("champion_id"),
                                          "role": best["role"], "reason": (best.get("why") or [""])[0]}
        # Expected champion swap involving this seat (after both locks).
        for sw in g.get("swaps") or []:
            if sw["from_cell"] == slot.cell_id:
                partner = next((p for p in state.our_team if p.cell_id == sw["to_cell"]), None)
                trade = client_actions.trade_for_cell(session, sw["to_cell"])
                view["swap"] = {"with_cell": sw["to_cell"], "with_player": sw["to_player"], "give": sw["champion"],
                                "receive": partner.locked if partner else None, "to_role": sw["to_role"],
                                "trade_id": int(trade["id"]) if trade else None,
                                "trade_state": str(trade.get("state") or "") if trade else None,
                                "can_request": bool(trade and str(trade.get("state") or "").upper() == "AVAILABLE" and view["client"])}
        incoming = client_actions.incoming_trade(session)
        if incoming is not None:
            from_cell = int(incoming.get("cellId", -1))
            partner = next((p for p in state.our_team if p.cell_id == from_cell), None)
            expected = any(sw["to_cell"] == slot.cell_id and sw["from_cell"] == from_cell for sw in g.get("swaps") or [])
            view["incoming_swap"] = {"trade_id": int(incoming["id"]), "from_cell": from_cell,
                                     "from_player": partner.display_name if partner else f"seat {from_cell}",
                                     "champion": partner.locked if partner else None, "expected": expected,
                                     "can_accept": view["client"]}
        return view

    async def client_act(self, what: str, champion: str | None = None, trade_id: int | None = None, *,
                         action_id: int | None = None, family: str | None = None, swap_id: int | None = None,
                         verb: str | None = None, kind: str | None = None, game_id: object = MISSING) -> dict:
        """hover | lock | swap_request | swap_accept | swap_decline | seat_swap through the local client.

        ``action_id`` is the client action the clicked button was drawn for: a click from a screen that
        has since moved on (our ban became our pick, a new game) is refused instead of applied to
        whatever action is current now."""
        session = self.last_session
        live = bool(session) and self.state is not None and self.state.source == "lcu"
        if live and self.status.get("stale"):
            raise RuntimeError("League client state is stale; wait for it to reconnect")
        if what in ("hover", "lock") and live and (action_id is None or kind is None or game_id is MISSING):
            raise RuntimeError("this button is from an older screen: refresh and choose again")
        if what in ("hover", "lock") and live and str(game_id) != str(session.get("gameId")):
            raise RuntimeError("that choice is out of date: the draft has moved on, pick again")
        if what in ("hover", "lock") and (action_id is not None or kind is not None):
            # The same raw session object is handed to ClientActions below, so the action checked here is
            # the action executed (its own localPlayerCellId, not the last published state's).
            cell = client_actions.local_cell(session or {})
            current = client_actions.active_action(session or {}, cell) if cell is not None else None
            if current is None or (action_id is not None and int(current.get("id", -1)) != int(action_id)) \
                    or (kind is not None and current.get("type") != kind):
                raise RuntimeError("that choice is out of date: the draft has moved on, pick again")
        if session and self.state is not None and self.state.source == "simulation":
            return await self._simulated_client_act(what, champion)
        if self.status.get("stale"):
            raise RuntimeError("League client state is stale; wait for it to reconnect")
        rest = self.client_rest()
        if rest is None:
            raise RuntimeError("no League client connected on this machine")
        if not session or self.state is None or self.state.source != "lcu":
            raise RuntimeError("this machine is not in champion select")
        view = self.local_view() or {}
        actions = client_actions.ClientActions(rest)
        if what in ("hover", "lock"):
            champion = champion or ((view.get("suggestion") or {}).get("champion"))
            if champion and kind == "pick" and not self.can_pick(champion):
                raise RuntimeError(f"You can't pick {champion} on this account (not owned or disabled)")
            if not champion:
                raise RuntimeError("nothing to accept: no suggestion for this seat")
            result = await asyncio.to_thread(getattr(actions, what), session, champion)
            self.log_event(f"client: {what} {champion}", "board")
        elif what == "swap_request":
            swap = view.get("swap")
            if not swap:
                raise RuntimeError("no champion swap is expected for this seat")
            result = await asyncio.to_thread(actions.request_trade, session, int(swap["with_cell"]))
            self.log_event(f"client: swap requested with {swap['with_player']}", "board")
        elif what == "swap_accept":
            result = await asyncio.to_thread(actions.accept_trade, session, trade_id)
            self.log_event("client: swap accepted", "board")
        elif what == "swap_decline":
            result = await asyncio.to_thread(actions.decline_trade, session, trade_id)
            self.log_event("client: swap declined", "board")
        elif what == "seat_swap":
            if family not in client_actions.SWAP_FAMILIES or verb not in ("request", "accept", "decline", "cancel") or swap_id is None:
                raise RuntimeError("seat swap needs a family, swap id and verb")
            result = await asyncio.to_thread(actions.swap, family, int(swap_id), verb)
            self.log_event(f"client: {'role' if family == 'position-swaps' else 'pick-order'} swap {verb}", "board")
            self.refresh_offers(force=True)
        else:
            raise RuntimeError(f"unknown client action: {what}")
        return result

    async def _simulated_client_act(self, what: str, champion: str | None) -> dict:
        """FLEXERS: demo-mode stand-in for the League client. Hover / lock land on the simulated session
        for this machine's seat; the scripted draft then carries on from its own next step."""
        import copy

        if what not in ("hover", "lock"):
            raise RuntimeError("champion swaps are not simulated")
        session = copy.deepcopy(self.last_session)
        cell = client_actions.local_cell(session)
        action = client_actions.active_action(session, cell) if cell is not None else None
        if action is None:
            raise RuntimeError("it is not this seat's turn")
        view = self.local_view() or {}
        champion = champion or ((view.get("suggestion") or {}).get("champion"))
        if not champion:
            raise RuntimeError("nothing to accept: no suggestion for this seat")
        from . import champions as champ_map

        cid = champ_map.id_for(champion)
        if not cid:
            raise RuntimeError(f"unknown champion: {champion}")
        action["championId"] = cid
        if action.get("type") == "pick":
            for member in session.get("myTeam") or []:
                if int(member.get("cellId", -1)) == cell:
                    member["championId"] = cid
        if what == "lock":
            action["completed"] = True
            action["isInProgress"] = False
            if action.get("type") == "ban":
                session.setdefault("bans", {}).setdefault("myTeamBans", []).append(cid)
        self.log_event(f"simulated client: {what} {champion}", "board")
        await self.ingest_lcu_session(session, "ChampSelect", self.status.get("queue"), source="simulation")
        await self.settle()
        return {"action_id": int(action["id"]), "kind": action.get("type"), "champion": champion,
                "completed": what == "lock", "simulated": True}

    # ---- any-queue assistance ----------------------------------------------------------------
    def tierlist(self) -> dict:
        if self._tierlist is None:
            self._tierlist = assist.load_tierlist()
        return self._tierlist

    def _local_slot(self) -> ds.PlayerSlot | None:
        state = self.state
        if state is None or state.local_cell_id is None:
            return None
        return next((p for p in state.our_team if p.cell_id == state.local_cell_id), None)

    def _link_local_account(self, session: dict) -> None:
        """This PC's owner plays whatever account is logged in here: record it on their roster entry so
        the seat matches them (pools, priorities) on a smurf too."""
        from . import identity

        owner = identity.load_owner()
        player = self.roster.players.get(owner) if owner else None
        if player is None:
            return
        local = session.get("localPlayerCellId")
        me = next((m for m in session.get("myTeam") or [] if isinstance(m, dict) and m.get("cellId") == local), None)
        if not me:
            return
        riot_id = f"{me.get('gameName')}#{me.get('tagLine')}" if me.get("gameName") and me.get("tagLine") else None
        from . import my_champs

        if my_champs.link_unique(self.roster, player, riot_id, me.get("puuid")):
            self.profiles.record_accounts(player)
            self.roster.touch()
            self.log_event(f"this PC is {player.name}: linked {riot_id or 'this account'}", "roster")
            self.persist()

    def effective_source(self) -> str:
        """"discord" when this seat follows the team's shared drafts right now, else "app".

        auto follows only on affirmative evidence that the team is in this lobby (the bridge's
        ``control.team_present``); missing or old bridge data means the app's own ranking."""
        setting = str(self.roster.settings.get("draft_source") or "auto")
        if setting == "app":
            return "app"
        shared = self.intent_guidance
        if setting == "discord":
            return "discord" if isinstance(shared, dict) else "app"
        control = (shared or {}).get("control") or {}
        return "discord" if isinstance(shared, dict) and control.get("team_present") is True else "app"

    def shared_guidance(self) -> dict | None:
        """The bridge's guidance when it is in effect (see effective_source), else None."""
        return self.intent_guidance if self.effective_source() == "discord" else None

    def can_pick(self, champion: str | None) -> bool:
        """False only when the client's pickable list for this champ select is known and lacks it
        (not owned, disabled, or a free-rotation champion that left rotation)."""
        if not champion:
            return False
        if self._pickable is None or self._pickable_epoch != self._epoch(self.last_session):
            return True
        return champ_map.id_for(champion) in self._pickable

    def wanted_role(self, state: ds.DraftState | None = None) -> str | None:
        """The role this seat should end up in: the team's role lock for our verified identity, else
        our own "My role tonight" choice. Display names never authorise anything: a stranger called
        "Alice" in Solo/Duo is not the Alice the team draft placed."""
        state = state or self.state
        slot = None
        if state is not None and state.local_cell_id is not None:
            slot = next((p for p in state.our_team if p.cell_id == state.local_cell_id), None)
        control = (self.shared_guidance() or {}).get("control") or {}
        if slot is not None and slot.user_id and slot.player_key:
            for role, uid in (control.get("role_locks") or {}).items():
                try:
                    same = int(uid) == int(slot.user_id) or float(uid) == float(slot.user_id)
                except (TypeError, ValueError):
                    same = False
                if role in assist.ROLES and same:
                    return role
        try:
            role = json.loads((VAR_DIR / "my-role.json").read_text(encoding="utf-8")).get("role")
        except (OSError, ValueError, AttributeError):
            role = None
        return role if role in assist.ROLES else None

    def _apply_wanted_role(self, state: ds.DraftState) -> None:
        """Blind pick and role-less lobbies: our seat plays the role we chose, not a seat-order guess.

        Only a seat whose role is a guess (or unknown) moves; whoever was guessed into the wanted role
        takes our old guess. Client-assigned and champion-inferred roles are left alone."""
        slot = None
        if state.local_cell_id is not None:
            slot = next((p for p in state.our_team if p.cell_id == state.local_cell_id), None)
        if slot is None or slot.role_source not in ("guessed", "unknown"):
            return
        wanted = self.wanted_role(state)
        if wanted is None or slot.role == wanted:
            return
        holder = next((p for p in state.our_team if p is not slot and p.role == wanted), None)
        if holder is not None and holder.role_source not in ("guessed", "unknown"):
            return
        if holder is not None:
            holder.role = slot.role
        slot.role, slot.role_source = wanted, "wanted"

    def _team_targets(self, state: ds.DraftState | None) -> dict[int, tuple]:
        """{cell: (planned role, planned pick slot)} for every seat the team draft pins (verified ids only)."""
        locks = ((self.shared_guidance() or {}).get("control") or {}).get("role_locks") or {}
        out: dict[int, tuple] = {}
        for slot in (state.our_team if state is not None else []):
            if not slot.user_id or not slot.player_key:
                continue
            for role, uid in locks.items():
                try:
                    same = int(uid) == int(slot.user_id)
                except (TypeError, ValueError):
                    same = False
                if role in assist.ROLES and same:
                    out[slot.cell_id] = (role, self.wanted_pick_slot(role))
        return out

    def wanted_pick_slot(self, role: str | None) -> int | None:
        """Our 0-based pick slot in the leading draft's reveal order (counter-pick roles go last)."""
        if role not in assist.ROLES:
            return None
        comps = (self.shared_guidance() or {}).get("comps") or []
        rows = (comps[0].get("pick_order") if comps and isinstance(comps[0], dict) else None) \
            or (self.guidance or {}).get("pick_order") or []
        order = [r.get("role") for r in rows if isinstance(r, dict) and r.get("role") in assist.ROLES]
        return order.index(role) if role in order else None

    def assist_view(self) -> dict | None:
        """Enemy pick prediction, comfort-first picks for this seat and the swap plan."""
        state = self.state
        if state is None or state.source not in ("lcu", "simulation"):
            return None
        tier = self.tierlist()
        try:
            out: dict = {"patch": tier.get("patch"), "enemy": assist.predict_enemy(state, tier, self.matchups)}
            slot = self._local_slot()
            if slot is not None:
                local = self.local_view() or {}
                role = local.get("picking_role") or slot.role
                # Only a lane we are sure of gets matchup bonuses; a flex pick's guessed lane does not.
                enemy = next((p.locked for p in state.enemy_team
                              if p.locked and p.role == role and assist.confident_lane(p, tier)), None)
                out["role"] = role
                out["picks"] = [p for p in assist.comfort_picks(
                    role, state.unavailable() | set(state.never_pick), tier, self.matchups, comfort=self.comfort,
                    pool=slot.pool.get(role, []), enemy_laner=enemy, avoid=set(slot.avoid), limit=12)
                    if self.can_pick(p["champion"])][:6] if role else []
                target = self.wanted_role()
                out["swaps"] = assist.swap_plan(self.last_session or {}, target, self.wanted_pick_slot(target),
                                                self._team_targets(state))
                out["comfort_known"] = bool(self.comfort)
                current = self._offers if self._offers_epoch == self._epoch(self.last_session) else {}
                names = {p.cell_id: p.display_name for p in state.our_team}
                out["offers"] = assist.swap_offer_rows(self.last_session or {}, current, names)
                out["auto_swap"] = bool(self.roster.settings.get("auto_swap", True))
            return out
        except Exception as exc:  # noqa: BLE001 - assistance must never break the snapshot
            log.exception("assist failed")
            return {"error": str(exc)}

    def _draft_token(self) -> str:
        """Names this champion select for this account and scoring generation (a dodge or a new queue
        bumps the generation), so a click drawn for an earlier screen cannot act on a later one even
        when the client reuses game ids or cells."""
        import hashlib
        return hashlib.sha256(repr(self._epoch(self.last_session)).encode("utf-8")).hexdigest()[:16]

    def _epoch(self, session: dict | None) -> tuple:
        """What a background client action was decided for: scoring generation, game, our cell, us."""
        session = session or {}
        me = next((m for m in session.get("myTeam") or [] if isinstance(m, dict)
                   and m.get("cellId") == session.get("localPlayerCellId")), {})
        return (self._generation, session.get("gameId"), session.get("localPlayerCellId"), me.get("puuid"))

    def _epoch_current(self, epoch: tuple) -> bool:
        return self.last_session is not None and self._epoch(self.last_session) == epoch \
            and self.status.get("connection") == "connected" and not self.status.get("stale")

    def _after_live_session(self, session: dict, state: ds.DraftState) -> None:
        """Per live session update: load this seat's mastery once per game, then chase the plan."""
        epoch = self._epoch(session)
        game = (epoch[1], epoch[3])
        loop = asyncio.get_running_loop()
        if self._comfort_game != game:
            self._comfort_game = game
            self.comfort = {}
            self._swaps_done.clear()
            loop.create_task(self._load_comfort(game))
        if self._swap_wanted(epoch) and (self._swap_task is None or self._swap_task.done()):
            self._swap_task = loop.create_task(self._auto_swap(session, state, epoch))
        self.refresh_offers(epoch)

    def refresh_offers(self, epoch: tuple | None = None, *, force: bool = False) -> None:
        """Re-read the client's swap offers and pickable champions (at most once a second unless forced).
        A forced refresh asked for while one is running is not dropped: it runs right after."""
        epoch = epoch or self._epoch(self.last_session)
        if self._offers_task is not None and not self._offers_task.done():
            self._offers_again = self._offers_again or force
            return
        if not force and epoch == self._offers_epoch and time.time() - self._offers_at < 1.0:
            return
        self._offers_task = asyncio.get_running_loop().create_task(self._load_offers(epoch))
        if self._offers_poll is None or self._offers_poll.done():
            self._offers_poll = asyncio.get_running_loop().create_task(self._poll_offers())

    async def _poll_offers(self) -> None:
        """While this client is in champion select, keep the swap buttons fresh without waiting for events."""
        while self.last_session is not None and self.status.get("lcu_in_champ_select"):
            await asyncio.sleep(2.0)
            if self.last_session is not None and self.status.get("lcu_in_champ_select"):
                self.refresh_offers()

    async def _load_offers(self, epoch: tuple) -> None:
        rest = self.client_rest()
        if rest is None:
            return
        actions = client_actions.ClientActions(rest)
        offers: dict = {}
        for family in client_actions.SWAP_FAMILIES:   # each resource on its own: one failing never hides the others
            try:
                offers[family] = await asyncio.to_thread(actions.swap_offers, family)
            except Exception as exc:  # noqa: BLE001
                log.debug("%s unavailable: %s", family, exc)
        try:
            pickable = await asyncio.to_thread(rest.get, "/lol-champ-select/v1/pickable-champion-ids")
        except Exception as exc:  # noqa: BLE001
            log.debug("pickable champions unavailable: %s", exc)
            pickable = None
        self._offers_at = time.time()
        if self._epoch(self.last_session) == epoch:
            changed = False
            if isinstance(pickable, list):
                fresh = {int(c) for c in pickable if str(c).lstrip("-").isdigit()}
                if fresh != self._pickable or epoch != self._pickable_epoch:
                    self._pickable, self._pickable_epoch = fresh, epoch
                    changed = True
            merged = {**(self._offers if epoch == self._offers_epoch else {}), **offers}
            if merged != self._offers or epoch != self._offers_epoch:
                self._offers, self._offers_epoch = merged, epoch
                changed = True
            if changed:
                await self._push()
        if self._offers_again:
            self._offers_again = False
            self._offers_task = asyncio.get_running_loop().create_task(self._load_offers(self._epoch(self.last_session)))

    async def load_comfort_now(self) -> None:
        """Mastery outside champion select (the "My champs" tab); a game in progress keeps its own."""
        rest = self.client_rest()
        if rest is None:
            return
        try:
            rows = await asyncio.to_thread(rest.get, "/lol-champion-mastery/v1/local-player/champion-mastery")
        except Exception as exc:  # noqa: BLE001
            log.warning("champion mastery unavailable: %s", exc)
            return
        if not self.comfort:
            self.comfort = assist.comfort_from_mastery(rows if isinstance(rows, list) else [])

    async def _load_comfort(self, game: tuple) -> None:
        rest = self.client_rest()
        if rest is None:
            return
        try:
            rows = await asyncio.to_thread(rest.get, "/lol-champion-mastery/v1/local-player/champion-mastery")
        except Exception as exc:  # noqa: BLE001
            log.warning("champion mastery unavailable: %s", exc)
            return
        if self._comfort_game != game:
            return   # a new game / account arrived while this was loading
        self.comfort = assist.comfort_from_mastery(rows if isinstance(rows, list) else [])
        await self._push()

    def _swap_wanted(self, epoch: tuple) -> bool:
        """Chase the planned role and pick slot: always with Auto on, else only for the game in which the
        player pressed the one-button swap (a new game goes back to hands-off)."""
        return bool(self.roster.settings.get("auto_swap", True)) or self._chase_game == (epoch[1], epoch[3])

    async def chase_plan(self, game_id: object = MISSING, cell_id: object = MISSING, token: object = MISSING) -> dict:
        """The overlay's one swap button: request / accept every swap that moves this seat to the team
        draft's role and pick slot, now and for the rest of this champion select.

        ``game_id`` / ``cell_id`` are the screen the button was drawn for: a click from before a dodge
        or on another seat is refused rather than applied to whatever draft is current now."""
        session, state = self.last_session, self.state
        if not session or state is None or state.source != "lcu":
            raise RuntimeError("this machine is not in champion select")
        if game_id is MISSING or cell_id is MISSING or token is MISSING or str(game_id) != str(session.get("gameId")) \
                or str(cell_id) != str(session.get("localPlayerCellId")) or str(token) != self._draft_token():
            raise RuntimeError("that button is from an older screen: the draft has moved on")
        if self.client_rest() is None:
            raise RuntimeError("no League client connected on this machine")
        target = self.wanted_role(state)
        if target is None:
            raise RuntimeError("no planned role for this seat: send a team draft or choose your role first")
        epoch = self._epoch(session)
        self._chase_game = (epoch[1], epoch[3])
        if self._swap_task is None or self._swap_task.done():   # one worker at a time
            self._swap_task = asyncio.get_running_loop().create_task(self._auto_swap(session, state, epoch))
            await asyncio.wait({self._swap_task}, timeout=10)
        self.refresh_offers(force=True)
        return {"target_role": target, "target_pick_slot": self.wanted_pick_slot(target),
                "pending": self._swap_task is not None and not self._swap_task.done()}

    async def _auto_swap(self, session: dict, state: ds.DraftState, epoch: tuple) -> None:
        """Request the swap towards the wanted role / pick slot and accept offers that move us there.

        Everything is decided for ``state``/``epoch`` and re-checked after every await: a dodge, a new
        game, another local seat or a disconnect cancels the rest. Each (swap id, verb) is tried once
        per game, no new request goes out while one of ours is pending in either family, and nothing is
        declined automatically."""
        target = self.wanted_role(state)
        rest = self.client_rest() if target else None
        if rest is None:
            return
        actions = client_actions.ClientActions(rest)
        session = dict(session)
        try:
            for family, key in (("position-swaps", "positionSwaps"), ("pick-order-swaps", "pickOrderSwaps")):
                if key not in session:
                    session[key] = await asyncio.to_thread(actions.swap_offers, family)
        except Exception as exc:  # noqa: BLE001
            log.debug("swap offers unavailable: %s", exc)
            return
        if not self._epoch_current(epoch) or not self._swap_wanted(epoch):
            return
        plan = assist.swap_plan(session, target, self.wanted_pick_slot(target), self._team_targets(state))
        goal = (target, self.wanted_pick_slot(target))      # a move tried for one plan may be retried for another
        todo = [(row["family"], row["swap_id"], "accept", row["with_cell"]) for row in plan["accept"]]
        pending = any(str((o or {}).get("state") or "").upper() == "SENT"
                      for o in list(session.get("positionSwaps") or []) + list(session.get("pickOrderSwaps") or [])
                      if isinstance(o, dict))
        if not pending:
            for kind in ("position", "pick_order"):   # one outgoing request at a time; the role swap first
                row = plan.get(kind)
                if row and row["state"] == "AVAILABLE" and row["swap_id"] is not None \
                        and (row["family"], row["swap_id"], "request", row["with_cell"], goal) not in self._swaps_done:
                    todo.append((row["family"], row["swap_id"], "request", row["with_cell"]))
                    break
        for family, swap_id, verb, cell in todo:
            key = (family, swap_id, verb, cell, goal)
            if swap_id is None or key in self._swaps_done:
                continue
            if not self._epoch_current(epoch) or not self._swap_wanted(epoch) \
                    or self.wanted_role(self.state) != target:
                return   # the game, our seat, the setting or the wanted role changed while we waited
            try:
                fresh = {f: await asyncio.to_thread(actions.swap_offers, f) for f in client_actions.SWAP_FAMILIES}
            except Exception as exc:  # noqa: BLE001
                log.debug("swap offers unavailable: %s", exc)
                return
            if not self._epoch_current(epoch) or not self._swap_wanted(epoch) or self.wanted_role(self.state) != target:
                return
            offer = next((o for o in fresh.get(family) or [] if isinstance(o, dict) and o.get("id") == swap_id), None)
            wanted_state = "AVAILABLE" if verb == "request" else "RECEIVED"
            if offer is None or str(offer.get("state") or "").upper() != wanted_state \
                    or str(offer.get("cellId")) != str(cell):
                continue
            # The team draft (and so every seat's planned slot) can change while we waited: decide again
            # from the latest session and these fresh offers, and act only if the same move still stands.
            latest = dict(self.last_session or session)
            latest["positionSwaps"], latest["pickOrderSwaps"] = fresh.get("position-swaps") or [], fresh.get("pick-order-swaps") or []
            again = assist.swap_plan(latest, target, self.wanted_pick_slot(target), self._team_targets(self.state))
            if verb == "accept":
                still = any(r["family"] == family and r["swap_id"] == swap_id and r["with_cell"] == cell for r in again["accept"])
            else:
                still = any((again.get(k) or {}).get("swap_id") == swap_id and (again.get(k) or {}).get("family") == family
                            for k in ("position", "pick_order"))
            if not still:
                continue
            if verb == "request" and any(str((o or {}).get("state") or "").upper() == "SENT"
                                         for rows in fresh.values() for o in rows or [] if isinstance(o, dict)):
                return   # one of ours went out meanwhile
            self._swaps_done.add(key)
            what = "role swap" if family == "position-swaps" else "pick-order swap"
            try:
                await asyncio.to_thread(actions.swap, family, int(swap_id), verb)
                self.log_event(f"auto {what} {verb} with seat {cell} (towards {target})", "board")
            except Exception as exc:  # noqa: BLE001
                self.log_event(f"auto {what} {verb} refused: {exc}", "info")
            await self._push()

    # ---- outputs ----------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "status": dict(self.status),
            "state": self.state.to_dict() if self.state else None,
            "guidance": self.guidance,
            "draft": self.draft.to_dict(),
            "draft_type": self.draft_type,
            "draft_types": {k: {"label": v["label"], "description": v["description"]} for k, v in DRAFT_TYPES.items()},
            "series": list(self.roster.series),
            "lobby": self.lobby(),
            "local": self.local_view(),
            "last_change": self.last_change,
            "roster": self.roster.summary(),
            "events": list(self.events)[-40:],
            "imports": list(self.roster.history)[-12:],
            "engine_data": engine_data_info(),
            "assist": self.assist_view(),
            "draft_source": {"setting": str(self.roster.settings.get("draft_source") or "auto"),
                             "effective": self.effective_source()},
        }

    async def _push(self) -> None:
        if self.broadcast:
            await self.broadcast(self.snapshot())

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot(), default=_json_default)


def engine_data_info() -> dict:
    from . import ENGINE_DIR

    out = {}
    for name in ("league_comp_meta.json", "league_lane_matchups.json", "league_player_profiles.json",
                 "league_recent_pro.json", "league_role_champions.json"):
        path = ENGINE_DIR / "data" / name
        try:
            stat = path.stat()
            out[name] = {"size": stat.st_size, "modified": stat.st_mtime}
        except OSError:
            out[name] = None
    try:
        out["league_comp_meta.json"]["generated_at"] = engine.load_meta().get("generated_at")
        out["league_lane_matchups.json"]["generated_at"] = engine.load_lane_matchups().get("generated_at")
    except Exception:  # noqa: BLE001
        pass
    return out


def _json_default(value: Any):
    if isinstance(value, set):
        return sorted(value)
    return str(value)
