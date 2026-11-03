"""Who sits at this PC, whatever account they log into.

Players use smurfs, so a Riot ID alone does not say who is drafting. This PC is linked once to a roster
player ("This PC is: Will" in the overlay's My champs tab); from then on every account seen in this
machine's League client (its PUUID and Riot ID) is added to that player, so their pools, priorities and
comfort apply on any account.

The device id is a salted SHA-256 of the Windows MachineGuid (``uuid.getnode()`` elsewhere): stable for
this install of Windows, meaningless outside this app, and stored only in ``var/device-owner.json``.
Nothing about other players' machines is collected; IP addresses are not used (they change, and other
people's are not ours to record).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from . import VAR_DIR

SALT = b"flexers-draft-coach/device/v1"
OWNER_FILE = "device-owner.json"


def _machine_secret() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            pass
    return f"node-{uuid.getnode():x}"


def device_id() -> str:
    # DRAFTCOACH_DEVICE_TAG: harnesses that run several apps on one PC give each its own device.
    tag = os.environ.get("DRAFTCOACH_DEVICE_TAG", "")
    secret = _machine_secret() + (f"|{tag}" if tag else "")
    return hashlib.sha256(SALT + secret.encode("utf-8")).hexdigest()[:20]


def _path(var_dir: Path | None = None) -> Path:
    return (var_dir or VAR_DIR) / OWNER_FILE


def load_owner(var_dir: Path | None = None) -> str | None:
    """The roster player key this PC belongs to (only if the link was made on this machine)."""
    try:
        data = json.loads(_path(var_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("device") != device_id():
        return None   # a copied var folder from another PC does not carry its owner along
    key = data.get("player_key")
    return str(key) if key else None


def save_owner(player_key: str | None, var_dir: Path | None = None) -> None:
    path = _path(var_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"device": device_id(), "player_key": player_key, "linked_at": time.time()}),
                    encoding="utf-8")


def link_account(player, riot_id: str | None, puuid: str | None, riot_key) -> bool:
    """Add this account to the player; True when something new was recorded."""
    changed = False
    if puuid and puuid not in player.puuids:
        player.puuids.append(str(puuid))
        changed = True
    if riot_id and riot_key(riot_id) not in {riot_key(r) for r in player.riot_ids}:
        player.riot_ids.append(str(riot_id))
        changed = True
    return changed
