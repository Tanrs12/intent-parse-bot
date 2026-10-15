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
GET  /api/champion-icon/{id} portrait (proxied from the client, else redirected to CommunityDragon)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import LOADFILE_DIR, ROOT, VAR_DIR, __version__, champions, loadfiles
from .draft_order import ManualDraft
from .hub import Hub, _json_default
from .lcu import LcuWatcher
from .roster import Roster

log = logging.getLogger("draftcoach.server")
STATIC = Path(__file__).with_name("static")
CDRAGON = "https://cdn.communitydragon.org/latest/champion/{id}/square"
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
               sync_interval: float = 60.0) -> FastAPI:
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

    hub = Hub(roster, draft, broadcast, debounce_ms=int(config.get("debounce_ms", 30)), roster_path=roster_path)
    art_dir = find_art_dir(config.get("art_dir"))
    app.state.art_dir = art_dir
    art_cache: dict[str, bytes] = {}
    splash_paths: dict[int, dict] = {}
    app.state.hub = hub
    app.state.roster = roster
    app.state.draft = draft
    app.state.config = config
    icon_cache: dict[int, bytes] = {}

    async def on_session(session: dict | None, phase: str | None) -> None:
        await hub.ingest_lcu_session(session, phase)
        if session is None and hub.state is None:
            await hub.ingest_manual()

    async def on_status(state: str, detail: str) -> None:
        hub.rest = watcher.rest if (watcher and state == "connected") else None
        await hub.set_status(state, detail)

    # ---- pages --------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    @app.get("/coach", response_class=HTMLResponse)
    async def coach_page() -> FileResponse:
        return FileResponse(STATIC / "coach.html", media_type="text/html")

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
        await hub.ingest_lcu_session(session or None, phase or "ChampSelect", source="simulation")
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
    @app.get("/api/client/local")
    async def client_local() -> JSONResponse:
        return JSONResponse({"local": hub.local_view(), "client": hub.client_rest() is not None})

    @app.post("/api/client/{what}")
    async def client_act(what: str, request: Request) -> JSONResponse:
        payload = await _json(request) if int(request.headers.get("content-length") or 0) else {}
        if what not in ("hover", "lock", "swap_request", "swap_accept", "swap_decline"):
            raise HTTPException(404, "unknown client action")
        try:
            result = await hub.client_act(what, payload.get("champion"), payload.get("trade_id"))
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
            return Response(art_cache[key], media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
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
        return RedirectResponse(CDRAGON_ART[kind].format(id=int(champion_id)), status_code=302)

    @app.get("/api/champion-icon/{champion_id}")
    async def champion_icon(champion_id: int) -> Response:
        if champion_id in icon_cache:
            return Response(icon_cache[champion_id], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        rest = watcher.rest if watcher and watcher.connected else None
        if rest is None:
            return RedirectResponse(CDRAGON.format(id=int(champion_id)), status_code=302)
        try:
            data = await asyncio.to_thread(rest.champion_icon, champion_id)
        except Exception:  # noqa: BLE001
            return RedirectResponse(CDRAGON.format(id=int(champion_id)), status_code=302)
        if not data:
            return RedirectResponse(CDRAGON.format(id=int(champion_id)), status_code=302)
        icon_cache[champion_id] = data
        return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    return app


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
    while True:
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
