"""Outside-left six-composition overlay for INTent Draft Coach.

Shows the current six /league-game plans beside the League client.  Live plans re-rank as the
shared backend reacts to picks/bans; an invalidated plan remains red/grey for a few seconds so the
user can see what changed.  Champion portraits, role/player labels and full planned reveal-order
badges are presentation only: scoring still comes from league_draft_backend + Rat's bounded live
matchup adjustment in intent_live_draft_bridge.py.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import socket
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request

GUIDANCE_URL = "http://127.0.0.1:8766/api/guidance"
STATE_URL = "http://127.0.0.1:8765/api/state"
ICON_URL = "http://127.0.0.1:8765/api/champion-icon/{id}"
CLIENT_CLASS = "RCLIENT"
KEY = "#010203"
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]
ROLE_SHORT = {"Top": "T", "Jungle": "J", "Mid": "M", "ADC": "A", "Support": "S"}
SM_XVIRTUALSCREEN = 76
DEAD_HOLD_SECONDS = 20.0


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
        self._lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._lock_socket.bind(("127.0.0.1", 18767))
        except OSError:
            self._lock_socket.close()
            raise SystemExit(0)

        self.u = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\INTentSixCompOverlay")
        if ctypes.windll.kernel32.GetLastError() == 183:
            raise SystemExit(0)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.configure(bg=KEY)
        self.root.geometry("150x720+0+0")
        self.root.withdraw()
        self.canvas = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.client = 0
        self.hwnd = 0
        self.rect = (0, 0, 0, 0)
        self.snapshot: dict = {}
        self.bridge: dict = {}
        self.cards: list[dict] = []
        self.dead_archive: dict[tuple, dict] = {}
        self.in_draft = False
        self._stop = False

        self.font_title = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.font_role = tkfont.Font(family="Consolas", size=7, weight="bold")
        self.font_player = tkfont.Font(family="Segoe UI", size=7)
        self.font_tiny = tkfont.Font(family="Segoe UI", size=6)

        self.icon_sources: dict[int, tk.PhotoImage] = {}
        self.icon_scaled: dict[tuple[int, int], tk.PhotoImage] = {}
        self.icon_mailbox: dict[int, bytes | None] = {}
        self.icon_pending: set[int] = set()

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

    def _effective_marks(self) -> tuple[set[str], set[str], set[str]]:
        g = self.bridge.get("guidance") or {}
        state = self.snapshot.get("state") or {}
        bans = set(g.get("our_bans") or state.get("our_bans") or []) | set(g.get("enemy_bans") or state.get("enemy_bans") or [])
        enemy = set((g.get("enemy_locked") or {}).values())
        if not enemy:
            enemy = {row.get("locked") for row in (state.get("enemy_team") or []) if row.get("locked")}
        ours = set((g.get("our_locked") or {}).values())
        return bans, enemy, ours

    def _status(self, comp: dict) -> tuple[bool, dict[str, str]]:
        bans, enemy, ours = self._effective_marks()
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
                and state.get("source") in ("lcu", "simulation", "manual")
                and state.get("phase") not in (None, "IDLE", "GAME_STARTING")
                and not status.get("stale")
            )
            fresh = [dict(c) for c in ((bridge.get("guidance") or {}).get("comps") or [])[:6]]
            now = time.time()

            if live:
                if not self.in_draft:
                    self.dead_archive.clear()
                    self.in_draft = True
                fresh_sigs = {_signature(card) for card in fresh}
                for old_rank, card in enumerate(self.cards, start=1):
                    sig = _signature(card)
                    dead, _ = self._status(card)
                    if dead and sig not in fresh_sigs and sig not in self.dead_archive:
                        archived = dict(card)
                        archived["_was_rank"] = old_rank
                        archived["_dead_until"] = now + DEAD_HOLD_SECONDS
                        self.dead_archive[sig] = archived

                for sig, card in list(self.dead_archive.items()):
                    dead, _ = self._status(card)
                    if not dead or now >= float(card.get("_dead_until", 0)):
                        self.dead_archive.pop(sig, None)

                dead_cards = sorted(self.dead_archive.values(), key=lambda c: float(c.get("_dead_until", 0)), reverse=True)
                reserve = min(3, len(dead_cards))
                self.cards = fresh[: max(0, 6 - reserve)] + dead_cards[:reserve]
            else:
                self.cards = []
                self.dead_archive.clear()
                self.in_draft = False
            time.sleep(0.30)

    def _icon(self, champion_id: int | None, size: int) -> tk.PhotoImage | None:
        if not champion_id:
            return None
        cid = int(champion_id)
        key = (cid, size)
        if key in self.icon_scaled:
            return self.icon_scaled[key]

        if cid in self.icon_mailbox:
            payload = self.icon_mailbox.pop(cid)
            self.icon_pending.discard(cid)
            if payload:
                try:
                    source = tk.PhotoImage(master=self.root, data=base64.b64encode(payload))
                    self.icon_sources[cid] = source
                except Exception:
                    pass

        source = self.icon_sources.get(cid)
        if source is None:
            if cid not in self.icon_pending:
                self.icon_pending.add(cid)
                def download() -> None:
                    try:
                        with urllib.request.urlopen(ICON_URL.format(id=cid), timeout=5) as response:
                            self.icon_mailbox[cid] = response.read()
                    except Exception:
                        self.icon_mailbox[cid] = None
                threading.Thread(target=download, daemon=True).start()
            return None

        factor = max(1, min(source.width(), source.height()) // max(1, size))
        scaled = source.subsample(factor, factor)
        self.icon_scaled[key] = scaled
        return scaled

    @staticmethod
    def _fit(text: str, font: tkfont.Font, width: int) -> str:
        text = " ".join(str(text or "").split())
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…" if text else ""

    @staticmethod
    def _pick_numbers(comp: dict) -> dict[str, int]:
        result = {}
        for index, row in enumerate(comp.get("pick_order") or [], start=1):
            role = row.get("role")
            if role in ROLES and role not in result:
                result[role] = index
        return result

    def _draw(self) -> None:
        w, h = self.rect[2], self.rect[3]
        if w <= 0 or h <= 0:
            return
        u = max(0.75, min(1.2, h / 900.0))
        px = lambda n: max(1, round(n * u))
        self.font_title.configure(size=-px(10))
        self.font_role.configure(size=-px(8))
        self.font_player.configure(size=-px(8))
        self.font_tiny.configure(size=-px(7))
        c = self.canvas
        c.delete("all")

        gap = px(5)
        top = px(8)
        bottom = h - px(8)
        count = max(1, min(6, len(self.cards)))
        card_h = max(px(86), (bottom - top - gap * (count - 1)) // count)
        g = self.bridge.get("guidance") or {}
        live_sigs = [_signature(comp) for comp in (g.get("comps") or [])]
        current_best = live_sigs[0] if live_sigs else None

        for i, comp in enumerate(self.cards[:6]):
            y0 = top + i * (card_h + gap)
            y1 = min(bottom, y0 + card_h)
            dead, marks = self._status(comp)
            sig = _signature(comp)
            live_rank = (live_sigs.index(sig) + 1) if sig in live_sigs else None
            is_best = live_rank == 1 and not dead
            score = float(comp.get("live_score", comp.get("score", 0.0)) or 0.0)
            lane = float(comp.get("lane_adjustment", 0.0) or 0.0)

            surface = "#0a1219" if not dead else "#0b0f13"
            border = "#35434a" if not dead else "#512d32"
            ink = "#f0e6d2" if not dead else "#60666b"
            secondary = "#b2ada1" if not dead else "#4f555a"
            accent = "#ee7d87" if dead else ("#0ac8b9" if is_best else "#c8aa6e")

            c.create_rectangle(px(2), y0 + px(2), w - px(2), y1 + px(2), fill="#000000", outline="")
            c.create_rectangle(0, y0, w - px(4), y1, fill=surface, outline=border, width=1)
            c.create_rectangle(0, y0, px(2), y1, fill=accent, outline="")

            if dead:
                head = f"OUT · WAS #{int(comp.get('_was_rank', i + 1))} · {score:.1f}"
            else:
                head = f"#{live_rank or i + 1} {'BEST' if is_best else 'LIVE'} · {score:.1f}"
                if lane:
                    head += f" · {lane:+.1f}"
            c.create_text(px(7), y0 + px(4), text=self._fit(head, self.font_title, w - px(14)), anchor="nw", font=self.font_title, fill=accent)

            champions = comp.get("champions") or {}
            champion_ids = comp.get("champion_ids") or {}
            players = comp.get("players") or {}
            pick_nums = self._pick_numbers(comp)
            cell_w = max(px(23), (w - px(10)) // 5)
            icon_size = min(px(24), max(px(17), cell_w - px(5)))
            icons_y = y0 + px(24)
            labels_y = icons_y + icon_size + px(2)
            player_y = labels_y + px(11)
            locked = set((g.get("our_locked") or {}).values())
            next_role = next((row.get("role") for row in (comp.get("pick_order") or []) if row.get("champion") not in locked), None)

            for ri, role in enumerate(ROLES):
                cx = px(5) + ri * cell_w + cell_w // 2
                champ = champions.get(role) or "—"
                mark = marks.get(role)
                cid = champion_ids.get(role)
                img = self._icon(cid, icon_size)
                x0 = cx - icon_size // 2
                outline = "#0ac8b9" if role == next_role and not dead else ("#ee7d87" if mark in ("BAN", "ENEMY") else ("#c8aa6e" if mark == "LOCKED" else border))
                c.create_rectangle(x0 - 1, icons_y - 1, x0 + icon_size + 1, icons_y + icon_size + 1, fill="#101820", outline=outline, width=1)
                if img is not None:
                    c.create_image(cx, icons_y + icon_size // 2, image=img, anchor="center")
                else:
                    initials = "".join(part[0] for part in str(champ).replace("'", " ").split()[:2]).upper()[:2]
                    c.create_text(cx, icons_y + icon_size // 2, text=initials, font=self.font_role, fill=secondary)

                number = pick_nums.get(role)
                if number:
                    badge_r = px(7)
                    bx, by = x0 + icon_size - px(2), icons_y + px(2)
                    c.create_oval(bx - badge_r, by - badge_r, bx + badge_r, by + badge_r, fill="#14212b", outline=accent)
                    c.create_text(bx, by, text=str(number), font=self.font_tiny, fill=accent)

                if mark in ("BAN", "ENEMY"):
                    c.create_line(x0, icons_y, x0 + icon_size, icons_y + icon_size, fill="#ee7d87", width=px(2))
                    c.create_line(x0 + icon_size, icons_y, x0, icons_y + icon_size, fill="#ee7d87", width=px(2))

                role_colour = "#ee7d87" if mark in ("BAN", "ENEMY") else ("#c8aa6e" if mark == "LOCKED" else ink)
                c.create_text(cx, labels_y, text=ROLE_SHORT[role], anchor="n", font=self.font_role, fill=role_colour)
                player = str(players.get(role) or role)
                c.create_text(cx, player_y, text=self._fit(player, self.font_player, cell_w - px(2)), anchor="n", font=self.font_player, fill=secondary)

            # Tiny footer makes test events and counter-driven movement obvious without calling the score a win %.
            footer = "DRAFT SCORE"
            test = g.get("test_overrides") or {}
            if test.get("bans") or test.get("enemy") or test.get("ours"):
                footer += " · TEST EVENT"
            c.create_text(px(7), y1 - px(12), text=footer, anchor="nw", font=self.font_tiny, fill=secondary)

    def _tick(self) -> None:
        client_rect = self._client_rect()
        foreground = self.u.GetForegroundWindow()
        show = bool(
            client_rect and self.in_draft and self.cards and not self.u.IsIconic(self.client)
            and foreground in (self.client, self.hwnd)
        )
        if show:
            cx, cy, cw, ch = client_rect
            virtual_left = self.u.GetSystemMetrics(SM_XVIRTUALSCREEN)
            available = max(0, cx - virtual_left - 4)
            panel_w = min(310, available)
            if panel_w < 105:
                panel_w = min(130, max(90, cx - virtual_left))
            panel_x = max(virtual_left, cx - panel_w - 4)
            rect = (panel_x, cy, panel_w, ch)
            if rect != self.rect:
                self.rect = rect
                self.root.geometry(f"{panel_w}x{ch}+{panel_x}+{cy}")
            if str(self.root.state()) == "withdrawn":
                self.root.deiconify()
                self.u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self._draw()
        elif str(self.root.state()) != "withdrawn":
            self.root.withdraw()
        self.root.after(80, self._tick)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._stop = True
            try:
                self._lock_socket.close()
            except Exception:
                pass


if __name__ == "__main__":
    CompOverlay().run()
