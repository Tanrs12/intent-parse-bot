"""Refresh solo-queue per-role win/pick/ban rates for the any-queue draft coach.

The bundled role statistics come from pro play, which says little about a Solo/Duo or normal
lobby. lolalytics' public tier-list page inlines its table as Qwik state keyed by Riot champion
id; this reads the five lane pages once (honest User-Agent, robots.txt allows ``/``) and writes
``data/league_soloq_tierlist.json``:

    {"patch": "26.21", "tier": "emerald_plus", "fetched_at": "...", "source": "...",
     "roles": {"Top": {"Garen": {"win": 51.2, "pick": 4.1, "ban": 2.3, "games": 41234}, ...}, ...}}

    python tools/refresh_soloq_tierlist.py [--tier emerald_plus]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "league_soloq_tierlist.json"
LANES = {"Top": "top", "Jungle": "jungle", "Mid": "middle", "ADC": "bottom", "Support": "support"}
UA = "FlexersDraftCoach/1.0 (personal champion-select tool)"
REF = re.compile(r"^[\x00-\x1f]?([0-9a-z]+)$")


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def decode(html: str) -> dict[str, dict]:
    """The champion-id -> row table inside the page's Qwik state."""
    blob = re.search(r'<script type="qwik/json">(.*?)</script>', html, re.S)
    if not blob:
        raise RuntimeError("no qwik/json state on the page")
    objs = json.loads(blob.group(1))["objs"]

    def ref(value) -> int | None:
        if isinstance(value, str) and len(value) <= 5 and REF.match(value):
            try:
                return int(REF.match(value).group(1), 36)
            except ValueError:
                return None
        return None

    def resolve(value, depth=0):
        index = ref(value)
        if depth <= 6 and index is not None and 0 <= index < len(objs) and objs[index] is not value:
            return resolve(objs[index], depth + 1)
        if isinstance(value, dict) and depth <= 6:
            return {k: resolve(v, depth + 1) for k, v in value.items()}
        return value

    rows = {i for i, o in enumerate(objs) if isinstance(o, dict) and "pr" in o and "wr" in o}
    for obj in objs:
        if isinstance(obj, dict) and len(obj) >= 20 and sum(ref(v) in rows for v in obj.values()) >= 20:
            return {k: resolve(v) for k, v in obj.items()}
    raise RuntimeError("tier-list table not found in page state")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tier", default="emerald_plus")
    args = parser.parse_args()
    names = json.loads((ROOT / "draftcoach" / "champion_ids.json").read_text(encoding="utf-8"))["champions"]
    roles: dict[str, dict] = {}
    patch = None
    for role, lane in LANES.items():
        url = f"https://lolalytics.com/lol/tierlist/?lane={lane}&tier={args.tier}"
        html = fetch(url)
        found = re.search(r"Patch (\d+\.\d+)", html) or re.search(r"patch=(\d+\.\d+)", html)
        patch = patch or (found.group(1) if found else None)
        table = decode(html)
        rows = {}
        for champion_id, row in table.items():
            name = names.get(str(champion_id))
            if not name or not isinstance(row, dict) or row.get("wr") is None:
                continue
            rows[name] = {"win": row.get("wr"), "pick": row.get("pr"), "ban": row.get("br"), "games": row.get("games")}
        if len(rows) < 30:
            raise RuntimeError(f"{role}: only {len(rows)} champions decoded")
        roles[role] = rows
        print(f"{role}: {len(rows)} champions")
        time.sleep(2)
    OUT.write_text(json.dumps({
        "schema_version": 1, "patch": patch, "tier": args.tier, "queue": "RANKED_SOLO_5x5",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "source": "lolalytics.com public tier list (lane pages)", "roles": roles,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT} (patch {patch})")


if __name__ == "__main__":
    main()
