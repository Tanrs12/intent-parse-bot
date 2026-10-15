"""Build a shareable Windows package: dist/FlexersDraftCoach/ and dist/FlexersDraftCoach-<version>.zip.

    python build_exe.py            # build + zip
    python build_exe.py --no-zip   # just the folder

Needs `pip install pyinstaller`. The folder holds FlexersDraftCoach.exe, the engine and web assets
under _internal/, the team loadfiles, launcher .bat files and a START HERE.txt.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "FlexersDraftCoach"
DIST = ROOT / "dist" / NAME
ART_CANDIDATES = [ROOT / "art"]   # the MatchupMaster porcelain set is 143 MB; the app falls back to CommunityDragon art

LAUNCHERS = {
    "Draft Coach (live client).bat": r'''@echo off
cd /d "%~dp0"
echo Starting Flexers Draft Coach against your League client...
echo Coach page: http://127.0.0.1:8765/   (close this window to stop)
FlexersDraftCoach.exe --load "loadfiles\flexers-team.json" --window --hud
pause
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
echo Using your own League client, roster synced from http://%COACH%
FlexersDraftCoach.exe --window --hud --sync http://%COACH%
pause
''',
    "Coach PC (share roster on the LAN).bat": r'''@echo off
cd /d "%~dp0"
echo Coach mode: other players point "Player (sync from coach PC).bat" at this machine's IP.
FlexersDraftCoach.exe --host 0.0.0.0 --load "loadfiles\flexers-team.json" --window --hud
pause
''',
}

START_HERE = """FLEXERS DRAFT COACH
===================

Unzip the whole folder anywhere (keep FlexersDraftCoach.exe next to the _internal folder).

Double-click one of these:

  Draft Coach (live client).bat          You are in champ select on this PC. Opens the small
                                         always-on-top panel and the in-client HUD, and the full
                                         coach page at http://127.0.0.1:8765/
  Draft Coach (practice, no client).bat  Replays a scripted draft so you can see how it works.
  Draft Coach (any queue).bat            Also coaches normals / solo queue (default: Ranked Flex + customs).
  Player (sync from coach PC).bat        Five machines: each player runs this, types the coach's
                                         IP, and gets the shared roster with their own client.
  Coach PC (share roster on the LAN).bat The coach runs this one so the players can sync.

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
        "--name", NAME, "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'draftcoach' / 'static'}{sep}draftcoach/static",
        "--add-data", f"{ROOT / 'draftcoach' / 'champion_ids.json'}{sep}draftcoach",
        "--add-data", f"{ROOT / 'league_comp_engine.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_flex_core.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_draft_backend.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_player_data.py'}{sep}engine",
        "--add-data", f"{ROOT / 'league_pro_data.py'}{sep}engine",
        "--add-data", f"{ROOT / 'data'}{sep}engine/data",
        "--add-data", f"{ROOT / 'tools'}{sep}engine/tools",
        # The brain is imported from _internal/engine at runtime, exactly like the source tree.
        "--exclude-module", "league_comp_engine", "--exclude-module", "league_flex_core",
        "--exclude-module", "league_draft_backend", "--exclude-module", "league_player_data",
        "--exclude-module", "league_pro_data",
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
    skip_dirs = {".git", ".github", "dist", "build", "var", "__pycache__", ".pytest_cache", ".venv", "handoff"}
    for item in sorted(ROOT.iterdir()):
        if item.name in skip_dirs or item.name.startswith("."):
            continue
        if item.is_dir():
            shutil.copytree(item, source / item.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        elif item.suffix.lower() in (".py", ".txt", ".md", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini") or item.name == "Dockerfile":
            shutil.copy2(item, source / item.name)
    (DIST / "var").mkdir(exist_ok=True)
    for name, body in LAUNCHERS.items():
        (DIST / name).write_text(body, encoding="utf-8")
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
