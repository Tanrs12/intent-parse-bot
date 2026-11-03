"""Collect up to two OP.GG top-3 strong/weak summaries into a private cache.

Official OP.GG MCP only; no website scraping, bulk exports, or guessed patches.
Example:
 python tools/collect_opgg_mcp_champion_counters.py --role Top --champions Malphite Camille
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from league_opgg_mcp_reference import CACHE_PATH, MIN_REQUEST_GAP_SECONDS, _key
from league_opgg_mcp_champion_counters import request_summary, save_summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role", required=True, choices=["Top","Jungle","Mid","ADC","Support"])
    p.add_argument("--champions", nargs="+", required=True)
    p.add_argument("--mode", default="ranked", choices=["ranked","flex"])
    p.add_argument("--max-new", type=int, default=2)
    p.add_argument("--delay", type=float, default=MIN_REQUEST_GAP_SECONDS)
    args = p.parse_args()
    max_new = max(0, min(3, args.max_new))
    cache_dir = CACHE_PATH / "_summaries"
    cache_dir.mkdir(parents=True, exist_ok=True)
    calls = successes = 0
    for champion in dict.fromkeys(args.champions):
        cache_file = cache_dir / f"{_key(args.role)}-{_key(champion)}-{args.mode}.json"
        try:
            old = json.loads(cache_file.read_text(encoding="utf8"))
            if (old.get("champion") == champion and old.get("mode") == args.mode
                and time.time() - float(old["captured_epoch"]) <= 24 * 3600):
                print(f"CACHED {args.role} {champion}: {old.get('pairs', 0)} listed matchups")
                successes += 1
                continue
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if calls >= max_new:
            print(f"SKIPPED {champion}: at most {max_new} fresh queries per run")
            continue
        if calls:
            time.sleep(max(MIN_REQUEST_GAP_SECONDS, args.delay))
        calls += 1
        try:
            rows = request_summary(args.role, champion, game_mode=args.mode)
            if not rows:
                raise ValueError("provider returned no sufficient-matchup counters")
            saved, skipped = save_summary(rows)
            cache_file.write_text(json.dumps({
                "champion": champion, "mode": args.mode,
                "captured_epoch": time.time(), "pairs": len(rows),
                "saved": saved, "skipped_existing": skipped,
            }, indent=2), encoding="utf8")
            print(f"NEW {args.role} {champion}: {len(rows)} observed counters, "
                  f"{saved} saved, {skipped} already cached")
            for row in rows:
                print(f"  {row['provider_group']}: {champion} vs {row['enemy']}: "
                      f"{row['head_to_head_win_rate']:.1f}% (n={row['games']})")
            successes += 1
        except Exception as exc:
            print(f"ERROR {champion}: {type(exc).__name__}: {str(exc)[:180]}")
    print(f"Finished: {calls} new official-MCP requests, {successes} champions processed")
    return 2 if calls and not successes else 0


if __name__ == "__main__":
    raise SystemExit(main())
