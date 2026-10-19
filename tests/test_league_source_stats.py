"""Rapid per-draft source sampling never hides a newer data snapshot."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from league_source_stats import source_mtime_ns, source_stat_scope


def test_one_stat_per_source_inside_one_draft_and_refresh_next_draft(tmp_path):
    f = tmp_path / "pro.json"
    f.write_text("{}", encoding="utf8")
    first = f.stat().st_mtime_ns

    @source_stat_scope
    def evaluate():
        sampled = source_mtime_ns(f)
        os.utime(f, ns=(first + 5_000_000_000, first + 5_000_000_000))
        # Even if an update lands mid-draft, scoring remains consistent:
        # the next independent draft sees the new file timestamp.
        assert source_mtime_ns(f) == sampled
        return sampled

    assert evaluate() == first
    assert source_mtime_ns(f) == first + 5_000_000_000
    assert evaluate() == first + 5_000_000_000


def test_nested_source_scopes_share_snapshot_but_threads_do_not(tmp_path):
    f = tmp_path / "pairs.json"
    f.write_text("{}", encoding="utf8")
    first = f.stat().st_mtime_ns

    @source_stat_scope
    def inner():
        return source_mtime_ns(f)

    @source_stat_scope
    def outer():
        before = inner()
        os.utime(f, ns=(first + 6_000_000_000, first + 6_000_000_000))
        with ThreadPoolExecutor(max_workers=1) as executor:
            # A separate drafting thread cannot borrow this source snapshot.
            assert executor.submit(source_mtime_ns, f).result() == first + 6_000_000_000
        assert inner() == before
        return before

    assert outer() == first
    assert inner() == first + 6_000_000_000
