"""The League-client process probe in a console-less build: no visible powershell window, and no
powershell at all every two seconds while the client is simply not running."""
from __future__ import annotations

import subprocess
import types

import pytest

from draftcoach import lcu

CREATE_NO_WINDOW = 0x08000000
UX_COMMAND_LINE = ('"C:\\Riot Games\\League of Legends\\LeagueClientUx.exe" --app-port=51234 '
                   '--remoting-auth-token=s3cret --no-proxy-server')


@pytest.fixture(autouse=True)
def fresh_probe(monkeypatch):
    """Each test starts with no remembered empty probe and a frozen clock it can advance."""
    monkeypatch.setattr(lcu, "_last_empty_probe", None)
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(lcu.time, "monotonic", lambda: clock.now)
    return clock


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(lcu.platform, "system", lambda: "Windows")
    # The Windows-only subprocess attributes, so the test also passes on a POSIX runner.
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: types.SimpleNamespace(dwFlags=0, wShowWindow=1), raising=False)


def _capture_run(monkeypatch, stdout: str):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(lcu.subprocess, "run", fake_run)
    return calls


def test_the_probe_hides_its_powershell_window_on_windows(windows, monkeypatch):
    calls = _capture_run(monkeypatch, "")
    assert lcu._lockfile_from_process() is None
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == "powershell" and "LeagueClientUx.exe" in cmd[-1]
    assert kwargs["creationflags"] & CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & 1           # STARTF_USESHOWWINDOW
    assert kwargs["startupinfo"].wShowWindow == 0      # SW_HIDE
    assert kwargs["timeout"] == 5 and kwargs["capture_output"] is True


def test_no_creation_flags_outside_windows(monkeypatch):
    monkeypatch.setattr(lcu.platform, "system", lambda: "Linux")
    calls = _capture_run(monkeypatch, "")
    assert lcu._lockfile_from_process() is None
    cmd, kwargs = calls[0]
    assert cmd[0] == "sh"
    assert "creationflags" not in kwargs and "startupinfo" not in kwargs


def test_an_empty_probe_is_not_repeated_for_ten_seconds(windows, monkeypatch, fresh_probe):
    calls = _capture_run(monkeypatch, "")
    assert lcu._lockfile_from_process() is None
    fresh_probe.now += 2.0                      # the watcher's next poll
    assert lcu._lockfile_from_process() is None
    fresh_probe.now += 2.0
    assert lcu._lockfile_from_process() is None
    assert len(calls) == 1, "powershell must not be spawned on every poll while the client is absent"
    fresh_probe.now += 6.5                      # 10.5 s after the empty probe
    assert lcu._lockfile_from_process() is None
    assert len(calls) == 2


def test_a_failed_probe_is_throttled_too(windows, monkeypatch, fresh_probe):
    calls = []

    def boom(cmd, **kwargs):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(lcu.subprocess, "run", boom)
    assert lcu._lockfile_from_process() is None
    fresh_probe.now += 2.0
    assert lcu._lockfile_from_process() is None
    assert len(calls) == 1


def test_a_successful_probe_is_never_throttled(windows, monkeypatch, fresh_probe):
    calls = _capture_run(monkeypatch, UX_COMMAND_LINE)
    first = lcu._lockfile_from_process()
    assert first == lcu.LockfileInfo(pid=0, port=51234, password="s3cret")
    fresh_probe.now += 0.1
    second = lcu._lockfile_from_process()
    assert second == first
    assert len(calls) == 2
    assert lcu._last_empty_probe is None


def test_the_client_starting_during_the_backoff_is_seen_as_soon_as_a_lockfile_exists(windows, monkeypatch, tmp_path, fresh_probe):
    """The throttle only covers the process probe: lockfile paths are checked on every call."""
    calls = _capture_run(monkeypatch, "")
    monkeypatch.delenv("LCU_LOCKFILE", raising=False)
    monkeypatch.setattr(lcu, "DEFAULT_LOCKFILES", [str(tmp_path / "default-lockfile")])
    explicit = tmp_path / "lockfile"
    assert lcu.find_lockfile(str(explicit)) is None
    assert len(calls) == 1
    fresh_probe.now += 2.0
    explicit.write_text("LeagueClient:4242:55555:tok:https", encoding="utf-8")
    info = lcu.find_lockfile(str(explicit))
    assert info == lcu.LockfileInfo(pid=4242, port=55555, password="tok", protocol="https")
    assert len(calls) == 1                      # found through the file, no second probe needed
    explicit.unlink()
    (tmp_path / "default-lockfile").write_text("LeagueClient:1:2:pw:https", encoding="utf-8")
    assert lcu.find_lockfile(str(explicit)).port == 2
    assert len(calls) == 1


def test_a_client_appearing_after_the_backoff_is_found_by_the_probe(windows, monkeypatch, tmp_path, fresh_probe):
    monkeypatch.delenv("LCU_LOCKFILE", raising=False)
    monkeypatch.setattr(lcu, "DEFAULT_LOCKFILES", [str(tmp_path / "none")])
    outputs = iter(["", UX_COMMAND_LINE])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=next(outputs), returncode=0)

    monkeypatch.setattr(lcu.subprocess, "run", fake_run)
    assert lcu.find_lockfile() is None
    fresh_probe.now += lcu.PROCESS_PROBE_BACKOFF + 0.1
    assert lcu.find_lockfile().port == 51234
    assert len(calls) == 2


def test_start_here_does_not_promise_an_exe_help_text():
    import build_exe

    assert "FlexersDraftCoach.exe --help" not in build_exe.START_HERE
    assert "python run.py --help" in build_exe.START_HERE
    assert "launchers above" in build_exe.START_HERE
