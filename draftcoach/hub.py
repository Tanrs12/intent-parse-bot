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
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import advisor, loadfiles
from . import client_actions
from . import state as ds
from .draft_order import DRAFT_TYPES, ManualDraft, normalise_type
from .roster import Roster

import league_comp_engine as engine  # type: ignore  # noqa: E402

log = logging.getLogger("draftcoach.hub")
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
        self.rest = None                 # LcuRest of the connected client (set by the server)
        self.lockfile: str | None = None # explicit lockfile path, if the server was given one
        self.lcu_enabled = False         # the server runs a client watcher (lockfile fallback is allowed)
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
    async def ingest_lcu_session(self, session: dict | None, gameflow_phase: str | None, source: str = "lcu") -> None:
        self.last_session = session
        if not session:
            self.status["lcu_in_champ_select"] = False
            if self.state is not None and self.state.source in ("lcu", "simulation"):
                if DRAFT_TYPES[self.draft_type]["fearless"] and len(self.state.our_locked()) == 5:
                    await self.series_record(self.state)
                self.log_event("left champion select", "phase")
                self._last_input = None
                await self.clear(detail=f"not in champ select (phase {gameflow_phase or 'None'})", source=source)
            return
        self.status["lcu_in_champ_select"] = source == "lcu"
        self._last_input = ("lcu", session, gameflow_phase, source)
        state = ds.state_from_lcu_session(session, self.roster, gameflow_phase)
        state.source = source
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
            _, session, phase, source = self._last_input
            state = ds.state_from_lcu_session(session, self.roster, phase)
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
        self.state = None
        self.guidance = None
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
                seq = self._submitted_seq
                self._pending = None
                started = time.perf_counter()
                try:
                    guidance = await asyncio.to_thread(advisor.advise, state, self.roster, meta=self.meta, matchups=self.matchups)
                except Exception as exc:  # noqa: BLE001
                    log.exception("advice failed")
                    guidance = {"error": str(exc), "headline": "Scoring failed", "phase_kind": "error", "picks": [], "bans": [],
                                "counters": [], "comps": [], "notes": [str(exc)], "alerts": [{"level": "error", "text": str(exc)}],
                                "likely_enemy": [], "flex": [], "roles_now": [], "picking_now": False, "draft_complete": False,
                                "our_locked": {}, "enemy_locked": {}, "swaps": []}
                elapsed = (time.perf_counter() - started) * 1000
                self._diff_events(self._prev_state, state)
                change = self._explain_change(self._prev_state, self._prev_guidance, state, guidance)
                if change:
                    self.last_change = change
                guidance["last_change"] = self.last_change
                self._prev_state, self._prev_guidance = state, guidance
                self.state = state
                self.guidance = guidance
                self.status.update({"source": state.source, "updated_at": time.time(), "compute_ms": round(elapsed, 1), "stale": False})
                self._scored_seq = seq
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
                effect = f"{prev_top} is gone, {new_top} leads now" if new_top else f"{prev_top} is gone"
            elif new_top:
                effect = f"{new_top} now leads over {prev_top}"
        elif removed:
            effect = f"{', '.join(removed)} dropped, {new_top} still leads"
        elif new_top and prev_top == new_top:
            effect = f"{new_top} still leads"
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
        if state is None or state.source != "lcu" or state.local_cell_id is None or g is None:
            return None
        slot = next((p for p in state.our_team if p.cell_id == state.local_cell_id), None)
        if slot is None:
            return None
        view: dict = {"cell_id": slot.cell_id, "name": slot.display_name, "role": slot.role, "locked": slot.locked,
                      "hover": slot.hover, "kind": None, "can_act": False, "suggestion": None, "swap": None,
                      "incoming_swap": None, "client": self.client_rest() is not None}
        session = self.last_session or {}
        action = client_actions.active_action(session, slot.cell_id)
        if action is not None and state.active.ours:
            view["kind"] = action.get("type")
            view["action_id"] = int(action["id"])
            view["can_act"] = view["client"]
            if action.get("type") == "ban":
                best = next(iter(g.get("bans") or []), None)
                if best:
                    view["suggestion"] = {"kind": "ban", "champion": best["champion"], "champion_id": best.get("champion_id"),
                                          "reason": (best.get("reasons") or [""])[0]}
            else:
                # A teammate may have locked a champion for this seat's role; then this seat picks the vacated role.
                picking_role = slot.role
                for sw in g.get("swaps") or []:
                    if sw["to_cell"] == slot.cell_id:
                        picking_role = sw["from_role"]
                view["picking_role"] = picking_role
                mine = [p for p in g.get("picks") or [] if p["role"] == picking_role] or list(g.get("picks") or [])
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

    async def client_act(self, what: str, champion: str | None = None, trade_id: int | None = None) -> dict:
        """hover | lock | swap_request | swap_accept | swap_decline through the local client."""
        rest = self.client_rest()
        if rest is None:
            raise RuntimeError("no League client connected on this machine")
        session = self.last_session
        if not session or self.state is None or self.state.source != "lcu":
            raise RuntimeError("this machine is not in champion select")
        view = self.local_view() or {}
        actions = client_actions.ClientActions(rest)
        if what in ("hover", "lock"):
            champion = champion or ((view.get("suggestion") or {}).get("champion"))
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
        else:
            raise RuntimeError(f"unknown client action: {what}")
        return result

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
