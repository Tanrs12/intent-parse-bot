"""Run the bot's draft-sync relay locally, without Discord, for the party test.

The relay handlers are loaded from bot.py itself (the section between ``_SYNC_SESSIONS`` and
``start_draft_sync_relay``), so the local relay cannot drift from the deployed one. On top of it:

* ``--latency MS``: every relay request waits that long first (a slow connection).
* ``POST /control/block`` / ``POST /control/unblock``: while blocked, relay requests hang until the
  client times out, which is what a firewall does.
* ``GET /control/rooms``: the rooms, participants and elected leaders, for assertions.

    python tools/local_relay.py --port 8795
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import time
from types import SimpleNamespace
from pathlib import Path

from aiohttp import web

REPO = Path(__file__).resolve().parents[1]
log = logging.getLogger("local-relay")


def load_relay_namespace() -> dict:
    source = (REPO / "bot.py").read_text(encoding="utf-8")
    start = source.index("_SYNC_SESSIONS: dict[str, dict] = {}")
    end = source.index("async def start_draft_sync_relay")
    namespace = {"web": web, "time": time, "re": re, "os": os, "log": log, "asyncio": asyncio, "__name__": "bot_relay"}
    exec(compile(source[start:end], "bot.py[relay]", "exec"), namespace)  # noqa: S102 - our own source
    return namespace


def build_app(latency_ms: int = 0) -> web.Application:
    ns = load_relay_namespace()
    state = {"blocked": False, "latency": latency_ms / 1000.0, "requests": 0, "http_error": False, "clock_skew": 0}
    ns["time"] = SimpleNamespace(time=lambda: time.time() + state["clock_skew"])

    @web.middleware
    async def quirks(request: web.Request, handler):
        if request.path.startswith("/control/"):
            return await handler(request)
        state["requests"] += 1
        if state["http_error"]:
            raise web.HTTPInternalServerError(text="injected relay outage")
        if state["blocked"]:
            await asyncio.sleep(30)          # a firewall drops the packets: the client times out
            raise web.HTTPGatewayTimeout(text="blocked")
        if state["latency"]:
            await asyncio.sleep(state["latency"])
        return await handler(request)

    app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[quirks])
    app.router.add_get("/health", ns["_relay_health"])
    app.router.add_post("/draft-sync/{key}/heartbeat", ns["_relay_heartbeat"])
    app.router.add_put("/draft-sync/{key}", ns["_relay_put"])
    app.router.add_get("/draft-sync/{key}", ns["_relay_get"])
    app.router.add_post("/draft-sync/{key}/preference", ns["_relay_preference"])

    async def block(_request: web.Request) -> web.Response:
        state["blocked"] = True
        return web.json_response({"ok": True, "blocked": True})

    async def unblock(_request: web.Request) -> web.Response:
        state["blocked"] = False
        return web.json_response({"ok": True, "blocked": False})

    async def latency(request: web.Request) -> web.Response:
        payload = await request.json()
        state["latency"] = float(payload.get("ms") or 0) / 1000.0
        return web.json_response({"ok": True, "latency_ms": state["latency"] * 1000})

    async def rooms(_request: web.Request) -> web.Response:
        out = {}
        for key, row in ns["_SYNC_SESSIONS"].items():
            leader = ns["_elect_leader"](row)
            out[key] = {
                "participants": {cid: {k: v for k, v in item.items() if k != "updated"}
                                 for cid, item in ns["_active_participants"](row).items()},
                "leader": (leader or {}).get("client_id"),
                "leader_name": (leader or {}).get("player_name"),
                "preference": row.get("preference"),
                "control": row.get("control"),
                "preference_version": row.get("preference_version"),
                "publisher_id": row.get("publisher_id"),
                "payload_age": round(time.time() - float(row.get("updated") or 0), 1) if row.get("payload") else None,
            }
        return web.json_response({"rooms": out, "requests": state["requests"], "blocked": state["blocked"],
                                  "latency_ms": state["latency"] * 1000})

    async def fault(request: web.Request) -> web.Response:
        body = await request.json()
        state.update({k: body[k] for k in ("http_error", "clock_skew") if k in body})
        return web.json_response(state)

    app.router.add_post("/control/block", block)
    app.router.add_post("/control/unblock", unblock)
    app.router.add_post("/control/latency", latency)
    app.router.add_get("/control/rooms", rooms)
    app.router.add_post("/control/fault", fault)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--latency", type=int, default=0, help="milliseconds added to every relay request")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    web.run_app(build_app(args.latency), host="127.0.0.1", port=args.port, print=lambda *_: None)


if __name__ == "__main__":
    main()
