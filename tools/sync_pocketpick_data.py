"""Download/import only the two PocketPick aggregates approved for private use.

Usage: python tools/sync_pocketpick_data.py [--from-dir existing-folder]
Defaults to local private var/pocketpick. The upstream data is NOT committed to
GitHub nor bundled into publicly downloadable releases.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_pocketpick_history import storage_dir, archive_summary

BASE = "https://pocketpick.gg/data/"
NAMES = ("matchv5-metrics.json", "gd15-matchups.json")
MAX_FILE_BYTES = 3_000_000


def fetch(name: str) -> bytes:
    if name not in NAMES:
        raise ValueError("Unapproved PocketPick file")
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "INTentDraftCoach-Private-DataImport/1"})
    with urllib.request.urlopen(req, timeout=35) as response:
        blob = response.read(MAX_FILE_BYTES + 1)
    if not blob or len(blob) > MAX_FILE_BYTES:
        raise ValueError("PocketPick aggregate has unexpected size")
    return blob


def validate(files: dict[str, bytes]) -> dict:
    m = json.loads(files[NAMES[0]])
    g = json.loads(files[NAMES[1]])
    if (m.get("patches") != {"16.16": 30000} or g.get("patch") != {"16.16": 30000}
        or m.get("queue") != 420 or m.get("region") != "euw"
        or m.get("matches") != 30000 or g.get("matches") != 30000):
        raise ValueError("Unexpected source provenance; refusing mixed patches/tiers")
    if len(m.get("matchups", {})) < 10000 or len(m.get("metrics", {})) < 170:
        raise ValueError("Incomplete or unsupported PocketPick aggregate")
    if not isinstance(g.get("data"), dict) or not isinstance(g.get("pairRes"), dict):
        raise ValueError("Incomplete PocketPick kit/synergy evidence")
    return {
        "provider": "PocketPick",
        "source_url": BASE, "license_scope": "Private non-commercial project, email approval 2026-10-20",
        "patch": "16.16", "tier": "Silver-Platinum (pooled)", "region": "EUW",
        "queue": 420, "matches": 30000,
        "champions": len(m["metrics"]),
        "matchups": len(m["matchups"]),
        "files": {
            name: {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
            for name, blob in files.items()
        },
        "caution": "Historical low-to-mid tier evidence only. No current-patch or D2+ claims.",
    }


def sync_archive(*, from_dir: Path | None = None, target: Path | None = None) -> dict:
    """Explicit private archive download, called at bot boot outside draft scoring."""
    target = target or storage_dir()
    files = {
        name: ((from_dir / name).read_bytes() if from_dir else fetch(name))
        for name in NAMES
    }
    manifest = validate(files)
    target.mkdir(parents=True, exist_ok=True)
    # Only write once both files have validated; avoid partial updates.
    for name, blob in files.items():
        temp = target / (name + ".incoming")
        temp.write_bytes(blob)
        temp.replace(target / name)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"manifest": manifest, "summary": archive_summary()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-dir", type=Path, help="Existing downloaded PocketPick aggregates")
    parser.add_argument("--target", type=Path, default=None)
    opts = parser.parse_args()
    print(json.dumps(sync_archive(from_dir=opts.from_dir, target=opts.target), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
