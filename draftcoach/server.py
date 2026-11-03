"""FastAPI server: coach panel, OBS overlay, REST API and websocket stream.

Pages
-----
GET  /                       coach panel (manual board, roster, loadfile import, guidance)
GET  /overlay                transparent overlay for OBS / a second monitor
GET  /demo                   mock champion select with the overlay on top

Live data
---------
GET  /ws                     websocket: a full snapshot on every change
GET  /api/state              the latest snapshot as JSON

Draft inputs
------------
POST /api/draft/action       {"champion": "Ornn", "role": "Top"?, "cell_id": 0?}   manual board: next ban/pick
POST /api/draft/undo | redo | reset {"side": "blue"?} | hover {"team": "R", "champion": ...} | role {"index", "role"}
POST /api/draft/type         {"draft_type": "tournament" | "ranked" | "fearless"}
POST /api/series/next | clear   fearless series: record this game's picks and start the next / forget the series
POST /api/roster/link        {"riot_id": "Name#TAG", "player_key": "chud"} or {"riot_id": ..., "name": "New player"}
GET  /api/client/local       this machine's seat: suggestion, whether the client can act, expected swap
POST /api/client/hover|lock  {"champion"?}   act through the local League client for this seat
POST /api/client/swap_request | swap_accept | swap_decline {"trade_id"?}
POST /api/state              simple draft-state JSON (bots, scripts)
POST /api/lcu-session        raw champ-select session JSON (simulator)
DELETE /api/state            clear the current draft

Roster / loadfiles
------------------
GET  /api/roster             full roster
GET  /api/roster/export?format=json|csv
POST /api/import             {"name": "pools.json", "text": "..."} or a raw loadfile object; "?name=" for raw text
GET  /api/imports            import history
POST /api/roster/lineup      {"lineup": [player keys]}      POST /api/roster/enemy-lineup {"lineup": [opponent keys]}
DELETE /api/roster/player/{key} | /api/roster/opponent/{key}
GET  /api/champions          names and ids
GET  /api/champion-icon/{id} portrait (bundled locally, else proxied from the client)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import ENGINE_DIR, LOADFILE_DIR, ROOT, VAR_DIR, __version__, champions, gameplan, loadfiles
from .draft_order import ManualDraft
from . import assist, identity, my_champs

import league_comp_engine as engine  # type: ignore  # noqa: E402

ROLE_ORDER = ("Top", "Jungle", "Mid", "ADC", "Support")
from .hub import MISSING, Hub, _json_default
from .lcu import LcuWatcher
from .roster import Roster

log = logging.getLogger("draftcoach.server")
STATIC = Path(__file__).with_name("static")
CDRAGON_ART = {"splash": "https://cdn.communitydragon.org/latest/champion/{id}/splash-art",
               "centered": "https://cdn.communitydragon.org/latest/champion/{id}/splash-art/centered",
               "tile": "https://cdn.communitydragon.org/latest/champion/{id}/tile",
               "portrait": "https://cdn.communitydragon.org/latest/champion/{id}/portrait"}
ART_DIR_CANDIDATES = [ROOT / "art", ROOT.parent.parent / "MatchupMaster" / "webdemo" / "Matchup-Master-App" / "assets" / "porcelain"]


def art_slug(name: str) -> str:
    """Matchup Master's file naming: "Dr. Mundo" -> dr-mundo, "K'Sante" -> ksante, "Tahm Kench" -> tahm-kench."""
    return "-".join(str(name).casefold().replace("'", "").replace(".", "").replace("&", "").split())


def find_art_dir(configured: str | None) -> Path | None:
    for candidate in ([Path(configured)] if configured else []) + ART_DIR_CANDIDATES:
        if candidate.is_dir() and (candidate / "champions").is_dir():
            return candidate
    return None


def cached_remote_art(url: str, file: Path) -> bytes | None:
    """Keep artwork same-origin and retain successful downloads for offline use."""
    try:
        if file.is_file():
            return file.read_bytes()
        # CommunityDragon answers 403 to Python's default user agent.
        request = urllib.request.Request(url, headers={"User-Agent": f"FlexersDraftCoach/{__version__} (+https://github.com/Tanrs12/intent-parse-bot)"})
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(12 * 1024 * 1024 + 1)
        if len(data) > 12 * 1024 * 1024 or not data.startswith((b"\xff\xd8\xff", b"\x89PNG")):
            return None
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            temporary = file.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(file)
        except OSError:
            pass  # Read-only installations can still display the downloaded art.
        return data
    except (OSError, ValueError):
        return None


def load_config(path: str | Path | None) -> dict:
    if not path:
        return {}
    file = Path(path)
    if not file.is_file():
        log.info("config %s not found, starting with an empty roster", file)
        return {}
    return json.loads(file.read_text(encoding="utf-8-sig"))


def build_roster(config: dict, roster_path: Path | None, reset: bool = False) -> Roster:
    """Persisted roster (var/roster.json) wins over config.json unless reset."""
    if roster_path and roster_path.is_file() and not reset:
        try:
            roster = Roster.load(roster_path, auto_profiles=config.get("auto_profiles", True))
            roster.settings.update({k: v for k, v in (config.get("settings") or {}).items() if k in roster.settings})
            return roster
        except (OSError, ValueError) as exc:
            log.warning("could not load %s (%s), using config", roster_path, exc)
    return Roster.from_config(config)


def create_app(config: dict | None = None, *, enable_lcu: bool = True, lockfile: str | None = None,
               simulation: dict | None = None, roster_path: Path | None = None, watch_dir: Path | None = None,
               load_files: list[Path] | None = None, reset_roster: bool = False, sync_url: str | None = None,
               sync_interval: float = 60.0, any_queue: bool = False,
               profile_relay: tuple[str, str] | None = None) -> FastAPI:
    config = config or {}
    roster = build_roster(config, roster_path, reset=reset_roster)
    draft = ManualDraft(side=str(config.get("side") or "blue"))
    clients: set[WebSocket] = set()
    watcher: LcuWatcher | None = None
    hub: Hub

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal watcher
        tasks: list[asyncio.Task] = []
        for path in load_files or []:
            result = await hub.import_path(Path(path))
            log.info("loadfile %s: %s", path, "ok" if result.ok else result.errors)
        if simulation is not None:
            from . import simulate

            async def sink(session, description):
                log.info("simulation: %s", description)
                await hub.ingest_lcu_session(session, "ChampSelect" if session else "Lobby", source="simulation")

            await hub.set_status("simulation", "replaying a scripted draft")
            script = {"side": simulation.get("side", "blue")}
            accounts = [p.riot_ids[0] for p in roster.players.values() if p.riot_ids]
            if len(accounts) >= 5 and not simulation.get("fake_names"):
                script["our_names"] = accounts[:5]      # the lobby then matches real roster players
            tasks.append(asyncio.create_task(simulate.play(sink, delay=float(simulation.get("delay", 1.2)),
                                                           script=script, loop_forever=bool(simulation.get("loop", True)))))
        elif enable_lcu:
            hub.lockfile = lockfile or config.get("lockfile")
            hub.lcu_enabled = True
            watcher = LcuWatcher(on_session, on_status, lockfile=hub.lockfile)
            app.state.watcher = watcher
            tasks.append(asyncio.create_task(watcher.run()))
        else:
            await hub.set_status("manual", "no League client: use the board or POST /api/state")
        if watch_dir is not None:
            tasks.append(asyncio.create_task(_watch_loadfiles(hub, Path(watch_dir))))
        if sync_url:
            tasks.append(asyncio.create_task(_sync_roster(hub, sync_url, sync_interval)))
        # Team profiles: what is stored here wins over loadfiles imported at start (those are older), then
        # the relay exchange keeps every teammate's app converging.
        if hub.profiles.apply_to(roster):
            await hub.roster_changed("team profiles applied")
        if profile_relay and profile_relay[0] and profile_relay[1]:
            from .profile_sync import RelaySync

            hub.profile_relay = RelaySync(hub.profiles, profile_relay[0], profile_relay[1])
            tasks.append(asyncio.create_task(_exchange_profiles(hub)))
        # FLEXERS: keep the shared backend's latest picks and bans on the hub, so this seat's
        # suggestion (and the Hover / Lock buttons) exist even when the app computes nothing locally.
        tasks.append(asyncio.create_task(_poll_bridge(hub, str(config.get("bridge_url") or os.environ.get("INTENT_BRIDGE_URL") or "http://127.0.0.1:8766"))))
        if hub.state is None:
            await hub.ingest_manual()
            await hub.settle()
        try:
            yield
        finally:
            if watcher:
                watcher.stop()
            for task in tasks:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            hub.persist()

    app = FastAPI(title="Flexers Draft Coach", version=__version__, lifespan=lifespan)

    async def broadcast(snapshot: dict) -> None:
        if not clients:
            return
        message = json.dumps(snapshot, default=_json_default)
        dead = []
        for ws in list(clients):
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    # INTent live mode gets its ranked recommendations from the shared bridge, so Rat's speculative
    # prewarm passes only duplicate work. Keep Rat's original default everywhere else.
    prewarm = 0 if os.environ.get("INTENT_LIVE") == "1" else int(config.get("prewarm", 3))
    hub = Hub(roster, draft, broadcast, debounce_ms=int(config.get("debounce_ms", 30)), prewarm=prewarm, roster_path=roster_path)
    art_dir = find_art_dir(config.get("art_dir"))
    app.state.art_dir = art_dir
    art_cache: dict[str, bytes] = {}
    art_disk = Path(config.get("art_cache_dir") or VAR_DIR / "champion-art")
    art_misses: dict[str, float] = {}
    icon_files = {"".join(c for c in p.stem.casefold() if c.isalnum()): p
                  for p in (ENGINE_DIR / "assets" / "league_champions").glob("*.png")}
    splash_paths: dict[int, dict] = {}
    app.state.hub = hub
    app.state.roster = roster
    # FLEXERS: pre-game drafts built here, from the same signup pools and engine as /league game.
    planner = gameplan.GamePlanner()
    app.state.planner = planner
    # FLEXERS: the INTent bridge serves on app port + 1 (8766 for the default 8765); several app
    # instances on one PC (the party test) each get their own pair.
    app.state.bridge_url = str(config.get("bridge_url") or os.environ.get("INTENT_BRIDGE_URL") or "http://127.0.0.1:8766")
    app.state.draft = draft
    app.state.config = config
    hub.any_queue = bool(any_queue)
    icon_cache: dict[int, bytes] = {}

    async def on_session(session: dict | None, phase: str | None, queue: dict | None = None) -> None:
        await hub.ingest_lcu_session(session, phase, queue)
        if session is None and hub.state is None:
            await hub.ingest_manual()

    async def _local_account(rest) -> dict | None:
        """Who is signed in to the League client, matched to the roster: lets the team room name this
        player (and the Game plan tick them) before champion select, instead of showing the PC's name."""
        def fetch() -> str:
            me = rest.get("/lol-summoner/v1/current-summoner") or {}
            return f"{me.get('gameName') or ''}#{me.get('tagLine') or ''}".strip("#")
        try:
            riot_id = await asyncio.to_thread(fetch)
        except Exception:  # noqa: BLE001 - the client is not ready for REST yet; the next status will try again
            return None
        if not riot_id:
            return None
        user_id = roster.profile_user_id_for(riot_id)
        player = roster.find_player(user_id=user_id) if user_id else None
        return {"riot_id": riot_id, "user_id": str(user_id) if user_id else None,   # a string: beyond JavaScript's exact integers
                "display_name": player.name if player is not None else None}

    async def on_status(state: str, detail: str) -> None:
        hub.rest = watcher.rest if (watcher and state == "connected") else None
        hub.status["local_account"] = await _local_account(hub.rest) if hub.rest is not None else None
        await hub.set_status(state, detail)

    app.state.local_account = _local_account      # exposed for tests

    # ---- pages --------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    @app.get("/coach", response_class=HTMLResponse)
    async def coach_page() -> FileResponse:
        return FileResponse(STATIC / "coach.html", media_type="text/html")

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page() -> FileResponse:
        return FileResponse(STATIC / "stats.html", media_type="text/html")

    @app.get("/studio", response_class=HTMLResponse)
    async def studio_page() -> FileResponse:
        return FileResponse(STATIC / "studio.html", media_type="text/html")

    @app.get("/overlay", response_class=HTMLResponse)
    async def overlay_page(layout: str | None = None) -> FileResponse:
        name = "overlay-full.html" if layout in ("full", "panel", "bar") else "overlay.html"
        return FileResponse(STATIC / name, media_type="text/html")

    @app.get("/hud", response_class=HTMLResponse)
    async def hud_page() -> FileResponse:
        return FileResponse(STATIC / "hud.html", media_type="text/html")

    @app.get("/demo", response_class=HTMLResponse)
    async def demo_page() -> FileResponse:
        return FileResponse(STATIC / "demo.html", media_type="text/html")

    @app.get("/static/{name:path}")
    async def static_file(name: str):
        file = (STATIC / name).resolve()
        if not file.is_file() or STATIC.resolve() not in file.parents:
            raise HTTPException(404)
        return FileResponse(file)

    # ---- live stream -------------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_text(hub.snapshot_json())
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            clients.discard(ws)

    @app.get("/api/state")
    async def get_state() -> Response:
        return Response(hub.snapshot_json(), media_type="application/json")

    @app.get("/api/intent-guidance")
    async def get_intent_guidance() -> JSONResponse:
        """LAN-safe relay for the coach PC's authoritative INTent result."""
        import urllib.request
        def fetch() -> dict:
            with urllib.request.urlopen(app.state.bridge_url + "/api/guidance", timeout=8) as response:
                return json.loads(response.read())
        try:
            return JSONResponse(await asyncio.to_thread(fetch))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"INTent bridge unavailable: {exc}")

    @app.post("/api/intent-control")
    async def set_intent_control(request: Request) -> JSONResponse:
        """Relay shared draft control (identity, hard locks, shake) to this PC's INTent bridge."""
        import urllib.request
        payload = await _json(request)
        def send() -> dict:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(app.state.bridge_url + "/api/control", data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.loads(response.read())
        try:
            return JSONResponse(await asyncio.to_thread(send))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"INTent control unavailable: {exc}")

    @app.post("/api/intent-preference")
    async def set_intent_preference(request: Request) -> JSONResponse:
        """Relay captain soft-preference changes to this coach PC's INTent bridge."""
        import urllib.request
        payload = await _json(request)
        def send() -> dict:
            data = json.dumps({"signature": payload.get("signature"), "control": payload.get("control")}).encode("utf-8")
            req = urllib.request.Request(app.state.bridge_url + "/api/preference", data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.loads(response.read())
        try:
            return JSONResponse(await asyncio.to_thread(send))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"INTent preference unavailable: {exc}")

    # ---- draft inputs --------------------------------------------------------------
    async def _json(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "expected a JSON body")
        if not isinstance(payload, dict):
            raise HTTPException(400, "expected a JSON object")
        return payload

    def _lcu_busy() -> bool:
        return hub.state is not None and hub.state.source in ("lcu", "simulation")

    async def _draft_reply() -> JSONResponse:
        await hub.settle()
        return JSONResponse(json.loads(hub.snapshot_json()))

    @app.get("/api/draft")
    async def get_draft() -> JSONResponse:
        return JSONResponse(draft.to_dict())

    @app.post("/api/draft/action")
    async def draft_action(request: Request) -> JSONResponse:
        payload = await _json(request)
        if _lcu_busy():
            raise HTTPException(409, f"a live draft ({hub.state.source}) is in progress; the board is read-only")
        try:
            action = draft.act(str(payload.get("champion") or ""), payload.get("role"), payload.get("cell_id"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        hub.log_event(f"board: {action.to_dict()['label']} {action.kind} {action.champion}", "board")
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/draft/undo")
    async def draft_undo() -> JSONResponse:
        action = draft.undo()
        if action:
            hub.log_event(f"board: undo {action.kind} {action.champion}", "board")
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/draft/redo")
    async def draft_redo() -> JSONResponse:
        draft.redo_last()
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/draft/reset")
    async def draft_reset(request: Request) -> JSONResponse:
        payload = await _json(request) if int(request.headers.get("content-length") or 0) else {}
        draft.reset(payload.get("side"))
        hub.log_event(f"board reset ({draft.side} side)", "board")
        hub._prev_state = None
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/draft/type")
    async def draft_type(request: Request) -> JSONResponse:
        payload = await _json(request)
        await hub.set_draft_type(str(payload.get("draft_type") or payload.get("type") or "tournament"))
        return await _draft_reply()

    @app.post("/api/series/next")
    async def series_next() -> JSONResponse:
        if _lcu_busy():
            await hub.series_record()
        else:
            await hub.series_next_game()
        return await _draft_reply()

    @app.post("/api/series/clear")
    async def series_clear() -> JSONResponse:
        await hub.series_clear()
        return await _draft_reply()

    @app.post("/api/roster/link")
    async def link_seat(request: Request) -> JSONResponse:
        payload = await _json(request)
        riot_id = str(payload.get("riot_id") or "")
        if not riot_id:
            raise HTTPException(400, "riot_id required")
        try:
            player = await hub.link_seat(riot_id, payload.get("player_key"), payload.get("name"))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        await hub.settle()
        return JSONResponse({"ok": True, "player": player, "lobby": hub.lobby()})

    @app.post("/api/draft/side")
    async def draft_side(request: Request) -> JSONResponse:
        payload = await _json(request)
        draft.side = "red" if str(payload.get("side", "blue")).casefold().startswith("r") else "blue"
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/draft/hover")
    async def draft_hover(request: Request) -> JSONResponse:
        payload = await _json(request)
        team = str(payload.get("team") or "R").upper()[:1]
        draft.hover(team, payload.get("champion"))
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/draft/role")
    async def draft_role(request: Request) -> JSONResponse:
        payload = await _json(request)
        try:
            draft.set_role(int(payload.get("index")), payload.get("role"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        await hub.ingest_manual()
        return await _draft_reply()

    @app.post("/api/state")
    async def post_state(request: Request) -> JSONResponse:
        payload = await _json(request)
        await hub.ingest_payload(payload)
        return await _draft_reply()

    @app.post("/api/lcu-session")
    async def post_lcu_session(request: Request) -> JSONResponse:
        payload = await request.json()
        session = payload.get("session") if isinstance(payload, dict) and "session" in payload else payload
        phase = payload.get("gameflow_phase") if isinstance(payload, dict) else None
        source = "lcu" if isinstance(payload, dict) and payload.get("source") == "lcu" else "simulation"
        queue = payload.get("queue") if isinstance(payload, dict) and isinstance(payload.get("queue"), dict) else None
        await hub.ingest_lcu_session(session or None, phase or "ChampSelect", queue, source=source)
        await hub.settle()
        return JSONResponse({"ok": True, "events": hub.status["events"], "compute_ms": hub.status["compute_ms"]})

    @app.delete("/api/state")
    async def clear_state() -> JSONResponse:
        draft.reset()
        hub._last_input = None
        await hub.clear("cleared")
        await hub.ingest_manual()
        return await _draft_reply()

    # ---- acting through this machine's League client -------------------------------------------
    @app.post("/api/window/open")
    async def open_window() -> JSONResponse:
        """Reopen the floating overlay after it was closed (the coach keeps running without it)."""
        supervisor = getattr(app.state, "window_supervisor", None)
        if supervisor is None:
            raise HTTPException(404, "this coach was started without the overlay window")
        started = await asyncio.to_thread(supervisor.open)
        return JSONResponse({"ok": True, "started": started, "running": supervisor.running})

    @app.get("/api/client/local")
    async def client_local() -> JSONResponse:
        return JSONResponse({"local": hub.local_view(), "client": hub.client_rest() is not None})

    @app.post("/api/client/{what}")
    async def client_act(what: str, request: Request) -> JSONResponse:
        payload = await _json(request) if int(request.headers.get("content-length") or 0) else {}
        if what == "swap_to_plan":
            try:
                return JSONResponse({"ok": True, "result": await hub.chase_plan(
                    payload["game_id"] if "game_id" in payload else MISSING,
                    payload["cell_id"] if "cell_id" in payload else MISSING,
                    payload["draft_token"] if "draft_token" in payload else MISSING)})
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if what not in ("hover", "lock", "swap_request", "swap_accept", "swap_decline", "seat_swap"):
            raise HTTPException(404, "unknown client action")
        try:
            result = await hub.client_act(what, payload.get("champion"), payload.get("trade_id"),
                                          action_id=payload.get("action_id"), family=payload.get("family"),
                                          swap_id=payload.get("swap_id"), verb=payload.get("verb"),
                                          kind=payload.get("kind"), game_id=payload["game_id"] if "game_id" in payload else MISSING)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return JSONResponse({"ok": True, "result": result})

    # ---- roster / loadfiles ------------------------------------------------------------
    @app.get("/api/roster")
    async def get_roster() -> JSONResponse:
        return JSONResponse(roster.to_dict())

    @app.get("/api/roster/export")
    async def export_roster(format: str = "json") -> Response:
        if format == "csv":
            return PlainTextResponse(loadfiles.export_csv(roster), media_type="text/csv",
                                     headers={"Content-Disposition": "attachment; filename=roster.csv"})
        return Response(json.dumps(loadfiles.export_loadfile(roster), indent=2, ensure_ascii=False),
                        media_type="application/json", headers={"Content-Disposition": "attachment; filename=roster-loadfile.json"})

    @app.post("/api/import")
    async def import_loadfile(request: Request, name: str | None = None) -> JSONResponse:
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        filename = name or "upload"
        text: str
        if "application/json" in content_type:
            try:
                payload = json.loads(body.decode("utf-8-sig"))
            except ValueError as exc:
                raise HTTPException(400, f"invalid JSON: {exc}")
            if isinstance(payload, dict) and "text" in payload and set(payload) <= {"name", "text"}:
                filename = str(payload.get("name") or filename)
                text = str(payload["text"])
                result = await hub.import_text(text, filename)
            elif isinstance(payload, dict) and "files" in payload:
                results = []
                for item in payload["files"]:
                    results.append((await hub.import_text(str(item.get("text", "")), str(item.get("name") or filename))).to_dict())
                await hub.settle()
                return JSONResponse({"results": results, "roster": roster.summary()})
            else:
                result = await hub.import_document(payload if isinstance(payload, dict) else {"players": payload}, filename)
        else:
            result = await hub.import_text(body.decode("utf-8-sig"), filename)
        await hub.settle()
        status = 200 if result.ok else 422
        return JSONResponse({"result": result.to_dict(), "roster": roster.summary()}, status_code=status)

    @app.get("/api/imports")
    async def import_history() -> JSONResponse:
        return JSONResponse(list(roster.history))

    @app.post("/api/roster/lineup")
    async def set_lineup(request: Request) -> JSONResponse:
        payload = await _json(request)
        roster.set_lineup(list(payload.get("lineup") or []))
        await hub.roster_changed("lineup changed")
        return await _draft_reply()

    @app.post("/api/roster/enemy-lineup")
    async def set_enemy_lineup(request: Request) -> JSONResponse:
        payload = await _json(request)
        roster.set_enemy_lineup(list(payload.get("lineup") or []))
        await hub.roster_changed("enemy lineup changed")
        return await _draft_reply()

    @app.post("/api/plan/commit")
    async def commit_plan(request: Request) -> JSONResponse:
        """Choose the composition the team is drafting towards (``{"id": "<plan id>"}``), or clear it with null."""
        payload = await _json(request) if int(request.headers.get("content-length") or 0) else {}
        wanted = str(payload.get("id") or "")
        if wanted and not any(p.get("id") == wanted for p in roster.playbook):
            raise HTTPException(404, f"unknown plan: {wanted}")
        result = await hub.import_document({"settings": {"committed_plan": wanted}}, "plan")
        await hub.settle()
        hub.log_event(f"plan: going with {wanted}" if wanted else "plan: released", "board")
        return JSONResponse({"result": result.to_dict(), "committed_plan": wanted or None})

    # ---- the game plan: five players -> ranked drafts, built in this app, no Discord needed -------
    @app.get("/api/gameplan")
    async def get_gameplan() -> JSONResponse:
        """The players this app can draft for and the last plan it built."""
        return JSONResponse(planner.snapshot(roster))

    @app.post("/api/gameplan")
    async def build_gameplan(request: Request) -> JSONResponse:
        """Build the drafts for ``{"players": [five Discord ids], "unavailable": [...], "token": N}``."""
        payload = await _json(request) if int(request.headers.get("content-length") or 0) else {}
        try:
            result = await planner.build(roster, payload.get("players") or [],
                                         unavailable=payload.get("unavailable"), token=payload.get("token"))
        except gameplan.PlanError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:  # noqa: BLE001 - the engine raised: say so instead of a bare 500
            log.exception("game plan failed")
            raise HTTPException(500, f"The draft engine hit an error: {exc.__class__.__name__}: {str(exc)[:200]}")
        hub.log_event(f"game plan: {len(result.get('recommendations') or [])} drafts for "
                      + ", ".join(str(p.get('display_name')) for p in result.get('players') or []), "info")
        return JSONResponse(result)

    @app.post("/api/roster/settings")
    async def set_settings(request: Request) -> JSONResponse:
        payload = await _json(request)
        result = await hub.import_document({"settings": payload}, "settings")
        await hub.settle()
        return JSONResponse({"result": result.to_dict(), "settings": roster.settings})

    @app.delete("/api/roster/player/{key}")
    async def delete_player(key: str) -> JSONResponse:
        if not roster.remove_player(key):
            raise HTTPException(404, "no such player")
        await hub.roster_changed(f"removed player {key}")
        return JSONResponse({"ok": True, "roster": roster.summary()})

    @app.delete("/api/roster/opponent/{key}")
    async def delete_opponent(key: str) -> JSONResponse:
        if not roster.remove_opponent(key):
            raise HTTPException(404, "no such opponent")
        await hub.roster_changed(f"removed opponent {key}")
        return JSONResponse({"ok": True, "roster": roster.summary()})

    @app.get("/api/config")
    async def get_config() -> JSONResponse:
        return JSONResponse({"version": __version__, "roster": roster.summary(), "settings": roster.settings,
                             "champions_known": len(champions.all_names()), "watch_dir": str(watch_dir) if watch_dir else None,
                             "roster_path": str(roster_path) if roster_path else None, "art_dir": str(art_dir) if art_dir else None})

    # ---- app update: the startup check, on demand from the Roster & imports drawer ----------------
    @app.get("/api/update")
    async def update_status() -> JSONResponse:
        from . import updater
        return JSONResponse(await asyncio.to_thread(updater.status, ROOT))

    @app.post("/api/update")
    async def update_now() -> JSONResponse:
        """Download the published build and hand over to the replacer; the server then shuts down and
        the replacer restarts the app on the new build (same flow as the startup prompt)."""
        from . import updater
        try:
            result = await asyncio.to_thread(updater.start, ROOT)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(409, str(exc)) from exc
        app.state.update_launched = True
        request_exit = getattr(app.state, "request_exit", None)
        if request_exit is not None:
            # Give the response time to leave before the listener closes.
            asyncio.get_running_loop().call_later(0.5, request_exit)
        log.info("update to %s launched from the app; shutting down for the replacer", result["commit"])
        return JSONResponse({"ok": True, **result})

    # ---- player review: this machine's own account, through its own client ----------------------
    stats_lock = asyncio.Lock()
    stats_state: dict = {"review": None, "error": None, "refreshing": False, "refreshed_at": None}

    async def _refresh_stats(count: int = 40) -> dict:
        from . import stats as player_stats

        rest = hub.client_rest()
        if rest is None:
            raise RuntimeError("no League client on this machine: open League and try again")
        payload = await asyncio.to_thread(player_stats.fetch_from_client, rest, count=count)
        review = await asyncio.to_thread(player_stats.build_review, payload)
        stats_state.update(review=review, error=None, refreshed_at=time.time())
        return review

    @app.get("/api/stats/me")
    async def get_my_stats(refresh: int = 0) -> JSONResponse:
        """OP.GG style review of the signed-in player's recent games (cached on disk, client refresh)."""
        from . import stats as player_stats

        puuid = None
        rest = hub.client_rest()
        if rest is not None:
            try:
                me = await asyncio.to_thread(rest.get, "/lol-summoner/v1/current-summoner")
                puuid = (me or {}).get("puuid")
            except Exception as exc:  # noqa: BLE001 - offline review remains readable
                stats_state["error"] = str(exc)
        previous = ((stats_state.get("review") or {}).get("account") or {}).get("puuid")
        if puuid and previous != puuid:
            stats_state.update(review=None, error=None, refreshed_at=None)
        if refresh or stats_state["review"] is None:
            if stats_state["review"] is None:
                cached = await asyncio.to_thread(player_stats.load_cached, puuid=puuid)
                if cached:
                    try:
                        stats_state["review"] = await asyncio.to_thread(player_stats.build_review, cached)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("cached stats unusable: %s", exc)
            if refresh or stats_state["review"] is None:
                if stats_lock.locked():
                    stats_state["refreshing"] = True
                else:
                    async with stats_lock:
                        stats_state["refreshing"] = True
                        try:
                            await _refresh_stats()
                        except Exception as exc:  # noqa: BLE001
                            stats_state["error"] = str(exc)
                        finally:
                            stats_state["refreshing"] = False
        return JSONResponse({"review": stats_state["review"], "error": stats_state["error"], "refreshing": stats_state["refreshing"],
                             "refreshed_at": stats_state["refreshed_at"], "client": hub.client_rest() is not None})

    # ---- "My champs": this PC's player, their pools per role and their best options -------------
    async def _account() -> dict:
        """The account signed in to this PC's League client (read only: links nothing)."""
        rest = hub.client_rest()
        if rest is None:
            raise HTTPException(409, "League client not detected on this PC")
        try:
            me = await asyncio.to_thread(rest.get, "/lol-summoner/v1/current-summoner") or {}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(409, f"League client did not answer: {exc}")
        game, tag = me.get("gameName") or me.get("displayName") or "", me.get("tagLine") or ""
        return {"riot_id": f"{game}#{tag}" if game and tag else (game or None), "puuid": me.get("puuid") or None,
                "game_name": game}

    async def _me() -> dict:
        """The account plus its player: the PC owner when this PC is linked (the account is then recorded
        on the owner unless another player owns it), else the player owning this exact account."""
        acct = await _account()
        owner = identity.load_owner()
        player = my_champs.account_player(roster, acct["riot_id"], acct["puuid"], owner)
        if player is not None and owner == player.key and my_champs.link_unique(roster, player, acct["riot_id"], acct["puuid"]):
            hub.profiles.record_accounts(player)
            await hub.roster_changed(f"this PC is {player.name}: linked {acct['riot_id']}")
        return {**acct, "player": player, "linked": player is not None and owner == player.key}

    @app.get("/api/me")
    async def get_me() -> JSONResponse:
        me = await _me()
        player = me["player"]
        if not hub.comfort:
            await hub.load_comfort_now()
        pools = {role: list(player.champions(role)) for role in ROLE_ORDER} if player else {role: [] for role in ROLE_ORDER}
        options = {}
        for role in ROLE_ORDER:
            rows = assist.comfort_picks(role, set(roster.never_pick), hub.tierlist(), hub.matchups,
                                        comfort=hub.comfort, pool=pools[role], avoid=set(player.avoid) if player else set(), limit=6)
            options[role] = [{k: r[k] for k in ("champion", "champion_id", "score", "win_rate", "comfort", "in_pool", "why")} for r in rows]
        relay = getattr(hub, "profile_relay", None)
        return JSONResponse({"team_sync": dict(relay.status) if relay else {"state": "off"},
                             "linked": me["linked"], "device_owner": identity.load_owner(),
                             "players": sorted(({"key": p.key, "name": p.name} for p in roster.players.values()),
                                               key=lambda p: p["name"].casefold()),
                             "riot_id": me["riot_id"], "name": player.name if player else me["game_name"],
                             "player_key": player.key if player else None, "roles": list(player.roles) if player else [],
                             "pools": pools, "options": options, "mastery_known": bool(hub.comfort),
                             "patch": hub.tierlist().get("patch")})

    @app.post("/api/me/owner")
    async def set_my_owner(request: Request) -> JSONResponse:
        """Link this PC to a roster player ({"player_key"}) or to a new one ({"new_name"}); {} unlinks.
        Validated before anything changes; the current account is recorded on the chosen player only."""
        payload = await _json(request)
        key = payload.get("player_key") or None
        new_name = str(payload.get("new_name") or "").strip()[:40]
        if key and key not in roster.players:
            raise HTTPException(404, "no such player")
        if payload.get("new_name") is not None and not new_name:
            raise HTTPException(400, "a name is needed")
        acct = await _account()
        if new_name:
            key = my_champs.new_player(roster, new_name, acct["riot_id"], acct["puuid"]).key
        identity.save_owner(key)
        if key:
            my_champs.link_unique(roster, roster.players[key], acct["riot_id"], acct["puuid"])
            hub.profiles.record_accounts(roster.players[key])
        await hub.roster_changed(f"this PC is {roster.players[key].name}" if key else "this PC unlinked")
        return JSONResponse({"ok": True, "device_owner": key})

    @app.post("/api/me/pool")
    async def set_my_pool(request: Request) -> JSONResponse:
        """Replace this player's pool for one role (order = priority). An account nobody owns gets its own
        new player (never adopted by display name)."""
        payload = await _json(request)
        role = payload.get("role")
        if role not in ROLE_ORDER:
            raise HTTPException(400, "role must be one of " + ", ".join(ROLE_ORDER))
        names, unknown = [], []
        for raw in payload.get("champions") or []:
            name = champions.resolve(str(raw))
            (names if name else unknown).append(name or str(raw))
        if unknown:
            raise HTTPException(400, "unknown champion: " + ", ".join(unknown))
        me = await _me()
        player = me["player"]
        if player is None:
            if not me["riot_id"]:
                raise HTTPException(409, "could not read this PC's Riot ID from the client")
            player = my_champs.new_player(roster, me["game_name"] or me["riot_id"], me["riot_id"], me["puuid"])
            my_champs.link_unique(roster, player, me["riot_id"], me["puuid"])
        pool = my_champs.set_pool(player, role, names)
        hub.profiles.record_pool(player, role)
        hub.profiles.record_accounts(player)
        await hub.roster_changed(f"{player.name}: {role} pool set ({len(pool)})")
        return JSONResponse({"ok": True, "player_key": player.key, "pool": pool})

    # ---- Draft Studio --------------------------------------------------------------------------
    def _lineup(payload: dict) -> dict:
        raw = payload.get("lineup") or {}
        return {role: (raw.get(role) if raw.get(role) in roster.players else None) for role in ROLE_ORDER}

    def _names(values) -> list[str]:
        return [c for c in (champions.resolve(str(v)) for v in values or []) if c]

    @app.post("/api/data/refresh")
    async def refresh_data() -> JSONResponse:
        """Pull the latest shared drafting data (player histories, matchups, pro pairs) now. Bad or partial
        downloads never replace the last good data; app code itself only changes through app updates."""
        import sys as _sys

        bridge = _sys.modules.get("intent_live_draft_bridge")
        if bridge is None or not hasattr(bridge, "refresh_shared_engine_data"):
            raise HTTPException(409, "drafting data refresh needs the INTent bridge (start with --bridge)")
        status = await asyncio.to_thread(bridge.refresh_shared_engine_data, True)
        hub.meta, hub.matchups = engine.load_meta(), engine.load_lane_matchups()
        await hub.rescore()
        return JSONResponse({"ok": all(v.get("ok") for v in status.values()) if status else False, "files": status})

    @app.get("/api/studio/setup")
    async def studio_setup() -> JSONResponse:
        import league_draft_backend as ldb

        players = [{"key": p.key, "name": p.name, "roles": [r for r in p.roles if r in ROLE_ORDER],
                    "pools": {r: list(p.champions(r)) for r in ROLE_ORDER if p.pools.get(r)}}
                   for p in sorted(roster.players.values(), key=lambda p: p.name.casefold())]
        identities = [{"key": k, "label": v.get("label", k), "description": v.get("description", "")}
                      for k, v in getattr(ldb, "IDENTITIES", {}).items()]
        return JSONResponse({"players": players, "roles": list(ROLE_ORDER), "identities": identities,
                             "device_owner": identity.load_owner(), "patch": hub.tierlist().get("patch")})

    @app.post("/api/studio/generate")
    async def studio_generate(request: Request) -> JSONResponse:
        from . import studio

        p = await _json(request)
        out = await asyncio.to_thread(
            studio.generate, roster, hub.tierlist(), lineup=_lineup(p),
            locks={r: c for r, c in (p.get("locks") or {}).items() if r in ROLE_ORDER},
            bans=_names(p.get("bans")), excluded={r: _names(v) for r, v in (p.get("excluded") or {}).items() if r in ROLE_ORDER},
            identity=p.get("identity") or None, creative=bool(p.get("creative")), seed=p.get("seed") or None,
            count=int(p.get("count") or 6), side="red" if p.get("side") == "red" else "blue",
            exclude_signatures=[str(s) for s in p.get("exclude_signatures") or []][:60],
            hard_exclude=bool(p.get("different")))
        return JSONResponse(out)

    @app.post("/api/studio/reroll")
    async def studio_reroll(request: Request) -> JSONResponse:
        from . import studio

        p = await _json(request)
        if p.get("role") not in ROLE_ORDER:
            raise HTTPException(400, "role must be one of " + ", ".join(ROLE_ORDER))
        out = await asyncio.to_thread(
            studio.reroll_lane, roster, hub.tierlist(), lineup=_lineup(p),
            draft={r: c for r, c in (p.get("draft") or {}).items() if r in ROLE_ORDER}, role=p["role"],
            reject=_names(p.get("reject")), bans=_names(p.get("bans")), side="red" if p.get("side") == "red" else "blue")
        return JSONResponse(out)

    @app.post("/api/studio/insight")
    async def studio_insight(request: Request) -> JSONResponse:
        """Per lineup seat: champions to learn and pool weaknesses for that role, plus team-wide gaps."""
        from . import studio

        p = await _json(request)
        lineup = _lineup(p)
        seats, pools = {}, {}
        for role in ROLE_ORDER:
            player = roster.players.get(lineup[role] or "")
            pool = list(player.champions(role)) if player else []
            pools[role] = pool
            seats[role] = {"player": player.name if player else None, "pool": pool,
                           "learn": studio.learn(pool, role, hub.tierlist(), exclude=set(roster.never_pick)),
                           "weaknesses": studio.weaknesses(pool, role, hub.tierlist(), hub.matchups)}
        return JSONResponse({"seats": seats, "team": studio.team_gaps(pools)})

    @app.get("/api/champions")
    async def get_champions() -> JSONResponse:
        return JSONResponse({"champions": champions.all_ids()})

    @app.get("/api/art/index")
    async def art_index() -> JSONResponse:
        porcelain = sorted(p.stem for p in (art_dir / "champions").glob("*.png")) if art_dir else []
        gesture = sorted(p.stem for p in (art_dir / "overlay-backgrounds").glob("*.jpg")) if art_dir else []
        return JSONResponse({"dir": str(art_dir) if art_dir else None, "porcelain": porcelain, "gesture": gesture,
                             "slugs": {name: art_slug(name) for name in champions.all_names()}})

    @app.get("/api/art/{kind}/{champion_id}")
    async def art(kind: str, champion_id: int) -> Response:
        """Champion artwork: porcelain / gesture from the Matchup Master collection when present,
        splash / centered / tile / portrait from the client when connected, else CommunityDragon."""
        name = champions.name_for(champion_id)
        if kind in ("porcelain", "gesture"):
            if not art_dir or not name:
                raise HTTPException(404, "no artwork collection")
            folder, ext = ("champions", ".png") if kind == "porcelain" else ("overlay-backgrounds", ".jpg")
            file = art_dir / folder / (art_slug(name) + ext)
            if not file.is_file():
                raise HTTPException(404, f"no {kind} art for {name}")
            return FileResponse(file, headers={"Cache-Control": "public, max-age=86400"})
        if kind not in CDRAGON_ART:
            raise HTTPException(404, "unknown art kind")
        key = f"{kind}:{champion_id}"
        if key in art_cache:
            data = art_cache[key]
            media_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            return Response(data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
        rest = watcher.rest if watcher and watcher.connected else None
        if rest is not None:
            try:
                if champion_id not in splash_paths:
                    splash_paths[champion_id] = (await asyncio.to_thread(rest.get, f"/lol-game-data/assets/v1/champions/{int(champion_id)}.json")) or {}
                skin = ((splash_paths[champion_id].get("skins") or [{}])[0])
                path = {"splash": skin.get("uncenteredSplashPath"), "centered": skin.get("splashPath"),
                        "tile": skin.get("tilePath"), "portrait": skin.get("loadScreenPath")}.get(kind)
                if path:
                    data = await asyncio.to_thread(rest.get, str(path), raw=True)
                    if data:
                        if len(art_cache) > 80:
                            art_cache.pop(next(iter(art_cache)))
                        art_cache[key] = data
                        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
            except Exception:  # noqa: BLE001 - fall through to the CDN
                pass
        if not name:
            raise HTTPException(404, "unknown champion")
        if time.monotonic() >= art_misses.get(key, 0):
            data = await asyncio.to_thread(cached_remote_art, CDRAGON_ART[kind].format(id=champion_id),
                                           art_disk / f"{kind}-{champion_id}.img")
            if data:
                if len(art_cache) > 80:
                    art_cache.pop(next(iter(art_cache)))
                art_cache[key] = data
                media_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
                return Response(data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
            art_misses[key] = time.monotonic() + 60
        raise HTTPException(404, "artwork unavailable offline")

    @app.get("/api/champion-icon/{champion_id}")
    async def champion_icon(champion_id: int) -> Response:
        if champion_id in icon_cache:
            return Response(icon_cache[champion_id], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        name = champions.name_for(champion_id) or ""
        asset_name = {"Wukong": "MonkeyKing", "Nunu & Willump": "Nunu", "Renata Glasc": "Renata"}.get(name, name)
        file = icon_files.get("".join(c for c in asset_name.casefold() if c.isalnum()))
        if file:
            return FileResponse(file, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        if art_dir and name:
            collection_icon = art_dir / "champions" / (art_slug(name) + ".png")
            if collection_icon.is_file():
                return FileResponse(collection_icon, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        rest = watcher.rest if watcher and watcher.connected else None
        if rest is None:
            raise HTTPException(404, "champion icon unavailable offline")
        try:
            data = await asyncio.to_thread(rest.champion_icon, champion_id)
        except Exception:  # noqa: BLE001
            raise HTTPException(404, "champion icon unavailable")
        if not data:
            raise HTTPException(404, "champion icon unavailable")
        icon_cache[champion_id] = data
        return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    return app


async def _exchange_profiles(hub: Hub) -> None:
    """Every 20 s: merge the team's profile room into this app and share what it has (see profile_sync)."""
    relay = hub.profile_relay
    while True:
        try:
            changed = await asyncio.to_thread(relay.exchange)
            if changed:
                players = {key for key, _ in changed}
                if hub.profiles.apply_to(hub.roster, players):
                    names = ", ".join(sorted(hub.roster.players[k].name for k in players if k in hub.roster.players))
                    await hub.roster_changed(f"team sync: updated {names}")
        except Exception as exc:  # noqa: BLE001 - offline, relay restart, firewall: keep the local copy, retry
            relay.status.update(state="offline", last_error=f"{type(exc).__name__}: {str(exc)[:120]}")
        await asyncio.sleep(relay.interval)


async def _poll_bridge(hub: Hub, bridge_url: str) -> None:
    """Every half second, copy the INTent bridge's guidance (picks, bans, swaps) onto the hub."""
    import urllib.request

    def fetch() -> dict | None:
        try:
            with urllib.request.urlopen(bridge_url + "/api/guidance", timeout=1.5) as response:
                data = json.loads(response.read())
        except Exception:  # noqa: BLE001
            return None
        return data if isinstance(data, dict) else None

    while True:
        data = await asyncio.to_thread(fetch)
        same_game = hub.state is not None and str((data or {}).get("game_id")) == str(hub.state.game_id)
        guidance = (data or {}).get("guidance") if same_game and (data or {}).get("active") else None
        guidance = guidance if isinstance(guidance, dict) else None
        if guidance != hub.intent_guidance:
            hub.intent_guidance = guidance
            await hub._push()   # the seat panel and swap plan read the shared guidance
        await asyncio.sleep(0.5)


async def _sync_roster(hub: Hub, url: str, interval: float) -> None:
    """Pull the coach machine's roster (players, pools, scouting, policy) and apply it here."""
    import urllib.request

    base = url.rstrip("/")
    hub.log_event(f"syncing roster from {base}", "info")
    last_version: int | None = None
    while True:
        try:
            def fetch() -> dict:
                with urllib.request.urlopen(base + "/api/roster", timeout=10) as response:
                    return json.loads(response.read())
            remote = await asyncio.to_thread(fetch)
            version = int(remote.get("version") or 0)
            if version != last_version:
                last_version = version
                remote.pop("history", None)
                remote["format"] = "flexers-roster"
                result = await hub.import_document(remote, f"sync:{base}")
                log.info("roster sync from %s: %s", base, "ok" if result.ok else result.errors)
        except Exception as exc:  # noqa: BLE001
            log.warning("roster sync from %s failed: %s", base, exc)
            hub.status["sync_error"] = str(exc)
        await asyncio.sleep(interval)


async def _watch_loadfiles(hub: Hub, folder: Path, interval: float = 2.0) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    watcher = loadfiles.LoadfileWatcher(folder, VAR_DIR / "loadfiles-seen.json")
    hub.log_event(f"watching {folder} for loadfiles", "info")
    # The live bridge (tools/intent_live_draft_bridge.py) caches the Discord signups here; import it
    # whenever it changes so the app ranks with the pools and priorities people set in Discord.
    from . import ENGINE_DIR, backend
    signup_cache = ENGINE_DIR / "data" / "live_flex_signups.json"
    signup_seen: float | None = None
    while True:
        # Keep the bridge's signup cache in step with the roster (unless the bridge owns it).
        if backend.write_signup_cache(hub.roster, signup_cache):
            try:
                signup_seen = signup_cache.stat().st_mtime
            except OSError:
                pass
        try:
            stamp = signup_cache.stat().st_mtime if signup_cache.exists() else None
        except OSError:
            stamp = None
        if stamp is not None and stamp != signup_seen and not signup_cache.with_name(backend.SEED_MARKER).exists():
            signup_seen = stamp
            result = await hub.import_path(signup_cache)
            log.info("discord signups %s: %s", signup_cache.name, "ok" if result.ok else result.errors)
        for path in watcher.pending():
            # Wait for the file to stop growing (editors and copies write in chunks).
            try:
                size = path.stat().st_size
                await asyncio.sleep(0.3)
                if path.stat().st_size != size:
                    continue
            except OSError:
                continue
            result = await hub.import_path(path)
            watcher.mark(path)
            log.info("loadfile %s: %s", path.name, "ok" if result.ok else result.errors)
        await asyncio.sleep(interval)
