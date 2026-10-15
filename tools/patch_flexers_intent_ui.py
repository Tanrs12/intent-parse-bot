from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_coach(path: Path):
    backup = path.with_suffix(path.suffix + ".intent.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    text = path.read_text(encoding="utf-8")
    if "INTENT_LIVE_BRIDGE" in text:
        return

    text = replace_once(
        text,
        '  let latest = null, champions = {}, names = [], popover = null, rosterCache = null, typesFilled = false, deadline = 0, changeKey = "", selectedChampion = null, lastStepIndex = null, browserInitialised = false;\n',
        '  let latest = null, champions = {}, names = [], popover = null, rosterCache = null, typesFilled = false, deadline = 0, changeKey = "", selectedChampion = null, lastStepIndex = null, browserInitialised = false;\n'
        '  // INTENT_LIVE_BRIDGE: the visible recommendations come from the same backend as /league game.\n'
        '  let intentBridge = { active: false, disabled: false, reason: "Connecting to INTent backend" };\n'
        '  async function refreshIntentBridge() {\n'
        '    try {\n'
        '      const r = await fetch("http://127.0.0.1:8766/api/guidance", { cache: "no-store" });\n'
        '      if (!r.ok) throw new Error("bridge " + r.status);\n'
        '      intentBridge = await r.json();\n'
        '    } catch (e) { intentBridge = { active: false, disabled: false, reason: "INTent backend unavailable" }; }\n'
        '    if (latest) render(latest);\n'
        '  }\n'
        '  setInterval(refreshIntentBridge, 1000);\n'
        '  refreshIntentBridge();\n',
        "coach bridge state",
    )

    text = replace_once(
        text,
        '    const st = snapshot.status || {}, state = snapshot.state, g = snapshot.guidance, draft = snapshot.draft || {};\n',
        '    const st = snapshot.status || {}, state = snapshot.state, g = intentBridge.active ? intentBridge.guidance : null, draft = snapshot.draft || {};\n',
        "coach guidance override",
    )

    text = replace_once(
        text,
        '    if (!state || !g) { key = st.connection === "manual" ? "Manual board" : "Standby"; head.textContent = st.connection === "manual" ? "Start a draft on the board" : "Waiting for champion select"; reason.textContent = st.connection === "manual" ? "Choose your lineup and draft format, then add picks and bans below." : "Your lobby and champion pools appear here automatically."; }\n',
        '    if (!intentBridge.active) { key = intentBridge.disabled ? "INTent disabled" : "INTent backend"; head.textContent = intentBridge.reason || (intentBridge.disabled ? "Draft Coach disabled" : "Waiting for INTent backend"); reason.textContent = intentBridge.disabled ? "No Flex draft recommendations are shown in ARAM or ARAM: Mayhem." : "Live recommendations will appear when the INTent backend has the current five players and champion select state."; }\n'
        '    else if (!state || !g) { key = st.connection === "manual" ? "Manual board" : "Standby"; head.textContent = st.connection === "manual" ? "Start a draft on the board" : "Waiting for champion select"; reason.textContent = st.connection === "manual" ? "Choose your lineup and draft format, then add picks and bans below." : "Your lobby and champion pools appear here automatically."; }\n',
        "coach disabled message",
    )

    text = replace_once(
        text,
        '    $("pick-section").hidden = showBan || !state || oursLocked === 5; $("ban-section").hidden = !showBan;\n',
        '    $("pick-section").hidden = !intentBridge.active || showBan || !state || oursLocked === 5; $("ban-section").hidden = !intentBridge.active || !showBan;\n',
        "coach sections gate",
    )
    text = replace_once(
        text,
        '    $("other-section").hidden = !state || oursLocked === 5;\n',
        '    $("other-section").hidden = !intentBridge.active || !state || oursLocked === 5;\n',
        "coach other gate",
    )

    text = replace_once(
        text,
        '  function renderClientActions(snapshot) {\n    const box = $("client-actions"), local = snapshot.local, accept = $("accept"), hoverBtn = $("hover-btn");\n',
        '  function renderClientActions(snapshot) {\n    const box = $("client-actions"), local = snapshot.local, accept = $("accept"), hoverBtn = $("hover-btn");\n    // INTENT_LIVE_BRIDGE: the packaged app\'s one-click action belongs to its old advisor.\n    // Hide it rather than ever locking a champion that disagrees with the shared backend.\n    if (intentBridge) { box.hidden = true; return; }\n',
        "coach client action gate",
    )
    path.write_text(text, encoding="utf-8")


def patch_hud(path: Path):
    backup = path.with_suffix(path.suffix + ".intent.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    text = path.read_text(encoding="utf-8")
    if "INTENT_LIVE_BRIDGE" in text:
        return

    marker = '  function hide(id) { $(id).hidden = true; }\n\n  function render(snap) {\n'
    injected = (
        '  function hide(id) { $(id).hidden = true; }\n\n'
        '  // INTENT_LIVE_BRIDGE: use the shared /league game backend, never the packaged advisor.\n'
        '  let intentBridge = { active: false, disabled: false, reason: "Connecting to INTent backend" };\n'
        '  async function refreshIntentBridge() {\n'
        '    try {\n'
        '      const r = await fetch("http://127.0.0.1:8766/api/guidance", { cache: "no-store" });\n'
        '      if (!r.ok) throw new Error("bridge " + r.status);\n'
        '      intentBridge = await r.json();\n'
        '    } catch (e) { intentBridge = { active: false, disabled: false, reason: "INTent backend unavailable" }; }\n'
        '    if (lastSnapshot) render(lastSnapshot);\n'
        '  }\n'
        '  setInterval(refreshIntentBridge, 1000);\n'
        '  refreshIntentBridge();\n\n'
        '  function render(snap) {\n'
    )
    text = replace_once(text, marker, injected, "hud bridge state")
    text = replace_once(
        text,
        '    const s = snap.state, g = snap.guidance, local = snap.local, st = snap.status || {};\n',
        '    const s = snap.state, g = intentBridge.active ? intentBridge.guidance : null, local = null, st = snap.status || {};\n',
        "hud guidance override",
    )
    path.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("coach_dir")
    args = parser.parse_args()
    root = Path(args.coach_dir)
    static = root / "_internal" / "draftcoach" / "static"
    patch_coach(static / "coach.html")
    patch_hud(static / "hud.html")
    print("Patched Flexers Draft Coach UI for INTent live backend.")


if __name__ == "__main__":
    main()
