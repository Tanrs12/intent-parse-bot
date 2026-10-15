"""Six-composition in-client overlay for INTent Draft Coach.

Runs beside the packaged Draft Coach and reads only its local HTTP endpoints:
- 8766 /api/guidance: six /league-game composition plans
- 8765 /api/state: live LCU bans/picks

The six plans are frozen when a champ-select starts.  Enemy picks or either team's bans mark the
relevant champion red and fade that plan; our own locks are marked as secured.  Player/role labels
come from the composition itself, not Riot's current seat/assigned-position labels, so pick trades
and deliberately adventurous role assignments remain readable.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request

GUIDANCE_URL = "http://127.0.0.1:8766/api/guidance"
STATE_URL = "http://127.0.0.1:8765/api/state"
CLIENT_CLASS = "RCLIENT"
KEY = "#010203"
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]
ROLE_SHORT = {"Top": "TOP", "Jungle": "JG", "Mid": "MID", "ADC": "ADC", "Support": "SUP"}


def _get(url: str, timeout: float = 2.0) -> dict:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def _signature(comp: dict) -> tuple:
    champions = comp.get("champions") or {}
    players = comp.get("players") or {}
    return tuple((role, champions.get(role), players.get(role)) for role in ROLES)


class CompOverlay:
    def __init__(self) -> None:
        self.u = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        # Do not stack duplicate overlays if the launcher is double-clicked twice.
        self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\INTentSixCompOverlay")
        if ctypes.windll.kernel32.GetLastError() == 183:
            raise SystemExit(0)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.configure(bg=KEY)
        self.root.geometry("1280x720+0+0")
        self.root.withdraw()
        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.client = 0
        self.hwnd = 0
        self.rect = (0, 0, 0, 0)
        self.snapshot: dict = {}
        self.bridge: dict = {}
        self.cards: list[dict] = []
        self.live_signatures: set[tuple] = set()
        self.in_draft = False
        self._stop = False
        self.font_title = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.font_row = tkfont.Font(family="Segoe UI", size=8)
        self.font_small = tkfont.Font(family="Consolas", size=7)
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.after(100, self._attach)
        self.root.after(100, self._tick)

    def _attach(self) -> None:
        self.hwnd = int(self.root.winfo_id())
        self.hwnd = self.u.GetAncestor(self.hwnd, 2) or self.hwnd
        style = self.u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        self.u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)

    def _client_rect(self):
        if not self.client or not self.u.IsWindow(self.client):
            self.client = self.u.FindWindowW(CLIENT_CLASS, None)
        if not self.client:
            return None
        r = wintypes.RECT()
        if not self.u.GetWindowRect(self.client, ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def _poll(self) -> None:
        while not self._stop:
            try:
                bridge = _get(GUIDANCE_URL)
            except Exception:
                bridge = {}
            try:
                snap = _get(STATE_URL)
            except Exception:
                snap = {}
            self.bridge = bridge
            self.snapshot = snap
            state = snap.get("state") or {}
            status = snap.get("status") or {}
            live = bool(
                bridge.get("active")
                and state.get("source") in ("lcu", "simulation")
                and state.get("phase") not in (None, "IDLE", "GAME_STARTING")
                and not status.get("stale")
            )
            comps = ((bridge.get("guidance") or {}).get("comps") or [])[:6]
            if live and not self.in_draft:
                self.cards = [dict(comp) for comp in comps]
                self.in_draft = True
            elif live and self.in_draft:
                # If the bridge needed a moment to compute on entry, fill missing initial cards.
                existing = {_signature(card) for card in self.cards}
                for comp in comps:
                    sig = _signature(comp)
                    if len(self.cards) >= 6:
                        break
                    if sig not in existing:
                        self.cards.append(dict(comp))
                        existing.add(sig)
                # Keep scores/counter edges current for any original plan that still survives.
                fresh = {_signature(comp): comp for comp in comps}
                for i, card in enumerate(self.cards):
                    if _signature(card) in fresh:
                        merged = dict(card)
                        merged.update(fresh[_signature(card)])
                        self.cards[i] = merged
            elif not live and self.in_draft:
                self.cards = []
                self.in_draft = False
            self.live_signatures = {_signature(comp) for comp in comps}
            time.sleep(0.35)

    def _status(self, comp: dict, state: dict) -> tuple[bool, dict[str, str]]:
        bans = set(state.get("our_bans") or []) | set(state.get("enemy_bans") or [])
        enemy = {row.get("locked") for row in (state.get("enemy_team") or []) if row.get("locked")}
        ours = {row.get("locked") for row in (state.get("our_team") or []) if row.get("locked")}
        marks: dict[str, str] = {}
        dead = False
        for role, champ in (comp.get("champions") or {}).items():
            if not champ:
                continue
            if champ in bans:
                marks[role] = "BAN"
                dead = True
            elif champ in enemy:
                marks[role] = "ENEMY"
                dead = True
            elif champ in ours:
                marks[role] = "LOCKED"
        return dead, marks

    @staticmethod
    def _fit(text: str, font: tkfont.Font, width: int) -> str:
        text = " ".join(str(text or "").split())
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…" if text else ""

    def _draw(self) -> None:
        state = self.snapshot.get("state") or {}
        w, h = self.rect[2], self.rect[3]
        if w <= 0 or h <= 0:
            return
        u = min(w / 1920.0, h / 1080.0)
        px = lambda n: max(1, round(n * u))
        self.font_title.configure(size=-px(13))
        self.font_row.configure(size=-px(11))
        self.font_small.configure(size=-px(9))
        c = self.canvas
        c.delete("all")

        x0 = round(w * 0.018)
        x1 = round(w * 0.205)
        top = round(h * 0.070)
        bottom = round(h * 0.790)
        gap = px(7)
        count = max(1, len(self.cards))
        card_h = max(px(72), (bottom - top - gap * (count - 1)) // count)
        live_top = None
        fresh = ((self.bridge.get("guidance") or {}).get("comps") or [])
        if fresh:
            live_top = _signature(fresh[0])

        for i, comp in enumerate(self.cards[:6]):
            y0 = top + i * (card_h + gap)
            y1 = y0 + card_h
            dead, marks = self._status(comp, state)
            sig = _signature(comp)
            is_best = sig == live_top and not dead
            surface = "#07131c" if not dead else "#11161b"
            border = "#c8aa6e" if is_best else ("#43515c" if not dead else "#552f34")
            ink = "#f0e6d2" if not dead else "#72777d"
            secondary = "#a09b90" if not dead else "#5b6065"
            c.create_rectangle(x0, y0, x1, y1, fill=surface, outline=border, width=max(1, px(1)))
            c.create_rectangle(x0, y0, x0 + px(3), y1, fill=("#0ac8b9" if is_best else border), outline="")
            score = comp.get("live_score", comp.get("score", 0.0))
            lane = float(comp.get("lane_adjustment", 0.0) or 0.0)
            head = f"COMP {i + 1}  {float(score):.1f}"
            if lane:
                head += f"  lane {lane:+.1f}"
            if dead:
                head += "  DEAD"
            elif is_best:
                head += "  BEST NOW"
            c.create_text(x0 + px(9), y0 + px(5), text=head, anchor="nw", font=self.font_title,
                          fill=("#0ac8b9" if is_best else ink))

            champions = comp.get("champions") or {}
            players = comp.get("players") or {}
            rows_top = y0 + px(25)
            row_h = max(px(13), (y1 - rows_top - px(4)) // 5)
            for ri, role in enumerate(ROLES):
                champ = champions.get(role) or "—"
                player = players.get(role) or role
                mark = marks.get(role)
                label = f"{ROLE_SHORT[role]:<3} {player} — {champ}"
                colour = ink
                suffix = ""
                if mark in ("BAN", "ENEMY"):
                    colour = "#ee6672"
                    suffix = "   X"
                elif mark == "LOCKED":
                    colour = "#c8aa6e"
                    suffix = "   ✓"
                c.create_text(x0 + px(9), rows_top + ri * row_h, text=self._fit(label + suffix, self.font_row, x1 - x0 - px(18)),
                              anchor="nw", font=self.font_row, fill=colour if mark else secondary)

    def _tick(self) -> None:
        rect = self._client_rect()
        foreground = self.u.GetForegroundWindow()
        show = bool(
            rect and self.in_draft and self.cards and not self.u.IsIconic(self.client)
            and foreground in (self.client, self.hwnd)
        )
        if show:
            if rect != self.rect:
                self.rect = rect
                x, y, w, h = rect
                self.root.geometry(f"{w}x{h}+{x}+{y}")
            if str(self.root.state()) == "withdrawn":
                self.root.deiconify()
                self.u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self._draw()
        else:
            if str(self.root.state()) != "withdrawn":
                self.root.withdraw()
        self.root.after(100, self._tick)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._stop = True


if __name__ == "__main__":
    CompOverlay().run()
