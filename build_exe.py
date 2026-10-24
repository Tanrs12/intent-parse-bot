"""Build a shareable Windows package: dist/FlexersDraftCoach/ and dist/FlexersDraftCoach-<version>.zip.

    python build_exe.py            # build + zip
    python build_exe.py --no-zip   # just the folder

Needs `pip install pyinstaller`. The folder holds FlexersDraftCoach.exe, the engine and web assets
under _internal/, the team loadfiles, launcher .bat files and a START HERE.txt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "FlexersDraftCoach"
DIST = ROOT / "dist" / NAME
ART_CANDIDATES = [ROOT / "art"]   # the MatchupMaster porcelain set is 143 MB; the app falls back to CommunityDragon art
TEAM_SYNC_RELAY = "https://draft--intent-parse-bot--cdzz6bjv54yz.code.run"
TEAM_SYNC_ROOM_KEY = "flexers-intent-ranked-flex-room-v1"  # shared room identity, not a credential

LAUNCHERS = {
    "Repair Draft Coach Update.bat": r'''@echo off
cd /d "%~dp0"
echo Repairing your Draft Coach installation without the in-app updater...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair-draftcoach.ps1"
if errorlevel 1 (
  echo.
  echo Repair failed. See var\updater.log for details.
  pause
  exit /b 1
)
echo.
echo Update repaired and verified. You can now open Draft Coach normally.
pause
''',
    "Draft Coach (live client).bat": r'''@echo off
cd /d "%~dp0"
echo Starting Flexers Draft Coach against your League client...
echo Ten INTent composition plans are available during champ select (PgUp/PgDn for pages).
echo Coach page: http://127.0.0.1:8765/   (close this window to stop)
:restart
FlexersDraftCoach.exe --load "loadfiles\flexers-team.json" --hud
set EXITCODE=%ERRORLEVEL%
if "%EXITCODE%"=="42" exit /b 0
if not "%EXITCODE%"=="0" echo Draft Coach stopped unexpectedly; restarting in 2 seconds...
timeout /t 2 /nobreak >nul
goto restart
''',
    "Draft Coach (practice, no client).bat": r'''@echo off
cd /d "%~dp0"
echo Replaying a scripted draft with the team roster. No League client needed.
echo Coach page: http://127.0.0.1:8765/   (close this window to stop)
FlexersDraftCoach.exe --simulate --load "loadfiles\flexers-team.json" --window
pause
''',
    "Draft Coach (any queue).bat": r'''@echo off
cd /d "%~dp0"
echo Coaching every champion select (normals too). Default launchers: Ranked Flex and custom games only.
FlexersDraftCoach.exe --load "loadfileslexers-team.json" --window --hud --any-queue
pause
''',
    "Player (sync from coach PC).bat": r'''@echo off
cd /d "%~dp0"
set /p COACH=Coach PC address (for example 192.168.1.20:8765):
if "%COACH%"=="" set COACH=127.0.0.1:8765
echo Using your own League client, with roster AND authoritative live draft synced from http://%COACH%
FlexersDraftCoach.exe --window --hud --sync http://%COACH%
pause
''',
    "Coach PC (share roster on the LAN).bat": r'''@echo off
cd /d "%~dp0"
echo Coach mode: this PC is the authoritative INTent draft brain; players point the sync launcher at this machine's IP.
FlexersDraftCoach.exe --host 0.0.0.0 --load "loadfiles\flexers-team.json" --window --hud
pause
''',
}

START_HERE = """FLEXERS DRAFT COACH
===================

Unzip the whole folder anywhere (keep FlexersDraftCoach.exe next to the _internal folder).

Double-click one of these:

  Draft Coach (live client).bat          Normal launcher for EVERYONE. It checks for updates, then identifies your Riot
                                         account and automatically joins the shared Flexers draft room.
                                         No code/IP needs to be pasted or shared.
  Draft Coach (practice, no client).bat  Replays a scripted draft so you can see how it works.
                                         In the outside-left overlay, PgDn/PgUp switches between
                                         drafts 1-5 and 6-10 while in champion select.
  Draft Coach (any queue).bat            Also coaches normals / solo queue (default: Ranked Flex + customs).
  Player (sync from coach PC).bat        LAN-only alternative: type the coach PC's local IP.
  Coach PC (share roster on the LAN).bat LAN-only host for the above.

If the in-app updater keeps requesting the same update, close Draft Coach, then
double-click "Repair Draft Coach Update.bat" in this folder. It independently downloads
and verifies the latest GitHub build without depending on the old executable. Your
var folder, config.json and team-sync.json are preserved. Check var\\updater.log
if the repair fails.

If Windows marked the zip as "downloaded from the internet", the app removes that mark from its own
files on first start (what Explorer's Unblock button does). If a start still fails on a .NET error, right-click
the zip, Properties, tick Unblock, and extract it again.

If it stops with an error, the details are in var\\crash.log (and a dialog shows them); the normal
log is var\\draftcoach.log. Both sit next to the exe. "Failed to load Python DLL" means the exe was run
from inside the zip or moved away from its _internal folder: extract the whole zip first.

Windows SmartScreen may warn the first time because the .exe is unsigned: choose
"More info" > "Run anyway". Windows Firewall may ask to allow it on private networks
(needed only for the sync between machines).

The complete source code is in the source folder (the same repository): after
pip install -r requirements.txt -r requirements-draftcoach.txt, python run.py there runs it uncompiled.

Edit loadfiles\\flexers-team.json to change pools, familiarity (comfort / refresh / developing),
preference, scouting and the playbook. Drop any JSON or CSV loadfile into loadfiles\\ while it
runs and it is imported within seconds. Your merged roster is saved in var\\roster.json.

Command line (from this folder): FlexersDraftCoach.exe --help
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    version = "1.0.0"
    for line in (ROOT / "draftcoach" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            version = line.split("=", 1)[1].strip().strip("\"'")

    if DIST.exists():
        shutil.rmtree(DIST)
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", NAME, "--icon", str(ROOT / "draftcoach" / "flexers_draft.ico"),
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'draftcoach' / 'static'}{sep}draftcoach/static",
        "--add-data", f"{ROOT / 'draftcoach' / 'champion_ids.json'}{sep}draftcoach",
        "--add-data", f"{ROOT / 'draftcoach' / 'flexers_draft.ico'}{sep}draftcoach",
        "--add-data", f"{ROOT / 'league_comp_engine.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_patch_live.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_flex_core.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_draft_backend.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_player_data.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_pro_data.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_multi_source.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_aggregate_sources.py'}{sep}engine",
        "--add-data", f"{ROOT / 'data'}{sep}engine/data",
        "--add-data", f"{ROOT / 'tools'}{sep}engine/tools",
        # The brain is imported from _internal/engine at runtime, exactly like the source tree.
        "--exclude-module", "league_comp_engine", "--exclude-module", "league_flex_core",
        "--exclude-module", "league_draft_backend", "--exclude-module", "league_player_data",
        "--exclude-module", "league_pro_data", "--exclude-module", "league_multi_source",
        "--exclude-module", "league_aggregate_sources",
        "--collect-submodules", "aiohttp", "--hidden-import", "aiohttp",   # the bridge's pro-data refresh
        "--exclude-module", "discord", "--exclude-module", "asyncpg", "--exclude-module", "cogs",
        "--collect-submodules", "uvicorn", "--collect-submodules", "websockets",
        "--hidden-import", "anyio._backends._asyncio",
        "--collect-all", "webview", "--collect-all", "clr_loader", "--collect-all", "pythonnet",
        "--hidden-import", "webview.platforms.winforms", "--hidden-import", "webview.platforms.edgechromium",
        "--exclude-module", "pytest", "--exclude-module", "httpx",
        str(ROOT / "run.py"),
    ]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)

    # Tanoras's flow: the packaged coach page and HUD take their visible picks and bans from the
    # in-process INTent bridge (tools/intent_live_draft_bridge.py), never from the packaged advisor.
    sys.path.insert(0, str(ROOT / "tools"))
    import patch_flexers_intent_ui as intent_ui
    static = DIST / "_internal" / "draftcoach" / "static"
    intent_ui.patch_coach(static / "coach.html")
    intent_ui.patch_hud(static / "hud.html")
    for backup in static.glob("*.intent.bak"):
        backup.unlink()
    print("packaged UI patched for the INTent live bridge")

    # Everything the team edits sits next to the executable.
    (DIST / "loadfiles").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "loadfiles" / "flexers-team.json", DIST / "loadfiles" / "flexers-team.json")
    if (ROOT / "loadfiles" / "examples").is_dir():
        shutil.copytree(ROOT / "loadfiles" / "examples", DIST / "loadfiles" / "examples", dirs_exist_ok=True)
    shutil.copy2(ROOT / "config.example.json", DIST / "config.example.json")
    shutil.copy2(ROOT / "README.md", DIST / "README.md")
    shutil.copy2(ROOT / "FLEXERS_DRAFT_COACH.md", DIST / "FLEXERS_DRAFT_COACH.md")
    (DIST / "docs").mkdir(exist_ok=True)
    for name in ("draftcoach/research.md",):
        if (ROOT / "docs" / name).exists():
            shutil.copy2(ROOT / "docs" / name, DIST / "docs" / Path(name).name)
    for art in ART_CANDIDATES:
        if art.is_dir():
            shutil.copytree(art, DIST / "art", dirs_exist_ok=True)
            break
    # The full source next to the exe, so the zip is also a runnable / readable checkout.
    source = DIST / "source"
    if source.exists():
        shutil.rmtree(source)
    source.mkdir(parents=True, exist_ok=True)
    skip_dirs = {".git", ".github", "dist", "build", "var", "__pycache__", ".pytest_cache", ".venv", "handoff"}
    for item in sorted(ROOT.iterdir()):
        if item.name in skip_dirs or item.name.startswith("."):
            continue
        if item.is_dir():
            shutil.copytree(item, source / item.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        elif item.suffix.lower() in (".py", ".txt", ".md", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini") or item.name == "Dockerfile":
            shutil.copy2(item, source / item.name)
    (DIST / "var").mkdir(exist_ok=True)
    # The same opaque Flexers room ID is shipped in every build, including GitHub Actions releases.
    # It is coordination metadata rather than a credential: anyone with the app already has it.
    room_key = TEAM_SYNC_ROOM_KEY
    room_label = "FLEX-" + hashlib.sha256(room_key.encode("utf-8")).hexdigest()[:6].upper()
    (DIST / "team-sync.json").write_text(json.dumps({
        "relay": TEAM_SYNC_RELAY,
        "room_key": room_key,
        "room_label": room_label,
    }, indent=2), encoding="utf-8")
    print(f"automatic Flexers sync room: {room_label}")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = ""
    (DIST / "build-info.json").write_text(json.dumps({
        "commit": commit,
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "release_api": "https://api.github.com/repos/Tanrs12/intent-parse-bot/releases/latest",
    }, indent=2), encoding="utf-8")
    for name, body in LAUNCHERS.items():
        (DIST / name).write_text(body, encoding="utf-8")
    # Standalone repair helper: works even if the installed EXE has a broken updater.
    shutil.copy2(ROOT / "tools" / "repair_draftcoach.ps1", DIST / "repair-draftcoach.ps1")
    (DIST / "START HERE.txt").write_text(START_HERE, encoding="utf-8")

    if not args.no_zip:
        out = ROOT / "dist" / f"{NAME}.zip"          # fixed name: stable download link on GitHub Releases
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path in sorted(DIST.rglob("*")):
                if path.is_file():
                    z.write(path, Path(NAME) / path.relative_to(DIST))
        print(f"zip: {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"folder: {DIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
