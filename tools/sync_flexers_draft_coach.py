from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[1]
if str(REPO_DEFAULT) not in sys.path:
    sys.path.insert(0, str(REPO_DEFAULT))

from league_pro_data import refresh_recent_pro_data

SNAPSHOTS = (
    "league_comp_meta.json",
    "league_lane_matchups.json",
    "league_player_profiles.json",
    "league_recent_pro.json",
    "league_role_champions.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def server_up(server: str) -> bool:
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/api/config", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def import_files(server: str, paths: list[Path]) -> None:
    payload = {
        "files": [
            {"name": path.name, "text": path.read_text(encoding="utf-8")}
            for path in paths
        ]
    }
    request = urllib.request.Request(
        server.rstrip("/") + "/api/import",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    failures = [row for row in result.get("results", []) if not row.get("ok")]
    if failures:
        raise RuntimeError("Draft Coach rejected an engine snapshot import")


def sync_snapshots(repo: Path, coach_dir: Path, state: dict, *, push: bool, server: str) -> list[Path]:
    loadfiles = coach_dir / "loadfiles"
    loadfiles.mkdir(parents=True, exist_ok=True)
    changed: list[Path] = []
    hashes = state.setdefault("hashes", {})

    for name in SNAPSHOTS:
        source = repo / "data" / name
        if not source.exists():
            continue
        fingerprint = sha256(source)
        if hashes.get(name) == fingerprint:
            continue
        shutil.copy2(source, loadfiles / name)
        hashes[name] = fingerprint
        changed.append(source)

    if changed and push:
        import_files(server, changed)
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep Flexers Draft Coach synced with the Intent League backend snapshots."
    )
    parser.add_argument("--coach-dir", type=Path, required=True, help="FlexersDraftCoach folder")
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_DEFAULT,
        help="intent-parse-bot checkout (defaults to this repository)",
    )
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--interval", type=int, default=600, help="refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="refresh/import once and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    coach_dir = args.coach_dir.resolve()
    state_path = coach_dir / "var" / "intent-backend-sync-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {"hashes": {}}

    while True:
        try:
            data = asyncio.run(refresh_recent_pro_data(repo / "data" / "league_recent_pro.json"))
            print(
                f"pro refresh: {data.get('game_count', 0)} games, "
                f"patch {data.get('latest_patch', 'unknown')}"
            )
        except Exception as exc:
            print(f"pro refresh failed; retained last good snapshot: {type(exc).__name__}: {exc}")

        up = server_up(args.server)
        try:
            changed = sync_snapshots(repo, coach_dir, state, push=up, server=args.server)
            if changed:
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                print("updated: " + ", ".join(path.name for path in changed))
                if up:
                    print("imported into running Draft Coach")
        except Exception as exc:
            print(f"sync/import failed: {type(exc).__name__}: {exc}")

        if args.once:
            return
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()

