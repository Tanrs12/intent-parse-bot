"""Single read-only, versioned source for Discord and desktop League snapshots.

The GitHub scheduled job collects matches for all linked accounts. This module
downloads those public validated JSON snapshots onto the running Discord host;
the desktop bridge already reads the same URLs into its own cache.
"""
from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

DATA_BASE_URL = "https://raw.githubusercontent.com/Tanrs12/intent-parse-bot/main/data"
SNAPSHOT_SCHEMAS = {
    "league_player_profiles.json": "players",
    "league_lane_matchups.json": "matchups",
}
MAX_BYTES = 20_000_000


def sync_shared_league_data(
    directory: Path | str,
    *,
    base_url: str = DATA_BASE_URL,
) -> dict:
    """Download changed validated snapshots; preserve last known good on failure.

    The caller invalidates in-process caches for names in changed.
    This does not scrape other providers or read user credentials.
    """
    root = Path(directory)
    changes: list[str] = []
    errors: dict[str, str] = {}
    checked: list[str] = []

    for filename, required in SNAPSHOT_SCHEMAS.items():
        try:
            request = urllib.request.Request(
                f"{base_url.rstrip('/')}/{filename}",
                headers={"User-Agent": "FlexersDraftCoach/1.0"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("snapshot exceeds 20MB")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get(required), dict) or not data[required]:
                raise ValueError(f"invalid {filename} schema")

            target = root / filename
            checked.append(filename)
            if target.exists() and target.read_bytes() == raw:
                continue
            root.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".shared-download")
            temporary.write_bytes(raw)
            try:
                temporary.replace(target)
            except PermissionError:
                # Some Windows installs block replacing an existing file by rename.
                shutil.copy2(temporary, target)
                temporary.unlink(missing_ok=True)
            changes.append(filename)
        except Exception as exc:
            errors[filename] = type(exc).__name__
    return {"changed": changes, "checked": checked, "errors": errors}
