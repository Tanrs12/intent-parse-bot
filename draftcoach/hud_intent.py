"""In-client HUD: two small cards drawn over the League client itself.

Why not a web view: WebView2 cannot be composited as a transparent desktop
layer (a layered window turns its content black, a colour key leaves the host
form's grey). So the native HUD is a tkinter window using Windows colour-key
transparency: everything painted in the key colour is see-through, and the
window is click-through (``WS_EX_TRANSPARENT``) except while the cursor is over
the Hover / Lock buttons. It draws the same two elements as ``static/hud.html``
(the browser demo), at the same positions, measured against the client window:

* a decision strip above LOCK IN (x 26%, y 79.5%, w 48%, h 5.5%), and
* an expiring notice below the enemy rows (x 77%, y 80%, w 21%).

The window follows the client (class ``RCLIENT``) at 30 Hz and hides whenever
the client is not the foreground window, is minimised, or is not in champion
select. Data comes from ``/api/state``; actions go through ``/api/client/*``.

    python run.py --hud                     # server + HUD
    python run.py --hud --simulate          # scripted draft drawn over the client (demo)
    python -m draftcoach.hud --url http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import platform
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.request

CLIENT_CLASS = "RCLIENT"
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
KEY = "#010203"
ROLE_LONG = {"Top": "Top", "Jungle": "Jungle", "Mid": "Mid", "ADC": "Bot", "Support": "Support"}

THEMES = {
    "porcelain": {"surface": "#f6f7f8", "border": "#c6cdd6", "ink": "#14171b", "secondary": "#505a66", "action": "#1749ee",
                  "action_ink": "#ffffff", "warn": "#855b16", "ban": "#9b3e48"},
    "graphite": {"surface": "#191d23", "border": "#4a5563", "ink": "#f2f5fa", "secondary": "#bdc6d2", "action": "#9cb6ff",
                 "action_ink": "#14171b", "warn": "#f0cf8b", "ban": "#f0a3a8"},
    # Sits inside the League client: its own near-black panels, gold hairlines and parchment text.
    "client": {"surface": "#0a1219", "raised": "#14212b", "border": "#35434a", "ink": "#f0e6d2", "secondary": "#b2ada1",
               "action": "#0ac8b9", "action_ink": "#04100f", "team": "#c8aa6e", "warn": "#e8b35a", "ban": "#ee7d87"},
}

# Where the card sits, as fractions of the client window. The client leaves these corners empty in
# every champ-select layout (team cards end around 78 %, chat and the quit button start around 90 %).
DECISION_BOX = (0.748, 0.744, 0.978, 0.897)
NOTICE_BOX = (0.022, 0.791, 0.238, 0.847)
VK_LBUTTON = 0x01


def short_pick_reason(p: dict) -> str:
    if p.get("source") == "exhausted":
        return "No prepared pick remains"

    risk = (p.get("counter_risk_by") or [{}])[0].get("enemy")
    if p.get("counter_risk", 0) >= 0.5 and risk:
        return f"Can still be answered by {risk}"

    edge = (p.get("lane") or [None])[0]
    if edge:
        value = edge.get("adjusted", edge.get("advantage"))
        if isinstance(value, (int, float)):
            label = (
                "Favourable" if value > 0.2
                else "Unfavourable" if value < -0.2
                else "Even"
            )
            state = "locked" if edge.get("locked") else "hovered"
            return f"{label} into {state} {edge['enemy']}"

    if p.get("familiarity") == "developing":
        return "Practice pick"
    if p.get("familiarity") == "comfort":
        return "Confirmed comfort"
    if p.get("priority") == 1:
        return "Preferred pick"
    if p.get("blind_safe"):
        return "Blind-safe option"
    if p.get("risk_known") is False:
        return "Blind safety unknown"
    if p.get("flex_roles"):
        return "Can flex to " + " / ".join(
            ROLE_LONG.get(r, r) for r in p["flex_roles"]
        )
    if p.get("source") in ("history", "history-fallback"):
        return "From account history"
    if p.get("source") == "meta":
        return "Role-meta fallback"
    return "Best available fit for this role"


def short_ban_reason(b: dict) -> str:
    tags = b.get("tags") or []
    if "hover" in tags:
        return "Enemy is hovering it"
    if "scouted" in tags:
        return "Scouted enemy champion"
    if "policy" in tags:
        return "Team ban policy"
    import re

    text = (b.get("reasons") or [""])[0].replace(
        "beats our", "strong into our"
    )
    text = re.sub(
        r"\s+by\s+[+-]?\d+(?:\.\d+)?\b.*$", "", text,
        flags=re.IGNORECASE,
    ).rstrip(".")
    return text[:1].upper() + text[1:]


def decide(snapshot: dict) -> tuple[dict | None, dict | None]:
    """(decision, notice) for a snapshot, following the HUD's state table. Pure, so it is testable."""
    s, g, local, st = snapshot.get("state"), snapshot.get("guidance"), snapshot.get("local"), snapshot.get("status") or {}
    live = s and s.get("source") in ("lcu", "simulation") and s.get("phase") not in ("IDLE", "GAME_STARTING")
    if not live or not g or st.get("stale"):
        return None, None
    picks, bans = g.get("picks") or [], g.get("bans") or []
    visual_test = bool(g.get("visual_test"))
    ours_locked = sum(1 for p in s["our_team"] if p.get("locked"))
    theirs_locked = sum(1 for p in s["enemy_team"] if p.get("locked"))
    active = s.get("active") or {}
    ours = active.get("kind") != "none" and active.get("ours")
    me = local.get("suggestion") if local and local.get("can_act") and local.get("suggestion") else None
    my_role = (local or {}).get("picking_role") or (local or {}).get("role")

    def role(r):
        return ROLE_LONG.get(r, r or "").upper()

    change = snapshot.get("last_change")
    notice = None
    if change and change.get("effect") and ours_locked < 5 and time.time() - float(change.get("at", 0)) < 12:
        notice = {"event": change["event"].split("; ")[-1], "effect": change["effect"], "at": float(change["at"])}
    decision = None
    preview = g.get("visual_test_pick") if visual_test else None
    if preview and ours_locked < 5:
        preview_actionable = bool(local and local.get("can_act"))
        # Custom/bot visual preview still follows the #1 INTent comp's next team pick. When this
        # League client is actually on the clock, render Rat's original actionable Hover/Lock row.
        decision = {
            "key": f"test-team:{preview.get('role')}:{preview.get('champion')}:{preview_actionable}",
            "turn": "local" if preview_actionable else "team", "kind": "pick",
            "title": str(preview.get("champion") or "No recommendation yet"),
            "reason": "Next planned team pick",
            "meta": "TEAM PICK ? " + role(preview.get("role")),
            "champion": preview.get("champion"),
            "champion_id": preview.get("champion_id"),
            "warning": False,
        }
    elif local and local.get("incoming_swap") and local["incoming_swap"].get("can_accept"):
        sw = local["incoming_swap"]
        decision = {"key": f"inswap:{sw['trade_id']}", "turn": "local", "kind": "swap",
                    "title": f"Give {local.get('locked') or 'your champion'} → receive {sw.get('champion') or 'theirs'}",
                    "reason": "" if sw.get("expected") else "Not part of the plan",
                    "meta": f"{sw['from_player']} requested · confirm in League".upper(), "champion": None, "warning": not sw.get("expected")}
    elif local and local.get("swap"):
        sw = local["swap"]
        sent = str(sw.get("trade_state") or "").upper() == "SENT"
        decision = {"key": f"swap:{sw['give']}", "turn": "local", "kind": "swap", "title": f"Give {sw['give']} → receive {sw.get('receive') or 'their pick'}",
                    "reason": "", "meta": (f"waiting for {sw['with_player']}" if sent else f"swap with {sw['with_player']} in League").upper(), "champion": None}
    elif ours_locked == 5:
        decision = None
    elif active.get("kind") == "ban" and ours:
        b = next((x for x in bans if me and x["champion"] == me["champion"]), None) or (bans[0] if bans else None)
        if b:
            decision = {"key": f"ban:{b['champion']}:{bool(me)}", "turn": "local" if me else "team", "kind": "ban",
                        "title": ("Ban " if me else "Suggest banning ") + b["champion"], "reason": short_ban_reason(b),
                        "meta": "YOUR BAN" if me else "TEAM BAN", "champion": b["champion"] if (me or visual_test) else None, "champion_id": b.get("champion_id")}
        else:
            decision = {"key": "ban:none", "turn": "local" if me else "team", "kind": "ban", "title": "No ban stands out",
                        "reason": "", "meta": "YOUR BAN" if me else "TEAM BAN", "champion": None}
    elif ours:
        top = next((p for p in picks if me and p["champion"] == me["champion"]), None) or (picks[0] if picks else None)
        if top:
            decision = {"key": f"pick:{top['champion']}:{bool(me)}", "turn": "local" if me else "team", "kind": "pick",
                        "title": ("Pick " if me else "Suggest ") + top["champion"], "reason": short_pick_reason(top),
                        "meta": ("YOUR PICK · " if me else "TEAM PICK · ") + role(top["role"])
                        + (f" · {top['plan']['name'].upper()} {top['plan']['progress']}/5" if top.get("plan") else ""),
                        "champion": top["champion"] if (me or visual_test) else None, "champion_id": top.get("champion_id"), "warning": top.get("counter_risk", 0) >= 0.5}
        else:
            decision = {"key": "pick:none", "turn": "local" if me else "team", "kind": "pick", "title": "No recommendation yet",
                        "reason": "", "meta": "YOUR PICK" if me else "TEAM PICK", "champion": None}
    else:
        plan = next((p for p in picks if my_role and p["role"] == my_role), None) or (picks[0] if picks else None)
        if plan:
            head = "ENEMY BAN" if active.get("kind") == "ban" else "ENEMY PICK" if active.get("kind") == "pick" else "PLANNING"
            meta = head + (f" · YOUR {role(my_role)} PLAN" if my_role else f" · {role(plan['role'])}")
            decision = {"key": f"prep:{plan['champion']}", "turn": "", "kind": "pick",
                        "title": ("Consider " if active.get("kind") == "none" else "Prepare ") + plan["champion"],
                        "reason": short_pick_reason(plan), "meta": meta, "champion": plan["champion"] if visual_test else None, "champion_id": plan.get("champion_id")}
    return decision, notice


class NativeHud:
    """Tkinter colour-key overlay that mirrors the League client window."""

    def __init__(self, url: str, theme: str = "porcelain", always: bool = False):
        self.url = url.rstrip("/")
        self.theme = THEMES.get(theme, THEMES["porcelain"])
        self.always = always
        self.u = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.configure(bg=KEY)
        self.root.geometry("1280x720+0+0")
        self.root.withdraw()
        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.hwnd = 0
        self.client = 0
        self.rect = (0, 0, 0, 0)
        self.shown = False
        self.snapshot: dict | None = None
        self.decision_key: tuple | None = None
        self._render_key: str | None = None
        self.hotspots: list[tuple[int, int, int, int, str, str]] = []   # x, y, w, h, action, champion
        self.buttons: list[tk.Button] = []
        self.feedback: tuple[str, float] | None = None                     # last button result, shown for a few seconds
        self._mouse_was_down = False
        self.images: dict[tuple[int, int], tk.PhotoImage] = {}
        self._stop = False
        self._acting = False
        self.fonts = {
            "title": tkfont.Font(family="Segoe UI", size=11, weight="bold"),
            "reason": tkfont.Font(family="Segoe UI", size=9),
            "meta": tkfont.Font(family="Consolas", size=7),
            "button": tkfont.Font(family="Segoe UI", size=9, weight="bold"),
        }
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.after(200, self._attach)
        self.root.after(300, self._tick)

    # ---- win32 ---------------------------------------------------------------------
    def _attach(self) -> None:
        self.hwnd = int(self.root.winfo_id())
        self.hwnd = self.u.GetAncestor(self.hwnd, 2) or self.hwnd
        style = self.u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        self.u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)

    def _set_clickthrough(self, on: bool) -> None:
        if not self.hwnd:
            return
        style = self.u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        new = (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
        if new != style:
            self.u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, new)

    def _client_rect(self) -> tuple[int, int, int, int] | None:
        if not self.client or not self.u.IsWindow(self.client):
            self.client = self.u.FindWindowW(CLIENT_CLASS, None)
        if not self.client:
            return None
        r = wintypes.RECT()
        if not self.u.GetWindowRect(self.client, ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def _hotspot_under_cursor(self) -> tuple | None:
        if not self.hotspots:
            return None
        pt = wintypes.POINT()
        self.u.GetCursorPos(ctypes.byref(pt))
        x, y = pt.x - self.rect[0], pt.y - self.rect[1]
        return next((h for h in self.hotspots if h[0] <= x <= h[0] + h[2] and h[1] <= y <= h[1] + h[3]), None)

    def _cursor_in_hotspot(self) -> bool:
        return self._hotspot_under_cursor() is not None

    def _check_click(self) -> None:
        """Fire a button from the raw mouse state. The window never activates (WS_EX_NOACTIVATE), so
        this does not rely on tkinter receiving a focused click."""
        down = bool(self.u.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        pressed_edge = down and not self._mouse_was_down
        self._mouse_was_down = down
        if not pressed_edge:
            return
        hit = self._hotspot_under_cursor()
        if hit is not None:
            self._act(hit[4], hit[5])

    # ---- data ----------------------------------------------------------------------------
    def _poll(self) -> None:
        while not self._stop:
            try:
                with urllib.request.urlopen(self.url + "/api/state", timeout=5) as response:
                    self.snapshot = json.loads(response.read())
            except Exception:  # noqa: BLE001
                self.snapshot = None
            time.sleep(0.4)

    def _act(self, what: str, champion: str) -> None:
        if self._acting:
            return
        self._acting = True
        verb = {"hover": "Hovering", "lock": "Locking"}.get(what, what.title())
        self.feedback = (f"{verb} {champion}...", time.time())
        print(f"hud: {what} {champion}")

        def run() -> None:
            try:
                req = urllib.request.Request(self.url + f"/api/client/{what}", data=json.dumps({"champion": champion}).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=10).read()
                self.feedback = (f"{champion} {'locked in' if what == 'lock' else 'hovered'}", time.time())
                print(f"hud: {what} {champion} ok")
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read()).get("detail") or exc.reason
                except Exception:  # noqa: BLE001
                    detail = exc.reason
                self.feedback = (f"Client refused: {detail}", time.time())
                print(f"hud: {what} {champion} failed: {detail}")
            except Exception as exc:  # noqa: BLE001
                self.feedback = (f"Failed: {exc}", time.time())
                print(f"hud: {what} {champion} failed: {exc}")
            finally:
                self._acting = False
        threading.Thread(target=run, daemon=True).start()

    def _portrait(self, champion_id: int | None, size: int) -> tk.PhotoImage | None:
        if not champion_id:
            return None

        cid = int(champion_id)
        size = max(1, int(size))

        if not hasattr(self, "_hud_icon_sources"):
            self._hud_icon_sources = {}
            self._hud_icon_downloads = {}
            self._hud_icon_pending = set()
            self._hud_icon_retry = {}

        cache_key = (cid, size)
        if cache_key in self.images:
            return self.images[cache_key]

        # Only the UI thread touches PhotoImage or the pending/retry collections.
        if cid in self._hud_icon_downloads:
            payload = self._hud_icon_downloads.pop(cid)
            self._hud_icon_pending.discard(cid)

            try:
                if payload is None:
                    raise ValueError("portrait unavailable")
                source = tk.PhotoImage(master=self.root, data=payload)
                if source.width() < 1 or source.height() < 1:
                    raise ValueError("empty portrait")
                self._hud_icon_sources[cid] = source
            except Exception:
                self._hud_icon_retry[cid] = time.time() + 10

        source = self._hud_icon_sources.get(cid)

        if source is None:
            if (
                cid not in self._hud_icon_pending
                and time.time() >= self._hud_icon_retry.get(cid, 0)
            ):
                self._hud_icon_pending.add(cid)

                def download() -> None:
                    try:
                        with urllib.request.urlopen(
                            self.url + f"/api/champion-icon/{cid}", timeout=5
                        ) as response:
                            payload = response.read()
                    except Exception:
                        payload = None

                    # A single mailbox assignment; no Tk calls from this thread.
                    self._hud_icon_downloads[cid] = payload

                threading.Thread(target=download, daemon=True).start()

            return None

        # Exact square, nearest-neighbour resize without a large zoom buffer.
        sw, sh = source.width(), source.height()
        rows = []

        for y in range(size):
            sy = min(sh - 1, int((y + 0.5) * sh / size))
            colours = []
            for x in range(size):
                sx = min(sw - 1, int((x + 0.5) * sw / size))
                red, green, blue = source.get(sx, sy)
                colours.append(f"#{red:02x}{green:02x}{blue:02x}")
            rows.append("{" + " ".join(colours) + "}")

        image = tk.PhotoImage(master=self.root, width=size, height=size)
        image.put(" ".join(rows))
        self.images[cache_key] = image
        return image

    # ---- loop ----------------------------------------------------------------------------
    def _tick(self) -> None:
        if self._stop:
            return
        rect = self._client_rect()
        foreground = self.u.GetForegroundWindow()
        up = bool(rect) and not self.u.IsIconic(self.client) and (self.always or foreground in (self.client, self.hwnd))
        decision, notice = decide(self.snapshot or {}) if up else (None, None)
        state = (self.snapshot or {}).get("state") or {}
        status = (self.snapshot or {}).get("status") or {}
        feedback_live = bool(
            self.feedback
            and (self._acting or time.time() - self.feedback[1] < 5)
            and state.get("source") in ("lcu", "simulation")
            and state.get("phase") not in ("IDLE", "GAME_STARTING")
            and not status.get("stale")
        )

        if up and (decision or notice or feedback_live):
            size_changed = False
            if rect != self.rect:
                old = self.rect
                self.rect = rect
                x, y, w, h = rect
                size_changed = old[2:] != (w, h)
                # Move the Tk top-level itself. SetWindowPos on Tk's wrapper/ancestor can be a
                # no-op on some Windows/Tk builds, which leaves TEAM PICK at League's old position.
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                if size_changed:
                    self.decision_key = None
            if not self.shown:
                x, y, w, h = rect
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                self.root.deiconify()
                self.shown = True
                size_changed = True

            feedback_text = self.feedback[0] if feedback_live and self.feedback else None
            render_key = json.dumps(
                {"decision": decision, "notice": notice, "feedback": feedback_text, "acting": self._acting,
                 "size": rect[2:]}, sort_keys=True, default=str,
            )
            icons_ready = bool(getattr(self, "_hud_icon_downloads", {}))
            if size_changed or icons_ready or render_key != self._render_key:
                self._draw(decision, notice)
                self._render_key = render_key
            self._set_clickthrough(not self._cursor_in_hotspot())
            self._check_click()
        elif self.shown:
            self.root.withdraw()
            self.shown = False
            self.hotspots.clear()
            self.decision_key = None
            self._render_key = None
            self._set_clickthrough(True)
        self.root.after(33, self._tick)

    def _draw(self, decision: dict | None, notice: dict | None) -> None:
        now = time.time()
        feedback = (
            self.feedback
            if self.feedback and (
                self._acting or now - self.feedback[1] < 5
            )
            else None
        )

        feedback_error = bool(
            feedback and feedback[0].startswith(("Client refused:", "Failed:"))
        )
        feedback_state = (
            "error" if feedback_error
            else "pending" if self._acting
            else "success"
        )

        # Retain acknowledgement if locking removed the recommendation.
        if feedback and decision is None:
            notice = {
                "event": "CLIENT ACTION",
                "effect": feedback[0],
                "at": feedback[1],
                "feedback_state": feedback_state,
            }

        w, h = self.rect[2], self.rect[3]
        if w <= 0 or h <= 0:
            return

        u = min(w / 1280, h / 720)

        def px(value: float) -> int:
            return max(1, round(value * u))

        # Resolve before the redraw key: a downloaded portrait must trigger paint.
        portrait_size = max(1, px(32) - 2)
        img = (
            self._portrait(decision.get("champion_id"), portrait_size)
            if decision and decision.get("kind") != "swap"
            else None
        )

        hit = self._hotspot_under_cursor()
        hovered = (hit[4], hit[5]) if hit else None

        # Reasons, warnings and plan progress can change without a new champion.
        key = (
            json.dumps(decision, sort_keys=True, ensure_ascii=False),
            json.dumps(notice, sort_keys=True, ensure_ascii=False),
            feedback,
            self._acting,
            w,
            h,
            id(img) if img is not None else None,
            hovered,
        )
        if key == self.decision_key:
            return
        self.decision_key = key

        c, t = self.canvas, self.theme
        raised = t.get("raised", t["surface"])
        team_colour = t.get("team", t["action"])

        # Keep Rat's compact HUD proportions. The INTent data can be wordier than Rat's original
        # advisor output, so smaller type preserves the clean in-client feel instead of looking like
        # a floating settings panel.
        self.fonts["title"].configure(
            family="Segoe UI", size=-px(13), weight="bold"
        )
        self.fonts["reason"].configure(
            family="Segoe UI", size=-px(9), weight="normal"
        )
        self.fonts["meta"].configure(
            family="Consolas", size=-px(7), weight="normal"
        )
        self.fonts["button"].configure(
            family="Segoe UI", size=-px(11), weight="bold"
        )

        def fit(value: str, font: tkfont.Font, width: int) -> str:
            text = " ".join(str(value or "").split())
            if font.measure(text) <= width:
                return text
            while text and font.measure(text + "…") > width:
                text = text[:-1]
            return text.rstrip() + "…" if text else ""

        def wrap(
            value: str, font: tkfont.Font, width: int, count: int
        ) -> str:
            words = str(value or "").split()
            lines = []

            for index in range(count):
                if not words:
                    break
                line = words.pop(0)
                while words and font.measure(line + " " + words[0]) <= width:
                    line += " " + words.pop(0)

                if index == count - 1 and words:
                    line += " " + " ".join(words)
                    words.clear()

                lines.append(fit(line, font, width))

            return "\n".join(lines)

        def rectangle(box: tuple) -> tuple[int, int, int, int]:
            return (
                round(w * box[0]), round(h * box[1]),
                round(w * box[2]), round(h * box[3]),
            )

        def surface(bounds: tuple[int, int, int, int]) -> None:
            x0, y0, x1, y1 = bounds
            shadow = px(2)
            c.create_rectangle(
                x0 + shadow, y0 + shadow,
                x1 + shadow, y1 + shadow,
                fill="#000000", outline="",
            )
            c.create_rectangle(
                x0, y0, x1, y1,
                fill=t["surface"], outline=t["border"], width=1,
            )

        def line(
            x: int, y: int, value: str, font: tkfont.Font,
            colour: str, width: int,
        ) -> None:
            c.create_text(
                x, y, text=fit(value, font, width),
                font=font, fill=colour, anchor="nw",
            )

        c.delete("all")
        self.hotspots = []

        # Reuse widgets; destroying buttons on a feedback repaint causes flicker.
        for button in self.buttons:
            button.place_forget()

        if decision:
            # Rat's full card is useful when there is an actual champion/action. When there is no
            # recommendation, don't render an empty portrait and a giant blank panel: collapse to a
            # small status chip in the same corner.
            compact_empty = bool(
                not decision.get("champion")
                and not (decision.get("reason") or "").strip()
                and str(decision.get("title") or "").startswith("No ")
            )
            bounds = rectangle((0.842, 0.804, 0.978, 0.866) if compact_empty else DECISION_BOX)
            x0, y0, x1, y1 = bounds
            surface(bounds)

            kind = decision.get("kind")
            turn = decision.get("turn")
            swap = kind == "swap"
            champion = decision.get("champion")
            can_act = bool(turn == "local" and not swap and champion)

            accent = (
                t["ban"] if kind == "ban"
                else t["action"] if turn == "local"
                else team_colour if turn == "team"
                else t["secondary"]
            )

            c.create_rectangle(
                x0, y0, x0 + px(2), y1,
                fill=accent, outline="",
            )

            full_x = x0 + px(8)
            full_width = x1 - px(8) - full_x

            line(
                full_x, y0 + px(4),
                (decision.get("meta") or "").upper(),
                self.fonts["meta"], accent, full_width,
            )

            if compact_empty:
                # No portrait/reason row. Two restrained lines are enough for an empty state.
                reason_x = full_x
                reason_width = full_width
                reason_y = y0 + px(31)
                line(
                    full_x, y0 + px(17), decision["title"],
                    self.fonts["title"], t["ink"], full_width,
                )
            elif swap:
                # Separate give/receive lines preserve the complete exchange.
                title_lines = str(decision["title"]).replace(
                    " → receive ", "\nReceive "
                ).splitlines()

                for index, value in enumerate(title_lines[:2]):
                    line(
                        full_x, y0 + px(16 + index * 21),
                        value, self.fonts["title"], t["ink"], full_width,
                    )

                reason_x = full_x
                reason_y = y1 - px(16)
                reason_width = full_width
            else:
                # Keep the TEAM PICK / TEAM BAN meta header clear. Actionable cards also need
                # room for Rat's original Hover / Lock-in button row at the bottom.
                content_y = 25 if can_act else 27
                portrait_x, portrait_y = full_x, y0 + px(content_y)
                portrait_side = px(32)

                c.create_rectangle(
                    portrait_x, portrait_y,
                    portrait_x + portrait_side, portrait_y + portrait_side,
                    fill=raised, outline=t["border"], width=1,
                )
                if img is not None:
                    c.create_image(
                        portrait_x + 1, portrait_y + 1,
                        image=img, anchor="nw",
                    )
                else:
                    name = champion or str(decision["title"]).split(" ", 1)[-1]
                    name = name.removeprefix("banning ")
                    initials = "".join(
                        word[0] for word in name.replace("'", " ").split()[:2]
                    ).upper()
                    c.create_text(
                        portrait_x + portrait_side / 2,
                        portrait_y + portrait_side / 2,
                        text=initials,
                        font=self.fonts["button"],
                        fill=t["secondary"],
                    )

                reason_x = x0 + px(48)
                reason_width = x1 - px(8) - reason_x
                reason_y = y0 + px(50 if can_act else 50)

                line(
                    reason_x, y0 + px(27 if can_act else 28), decision["title"],
                    self.fonts["title"], t["ink"], reason_width,
                )

            reason = feedback[0] if feedback else decision.get("reason") or ""
            reason_colour = (
                t["ban"] if feedback_error
                else t["action"] if feedback
                else t["warn"] if decision.get("warning")
                else t["secondary"]
            )

            if reason and not compact_empty:
                if can_act or swap:
                    line(
                        reason_x, reason_y, reason,
                        self.fonts["reason"], reason_colour, reason_width,
                    )
                else:
                    c.create_text(
                        reason_x, reason_y,
                        text=wrap(reason, self.fonts["reason"], reason_width, 2),
                        font=self.fonts["reason"], fill=reason_colour,
                        anchor="nw",
                    )

            if can_act:
                left = x0 + px(48)
                right = x1 - px(8)
                gap = px(6)
                bh = px(24)
                by = y1 - px(7) - bh
                first_width = (right - left - gap) // 2

                definitions = [
                    (
                        "Select" if kind == "ban" else "Hover",
                        "hover", False, left, first_width,
                    ),
                    (
                        "Lock ban" if kind == "ban" else "Lock in",
                        "lock", True,
                        left + first_width + gap,
                        right - left - first_width - gap,
                    ),
                ]

                while len(self.buttons) < 2:
                    self.buttons.append(tk.Button(
                        self.root,
                        bd=0,
                        relief="flat",
                        overrelief="flat",
                        takefocus=0,
                        padx=0,
                        pady=0,
                        highlightthickness=1,
                        # Mouse actions are dispatched only by _check_click().
                    ))

                for index, (label, what, primary, bx, bw) in enumerate(definitions):
                    button = self.buttons[index]
                    is_hovered = hovered == (what, champion)

                    bg = accent if primary else raised
                    fg = t["action_ink"] if primary else t["ink"]
                    border = accent

                    if is_hovered and not self._acting:
                        bg = t["ink"] if primary else t["border"]
                        border = t["ink"] if primary else accent

                    if self._acting:
                        bg = raised
                        fg = t["secondary"]
                        border = t["border"]
                        pending_hover = bool(
                            feedback and feedback[0].startswith(
                                ("Hovering", "Selecting")
                            )
                        )
                        if pending_hover and what == "hover":
                            label = "Selecting…" if kind == "ban" else "Hovering…"
                        elif not pending_hover and what == "lock":
                            label = "Banning…" if kind == "ban" else "Locking…"

                    button.configure(
                        text=label,
                        font=self.fonts["button"],
                        bg=bg,
                        fg=fg,
                        activebackground=t["ink"] if primary else t["border"],
                        activeforeground=t["action_ink"] if primary else t["ink"],
                        disabledforeground=t["secondary"],
                        highlightbackground=border,
                        highlightcolor=border,
                        state="disabled" if self._acting else "normal",
                        cursor="arrow" if self._acting else "hand2",
                    )
                    button.place(x=bx, y=by, width=bw, height=bh)

                    # Keep pending buttons input-opaque; _act() rejects duplicates.
                    self.hotspots.append(
                        (bx, by, bw, bh, what, champion)
                    )

        if notice:
            bounds = rectangle(NOTICE_BOX)
            x0, y0, x1, y1 = bounds
            surface(bounds)

            tx = x0 + px(8)
            tw = x1 - px(8) - tx
            state = notice.get("feedback_state")
            effect_colour = (
                t["ban"] if state == "error"
                else t["action"] if state in ("pending", "success")
                else t["ink"]
            )

            line(
                tx, y0 + px(7), notice.get("event") or "",
                self.fonts["reason"], t["secondary"], tw,
            )
            line(
                tx, y0 + px(22), notice.get("effect") or "",
                self.fonts["reason"], effect_colour, tw,
            )

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._stop = True


def wait_for_server(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/config", timeout=2) as response:
                json.loads(response.read())
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def run(url: str, theme: str | None = None) -> None:
    """INTent copy of Rat's HUD presentation.

    Rat's original ``draftcoach/hud.py`` stays untouched. This copy is fed by the
    INTent proxy and remains visible while League champ select is open even when
    focus moves to another monitor.
    """
    if platform.system() != "Windows":
        print("the in-client HUD is Windows-only; opening the floating panel instead")
        from . import window as panel

        panel.run(url, theme=theme)
        return
    wait_for_server(url)
    NativeHud(url, theme or "client", always=True).run()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="In-client draft HUD")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--theme", choices=["client", "porcelain", "graphite"])
    args = parser.parse_args(argv)
    run(args.url, theme=args.theme)


if __name__ == "__main__":
    main()
