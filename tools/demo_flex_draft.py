"""Recorded end-to-end demo: one flex team, five real apps, one fake ranked-flex lobby.

Runs the real Draft Coach (hub + bridge) five times, one per teammate, each connected to its own
fake League client (tools/fake_lcu.py, ranked format) sharing one champion select, and a local
team relay. A browser then drives the real pages the way the team would:

  1. Coach page: pick the five, build drafts, send one to the team.
  2. Draft Studio: generate drafts, reject one champion ("Not this").
  3. Overlay: link "This PC is", edit a pool, resize, open settings.
  4. Champion select opens with the wrong lanes and pick order: the apps auto-swap.
  5. Simultaneous bans: every teammate clicks Ban in their own overlay.
  6. Picks: the scripted enemy reveals all-physical champions; each teammate clicks Lock in.
  7. Finalisation, game start.

Every screenshot, the local player's video and a timeline of what each overlay said (taken from
the page, not invented) go to --out. Nothing writes to the real League client, the team's real relay,
Discord, the bot's database or the repository's own state: the apps run from a copy, with their own
state folders, and the Northflank CLI hidden. It is not offline: the apps still make their usual
public, read-only fetches (the shared data snapshots on GitHub, champion artwork).

    python tools/demo_flex_draft.py --out D:/demo        # headless, records video
    python tools/demo_flex_draft.py --headed             # watch it happen
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
ROOM_KEY = "flexers-demo-room-2026-11"
RELAY_PORT, LOBBY_PORT, CLIENT_PORT, APP_PORT = 8895, 8900, 8910, 8920

# Seat order = our cells 5..9 (red side): R1 first pick ... R5 last pick.
TEAM = [  # (Riot ID, roster key, lane the client assigns before any swap)
    ("Adittive#EUW", "justkayla", "middle"),                      # Kayla: plays Top tonight, the local player
    ("FakerChovyHilary#ALLY", "chud-useless-robber-of-progress", "jungle"),   # Cian
    ("LargeLarry#EUW", "weekend-stev", "top"),                   # Ste: plays Mid
    ("JackJak#EUW", "the-ice-cream-fella", "bottom"),            # Jack
    ("ReformUKGaming#7710", "the-cheeky-chuckler", "utility"),   # Jezz
]
PLAYERS = ["Kayla", "Cian", "Ste", "Jack", "Jezz"]
# Blue side picks in lane order (top, jungle, mid, bot, support): every one deals physical damage.
ENEMY = {
    "names": ["Blue Top#EUW", "Blue Jungle#EUW", "Blue Mid#EUW", "Blue Bot#EUW", "Blue Support#EUW"],
    "bans": ["Sylas", "Vi", "Orianna", "Lee Sin", "Akali"],
    "picks": ["Yorick", "Kha'Zix", "Zed", "Caitlyn", "Pyke"],
    "hover_first": [None, None, None, None, None],
}


def http(method: str, url: str, body: dict | None = None, timeout: float = 8.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def get(url: str, timeout: float = 5.0):
    try:
        return http("GET", url, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def wait_for(predicate, timeout: float, every: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = predicate()
        except Exception:  # noqa: BLE001
            value = None
        if value:
            return value
        time.sleep(every)
    return None


class Demo:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Path(args.out).resolve()
        self.work = self.out / "_work"
        self.tree = self.work / "tree"
        self.procs: list[subprocess.Popen] = []
        self.timeline: list[dict] = []
        self.shots = 0
        self.lobby = f"http://127.0.0.1:{LOBBY_PORT}"

    # ---- isolated copy -------------------------------------------------------------------------
    def prepare(self) -> None:
        if self.work.exists():
            shutil.rmtree(self.work, ignore_errors=True)
        (self.out / "screens").mkdir(parents=True, exist_ok=True)
        for old in (self.out / "screens").glob("*.png"):
            old.unlink()
        listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True,
                                text=True, check=True).stdout.splitlines()
        for rel in listed:
            if rel.startswith(("dist/", "build/", "var/")) or not (REPO / rel).is_file():
                continue
            dest = self.tree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dest)
        # The team's signups (pools, Discord ids) are a git-ignored cache the bridge refreshes from the bot;
        # without it the bridge recognises nobody and the team draft never steers champion select.
        signups = REPO / "data" / "live_flex_signups.json"
        if signups.is_file():
            shutil.copy2(signups, self.tree / "data" / "live_flex_signups.json")
        roster = REPO / "var" / "roster.json"
        for i in range(5):
            var = self.work / f"app{i}"
            var.mkdir(parents=True, exist_ok=True)
            shutil.copy2(roster, var / "roster.json")
        (self.work / "appdata").mkdir(exist_ok=True)       # no npm/northflank.CMD here: no live bot queries
        (self.work / "localappdata").mkdir(exist_ok=True)

    def env(self, i: int | None = None) -> dict:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "APPDATA": str(self.work / "appdata"),
               "LOCALAPPDATA": str(self.work / "localappdata")}
        if i is not None:
            var = self.work / f"app{i}"
            env.update({"INTENT_LIVE": "1", "DRAFTCOACH_VAR_DIR": str(var), "DRAFTCOACH_DEVICE_TAG": f"demo-app{i}",
                        "INTENT_ROLE_FILE": str(var / "my-role.json")})
        return env

    def spawn(self, name: str, cmd: list[str], i: int | None = None) -> None:
        log = open(self.work / f"{name}.log", "w", encoding="utf-8")
        self.procs.append(subprocess.Popen(cmd, cwd=str(self.tree), stdout=log, stderr=subprocess.STDOUT, env=self.env(i),
                                           creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))

    def start(self) -> None:
        self.spawn("relay", [PY, "tools/local_relay.py", "--port", str(RELAY_PORT)])
        self.spawn("lobby", [PY, "tools/fake_lcu.py", "--dir", str(self.work / "lobby"), "--control-port", str(LOBBY_PORT),
                             "--client-port", str(CLIENT_PORT), "--our", ",".join(t[0] for t in TEAM), "--side", "red",
                             "--format", "ranked", "--planning", "20", "--turn", "45", "--finalization", "15",
                             "--enemy-delay", "4"])
        if not (wait_for(lambda: get(f"http://127.0.0.1:{RELAY_PORT}/health"), 20)
                and wait_for(lambda: get(self.lobby + "/control/state"), 20)):
            raise SystemExit("relay or fake lobby did not start; see " + str(self.work))
        for i in range(5):
            port = APP_PORT + 10 * i
            self.spawn(f"app{i}", [PY, "run.py", "--port", str(port), "--lockfile", str(self.work / "lobby" / f"client{i}" / "lockfile"),
                                   "--bridge", "--cloud-sync-mode", "auto", "--cloud-sync-url", f"http://127.0.0.1:{RELAY_PORT}",
                                   "--cloud-sync-key", ROOM_KEY, "--roster", str(self.work / f"app{i}" / "roster.json"),
                                   "--watch", "", "--no-update", "--log-level", "info"], i)
        for i in range(5):
            url = self.app(i)
            ok = wait_for(lambda: (lambda s: s and s["status"].get("connection") == "connected")(get(url + "/api/state")), 90)
            if not ok:
                raise SystemExit(f"app {i} did not connect to its fake client; see {self.work / f'app{i}.log'}")
        print("five apps connected", flush=True)

        # Before champion select the five sit in one relay room with no captain ("auto-idle"); a draft
        # sent now still reaches everyone, and the captain is elected when champion select opens.
        if not wait_for(lambda: all((((get(self.bridge(i) + "/api/guidance") or {}).get("sync") or {}).get("participants") or 0) >= 5
                                    for i in range(5)), 90, 1.0):
            raise SystemExit("the five apps did not all join the team room; see the app logs in " + str(self.work))
        print("all five in the team room", flush=True)

    def stop(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
        for proc in self.procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    def app(self, i: int) -> str:
        return f"http://127.0.0.1:{APP_PORT + 10 * i}"

    def bridge(self, i: int) -> str:
        return f"http://127.0.0.1:{APP_PORT + 10 * i + 1}"   # the bridge runs on app port + 1

    # ---- recording helpers ---------------------------------------------------------------------
    def shot(self, page, name: str, note: str) -> None:
        self.shots += 1
        path = self.out / "screens" / f"{self.shots:02d}-{name}.png"
        page.screenshot(path=str(path), full_page=False)
        panel = ""
        try:
            panel = page.locator("#panel").inner_text(timeout=1500) if page.locator("#panel").count() else ""
        except Exception:  # noqa: BLE001
            panel = ""
        self.timeline.append({"shot": path.name, "note": note, "at": round(time.time(), 1), "panel_text": panel[:1500]})
        print(f"  [{self.shots:02d}] {note}", flush=True)

    def lobby_state(self) -> dict:
        return get(self.lobby + "/control/state") or {}

    # ---- the story -----------------------------------------------------------------------------
    def run(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not self.args.headed)
            # Only the local player's window is shown; the four teammates' overlays run unseen.
            mates_browser = pw.chromium.launch(headless=True) if self.args.headed else browser
            me_ctx = browser.new_context(viewport={"width": 1280, "height": 860}, color_scheme="light",
                                         record_video_dir=str(self.out / "video"), record_video_size={"width": 1280, "height": 860})
            me = me_ctx.new_page()
            mates = []
            for i in range(1, 5):
                ctx = mates_browser.new_context(viewport={"width": 460, "height": 860}, color_scheme="light")
                mates.append(ctx.new_page())
            try:
                self.scene_game_plan(me)
                self.scene_studio(me)
                self.scene_overlay_settings(me)
                for i, page in enumerate(mates, start=1):
                    page.goto(self.app(i) + "/overlay")
                self.scene_champ_select(me, [me] + mates)
            finally:
                me_ctx.close()
                for page in mates:
                    page.context.close()
                browser.close()
                if mates_browser is not browser:
                    mates_browser.close()

    def scene_game_plan(self, me) -> None:
        print("scene 1: coach page, game plan", flush=True)
        me.goto(self.app(0) + "/")
        me.wait_for_selector("#gameplan-players .gameplan-player", timeout=60000)
        time.sleep(1.5)
        for name in PLAYERS:
            button = me.locator("#gameplan-players .gameplan-player", has=me.locator("b", has_text=name)).first
            if button.get_attribute("aria-pressed") != "true":
                button.click()
        for name in [b.inner_text() for b in me.locator("#gameplan-players .gameplan-player[aria-pressed=true] b").all()]:
            if name not in PLAYERS:
                me.locator("#gameplan-players .gameplan-player", has=me.locator("b", has_text=name)).first.click()
        me.locator("#gameplan-card").scroll_into_view_if_needed()
        self.shot(me, "game-plan-five", "Coach page: the five playing tonight are picked")
        me.click("#gameplan-build")
        me.wait_for_selector("#gameplan-drafts .gameplan-draft", timeout=120000)
        me.click("#gameplan-red")
        time.sleep(1.0)
        self.shot(me, "game-plan-drafts", "Drafts built for these five (red side pick order)")
        me.locator("#gameplan-drafts .captain-prefer.send").first.click()
        follow = wait_for(lambda: all(((get(self.bridge(i) + "/api/control") or {}).get("control") or {}).get("chosen")
                                      for i in range(5)), 60)
        time.sleep(2.0)
        self.shot(me, "game-plan-sent", "Draft 1 sent to the team" + (": all five apps follow it" if follow else " (not every app confirmed it)"))
        self.timeline.append({"note": "team draft reached all five apps" if follow else "team draft did NOT reach all five apps"})

    def scene_studio(self, me) -> None:
        print("scene 2: Draft Studio", flush=True)
        me.goto(self.app(0) + "/studio")
        me.wait_for_selector("#generate", timeout=30000)
        time.sleep(2.0)
        me.click("#generate")
        try:
            me.wait_for_selector("button:has-text('Not this')", timeout=60000)
        except Exception:  # noqa: BLE001
            self.shot(me, "studio", "Draft Studio (no drafts came back)")
            return
        time.sleep(0.8)
        self.shot(me, "studio-drafts", "Draft Studio: auto-built drafts for the lineup")
        me.locator("button:has-text('Not this')").first.click()
        time.sleep(3.0)
        self.shot(me, "studio-not-this", "Draft Studio: 'Not this' on one champion re-rolls just that lane")

    def scene_overlay_settings(self, me) -> None:
        print("scene 3: overlay settings", flush=True)
        me.goto(self.app(0) + "/overlay")
        me.wait_for_selector("#tab-draft", timeout=30000)
        time.sleep(2.0)
        self.shot(me, "overlay-before-lobby", "Overlay before champion select: the team draft is shown")
        me.click("#tab-champs")
        me.wait_for_selector("#me-owner", timeout=30000)
        time.sleep(1.5)
        me.select_option("#me-owner", "justkayla")
        time.sleep(2.0)
        me.wait_for_selector("#me-add-top", timeout=30000)
        me.fill("#me-add-top", "Malphite")
        me.press("#me-add-top", "Enter")
        time.sleep(2.0)
        self.shot(me, "overlay-my-champs", "My champs: This PC is Kayla, Malphite added to her Top pool")
        me.click("#tab-stats")
        time.sleep(3.0)
        self.shot(me, "overlay-stats", "Stats tab")
        me.click("#tab-draft")
        size = me.locator("#size")
        for _ in range(4):
            if "L" == size.inner_text().strip().split()[-1]:
                break
            size.click()
            time.sleep(0.3)
        me.click("#settings-open")
        time.sleep(0.8)
        self.shot(me, "overlay-settings", "Overlay settings: size L, Drafts Auto, auto-swap on")
        me.keyboard.press("Escape")
        time.sleep(0.5)

    def scene_champ_select(self, me, pages) -> None:
        print("scene 4: champion select opens", flush=True)
        # Kayla swaps by hand (one button); her four teammates leave Auto on and their apps answer.
        http("POST", self.app(0) + "/api/roster/settings", {"auto_swap": False})
        self.timeline.append({"note": "Kayla turns auto-swap off: she will use the one-click swap"})
        http("POST", self.lobby + "/control/start", {"format": "ranked", "assigned": [t[2] for t in TEAM], "enemy": ENEMY,
                                                    "planning": 25, "turn": 45, "finalization": 15, "enemy_delay": 4})
        main = me.locator(".ov-swap-main")
        wait_for(lambda: main.is_visible() and main.is_enabled(), 15, 0.4)
        time.sleep(1.0)
        self.shot(me, "cs-open", "Champion select opens: Kayla was given Mid and first pick; the team draft has her Top, last pick")
        if main.is_visible() and main.is_enabled():
            self.timeline.append({"note": f"Kayla clicks '{main.inner_text().strip()}'"})
            main.click()
        def kayla_in_place():
            st = self.lobby_state()
            seat = next((s for s in st.get("seats", []) if s["ours"] and s["name"].startswith("Adittive")), {})
            picks = [a["cell"] for g in st.get("groups", []) for a in g if a["type"] == "pick" and a["ours"]]
            return seat.get("position") == "top" and picks and picks[-1] == 5
        placed = wait_for(kayla_in_place, 20, 0.5)
        time.sleep(1.5)
        self.shot(me, "cs-swapped", "Swaps done: Kayla is Top and picks last" if placed else "Swaps still pending")
        self.timeline.append({"note": "swaps", "kayla_in_place": bool(placed), "lobby_swaps": self.lobby_state().get("swaps"),
                              "seats": [(s["name"], s["position"]) for s in self.lobby_state().get("seats", []) if s["ours"]]})
        # Wait for the ban phase.
        wait_for(lambda: (self.lobby_state().get("timer") or {}).get("phase") == "BAN_PICK", 40)
        print("scene 5: bans", flush=True)
        accept = me.locator("#accept")
        wait_for(lambda: accept.is_visible() and accept.inner_text().strip().startswith("Ban"), 20, 0.4)
        self.shot(me, "ban-turn", "Ban phase: the overlay offers one-click Ban")
        for i, page in enumerate(pages):
            self.click_accept(page, "Ban", i)
            time.sleep(1.5)          # people click seconds apart; each overlay sees the bans before it
        time.sleep(1.0)
        self.shot(me, "ban-done", "Our five bans locked with one click each")
        print("scene 6: picks", flush=True)
        seen_enemy: set[str] = set()
        clicked: set[int] = set()
        deadline = time.time() + 420
        while time.time() < deadline:
            st = self.lobby_state()
            if st.get("phase") != "ChampSelect" or (st.get("timer") or {}).get("phase") != "BAN_PICK":
                break
            enemy_now = {s["champion"] for s in st.get("seats", []) if not s["ours"] and s["champion"]}
            fresh = sorted(enemy_now - seen_enemy)
            if fresh:
                seen_enemy |= enemy_now
                time.sleep(2.0)
                self.shot(me, "enemy-" + "-".join(c.lower().replace("'", "") for c in fresh),
                          "Enemy locked " + ", ".join(fresh) + ": the overlay re-ranks")
            current = next((g for g in st.get("groups", []) if g and g[0].get("current")), [])
            for action in current:
                if action["ours"] and action["type"] == "pick" and not action["completed"] and action["id"] not in clicked:
                    seat = action["cell"] - 5
                    clicked.add(action["id"])
                    if seat == 0:
                        time.sleep(1.5)
                        self.shot(me, "kayla-turn", "Kayla's last pick: the suggestion after all five enemy picks")
                    self.click_accept(pages[seat], "Lock in", seat)
                    if seat == 0:
                        time.sleep(1.5)
                        self.shot(me, "kayla-locked", "Kayla locked in with one click")
            time.sleep(0.7)
        print("scene 7: finalisation and game start", flush=True)
        wait_for(lambda: (self.lobby_state().get("timer") or {}).get("phase") == "FINALIZATION", 30)
        time.sleep(2.0)
        self.shot(me, "finalization", "Draft complete: finalisation")
        http("POST", self.lobby + "/control/end", {})
        time.sleep(3.0)
        self.shot(me, "game-start", "Game starting: the overlay stands down")
        self.timeline.append({"note": "final lobby", "lobby": {k: self.lobby_state().get(k) for k in ("seats", "swaps", "events")}})

    def click_accept(self, page, verb: str, seat: int) -> None:
        button = page.locator("#accept")
        ok = wait_for(lambda: button.is_visible() and button.is_enabled() and button.inner_text().strip().startswith(verb), 15, 0.4)
        label = button.inner_text().strip() if button.count() else ""
        self.timeline.append({"note": f"{PLAYERS[seat]} clicks '{label}'" if ok else f"{PLAYERS[seat]}: no '{verb}' button appeared",
                              "seat": seat})
        if not ok:
            return
        cell = 5 + seat
        def done():
            return any(a["cell"] == cell and a["completed"] and a["type"] == ("ban" if verb == "Ban" else "pick")
                       for g in self.lobby_state().get("groups", []) for a in g)
        button.click()
        if not wait_for(done, 4, 0.4):
            # The client refused (a teammate took that ban a moment earlier): click again, as a player would,
            # once the overlay has caught up.
            time.sleep(2.5)
            label = button.inner_text().strip() if button.is_visible() else ""
            self.timeline.append({"note": f"{PLAYERS[seat]} clicks again: '{label}'", "seat": seat})
            if label.startswith(verb) and button.is_enabled():
                button.click()

    def save(self) -> None:
        (self.out / "timeline.json").write_text(json.dumps(self.timeline, indent=2, ensure_ascii=False), encoding="utf-8")
        videos = sorted((self.out / "video").glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if videos:
            shutil.copy2(videos[-1], self.out / "flex-draft-demo.webm")
        print("demo written to", self.out, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(REPO / "var" / "demo"))
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--keep", action="store_true", help="leave the apps running afterwards")
    parser.add_argument("--setup-only", action="store_true", help="start relay, lobby and five apps, run no scenes")
    parser.add_argument("--setup-only-seconds", type=float, default=900, help=argparse.SUPPRESS)
    args = parser.parse_args()
    demo = Demo(args)
    demo.prepare()
    try:
        demo.start()
        if args.setup_only:
            print("apps running", flush=True)
            time.sleep(args.setup_only_seconds)
            return
        demo.run()
    finally:
        demo.save()
        if not args.keep:
            demo.stop()


if __name__ == "__main__":
    main()
