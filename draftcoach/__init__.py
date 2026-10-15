"""Flexers Draft Coach: a live League of Legends draft overlay and coaching panel.

The drafting brain is the repository's own ``league_comp_engine`` / ``league_flex_core`` /
``league_draft_backend`` (the same code behind the Discord ``/league game`` command). This
package adds everything around it:

* ``champions``   - champion id <-> name mapping, canonical spellings, fuzzy lookup
* ``roster``      - the live team roster: players, pools, priorities, scouting
* ``loadfiles``   - import "loadfiles" (JSON / CSV) that update pools, scouting, data
* ``draft_order`` - tournament draft sequence + the manual draft board state machine
* ``state``       - normalised draft state (from the League client or the manual board)
* ``advisor``     - turns a draft state into guidance: picks, bans, counters, alerts
* ``lcu``         - League Client connection (lockfile + websocket)
* ``hub``         - coalescing recompute loop and broadcast to the UI
* ``server``      - FastAPI app: overlay page, coach panel, REST + websocket
* ``simulate``    - scripted champ select for development without a client
"""

from __future__ import annotations

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller build: read-only code and the engine live in _internal/engine; everything the team
    # edits (loadfiles, var, config.json) sits next to the executable.
    ROOT = Path(sys.executable).resolve().parent
    ENGINE_DIR = Path(getattr(sys, "_MEIPASS", ROOT / "_internal")) / "engine"
else:
    # Source checkout of intent-parse-bot: the engine (league_comp_engine.py, league_flex_core.py,
    # league_draft_backend.py) and data/ live at the repository root next to this package.
    ROOT = Path(__file__).resolve().parent.parent
    ENGINE_DIR = ROOT
VAR_DIR = ROOT / "var"
LOADFILE_DIR = ROOT / "loadfiles"

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

__version__ = "1.0.0"
