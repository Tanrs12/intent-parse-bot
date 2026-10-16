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
ROLE_SHORT = {"Top": "TOP", "Jungle": "JGL", "Mid": "MID", "ADC": "ADC", "Support": "SUP"}
SM_XVIRTUALSCREEN = 76
DEAD_HOLD_SECONDS = 20.0
LIVE_GRACE_SECONDS = 4.0


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
        self._last_live_at = 0.0
        self._dirty = True
        self._render_key = ""
        self._stop = False

        self.font_title = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.font_order = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_role = tkfont.Font(family="Consolas", size=7, weight="bold")
        self.font_player = tkfont.Font(family="Segoe UI", size=7)
        self.font_tiny = tkfont.Font(family="Segoe UI", size=6)

        self.icon_sources: dict[int, tk.PhotoImage] = {}
        self.icon_scaled: dict[tuple[int, int], tk.PhotoImage] = {}
        self.icon_mailbox: dict[int, bytes | None] = {}
        self.icon_pending: set[int] = set()

        threading.Thread(target=self._poll, daemon=True).start()
        # Attach the native HWND before the first geometry tick. Previously both callbacks fired
        # at 100 ms, so _tick could cache the desired rect while hwnd was still 0. The later
        # attach then had nothing to move, leaving the panel at its 150px startup position (0,0).
        self.root.after(25, self._attach)
        self.root.after(100, self._tick)

    def _attach(self) -> None:
        self.hwnd = int(self.root.winfo_id())
        self.hwnd = self.u.GetAncestor(self.hwnd, 2) or self.hwnd
        style = self.u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        self.u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)
        # Force one real move/resize now that hwnd exists; do not trust geometry cached before attach.
        self.rect = (0, 0, 0, 0)
        self._dirty = True

    def _client_rect(self):
        # Riot can leave more than one RCLIENT top-level window around. Select the largest visible
        # non-minimised one instead of whichever FindWindow happens to return first.
        candidates: list[tuple[int, int, int, int, int, int]] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def enum_window(hwnd, _lparam):
            if not self.u.IsWindowVisible(hwnd) or self.u.IsIconic(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(128)
            self.u.GetClassNameW(hwnd, cls, len(cls))
            if cls.value != CLIENT_CLASS:
                return True
            r = wintypes.RECT()
            if self.u.GetWindowRect(hwnd, ctypes.byref(r)):
                width, height = r.right - r.left, r.bottom - r.top
                if width >= 500 and height >= 400:
                    candidates.append((width * height, int(hwnd), r.left, r.top, width, height))
            return True

        self.u.EnumWindows(enum_window, 0)
        if candidates:
            _, hwnd, left, top, width, height = max(candidates)
            self.client = hwnd
            return (left, top, width, height)
        self.client = 0
        return None

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
        champions = comp.get("champions") or {}
        role_options = comp.get("role_options") or {}
        uncontrolled = set(comp.get("uncontrolled_roles") or [])
        for role in ROLES:
            if role in uncontrolled:
                continue
            primary = champions.get(role)
            options = [champ for champ in (role_options.get(role) or [primary]) if champ]
            if not options:
                continue
            unavailable = [champ for champ in options if champ in bans or champ in enemy]
            if len(unavailable) == len(options):
                marks[role] = "OUT"
                dead = True
            elif primary in bans:
                marks[role] = "BAN"
            elif primary in enemy:
                marks[role] = "ENEMY"
            elif any(champ in ours for champ in options):
                marks[role] = "LOCKED"
        return dead, marks

    def _poll(self) -> None:
        while not self._stop:
            # Keep the last good payload during a slow backend recompute instead of flashing the
            # whole panel off. A pick/ban can make /api/guidance take a couple of seconds.
            try:
                bridge = _get(GUIDANCE_URL, timeout=8.0)
                if bridge:
                    self.bridge = bridge
            except Exception:
                bridge = self.bridge
            try:
                snap = _get(STATE_URL, timeout=3.0)
                if snap:
                    self.snapshot = snap
            except Exception:
                snap = self.snapshot

            bridge = self.bridge
            snap = self.snapshot
            state = snap.get("state") or {}
            status = snap.get("status") or {}
            now = time.time()
            # The panel is a champ-select accessory only. A stale/manual board must never keep it
            # pinned over the normal League client after we dodge, leave draft, or enter game.
            in_champ_select = bool(status.get("lcu_in_champ_select")) or state.get("source") == "simulation"
            live_now = bool(
                in_champ_select
                and bridge.get("active")
                and state.get("source") in ("lcu", "simulation")
                and state.get("phase") not in (None, "IDLE", "GAME_STARTING")
                and not status.get("stale")
            )
            if live_now:
                self._last_live_at = now
            # Grace is only for a backend recompute while LCU still says ChampSelect. The instant
            # League leaves ChampSelect we hide, even if the last six cards are still cached.
            live = live_now or (in_champ_select and self.in_draft and now - self._last_live_at <= LIVE_GRACE_SECONDS)
            fresh = [dict(c) for c in ((bridge.get("guidance") or {}).get("comps") or [])[:6]]

            if live:
                if not self.in_draft:
                    self.dead_archive.clear()
                    self.in_draft = True
                # Only replace the visible live list when a real fresh result exists. During a
                # recompute, retain the existing six cards rather than collapsing them to zero.
                if fresh:
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

            # Rendering the six cards is much more expensive than moving the transparent window.
            # Only redraw when draft data actually changed; window movement is handled separately.
            guidance = bridge.get("guidance") or {}
            render_key = json.dumps({
                "live": self.in_draft,
                "cards": self.cards,
                "our_bans": guidance.get("our_bans") or state.get("our_bans") or [],
                "enemy_bans": guidance.get("enemy_bans") or state.get("enemy_bans") or [],
                "our_locked": guidance.get("our_locked") or {},
                "enemy_locked": guidance.get("enemy_locked") or {},
                "test": guidance.get("test_overrides") or {},
            }, sort_keys=True, default=str)
            if render_key != self._render_key:
                self._render_key = render_key
                self._dirty = True
            time.sleep(0.45)

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
        u = max(0.85, min(1.3, h / 900.0))
        px = lambda n: max(1, round(n * u))
        self.font_title.configure(size=-px(13))
        self.font_order.configure(size=-px(17))
        self.font_role.configure(size=-px(10))
        self.font_player.configure(size=-px(9))
        self.font_tiny.configure(size=-px(8))
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

            side = str(g.get("side") or "blue").upper()
            pug_count = len(comp.get("uncontrolled_roles") or [])
            if dead:
                head = f"OUT · WAS #{int(comp.get('_was_rank', i + 1))} · {score:.1f} · {side}"
            else:
                head = f"#{live_rank or i + 1} {'BEST' if is_best else 'LIVE'} · {score:.1f} · {side}"
                if lane:
                    head += f" · {lane:+.1f}"
            if pug_count:
                head += f" · {pug_count} PUG" + ("S" if pug_count != 1 else "")
            c.create_text(px(7), y0 + px(4), text=self._fit(head, self.font_title, w - px(14)), anchor="nw", font=self.font_title, fill=accent)

            champions = comp.get("champions") or {}
            champion_ids = comp.get("champion_ids") or {}
            role_options = comp.get("role_options") or {}
            role_option_ids = comp.get("role_option_ids") or {}
            players = comp.get("players") or {}
            pick_nums = self._pick_numbers(comp)
            cell_w = max(px(48), (w - px(10)) // 5)
            icons_y = y0 + px(23)
            labels_y = icons_y + px(49)
            player_y = labels_y + px(12)
            locked = set((g.get("our_locked") or {}).values())
            bans, enemy_taken, ours_taken = self._effective_marks()
            uncontrolled = set(comp.get("uncontrolled_roles") or [])
            next_role = next((
                row.get("role") for row in (comp.get("pick_order") or [])
                if row.get("champion") not in locked and row.get("role") not in uncontrolled
            ), None)

            for ri, role in enumerate(ROLES):
                cx = px(5) + ri * cell_w + cell_w // 2
                primary = champions.get(role) or "—"
                if role in uncontrolled:
                    actual = (comp.get("live_uncontrolled_champions") or {}).get(role)
                    options = [actual] if actual else ["?"]
                    ids = [(comp.get("live_uncontrolled_champion_ids") or {}).get(role)]
                else:
                    options = [champ for champ in (role_options.get(role) or [primary]) if champ][:3]
                    ids = list(role_option_ids.get(role) or [])[:len(options)]
                    if not ids:
                        ids = [champion_ids.get(role)] + [None] * max(0, len(options) - 1)

                n = max(1, len(options))
                if n == 1:
                    icon_size = min(px(48), cell_w - px(6))
                    step = icon_size
                elif n == 2:
                    # Two alternatives remain proper portraits; a little overlap buys width without
                    # making either option unreadable.
                    icon_size = min(px(38), max(px(32), cell_w - px(16)))
                    step = max(px(24), icon_size - px(10))
                else:
                    # Three choices need to fit one role column. Keep the portraits ~30-32px and
                    # fan them tightly rather than shrinking them into the old ~18px thumbnails.
                    icon_size = min(px(32), max(px(30), cell_w - px(22)))
                    step = max(px(11), (cell_w - icon_size) // 2)
                total_w = icon_size + (n - 1) * step
                start_x = cx - total_w // 2

                for oi, champ in enumerate(options):
                    cid = ids[oi] if oi < len(ids) else None
                    ix = start_x + oi * step
                    option_mark = "BAN" if champ in bans else ("ENEMY" if champ in enemy_taken else ("LOCKED" if champ in ours_taken else None))
                    outline = border
                    if option_mark in ("BAN", "ENEMY"):
                        outline = "#ee7d87"
                    elif option_mark == "LOCKED":
                        outline = "#c8aa6e"
                    elif role == next_role and oi == 0 and not dead:
                        outline = "#0ac8b9"

                    c.create_rectangle(ix - 1, icons_y - 1, ix + icon_size + 1, icons_y + icon_size + 1, fill="#101820", outline=outline, width=1)
                    img = self._icon(cid, icon_size)
                    if img is not None:
                        c.create_image(ix + icon_size // 2, icons_y + icon_size // 2, image=img, anchor="center")
                    else:
                        initials = "".join(part[0] for part in str(champ).replace("'", " ").split()[:2]).upper()[:2]
                        c.create_text(ix + icon_size // 2, icons_y + icon_size // 2, text=initials, font=self.font_role, fill=secondary)

                    if option_mark in ("BAN", "ENEMY"):
                        c.create_line(ix, icons_y, ix + icon_size, icons_y + icon_size, fill="#ee7d87", width=px(2))
                        c.create_line(ix + icon_size, icons_y, ix, icons_y + icon_size, fill="#ee7d87", width=px(2))

                    # Pick order belongs to the role, so keep one badge on the primary option.
                    if oi == 0 and role not in uncontrolled and pick_nums.get(role):
                        badge_r = px(12)
                        bx, by = ix + icon_size - px(3), icons_y + px(3)
                        c.create_oval(bx - badge_r, by - badge_r, bx + badge_r, by + badge_r, fill="#14212b", outline=accent, width=max(1, px(2)))
                        c.create_text(bx, by, text=str(pick_nums[role]), font=self.font_order, fill=accent)

                role_colour = "#ee7d87" if marks.get(role) == "OUT" else ("#c8aa6e" if marks.get(role) == "LOCKED" else ink)
                c.create_text(cx, labels_y, text=ROLE_SHORT[role], anchor="n", font=self.font_role, fill=role_colour)
                player = str(players.get(role) or role)
                if role in uncontrolled:
                    player = "PUG · " + player
                c.create_text(cx, player_y, text=self._fit(player, self.font_player, cell_w - px(4)), anchor="n", font=self.font_player, fill=secondary)

            # Show only OUR side's ban plan for this exact composition. Portraits are much easier to
            # scan during draft than a tiny comma-separated text row.
            ban_rows = [row for row in list(comp.get("counter_bans") or [])[:5] if row.get("champion")]
            if ban_rows:
                ban_size = px(19)
                ban_gap = px(3)
                label_x = px(7)
                ban_y = y1 - ban_size - px(4)
                c.create_text(label_x, ban_y + ban_size // 2, text="BANS", anchor="w", font=self.font_tiny,
                              fill="#ee7d87" if not dead else secondary)
                x = label_x + px(34)
                for ban_index, row in enumerate(ban_rows, start=1):
                    cid = row.get("champion_id")
                    c.create_rectangle(x - 1, ban_y - 1, x + ban_size + 1, ban_y + ban_size + 1,
                                       fill="#101820", outline="#ee7d87" if not dead else border, width=1)
                    img = self._icon(cid, ban_size)
                    if img is not None:
                        c.create_image(x + ban_size // 2, ban_y + ban_size // 2, image=img, anchor="center")
                    else:
                        initials = "".join(part[0] for part in str(row.get("champion")).replace("'", " ").split()[:2]).upper()[:2]
                        c.create_text(x + ban_size // 2, ban_y + ban_size // 2, text=initials, font=self.font_tiny, fill=secondary)
                    c.create_text(x + px(2), ban_y + px(1), text=str(ban_index), anchor="nw", font=self.font_tiny,
                                  fill="#ffffff" if not dead else secondary)
                    x += ban_size + ban_gap

            test = g.get("test_overrides") or {}
            if test.get("bans") or test.get("enemy") or test.get("ours"):
                c.create_text(w - px(7), y1 - px(8), text="TEST", anchor="se", font=self.font_tiny, fill="#c8aa6e")

    def _tick(self) -> None:
        if not self.hwnd or not self.u.IsWindow(self.hwnd):
            self.root.after(33, self._tick)
            return
        client_rect = self._client_rect()
        # Stay attached to League while the user types/browses on another monitor. Visibility is
        # controlled by actual champ-select state, not foreground focus.
        show = bool(client_rect and self.in_draft and self.cards and not self.u.IsIconic(self.client))
        if show:
            cx, cy, cw, ch = client_rect
            virtual_left = self.u.GetSystemMetrics(SM_XVIRTUALSCREEN)
            available = max(0, cx - virtual_left - 4)
            # Five role columns need real horizontal room. On Cian's layout there is ample space
            # beside League, so use a 420px card strip rather than collapsing to the 150px fallback.
            panel_w = min(420, available)
            if panel_w < 300:
                panel_w = min(300, max(220, cx - virtual_left))
            panel_x = max(virtual_left, cx - panel_w - 4)
            rect = (panel_x, cy, panel_w, ch)
            if rect != self.rect:
                old_size = self.rect[2:]
                self.rect = rect
                # Tk owns the actual top-level wrapper HWND. Moving via root.geometry is reliable
                # across Tk/Windows builds; SetWindowPos on the cached ancestor could silently
                # target the wrong wrapper and leave the strip stuck at its startup (0,0).
                self.root.geometry(f"{panel_w}x{ch}+{panel_x}+{cy}")
                if old_size != (panel_w, ch):
                    self._dirty = True
            if str(self.root.state()) == "withdrawn":
                self.root.geometry(f"{panel_w}x{ch}+{panel_x}+{cy}")
                self.root.deiconify()
                self._dirty = True
            if self.icon_mailbox:
                self._dirty = True
            if self._dirty:
                self._draw()
                self._dirty = False
        elif str(self.root.state()) != "withdrawn":
            self.root.withdraw()
        # 30 Hz position following feels smooth while dragging, but redraws happen only on data/icon
        # changes, so idle champ select costs very little CPU.
        self.root.after(33, self._tick)

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
