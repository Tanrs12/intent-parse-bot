"""Launch Rat's actual native HUD unchanged; only its data endpoint is adapted to INTent."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draftcoach.hud import run


if __name__ == "__main__":
    run("http://127.0.0.1:8767", theme="client")
