"""The in-app "Check for updates" / "Update now" path (Roster & imports drawer)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from draftcoach import updater
from draftcoach.server import create_app
from draftcoach.updater import ReleaseInfo, _parse_time


def release(commit: str = "b" * 40, when: str = "2026-11-03T14:22:00Z") -> ReleaseInfo:
    return ReleaseInfo(commit=commit, asset_url="https://example.invalid/FlexersDraftCoach.zip",
                       asset_updated=_parse_time(when), release_name="latest")


def install(tmp_path, commit: str = "a" * 40):
    root = tmp_path / "app"
    (root / "var").mkdir(parents=True)
    (root / "build-info.json").write_text(json.dumps({"commit": commit, "built_at": "2026-11-03T10:00:00+00:00"}), encoding="utf-8")
    return root


def test_status_reports_available_update_for_packaged_build(tmp_path):
    root = install(tmp_path)
    s = updater.status(root, frozen=True, fetch=lambda: release())
    assert s["packaged"] and s["available"]
    assert s["build_commit"] == "a" * 7 and s["release_commit"] == "b" * 7
    assert s["release_at"].startswith("2026-11-03T14:22")
    assert s["error"] is None and s["last_failure"] is None


def test_status_is_up_to_date_on_same_commit_and_never_offers_source_runs(tmp_path):
    root = install(tmp_path, commit="b" * 40)
    assert updater.status(root, frozen=True, fetch=lambda: release())["available"] is False
    # A source checkout never updates itself, however old its (missing) build info is.
    assert updater.status(tmp_path, frozen=False, fetch=lambda: release())["available"] is False


def test_status_reports_offline_and_last_failure_instead_of_raising(tmp_path):
    root = install(tmp_path)
    (root / "var" / "update-failed.json").write_text(json.dumps({"commit": "b" * 40, "message": "copy failed"}), encoding="utf-8")

    def offline():
        raise OSError("no network")

    s = updater.status(root, frozen=True, fetch=offline)
    assert s["available"] is False and "no network" in s["error"] and s["last_failure"] == "copy failed"


def test_start_downloads_and_launches_replacer_only_when_newer(tmp_path):
    root = install(tmp_path)
    calls = []
    result = updater.start(root, fetch=lambda: release(),
                           download=lambda rel: (tmp_path / "tmp", tmp_path / "tmp" / "new" / "FlexersDraftCoach"),
                           launch=lambda r, package, temp: calls.append((r, package, temp)))
    assert result["commit"] == "b" * 7 and calls and calls[0][0] == root
    with pytest.raises(RuntimeError, match="latest"):
        updater.start(install(tmp_path / "same", commit="b" * 40), fetch=lambda: release(),
                      download=lambda rel: pytest.fail("must not download"), launch=lambda *a: None)


def test_update_routes_check_and_then_stop_the_server(tmp_path, monkeypatch):
    root = install(tmp_path)
    monkeypatch.setattr(updater, "_fetch_release", lambda: release())
    monkeypatch.setattr(updater, "_download_release", lambda rel: (tmp_path / "tmp", tmp_path / "tmp" / "new" / "FlexersDraftCoach"))
    launched = []
    monkeypatch.setattr(updater, "_launch_replacer", lambda r, package, temp: launched.append(r))
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    from draftcoach import server as server_module
    monkeypatch.setattr(server_module, "ROOT", root)
    app = create_app({"auto_profiles": False}, enable_lcu=False, roster_path=tmp_path / "roster.json")
    exits = []
    app.state.request_exit = lambda: exits.append(True)
    with TestClient(app) as client:
        s = client.get("/api/update").json()
        assert s["available"] and s["packaged"] and s["release_commit"] == "b" * 7
        r = client.post("/api/update")
        assert r.status_code == 200 and r.json()["commit"] == "b" * 7
        assert launched == [root]
        assert app.state.update_launched is True
        # the exit is scheduled on the loop shortly after the response
        import time
        for _ in range(30):
            if exits:
                break
            time.sleep(0.05)
            client.get("/api/config")
        assert exits
    with TestClient(app) as client:
        monkeypatch.setattr(updater, "_fetch_release", lambda: release(commit="a" * 40))
        assert client.post("/api/update").status_code == 409
