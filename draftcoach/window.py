"""Always-on-top overlay window: a frameless pywebview (WebView2) panel.

Hosts ``/overlay?host=webview`` from the running server, the same page OBS or a
browser can show, so there is one overlay design. Mirrors Matchup Master's
overlay host: the panel is the window, Windows DWM rounds the corners, the
head strip drags it, double-click toggles porcelain / graphite.

    python run.py --window              # server + window
    python run.py --window-only         # window only, against a running server
    python -m draftcoach.window --url http://127.0.0.1:8765

Falls back to the tkinter window (``window_tk.py``) when pywebview is missing.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
import webbrowser

WIDTH, HEIGHT = 430, 480


class Api:
    """Called from the page through ``window.pywebview.api``."""

    def __init__(self, url: str):
        self.url = url
        self._window = None   # underscore: pywebview must not try to expose the Window object to JS

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def open_coach(self) -> None:
        webbrowser.open(self.url + "/")

    def set_height(self, height: int) -> None:
        if self._window is not None:
            try:
                self._window.resize(WIDTH, max(260, min(900, int(height))))
            except Exception:  # noqa: BLE001
                pass


def round_corners(title: str) -> None:
    """Win11 DWM rounded corners on the frameless native window."""
    try:
        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                                       ctypes.byref(ctypes.c_int(DWMWCP_ROUND)), 4)
    except Exception as exc:  # noqa: BLE001 - cosmetic
        print(f"rounded corners unavailable: {exc!r}")


def wait_for_server(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/config", timeout=2) as response:
                json.loads(response.read())
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def run(url: str, *, x: int = 40, y: int = 120, theme: str | None = None) -> None:
    url = url.rstrip("/")
    try:
        import webview
    except ImportError:
        print("pywebview is not installed (pip install pywebview); using the tkinter window instead")
        from .window_tk import OverlayWindow

        OverlayWindow(url).run()
        return
    wait_for_server(url)
    api = Api(url)
    title = "Flexers Draft Overlay"
    page = f"{url}/overlay?host=webview" + (f"&theme={theme}" if theme else "")
    window = webview.create_window(title, url=page, width=WIDTH, height=HEIGHT, x=x, y=y,
                                   frameless=True, on_top=True, transparent=False, resizable=False, js_api=api)
    api._window = window

    def on_start() -> None:
        time.sleep(0.15)
        round_corners(title)

    webview.start(on_start)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Always-on-top draft overlay window")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--x", type=int, default=40)
    parser.add_argument("--y", type=int, default=120)
    parser.add_argument("--theme", choices=["porcelain", "graphite"])
    args = parser.parse_args(argv)
    run(args.url, x=args.x, y=args.y, theme=args.theme)


if __name__ == "__main__":
    main()
