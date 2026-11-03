"""Summarise the private OP.GG reference cache without transmitting its samples.

Read-only, offline, and safe to run before/after each research session.
These are *rolling unversioned* references, never calibrated draft evidence.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_opgg_mcp_reference import CACHE_PATH, TTL_SECONDS


def audit_cache(folder: Path = CACHE_PATH, *, now: float | None = None) -> dict:
    """Return counts only. Never upload or publish cache rows."""
    folder = Path(folder)
    now = time.time() if now is None else now
    role_counts = Counter()
    fresh_counts = Counter()
    invalid = expired = unversioned = total = 0
    for path in folder.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            role = str(row["role"])
            if not all(row.get(k) for k in ("ours", "enemy", "games")):
                raise ValueError("missing expected pair fields")
            if role not in ("Top", "Jungle", "Mid", "ADC", "Support"):
                raise ValueError("unexpected role")
            age = now - float(row["retrieved_epoch"])
        except (OSError, ValueError, TypeError, KeyError):
            invalid += 1
            continue
        total += 1
        role_counts[role] += 1
        if 0 <= age <= TTL_SECONDS:
            fresh_counts[role] += 1
        else:
            expired += 1
        if not row.get("source_patch") or not row.get("rank_filter"):
            unversioned += 1
    summaries = list((folder / "_summaries").glob("*.json"))
    return {
        "pair_files": total,
        "fresh": sum(fresh_counts.values()),
        "expired": expired,
        "invalid": invalid,
        "unversioned": unversioned,
        "summary_files": len(summaries),
        "by_role": {role: {
            "pairs": role_counts[role],
            "fresh": fresh_counts[role],
        } for role in ("Top", "Jungle", "Mid", "ADC", "Support")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=CACHE_PATH)
    args = parser.parse_args()
    report = audit_cache(args.folder)
    print("Private OP.GG rolling references (read-only, no requests)")
    print("Pairs: {pair_files}; fresh: {fresh}; expired: {expired}; "
          "invalid: {invalid}; rank/patch unverified: {unversioned}; "
          "summary metadata files: {summary_files}".format(**report))
    for role, stats in report["by_role"].items():
        print(f"  {role:<8} {stats['pairs']:>3} pairs ({stats['fresh']} fresh)")
    print("No data is sent to OP.GG, Discord, GitHub or the draft scorer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
