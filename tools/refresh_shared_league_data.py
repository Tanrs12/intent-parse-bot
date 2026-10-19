"""Scheduled, stdlib-only refresh of the full linked Flex roster's Ranked records.

Writes an updated snapshot into data/league_player_profiles.json and reports whether
new games were actually ingested. Does NOT fetch private LoLalytics endpoints.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_player_data import refresh_recent_player_data

def main() -> int:
    result = refresh_recent_player_data(
        ROOT / "data" / "league_player_profiles.json",
        match_limits={"RANKED": 20, "NORMAL": 0},
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["accounts"] or not result["players"]:
        print("No player accounts refreshed successfully; refusing to publish a stale snapshot.", file=sys.stderr)
        return 1
    changed = int(result.get("new_games") or 0) > 0
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"updated={'true' if changed else 'false'}\n")
            f.write(f"accounts={result['accounts']}\n")
            f.write(f"new_games={result['new_games']}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"Flex linked-account refresh: **{result['accounts']} accounts**, "
                    f"**{result['new_games']} new games**, {len(result['errors'])} errors.\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
