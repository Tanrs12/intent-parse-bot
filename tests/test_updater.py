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


def test_failed_update_is_suppressed_briefly_but_not_forever(tmp_path):
    import json
    from draftcoach.updater import _recent_failed_update
    root = tmp_path / "app"
    (root / "var").mkdir(parents=True)
    when = dt.datetime(2026, 10, 19, 18, 0, tzinfo=dt.timezone.utc)
    (root / "var" / "update-failed.json").write_text(
        json.dumps({"commit": "b" * 40, "failed_at": when.isoformat(), "message": "copy failed"}),
        encoding="utf-8",
    )
    assert _recent_failed_update(root, release(), now=when + dt.timedelta(minutes=30))
    assert not _recent_failed_update(root, release(), now=when + dt.timedelta(hours=5))
    assert not _recent_failed_update(root, release(commit="c" * 40), now=when + dt.timedelta(minutes=10))


def test_replacer_script_uses_safe_merge_logging_and_validation(tmp_path):
    from draftcoach.updater import _render_replacer_script
    script = _render_replacer_script(tmp_path / "App With Spaces",
                                      tmp_path / "new" / "FlexersDraftCoach",
                                      "b" * 40, ["--hud", "--load", r"loadfiles\team.json"])
    assert "robocopy.exe" in script and "/R:6" in script
    assert "build-info.json" in script and "$Installed.commit -ne $ExpectedCommit" in script
    assert "config.json team-sync.json" in script
    assert "update-failed.json" in script
    assert "NoRestart" in script
    assert "Remove-Item -Recurse" not in script
    assert "--hud" in script and "loadfiles" in script


def test_windows_replacer_copies_verifies_and_preserves_local_settings(tmp_path):
    import json
    import subprocess
    import sys
    import pytest
    from draftcoach.updater import _render_replacer_script
    if sys.platform != "win32":
        pytest.skip("PowerShell and robocopy test requires Windows")
    source = tmp_path / "extracted" / "FlexersDraftCoach"
    target = tmp_path / "Installed App"
    source.mkdir(parents=True)
    (target / "var").mkdir(parents=True)
    commit = "b" * 40
    (source / "build-info.json").write_text(json.dumps({"commit": commit}), encoding="utf-8")
    (source / "FlexersDraftCoach.exe").write_bytes(b"new-executable")
    (source / "team-sync.json").write_text("package", encoding="utf-8")
    (target / "build-info.json").write_text(json.dumps({"commit": "a" * 40}), encoding="utf-8")
    (target / "FlexersDraftCoach.exe").write_bytes(b"old-executable")
    (target / "config.json").write_text("local-config", encoding="utf-8")
    (target / "team-sync.json").write_text("local-room", encoding="utf-8")
    (target / "var" / "roster.json").write_text("important", encoding="utf-8")
    ps = tmp_path / "apply-update.ps1"
    ps.write_text(_render_replacer_script(target, source, commit, ["--hud"]), encoding="utf-8")
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ps), "-ParentPid", "0", "-NoRestart"],
        capture_output=True, text=True, timeout=40,
    )
    assert run.returncode == 0, (run.stdout, run.stderr, (target / "var" / "updater.log").read_text())
    assert (target / "FlexersDraftCoach.exe").read_bytes() == b"new-executable"
    assert json.loads((target / "build-info.json").read_text())["commit"] == commit
    assert (target / "var" / "roster.json").read_text() == "important"
    assert (target / "config.json").read_text() == "local-config"
    assert (target / "team-sync.json").read_text() == "local-room"
    assert not (target / "var" / "update-failed.json").exists()
    assert "Update verified" in (target / "var" / "updater.log").read_text()


def test_windows_replacer_logs_failure_without_deleting_app(tmp_path):
    import json
    import subprocess
    import sys
    import pytest
    from draftcoach.updater import _render_replacer_script
    if sys.platform != "win32":
        pytest.skip("PowerShell and robocopy test requires Windows")
    bad_source = tmp_path / "bad-source"
    target = tmp_path / "Installed App"
    bad_source.mkdir()
    (target / "var").mkdir(parents=True)
    (target / "FlexersDraftCoach.exe").write_bytes(b"old-intact")
    commit = "b" * 40
    ps = tmp_path / "apply-update.ps1"
    ps.write_text(_render_replacer_script(target, bad_source, commit), encoding="utf-8")
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ps), "-ParentPid", "0", "-NoRestart"],
        capture_output=True, text=True, timeout=35,
    )
    assert run.returncode != 0, (run.stdout, run.stderr)
    assert (target / "FlexersDraftCoach.exe").read_bytes() == b"old-intact"
    failed = json.loads((target / "var" / "update-failed.json").read_text(encoding="utf-8-sig"))
    assert failed["commit"] == commit
    assert "Downloaded package" in failed["message"]
    assert "Update failed" in (target / "var" / "updater.log").read_text()
