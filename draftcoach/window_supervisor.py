"""Run the overlay window in its own process, so a window problem never stops the coach.

The server (League client watcher, bridge, auto-swaps) used to share a process with the WebView2
windows: a WebView2 crash shut everything down mid champion select. Now the windows (coach window +
panel, see window.py) are a child process (``run.py --window-only``). If it crashes it is restarted (at
most a few times in a short spell); when the player closes the windows, ``on_closed`` quits the app.
Its output goes to ``var/window.log``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("draftcoach.window")

MAX_RESTARTS = 4            # crashes tolerated per RESTART_WINDOW seconds before giving up
RESTART_WINDOW = 600.0


def window_command(host: str, port: int, script: Path | None = None) -> list[str]:
    """The child command: the frozen exe re-runs itself, a source checkout runs run.py."""
    import os

    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(script)]
    return base + ["--window-only", "--host", host, "--port", str(port), "--no-update", "--no-auto-sync",
                   "--parent-pid", str(os.getpid())]


class WindowSupervisor:
    def __init__(self, command: list[str], log_path: Path, on_closed=None):
        """``on_closed`` runs when the player closes the windows (exit code 0): the app quits then, since a
        windowed build has no console to stop it from."""
        self.command = command
        self.on_closed = on_closed
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.crashes: list[float] = []
        self._lock = threading.Lock()
        self._stopped = False
        self._job = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def open(self) -> bool:
        """Start the window unless it is already open; returns True when a new window was started."""
        with self._lock:
            if self._stopped or self.running:
                return False
            self._job = self._job or _kill_on_close_job()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            out = open(self.log_path, "a", encoding="utf-8")
            out.write(f"\n--- overlay window started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            out.flush()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(self.command, stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
            out.close()
            proc = self.proc
            # Two independent guarantees that the window never outlives the server: the job object (kills it
            # with the server) and the window's own first act, watch_parent() on --parent-pid, which also
            # covers a server that died before the job could be assigned.
            if self._job is not None and not _assign_to_job(self._job, proc):
                log.warning("overlay window not bound to a job; its parent watch still closes it with the server")
        threading.Thread(target=self._watch, args=(proc,), daemon=True, name="overlay-window").start()
        return True

    def _watch(self, proc: subprocess.Popen) -> None:
        code = proc.wait()
        if self._stopped:
            return
        if code == 0:
            log.info("app windows closed")
            if self.on_closed is not None:
                self.on_closed()
            return
        now = time.time()
        self.crashes = [t for t in self.crashes if now - t < RESTART_WINDOW] + [now]
        if len(self.crashes) > MAX_RESTARTS:
            log.error("overlay window keeps failing (exit %s); see %s", code, self.log_path)
            return
        log.warning("overlay window exited with %s; reopening (details in %s)", code, self.log_path)
        time.sleep(2)
        self.open()

    def stop(self) -> None:
        with self._lock:   # no open() can publish a new child after this
            self._stopped = True
            proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def watch_parent(pid: int | None, on_exit) -> None:
    """In the window process: call ``on_exit`` when the server process ``pid`` ends (however it ends).
    The job object normally kills the window first; this is the backstop when the job could not bind it."""
    if not pid or sys.platform != "win32":
        return

    def wait() -> None:
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32")
            handle = kernel32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
            if not handle:
                on_exit()
                return
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return
        on_exit()

    threading.Thread(target=wait, daemon=True, name="server-watch").start()


def _kill_on_close_job():
    """A Windows job object that kills its processes when the server process ends, however it ends
    (killed from Task Manager included). None elsewhere or when the API is unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                           "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        return job   # deliberately never closed: closing it is exactly what kills the window
    except Exception:  # noqa: BLE001
        return None


def _assign_to_job(job, proc: subprocess.Popen) -> bool:
    if job is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        return bool(ctypes.WinDLL("kernel32").AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle))))
    except Exception:  # noqa: BLE001
        log.debug("could not bind the overlay window to the server's lifetime", exc_info=True)
        return False


def _resume(proc: subprocess.Popen) -> None:
    """Start a process created with CREATE_SUSPENDED (all its threads)."""
    try:
        import ctypes
        from ctypes import wintypes

        status = ctypes.WinDLL("ntdll").NtResumeProcess(wintypes.HANDLE(int(proc._handle)))
        if status != 0:
            raise OSError(f"NtResumeProcess returned 0x{status & 0xFFFFFFFF:08x}")
    except Exception:  # noqa: BLE001
        log.exception("could not start the overlay window")
        proc.kill()
        proc.wait(timeout=5)
