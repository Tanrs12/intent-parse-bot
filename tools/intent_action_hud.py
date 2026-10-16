"""Launch the preserved Rat HUD copy against the INTent data adapter."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draftcoach.hud_intent import run


if __name__ == "__main__":
    instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        instance_lock.bind(("127.0.0.1", 18769))
    except OSError:
        instance_lock.close()
        raise SystemExit(0)
    run("http://127.0.0.1:8767", theme="client")
