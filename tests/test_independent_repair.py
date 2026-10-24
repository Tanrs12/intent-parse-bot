"""Windows regression tests for the independently runnable Draft Coach repair path."""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from build_exe import LAUNCHERS, ROOT


def test_windows_release_contains_independent_repair_launcher():
    assert "Repair Draft Coach Update.bat" in LAUNCHERS
    assert "repair-draftcoach.ps1" in LAUNCHERS["Repair Draft Coach Update.bat"]
    script = ROOT / "tools" / "repair_draftcoach.ps1"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "Get-Process -Name 'FlexersDraftCoach'" in content
    assert "/IS /IT" in content
    assert "/XD var /XF config.json team-sync.json" in content
    assert "Installed build verification failed" in content


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows PowerShell and robocopy")
def test_windows_independent_repair_updates_and_preserves_user_data(tmp_path):
    target = tmp_path / "Installed Draft Coach"
    (target / "var").mkdir(parents=True)
    (target / "FlexersDraftCoach.exe").write_bytes(b"old executable")
    (target / "build-info.json").write_text(json.dumps({"commit": "a" * 40}), encoding="utf-8")
    (target / "var" / "roster.json").write_text("my roster", encoding="utf-8")
    (target / "config.json").write_text("my config", encoding="utf-8")
    (target / "team-sync.json").write_text("my room", encoding="utf-8")

    archive = tmp_path / "new release.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("FlexersDraftCoach/FlexersDraftCoach.exe", b"new executable")
        z.writestr("FlexersDraftCoach/build-info.json", json.dumps({"commit": "b" * 40}))
        z.writestr("FlexersDraftCoach/config.json", "package config")
        z.writestr("FlexersDraftCoach/team-sync.json", "package room")
        z.writestr("FlexersDraftCoach/other.txt", "new file")

    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "tools" / "repair_draftcoach.ps1"),
        "-Target", str(target),
        "-ArchivePath", str(archive),
        "-ExpectedCommit", "b" * 40,
    ]
    repaired = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert repaired.returncode == 0, (repaired.stdout, repaired.stderr)
    assert (target / "FlexersDraftCoach.exe").read_bytes() == b"new executable"
    assert json.loads((target / "build-info.json").read_text())["commit"] == "b" * 40
    assert (target / "other.txt").read_text() == "new file"
    assert (target / "var" / "roster.json").read_text() == "my roster"
    assert (target / "config.json").read_text() == "my config"
    assert (target / "team-sync.json").read_text() == "my room"

    rejected = subprocess.run(command[:-1] + ["c" * 40], capture_output=True, text=True, timeout=60)
    assert rejected.returncode != 0
    assert json.loads((target / "build-info.json").read_text())["commit"] == "b" * 40
    assert "mismatch" in (rejected.stdout + rejected.stderr).lower()
