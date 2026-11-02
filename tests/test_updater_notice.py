"""A failed self-update tells the player what happened, once, instead of silently restarting the old build."""
from __future__ import annotations

import datetime as dt
import json

from draftcoach.updater import failed_update_notice


def _marker(root, **extra):
    (root / "var").mkdir(exist_ok=True)
    record = {"commit": "a" * 40, "failed_at": "2026-11-02T18:00:00+00:00",
              "message": "File replacement failed (robocopy code 16). See updater.log.", **extra}
    (root / "var" / "update-failed.json").write_text(json.dumps(record), encoding="utf-8")


def test_recent_failure_is_explained_once(tmp_path):
    _marker(tmp_path)
    now = dt.datetime(2026, 11, 2, 18, 30, tzinfo=dt.timezone.utc)
    text = failed_update_notice(tmp_path, now=now)
    assert text and "robocopy code 16" in text and "Repair Draft Coach Update.bat" in text and "OneDrive" in text
    # the second start stays quiet: the marker is now flagged as notified
    assert failed_update_notice(tmp_path, now=now) is None
    assert json.loads((tmp_path / "var" / "update-failed.json").read_text(encoding="utf-8"))["notified"] is True


def test_old_or_missing_failures_stay_quiet(tmp_path):
    assert failed_update_notice(tmp_path) is None
    _marker(tmp_path)
    later = dt.datetime(2026, 11, 3, 6, 0, tzinfo=dt.timezone.utc)
    assert failed_update_notice(tmp_path, now=later) is None
    (tmp_path / "var" / "update-failed.json").write_text("not json", encoding="utf-8")
    assert failed_update_notice(tmp_path) is None
