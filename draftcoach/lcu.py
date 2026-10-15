"""League Client (LCU) connection.

Finds the running client through its lockfile, subscribes to the champ select
session over the client's websocket (so every hover, ban, lock and champion
swap arrives as an event within milliseconds), and hands each session to a
callback. REST calls use the standard library so the only dependency is
``websockets``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import platform
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("draftcoach.lcu")

CHAMP_SELECT_EVENT = "OnJsonApiEvent_lol-champ-select_v1_session"
GAMEFLOW_EVENT = "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"
DEFAULT_LOCKFILES = [
    r"C:\Riot Games\League of Legends\lockfile",
    r"D:\Riot Games\League of Legends\lockfile",
    r"E:\Riot Games\League of Legends\lockfile",
    r"C:\Program Files\Riot Games\League of Legends\lockfile",
    r"C:\Program Files (x86)\Riot Games\League of Legends\lockfile",
    "/Applications/League of Legends.app/Contents/LoL/lockfile",
]


@dataclass
class LockfileInfo:
    pid: int
    port: int
    password: str
    protocol: str = "https"

    @property
    def auth_header(self) -> str:
        token = base64.b64encode(f"riot:{self.password}".encode()).decode()
        return f"Basic {token}"

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"wss://127.0.0.1:{self.port}/"


def parse_lockfile(text: str) -> LockfileInfo:
    # Format: LeagueClient:PID:PORT:PASSWORD:PROTOCOL
    parts = text.strip().split(":")
    if len(parts) < 5:
        raise ValueError("unexpected lockfile format")
    return LockfileInfo(pid=int(parts[1]), port=int(parts[2]), password=parts[3], protocol=parts[4])


def _lockfile_from_process() -> LockfileInfo | None:
    """Fall back to reading the client's command line for the port/token."""
    system = platform.system()
    try:
        if system == "Windows":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process -Filter \"name = 'LeagueClientUx.exe'\" | Select-Object -ExpandProperty CommandLine"]
        else:
            cmd = ["sh", "-c", "ps -A -o command | grep LeagueClientUx | grep -v grep"]
        output = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return None
    port = token = None
    for part in output.replace('"', " ").split():
        if part.startswith("--app-port="):
            port = int(part.split("=", 1)[1])
        elif part.startswith("--remoting-auth-token="):
            token = part.split("=", 1)[1]
    if port and token:
        return LockfileInfo(pid=0, port=port, password=token)
    return None


def find_lockfile(explicit: str | None = None) -> LockfileInfo | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("LCU_LOCKFILE"):
        candidates.append(os.environ["LCU_LOCKFILE"])
    candidates.extend(DEFAULT_LOCKFILES)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            try:
                return parse_lockfile(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.debug("lockfile %s unreadable: %s", path, exc)
    return _lockfile_from_process()


class LcuRest:
    """Minimal blocking REST client (run it in an executor from async code)."""

    def __init__(self, info: LockfileInfo):
        self.info = info
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE  # the client uses a self-signed cert on 127.0.0.1

    def get(self, path: str, *, raw: bool = False, timeout: float = 5.0):
        request = urllib.request.Request(self.info.base_url + path, headers={"Authorization": self.info.auth_header,
                                                                                "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, context=self.ctx, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if raw:
            return body
        return json.loads(body or b"null")

    def request(self, method: str, path: str, body: dict | None = None, *, timeout: float = 5.0):
        """PATCH/POST/DELETE against the client; raises on HTTP errors other than 404."""
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": self.info.auth_header, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.info.base_url + path, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"client refused {method} {path}: {exc.code} {detail}") from exc
        return json.loads(raw or b"null") if raw else None

    def champ_select_session(self) -> dict | None:
        return self.get("/lol-champ-select/v1/session")

    def gameflow_phase(self) -> str | None:
        value = self.get("/lol-gameflow/v1/gameflow-phase")
        return str(value).strip('"') if value else None

    def champion_summary(self) -> list[dict]:
        return self.get("/lol-game-data/assets/v1/champion-summary.json") or []

    def champion_icon(self, champion_id: int) -> bytes | None:
        return self.get(f"/lol-game-data/assets/v1/champion-icons/{int(champion_id)}.png", raw=True)

    def summoner_by_puuid(self, puuid: str) -> dict | None:
        return self.get(f"/lol-summoner/v2/summoners/puuid/{puuid}")


SessionCallback = Callable[[dict | None, str | None], Awaitable[None]]
StatusCallback = Callable[[str, str], Awaitable[None]]


class LcuWatcher:
    """Keeps a connection to the client and streams champ select sessions.

    ``on_session(session_or_none, gameflow_phase)`` is awaited for every change.
    ``on_status(state, detail)`` reports ``waiting`` / ``connected`` / ``error``.
    """

    def __init__(self, on_session: SessionCallback, on_status: StatusCallback | None = None,
                 lockfile: str | None = None, poll_interval: float = 2.0):
        self.on_session = on_session
        self.on_status = on_status
        self.lockfile = lockfile
        self.poll_interval = poll_interval
        self.rest: LcuRest | None = None
        self.gameflow_phase: str | None = None
        self._summoner_cache: dict[str, dict] = {}
        self._stop = asyncio.Event()
        self.champion_names_loaded = 0
        self.connected = False

    async def _status(self, state: str, detail: str = "") -> None:
        log.info("lcu %s %s", state, detail)
        self.connected = state == "connected"
        if self.on_status:
            await self.on_status(state, detail)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            info = await asyncio.to_thread(find_lockfile, self.lockfile)
            if info is None:
                await self._status("waiting", "League client not running")
                await self._sleep(self.poll_interval)
                continue
            self.rest = LcuRest(info)
            try:
                await self._session_loop(info)
            except (OSError, asyncio.TimeoutError) as exc:
                await self._status("waiting", f"client not reachable yet ({exc.__class__.__name__})")
            except Exception as exc:  # noqa: BLE001 - keep the watcher alive
                log.exception("lcu loop failed")
                await self._status("error", str(exc))
            await self._sleep(self.poll_interval)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _prime(self) -> None:
        """Load champion names and the current session once connected."""
        from . import champions

        try:
            summary = await asyncio.to_thread(self.rest.champion_summary)
            self.champion_names_loaded = champions.refresh_from_client(summary)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not refresh champion names from client: %s", exc)
        try:
            self.gameflow_phase = await asyncio.to_thread(self.rest.gameflow_phase)
        except Exception:  # noqa: BLE001
            self.gameflow_phase = None
        session = await asyncio.to_thread(self.rest.champ_select_session)
        await self._emit(session)

    async def _emit(self, session: dict | None) -> None:
        if session:
            await self._fill_names(session)
        await self.on_session(session, self.gameflow_phase)

    async def _fill_names(self, session: dict) -> None:
        """Older clients omit gameName/tagLine on team members; look them up once."""
        for member in (session.get("myTeam") or []) + (session.get("theirTeam") or []):
            if member.get("gameName") or not member.get("puuid"):
                continue
            puuid = str(member["puuid"])
            cached = self._summoner_cache.get(puuid)
            if cached is None:
                try:
                    cached = await asyncio.to_thread(self.rest.summoner_by_puuid, puuid) or {}
                except Exception:  # noqa: BLE001
                    cached = {}
                self._summoner_cache[puuid] = cached
            member.setdefault("gameName", cached.get("gameName") or cached.get("displayName") or "")
            member.setdefault("tagLine", cached.get("tagLine") or "")

    async def _session_loop(self, info: LockfileInfo) -> None:
        import websockets

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        async with websockets.connect(info.ws_url, ssl=ctx, subprotocols=["wamp"],
                                      additional_headers={"Authorization": info.auth_header},
                                      max_size=16 * 1024 * 1024, open_timeout=5) as ws:
            await ws.send(json.dumps([5, CHAMP_SELECT_EVENT]))
            await ws.send(json.dumps([5, GAMEFLOW_EVENT]))
            await self._status("connected", f"port {info.port}")
            await self._prime()
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    try:
                        await ws.ping()
                    except Exception:  # noqa: BLE001
                        raise OSError("websocket ping failed")
                    continue
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, list) or len(message) < 3 or message[0] != 8:
                    continue
                event, payload = message[1], message[2]
                data = payload.get("data")
                event_type = payload.get("eventType")
                if event == GAMEFLOW_EVENT:
                    self.gameflow_phase = str(data).strip('"') if data else None
                    if self.gameflow_phase != "ChampSelect":
                        await self.on_session(None, self.gameflow_phase)
                    continue
                if event == CHAMP_SELECT_EVENT:
                    if event_type == "Delete":
                        await self.on_session(None, self.gameflow_phase)
                    else:
                        await self._emit(data if isinstance(data, dict) else None)
        await self._status("waiting", "client websocket closed")
