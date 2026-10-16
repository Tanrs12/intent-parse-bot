from __future__ import annotations

import datetime as dt

from draftcoach.updater import ReleaseInfo, _is_update_available, _parse_time


def release(commit: str = "b" * 40, when: str = "2026-10-16T12:00:00Z") -> ReleaseInfo:
    return ReleaseInfo(
        commit=commit,
        asset_url="https://example.invalid/FlexersDraftCoach.zip",
        asset_updated=_parse_time(when),
        release_name="latest",
    )


def test_same_commit_is_current():
    build = {"commit": "b" * 40, "built_at": "2026-10-16T11:00:00+00:00"}
    assert _is_update_available(build, release()) is False


def test_newer_release_is_update():
    build = {"commit": "a" * 40, "built_at": "2026-10-16T11:00:00+00:00"}
    assert _is_update_available(build, release()) is True


def test_newer_local_build_is_not_downgraded_while_release_lags():
    build = {"commit": "c" * 40, "built_at": "2026-10-16T13:00:00+00:00"}
    assert _is_update_available(build, release(when="2026-10-16T12:00:00Z")) is False


def test_legacy_build_without_commit_updates_when_release_is_newer():
    build = {"built_at": "2026-10-16T10:00:00+00:00"}
    assert _is_update_available(build, release()) is True


def test_parse_time_is_timezone_aware():
    value = _parse_time("2026-10-16T12:00:00Z")
    assert value is not None
    assert value.tzinfo is not None
    assert value == dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.timezone.utc)
