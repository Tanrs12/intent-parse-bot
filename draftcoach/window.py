"""The desktop app: a pywebview (WebView2) coach window plus the always-on-top overlay panel.

Two windows, one process, one taskbar entry ("Flexers Draft Coach"):

* the **coach window**: ``/?host=webview``, the same coach page a browser can show, but as a real,
  resizable application window with its own taskbar button and icon;
* the **panel**: a frameless always-on-top ``/overlay?host=webview`` strip, mirroring Matchup
  Master's overlay host: Windows DWM rounds the corners, the head strip drags it, double-click
  toggles porcelain / graphite.

Closing one window keeps the other (and the app) running; the panel's "Open coach" button and the
coach page's ``pywebview.api.open_panel()`` bring a closed window back. Closing both quits.

    python run.py --window              # server + both windows
    python run.py --window-only         # windows only, against a running server
    python -m draftcoach.window --url http://127.0.0.1:8765

Falls back to the tkinter panel (``window_tk.py``) when pywebview is missing or WebView2 refuses
to start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import webbrowser

APP_ID = "Flexers.DraftCoach"
MAIN_TITLE = "Flexers Draft Coach"
PANEL_TITLE = "Flexers Draft Overlay"
WIDTH, HEIGHT = 430, 620                 # the panel
MAIN_WIDTH, MAIN_HEIGHT = 1180, 860      # the coach window
MAIN_MIN_SIZE = (960, 640)


def main_window_options(url: str) -> dict:
    """create_window keyword arguments for the coach window (shared by first start and reopen)."""
    return dict(url=f"{url}/?host=webview", width=MAIN_WIDTH, height=MAIN_HEIGHT, min_size=MAIN_MIN_SIZE,
                resizable=True, frameless=False, on_top=False, text_select=True)


def panel_window_options(url: str, *, x: int = 40, y: int = 120, theme: str | None = None) -> dict:
    """create_window keyword arguments for the panel (shared by first start and reopen)."""
    page = f"{url}/overlay?host=webview" + (f"&theme={theme}" if theme else "")
    return dict(url=page, width=WIDTH, height=HEIGHT, x=x, y=y,
                frameless=True, on_top=True, transparent=False, resizable=False)


class Api:
    """Called from both pages through ``window.pywebview.api``.

    ``close`` / ``minimize`` / ``set_height`` are the panel's own chrome (its page has no native title
    bar) and always act on the panel; ``open_coach`` / ``open_panel`` bring the other window to the
    front, recreating it when it was closed.
    """

    def __init__(self, url: str, webview=None, *, x: int = 40, y: int = 120, theme: str | None = None):
        self.url = url
        # Underscore names: pywebview must not try to expose these objects to JS.
        self._webview = webview
        self._main = None
        self._panel = None
        self._panel_pos = (x, y)
        self._theme = theme

    # -- window bookkeeping ---------------------------------------------------------------------
    def _track_main(self, window) -> None:
        self._main = window
        window.events.closed += self._main_closed

    def _track_panel(self, window) -> None:
        self._panel = window
        window.events.closed += self._panel_closed

    def _main_closed(self) -> None:
        self._main = None

    def _panel_closed(self) -> None:
        self._panel = None

    def _create_main(self):
        window = self._webview.create_window(MAIN_TITLE, js_api=self, **main_window_options(self.url))
        self._track_main(window)
        return window

    def _create_panel(self):
        x, y = self._panel_pos
        window = self._webview.create_window(PANEL_TITLE, js_api=self,
                                             **panel_window_options(self.url, x=x, y=y, theme=self._theme))
        self._track_panel(window)
        return window

    @staticmethod
    def _bring_to_front(window, title: str) -> None:
        try:
            if is_minimized(title):
                window.restore()
            window.show()     # Show + Activate: also un-hides and focuses an open window
        except Exception:  # noqa: BLE001 - a window that is going away
            pass

    # -- called from the pages ------------------------------------------------------------------
    def open_coach(self) -> None:
        if self._webview is None:            # tkinter fallback / no GUI loop: the browser is all there is
            webbrowser.open(self.url + "/")
            return
        if self._main is not None:
            self._bring_to_front(self._main, MAIN_TITLE)
            return
        self._create_main()                  # after start(): pywebview creates it at once, from any thread
        style_windows_window(MAIN_TITLE, round_corners=False)

    def open_panel(self) -> None:
        if self._webview is None:
            return
        if self._panel is not None:
            self._bring_to_front(self._panel, PANEL_TITLE)
            return
        self._create_panel()
        style_windows_window(PANEL_TITLE)

    def close(self) -> None:
        if self._panel is not None:
            self._panel.destroy()

    def minimize(self) -> None:
        if self._panel is not None:
            self._panel.minimize()

    def set_height(self, height: int) -> None:
        if self._panel is not None:
            try:
                self._panel.resize(WIDTH, max(260, min(900, int(height))))
            except Exception:  # noqa: BLE001
                pass


def set_app_id() -> None:
    """Give Windows a stable application identity before the first window exists, so both windows
    group under one taskbar button with the Flexers name and icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001 - cosmetic
        pass


def find_own_window(title: str) -> int:
    """HWND of this process's top-level window called ``title`` (0 when there is none).

    FindWindow would also match another Draft Coach instance or a browser tab with the same title;
    only our own window must be styled.
    """
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        pid = os.getpid()
        buf = ctypes.create_unicode_buffer(256)
        found: list[int] = []

        def visit(hwnd, _lparam):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
            if owner.value == pid:
                user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
                if buf.value == title:
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(proc(visit), None)
        return found[0] if found else 0
    except Exception:  # noqa: BLE001
        return 0


def is_minimized(title: str) -> bool:
    try:
        import ctypes

        hwnd = find_own_window(title)
        return bool(hwnd) and bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)))
    except Exception:  # noqa: BLE001
        return False


def wait_for_own_window(title: str, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while True:
        hwnd = find_own_window(title)
        if hwnd or time.monotonic() >= deadline:
            return hwnd
        time.sleep(0.1)


def style_windows_window(title: str, *, round_corners: bool = True) -> None:
    """Apply the Flexers icon (and, for the frameless panel, Win11 corners) to our native window."""
    try:
        import ctypes
        from pathlib import Path

        hwnd = wait_for_own_window(title)
        if not hwnd:
            return
        user32 = ctypes.windll.user32

        if round_corners:
            DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(ctypes.c_int(DWMWCP_ROUND)), 4
            )

        icon_path = Path(__file__).resolve().with_name("flexers_draft.ico")
        if icon_path.exists():
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
            big = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            small = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            if big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
    except Exception as exc:  # noqa: BLE001 - cosmetic
        print(f"native window styling unavailable: {exc!r}")


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
    """Open the coach window and the panel; returns when the last of them is closed."""
    url = url.rstrip("/")
    try:
        import webview
    except ImportError:
        print("pywebview is not installed (pip install pywebview); using the tkinter window instead")
        from .window_tk import OverlayWindow

        OverlayWindow(url).run()
        return
    set_app_id()                             # before any window: taskbar grouping is decided at creation
    wait_for_server(url)
    api = Api(url, webview, x=x, y=y, theme=theme)
    # The coach window first: pywebview's first window is the GUI loop's master, the panel is a child
    # created once the master is shown. Closing either one leaves the loop running for the other;
    # webview.start() returns only when both are gone.
    api._create_main()
    api._create_panel()

    def on_start() -> None:
        style_windows_window(MAIN_TITLE, round_corners=False)
        style_windows_window(PANEL_TITLE)

    try:
        webview.start(on_start)
    except Exception as exc:  # noqa: BLE001 - e.g. .NET refusing a blocked Python.Runtime.dll
        print(f"WebView2 windows unavailable ({exc.__class__.__name__}: {str(exc)[:160]}); using the tkinter window instead")
        from .window_tk import OverlayWindow

        OverlayWindow(url).run()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Flexers Draft Coach windows (coach window + always-on-top panel)")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--x", type=int, default=40)
    parser.add_argument("--y", type=int, default=120)
    parser.add_argument("--theme", choices=["porcelain", "graphite"])
    args = parser.parse_args(argv)
    run(args.url, x=args.x, y=args.y, theme=args.theme)


if __name__ == "__main__":
    main()
