"""The desktop app: a coach window and the panel under one taskbar identity, each reopenable from the other,
and a windowed (console-less) build that never logs to a stream that does not exist."""
from __future__ import annotations

import importlib
import logging
import sys
import types

import pytest


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler()


class FakeWindow:
    def __init__(self, title, **options):
        self.title = title
        self.options = options
        self.events = types.SimpleNamespace(closed=FakeEvent(), closing=FakeEvent())
        self.calls = []
        self.alive = True

    def show(self):
        self.calls.append("show")

    def restore(self):
        self.calls.append("restore")

    def hide(self):
        self.calls.append("hide")

    def minimize(self):
        self.calls.append("minimize")

    def resize(self, width, height):
        self.calls.append(("resize", width, height))

    def destroy(self):
        self.calls.append("destroy")
        self.close()

    def close(self):
        """What the user's X button does: the window is gone, pywebview fires ``closed``."""
        self.alive = False
        self.events.closed.fire()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A fake ``webview`` module (no WebView2) plus the ctypes-free stand-ins for the Windows styling."""
    webview = types.ModuleType("webview")
    webview.windows = []
    webview.started = []
    webview.start_options = []

    def create_window(title, **options):
        window = FakeWindow(title, **options)
        webview.windows.append(window)
        return window

    def start(func=None, *args, **kwargs):
        webview.started.append(func)
        webview.start_options.append(kwargs)
        if func:
            func()

    webview.create_window = create_window
    webview.start = start
    monkeypatch.setitem(sys.modules, "webview", webview)
    import draftcoach
    monkeypatch.setattr(draftcoach, "VAR_DIR", tmp_path)
    from draftcoach import window as module

    styled, app_ids = [], []
    monkeypatch.setattr(module, "wait_for_server", lambda url, timeout=20.0: True)
    monkeypatch.setattr(module, "style_windows_window",
                        lambda title, round_corners=True: styled.append((title, round_corners)))
    monkeypatch.setattr(module, "set_app_id", lambda: app_ids.append(len(webview.windows)))
    monkeypatch.setattr(module, "is_minimized", lambda title: False)
    return types.SimpleNamespace(webview=webview, module=module, styled=styled, app_ids=app_ids)


def _started(fake):
    fake.module.run("http://127.0.0.1:8899/")
    main, panel = fake.webview.windows
    return main, panel, main.options["js_api"]


def test_run_creates_the_coach_window_and_the_panel_under_one_app_id(fake):
    main, panel, api = _started(fake)
    assert fake.app_ids == [0], "the AppUserModelID must be set before the first window exists"
    assert main.title == "Flexers Draft Coach"
    assert main.options["url"] == "http://127.0.0.1:8899/?host=webview"
    assert (main.options["width"], main.options["height"]) == (1180, 860)
    assert main.options["min_size"] == (960, 640)
    assert main.options["resizable"] is True and main.options["frameless"] is False
    assert main.options["on_top"] is False and main.options["text_select"] is True
    assert panel.title == "Flexers Draft Overlay"
    assert panel.options["url"] == "http://127.0.0.1:8899/overlay?host=webview"
    assert panel.options["frameless"] is True and panel.options["on_top"] is True
    assert (panel.options["width"], panel.options["height"]) == (430, 620)
    assert panel.options["resizable"] is False
    assert panel.options["js_api"] is api, "one Api serves both pages"
    assert len(fake.webview.started) == 1
    assert fake.webview.start_options[0]["private_mode"] is False
    assert (fake.module._settings_path().parent / "webview-data").is_dir()
    assert fake.webview.start_options[0]["storage_path"] == str(fake.module._settings_path().parent / "webview-data")
    # the icon lands on both windows; only the frameless panel gets the DWM corners
    assert ("Flexers Draft Coach", False) in fake.styled and ("Flexers Draft Overlay", True) in fake.styled


def test_open_coach_focuses_the_open_window_and_recreates_a_closed_one(fake, monkeypatch):
    main, panel, api = _started(fake)
    monkeypatch.setattr(fake.module, "is_minimized", lambda title: title == "Flexers Draft Coach")
    api.open_coach()
    assert main.calls == ["restore", "show"]
    assert len(fake.webview.windows) == 2
    fake.styled.clear()

    main.close()                       # the user closed the coach window; the panel keeps the app alive
    api.open_coach()
    assert len(fake.webview.windows) == 3
    again = fake.webview.windows[-1]
    assert again.title == "Flexers Draft Coach"
    assert again.options == main.options
    assert fake.styled == [("Flexers Draft Coach", False)]
    monkeypatch.setattr(fake.module, "is_minimized", lambda title: False)
    api.open_coach()                   # and now it is the one being focused, not created
    assert again.calls == ["show"] and len(fake.webview.windows) == 3


def test_open_panel_focuses_the_open_panel_and_recreates_a_closed_one(fake):
    main, panel, api = _started(fake)
    api.open_panel()
    assert panel.calls == ["show"] and len(fake.webview.windows) == 2
    fake.styled.clear()

    panel.close()
    api.open_panel()
    assert len(fake.webview.windows) == 3
    again = fake.webview.windows[-1]
    assert again.title == "Flexers Draft Overlay"
    assert again.options == panel.options
    assert fake.styled == [("Flexers Draft Overlay", True)]
    assert main.calls == []


def test_panel_chrome_calls_act_on_the_panel_only(fake):
    main, panel, api = _started(fake)
    api.minimize()
    api.set_height(5000)
    assert panel.calls == ["minimize", ("resize", 430, 900)]
    api.close()
    assert panel.calls[-1] == "destroy" and not panel.alive
    assert main.calls == [] and main.alive
    api.close()                        # already gone: nothing to destroy, nothing to crash
    api.set_height(400)
    assert len(fake.webview.windows) == 2
    api.open_panel()                   # the coach page's "Show panel" after the panel was closed
    assert len(fake.webview.windows) == 3


def test_without_a_gui_loop_open_coach_falls_back_to_the_browser(monkeypatch):
    from draftcoach import window as module

    opened = []
    monkeypatch.setattr(module.webbrowser, "open", lambda url: opened.append(url))
    module.Api("http://127.0.0.1:8899").open_coach()
    assert opened == ["http://127.0.0.1:8899/"]


@pytest.fixture
def run_module():
    module = importlib.import_module("run")
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield module
    for handler in root.handlers:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_windowed_build_logs_to_the_file_only_when_there_is_no_console(run_module, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdout", None)
    handlers = run_module.configure_logging("info", log_file=tmp_path / "draftcoach.log")
    assert all(isinstance(h, logging.FileHandler) for h in handlers), handlers
    assert len(handlers) == 1
    logging.getLogger("draftcoach").info("hello from the windowed exe")
    handlers[0].flush()
    assert "hello from the windowed exe" in (tmp_path / "draftcoach.log").read_text(encoding="utf-8")


def test_console_runs_still_log_to_the_terminal(run_module):
    handlers = run_module.configure_logging("debug")
    assert [type(h) for h in handlers] == [logging.StreamHandler]
    assert handlers[0].stream is sys.stderr
    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.parametrize("host, expected", [
    ("0.0.0.0", "http://127.0.0.1:8765"),       # the LAN coach launcher: listen everywhere, connect on loopback
    ("", "http://127.0.0.1:8765"),
    ("::", "http://[::1]:8765"),
    ("127.0.0.1", "http://127.0.0.1:8765"),
    ("localhost", "http://localhost:8765"),
    ("192.168.1.20", "http://192.168.1.20:8765"),
])
def test_the_windows_open_a_connectable_url_even_when_listening_on_all_interfaces(run_module, host, expected):
    assert run_module.local_url(host, 8765) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Windows dialog")
def test_alert_shows_a_dialog_when_there_is_no_console(run_module, monkeypatch):
    import ctypes

    shown = []
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda hwnd, text, title, flags: shown.append((text, title, flags)))
    run_module._alert("port taken")
    assert shown == []                 # a console run prints; no dialog
    monkeypatch.setattr(sys, "stderr", None)
    run_module._alert("port taken", error=True)
    assert shown == [("port taken", "Flexers Draft Coach", 0x10)]
