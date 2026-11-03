#!/usr/bin/env python3
"""Start Flexers Draft Coach.

Examples
--------
python run.py                               # connect to the League client, coach panel on http://127.0.0.1:8765/
python run.py --load loadfiles/week3.json   # import one or more loadfiles at start-up
python run.py --simulate                    # no client needed: replays a scripted draft
python run.py --no-lcu                      # manual board / REST only
python run.py --any-queue                   # also coach customs / normals (default: Ranked Flex only)
python run.py --reset-roster                # ignore var/roster.json and rebuild from config.json
python run.py --window                      # also open the app windows: the coach window and the always-on-top panel
python run.py --window-only                 # just the windows, against a server that is already running
python run.py --hud                         # in-client HUD: drawn over the League client itself
python run.py --window --sync http://COACH-PC:8765   # a player's machine: own client, roster from the coach
python run.py --host 0.0.0.0                # the coach's machine: lets the players' copies sync from it

Pages: /  (coach panel: roster, loadfiles, manual board)   /overlay  (compact web overlay, OBS-ready)
       /overlay?layout=full  (the big board)
The loadfiles/ folder is watched: drop a JSON or CSV loadfile in and it is imported within seconds.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from draftcoach import LOADFILE_DIR, VAR_DIR  # noqa: E402
from draftcoach.server import create_app, load_config  # noqa: E402

LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def configure_logging(level: str = "info", log_file: Path | None = None) -> list[logging.Handler]:
    """Root logging: var/draftcoach.log when asked for, the terminal only when there is one.

    The packaged exe is a windowed (no console) build: sys.stdout / sys.stderr are None there, and
    a StreamHandler on a None stream raises on every record. uvicorn gets log_config=None, so this
    is the only place handlers are installed.
    """
    root = logging.getLogger()
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = []
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
        except OSError:
            pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level.upper())
    return handlers


def local_url(host: str, port: int) -> str:
    """The URL this machine's own windows/HUD open the server on.

    ``--host 0.0.0.0`` (the LAN coach launcher) and ``::`` are listen-on-everything addresses: a
    WebView cannot connect to them, so they map to the matching loopback address. Any concrete
    host (127.0.0.1, a LAN IP, a name) is used as given.
    """
    if host in ("0.0.0.0", ""):
        connect_host = "127.0.0.1"
    elif host == "::":
        connect_host = "::1"
    else:
        connect_host = host
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"      # an IPv6 literal needs brackets in a URL
    return f"http://{connect_host}:{port}"


def _alert(text: str, *, error: bool = False) -> None:
    """Tell the user something when there may be no console to print to (the windowed exe)."""
    print(text, file=sys.stderr)
    if sys.platform == "win32" and sys.stderr is None:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "Flexers Draft Coach", 0x10 if error else 0x30)
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config.json"), help="roster/pool config JSON (default: config.json if present)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lockfile", help="explicit path to the League client lockfile")
    parser.add_argument("--no-lcu", action="store_true", help="do not look for the League client")
    parser.add_argument("--simulate", action="store_true", help="replay a scripted draft in-process (implies --no-lcu)")
    parser.add_argument("--simulate-delay", type=float, default=1.2, help="seconds between simulated draft events")
    parser.add_argument("--simulate-side", choices=["blue", "red"], default="blue")
    parser.add_argument("--load", action="append", default=[], metavar="FILE", help="loadfile(s) to import at start-up")
    parser.add_argument("--watch", default=str(LOADFILE_DIR), help="folder to watch for loadfiles ('' to disable)")
    parser.add_argument("--roster", default=str(VAR_DIR / "roster.json"), help="where the live roster is persisted")
    parser.add_argument("--reset-roster", action="store_true", help="ignore the persisted roster and rebuild from config")
    parser.add_argument("--sync", metavar="URL", help="pull the roster from the coach machine, e.g. http://192.168.1.20:8765")
    parser.add_argument("--sync-interval", type=float, default=60.0, help="seconds between roster syncs")
    parser.add_argument("--cloud-sync-mode", choices=["host", "follow", "auto"], help="internet sync: host/follow are manual fallbacks; auto elects a Flexers captain")
    parser.add_argument("--no-auto-sync", action="store_true", help="disable packaged Flexers automatic internet sync")
    parser.add_argument("--no-update", action="store_true", help="skip the packaged GitHub release update check")
    parser.add_argument("--cloud-sync-url", default=os.environ.get("INTENT_SYNC_RELAY", ""), help="public draft relay base URL")
    parser.add_argument("--cloud-sync-key", default=os.environ.get("INTENT_SYNC_KEY", ""), help="shared 16+ character draft session key")
    parser.add_argument("--window", action="store_true", help="open the app windows (coach window + always-on-top panel) as well")
    parser.add_argument("--window-only", action="store_true", help="only open the app windows (server already running)")
    parser.add_argument("--hud", action="store_true", help="draw the in-client HUD over the League client (Windows)")
    parser.add_argument("--hud-only", action="store_true", help="only the HUD, against a server that is already running")
    parser.add_argument("--comp-overlay", dest="comp_overlay", action="store_true", default=None,
                        help="show the six INTent composition cards on the left of the League client")
    parser.add_argument("--no-comp-overlay", dest="comp_overlay", action="store_false")
    parser.add_argument("--bridge", dest="bridge", action="store_true", default=None,
                        help="run Tanoras's INTent live bridge on port 8766 in-process (default in the packaged build)")
    parser.add_argument("--no-bridge", dest="bridge", action="store_false")
    parser.add_argument("--any-queue", action="store_true",
                        help="coach every champ select (customs, normals); default: Ranked Flex only (settings.queues)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    frozen = getattr(sys, "frozen", False)
    if frozen:
        os.environ.setdefault("INTENT_LIVE", "1")
    # Shared Flexers builds carry a shared team room. Normal live launches join it automatically;
    # explicit host/follow flags still override this for debugging and LAN/manual fallback work.
    if frozen and os.environ.get("INTENT_UPDATE_SKIPPED") != "1" and not args.no_auto_sync and not args.cloud_sync_mode and not args.sync and not args.simulate and not args.no_lcu:
        try:
            team_sync = json.loads((ROOT / "team-sync.json").read_text(encoding="utf-8"))
            relay = str(team_sync.get("relay") or "").rstrip("/")
            room_key = str(team_sync.get("room_key") or "").strip()
            if relay and len(room_key) >= 16:
                args.cloud_sync_mode = "auto"
                args.cloud_sync_url = relay
                args.cloud_sync_key = room_key
                print(f"Flexers sync {team_sync.get('room_label') or ''}: automatic captain election enabled.")
        except Exception:
            pass
    if frozen and not any((args.window, args.window_only, args.hud, args.hud_only, args.simulate, args.no_lcu)):
        # Double-clicked exe: behave like the live-client launcher.
        args.window, args.hud = True, True
        print("Opening the coach window, the floating panel and the in-client HUD (the launcher .bat files pick other modes).")

    app_url = local_url(args.host, args.port)     # what this machine's own windows connect to
    if args.window_only:
        from draftcoach import window

        window.run(app_url)
        return
    if args.hud_only:
        from draftcoach import hud

        hud.run(app_url)
        return

    if logging.getLogger().handlers:        # _entry() already logs to var/draftcoach.log
        logging.getLogger().setLevel(args.log_level.upper())
    else:
        configure_logging(args.log_level)
    # Reserve the app listener before starting the bridge, relay heartbeat, or roster writes.
    # A losing double launch must never become a sixth relay participant. On Windows,
    # SO_REUSEADDR can allow two listeners to bind the same port; require exclusive ownership.
    listener = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((args.host, args.port))
        listener.listen(2048)
        listener.setblocking(False)
    except OSError as exc:
        listener.close()
        # Usually a second copy of the app: with no console, a dialog is the only sign of it.
        _alert(f"Cannot start Draft Coach on {args.host}:{args.port}: {exc}\n\nIs Flexers Draft Coach already running?")
        raise SystemExit(1) from exc
    config = load_config(args.config)
    config.setdefault("bridge_url", f"http://127.0.0.1:{args.port + 1}")   # FLEXERS: bridge = app port + 1
    simulation = {"delay": args.simulate_delay, "side": args.simulate_side} if args.simulate else None
    app = create_app(config, enable_lcu=not args.no_lcu and not args.simulate, lockfile=args.lockfile, simulation=simulation,
                     roster_path=Path(args.roster) if args.roster else None, watch_dir=Path(args.watch) if args.watch else None,
                     load_files=[Path(p) for p in args.load], reset_roster=args.reset_roster,
                     sync_url=args.sync, sync_interval=args.sync_interval, any_queue=args.any_queue)
    bridge_enabled = bool(args.bridge or (args.bridge is None and frozen))
    intent_live = os.environ.get("INTENT_LIVE") == "1"
    if bridge_enabled:
        _start_bridge(port=args.port + 1, upstream=args.sync, cloud_url=args.cloud_sync_url, cloud_key=args.cloud_sync_key, cloud_mode=args.cloud_sync_mode)
    if args.hud and intent_live:
        _start_hud_proxy()       # Rat-style native HUD reads the same INTent guidance via 8767
    if args.comp_overlay or (args.comp_overlay is None and frozen and args.hud):
        _start_comp_overlay()
    listening = f" (listening on {args.host}:{args.port})" if app_url != f"http://{args.host}:{args.port}" else ""
    print(f"Coach panel: {app_url}/    Overlay: {app_url}/overlay    HUD demo: /demo{listening}")
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level=args.log_level, access_log=False, log_config=None))
    if not args.window and not args.hud:
        try:
            server.run(sockets=[listener])
        finally:
            listener.close()
        return

    import threading

    if intent_live:
        from draftcoach import hud_intent as hud
    else:
        from draftcoach import hud
    from draftcoach import window

    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    hud_url = "http://127.0.0.1:8767" if intent_live else app_url
    try:
        if args.hud and args.window:
            # Coach window + panel on the main thread (WebView2 needs it), HUD on its own tkinter thread.
            threading.Thread(target=hud.run, args=(hud_url,), daemon=True).start()
            window.run(app_url)
        elif args.hud:
            hud.run(hud_url)
        else:
            window.run(app_url)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def _start_hud_proxy() -> None:
    """Serve the INTent/LCU merged state used by the preserved native HUD copy."""
    import importlib
    import logging

    from draftcoach import ENGINE_DIR

    tools_dir = ENGINE_DIR / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        proxy = importlib.import_module("intent_hud_proxy")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("draftcoach").warning("INTent HUD proxy not started: %s", exc)
        return

    def serve() -> None:
        try:
            proxy.main()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("draftcoach").warning("INTent HUD proxy stopped: %s", exc)

    threading.Thread(target=serve, daemon=True, name="intent-hud-proxy").start()


def _start_comp_overlay() -> None:
    """Run the six-plan left-side overlay in its own Tk interpreter/thread."""
    import importlib
    import logging

    from draftcoach import ENGINE_DIR

    tools_dir = ENGINE_DIR / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        overlay = importlib.import_module("intent_comp_overlay")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("draftcoach").warning("INTent comp overlay not started: %s", exc)
        return

    def run_overlay() -> None:
        try:
            overlay.CompOverlay().run()
        except SystemExit:
            return
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("draftcoach").warning("INTent comp overlay stopped: %s", exc)

    threading.Thread(target=run_overlay, daemon=True, name="intent-comp-overlay").start()


def _start_bridge(port: int = 8766, upstream: str | None = None, *, cloud_url: str | None = None, cloud_key: str | None = None, cloud_mode: str | None = None) -> None:
    """Tanoras's live bridge (tools/intent_live_draft_bridge.py) inside this process: it reads our
    /api/state, calls the shared backend and serves http://127.0.0.1:8766/api/guidance, which the
    packaged coach page and HUD read for their visible picks and bans."""
    import importlib
    import logging

    from draftcoach import ENGINE_DIR

    tools_dir = ENGINE_DIR / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        bridge = importlib.import_module("intent_live_draft_bridge")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("draftcoach").warning("INTent bridge not started: %s", exc)
        return

    def serve() -> None:
        try:
            bridge.COACH_STATE_URL = f"http://127.0.0.1:{port - 1}/api/state"
            bridge.configure_upstream(upstream)
            bridge.configure_cloud(cloud_url, cloud_key, cloud_mode)
            follower = bool(upstream or cloud_mode == "follow")
            if not follower:
                bridge._load_cached_signups()
            from http.server import ThreadingHTTPServer

            httpd = ThreadingHTTPServer(("127.0.0.1", port), bridge.Handler)
            if not follower:
                threading.Thread(target=bridge.background_refresh, args=(httpd,), daemon=True).start()
            if cloud_mode == "host":
                threading.Thread(target=bridge.cloud_publish_loop, daemon=True, name="intent-cloud-publisher").start()
            elif cloud_mode == "auto":
                threading.Thread(target=bridge.cloud_auto_loop, daemon=True, name="intent-cloud-auto").start()
            suffix = f" (synced from {upstream})" if upstream else (f" (internet {cloud_mode})" if cloud_mode else "")
            print(f"INTent live bridge: http://127.0.0.1:{port}/api/guidance{suffix}")
            httpd.serve_forever()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("draftcoach").warning("INTent bridge stopped: %s", exc)

    threading.Thread(target=serve, daemon=True).start()


def _unblock_bundle() -> int:
    """Strip Windows' "downloaded from the internet" mark from the packaged files.

    A zip saved by a browser carries a Zone.Identifier stream, and Explorer copies it onto every
    file it extracts. .NET then refuses to load the marked Python.Runtime.dll that the WebView2
    panel needs ("Failed to resolve Python.Runtime.Loader.Initialize"). Removing the stream is
    exactly what Explorer's "Unblock" button does, and it needs no elevation for the user's own files.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return 0
    import os

    removed = 0
    roots = {Path(sys.executable).resolve().parent, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))}
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
                    removed += 1
                except OSError:
                    pass
    return removed


def _entry() -> None:
    """Log to var/draftcoach.log and never die silently: a crash lands in var/crash.log and a dialog."""
    import logging
    import traceback

    try:
        unblocked = _unblock_bundle()
        if unblocked:
            print(f"Removed the 'downloaded from the internet' mark from {unblocked} packaged files.")
    except Exception:  # noqa: BLE001
        pass

    if getattr(sys, "frozen", False):
        try:
            from draftcoach.updater import UPDATE_EXIT_CODE, maybe_update
            if maybe_update(ROOT):
                raise SystemExit(UPDATE_EXIT_CODE)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"Update check skipped: {exc}")

    configure_logging(log_file=VAR_DIR / "draftcoach.log")
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - report everything, including import errors in the frozen build
        text = traceback.format_exc()
        try:
            (VAR_DIR / "crash.log").write_text(text, encoding="utf-8")
            where = str(VAR_DIR / "crash.log")
        except OSError:
            where = "(could not write var/crash.log)"
        print(text, file=sys.stderr)
        print(f"Flexers Draft Coach stopped. Details: {where}", file=sys.stderr)
        if sys.platform == "win32":   # always a dialog, console or not: the windowed exe has nothing else
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0, "Flexers Draft Coach stopped with an error.\n\n" + text[-1500:] + "\n\nSaved to: " + where,
                    "Flexers Draft Coach", 0x10)
            except Exception:  # noqa: BLE001
                pass
        raise


if __name__ == "__main__":
    _entry()
