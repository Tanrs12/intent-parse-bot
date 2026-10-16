from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/Tanrs12/intent-parse-bot/releases/latest"
ASSET_NAME = "FlexersDraftCoach.zip"
UPDATE_EXIT_CODE = 42
_USER_AGENT = "FlexersDraftCoach-Updater/1"


@dataclass(frozen=True)
class ReleaseInfo:
    commit: str | None
    asset_url: str
    asset_updated: dt.datetime | None
    release_name: str


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_build_info(root: Path) -> dict:
    try:
        raw = json.loads((root / "build-info.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _fetch_release() -> ReleaseInfo:
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=5.0) as response:
        raw = json.load(response)
    body = str(raw.get("body") or "")
    match = re.search(r"Built from\s+([0-9a-fA-F]{40})", body)
    commit = match.group(1).lower() if match else None
    asset = next((item for item in raw.get("assets") or [] if item.get("name") == ASSET_NAME), None)
    if not asset or not asset.get("browser_download_url"):
        raise RuntimeError(f"{ASSET_NAME} is missing from the latest release")
    return ReleaseInfo(
        commit=commit,
        asset_url=str(asset["browser_download_url"]),
        asset_updated=_parse_time(asset.get("updated_at")),
        release_name=str(raw.get("name") or raw.get("tag_name") or "latest Flexers Draft Coach"),
    )


def _is_update_available(build: dict, release: ReleaseInfo) -> bool:
    current_commit = str(build.get("commit") or "").lower()
    if current_commit and release.commit and current_commit == release.commit:
        return False
    local_built = _parse_time(build.get("built_at"))
    # A local/dev build can legitimately be newer than the currently published GitHub asset while
    # Actions is still rebuilding. Never offer to replace that newer build with an older release.
    if local_built and release.asset_updated and release.asset_updated <= local_built:
        return False
    if release.commit and current_commit:
        return True
    return bool(release.asset_updated and (not local_built or release.asset_updated > local_built))


def _ask_update(release: ReleaseInfo) -> bool:
    if sys.platform != "win32":
        return False
    text = (
        "A newer Flexers Draft Coach build is available.\n\n"
        "Update now? The app will download the latest team build, replace itself, and restart.\n\n"
        "Choosing No continues locally, but shared Flexers draft sync is disabled until you update."
    )
    # MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND
    return ctypes.windll.user32.MessageBoxW(0, text, "Flexers Draft Coach update", 0x00000004 | 0x00000020 | 0x00010000) == 6


def _download_release(release: ReleaseInfo) -> tuple[Path, Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="flexers-draft-update-"))
    archive = temp_root / ASSET_NAME
    request = urllib.request.Request(release.asset_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=45.0) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    extract_root = temp_root / "new"
    extract_root.mkdir()
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract_root)
    package = extract_root / "FlexersDraftCoach"
    if not (package / "FlexersDraftCoach.exe").is_file():
        raise RuntimeError("downloaded update does not contain FlexersDraftCoach.exe")
    return temp_root, package


def _quote_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _launch_replacer(root: Path, package: Path, temp_root: Path) -> None:
    script = temp_root / "apply-update.ps1"
    restart_bat = root / "Draft Coach (live client).bat"
    script.write_text(
        "param([int]$ParentPid)\n"
        "$ErrorActionPreference = 'Stop'\n"
        f"$Source = {_quote_ps(str(package))}\n"
        f"$Target = {_quote_ps(str(root))}\n"
        f"$Restart = {_quote_ps(str(restart_bat))}\n"
        "try { Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue } catch {}\n"
        "Start-Sleep -Milliseconds 900\n"
        "$preserve = @('var', 'config.json')\n"
        "Get-ChildItem -LiteralPath $Target -Force | Where-Object { $preserve -notcontains $_.Name } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue\n"
        "Get-ChildItem -LiteralPath $Source -Force | Where-Object { $_.Name -ne 'var' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force }\n"
        "if (Test-Path -LiteralPath $Restart) { Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', ('\"' + $Restart + '\"')) -WorkingDirectory $Target }\n"
        "else { Start-Process -FilePath (Join-Path $Target 'FlexersDraftCoach.exe') -WorkingDirectory $Target }\n",
        encoding="utf-8",
    )
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-ParentPid", str(os.getpid())],
        cwd=str(temp_root),
        creationflags=flags,
        close_fds=True,
    )


def maybe_update(root: Path) -> bool:
    """Return True when an updater has been launched and this process should exit with code 42."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or "--no-update" in sys.argv:
        return False
    try:
        build = _load_build_info(root)
        release = _fetch_release()
        if not _is_update_available(build, release):
            return False
        if not _ask_update(release):
            os.environ["INTENT_UPDATE_SKIPPED"] = "1"
            return False
        temp_root, package = _download_release(release)
        _launch_replacer(root, package, temp_root)
        return True
    except Exception as exc:
        # An offline GitHub check or updater failure must never prevent the player from opening League.
        print(f"Update check skipped: {exc}")
        return False
