"""A few private, attributed OP.GG MCP matchup observations per invocation.

For a private noncommercial coach only. No bulk data harvesting. OP.GG's written
response requests attribution and avoidance of excessive traffic; default max 6
new requests per run, at least 3 seconds apart, 24-hour cache. This never
alters the legal draft or overwrites a bundled statistical dataset.

Example:
  python tools/collect_opgg_mcp_matchups.py --role Top --enemy Shen \
      --ours Camille Gnar Gwen Kayle
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_opgg_mcp_reference import MIN_REQUEST_GAP_SECONDS, fetch_cached, get_cached


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=["Top","Jungle","Mid","ADC","Support"])
    parser.add_argument("--enemy", required=True)
    parser.add_argument("--ours", nargs="+", required=True)
    parser.add_argument("--max-new", type=int, default=6)
    parser.add_argument("--delay", type=float, default=MIN_REQUEST_GAP_SECONDS)
    args = parser.parse_args()
    max_new = max(0, min(8, args.max_new))
    gap = max(MIN_REQUEST_GAP_SECONDS, args.delay)
    unique = list(dict.fromkeys(args.ours))
    new_calls = 0
    successful = 0
    for ours in unique:
        cached = get_cached(args.role, ours, args.enemy)
        if cached:
            row = cached
            mode = "cache"
        elif new_calls >= max_new:
            print(f"SKIPPED {ours} (per-run limit {max_new} reached)", flush=True)
            continue
        else:
            if new_calls:
                time.sleep(gap)
            try:
                row = fetch_cached(args.role, ours, args.enemy)
            except Exception as exc:
                print(f"ERROR {ours}: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
                new_calls += 1
                continue
            mode = "new"
            new_calls += 1
        successful += 1
        print(f"{mode:>5} {args.role} {ours} vs {args.enemy}: "
              f"{row['wins']}/{row['games']} ({row['head_to_head_win_rate']:.1f}%) "
              f"baseline={row['role_baseline_win_rate']}%, residual={row['residual_pp']}pp"
              " (OP.GG rolling; patch/rank unverified)", flush=True)
    print(f"Finished: {new_calls} new requests, <= {max_new} allowed this invocation.")
    return 2 if new_calls and not successful else 0


if __name__ == "__main__":
    raise SystemExit(main())
