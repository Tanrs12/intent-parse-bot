"""Always-on-top desktop overlay (tkinter, no extra dependencies).

A small dark window that floats over champion select and shows only what
matters: who is in the lobby and whether their pool is known, what to do now,
the top suggestions, the ban line and what the last enemy action changed.
Drag it anywhere by its top strip; right-click for draft type, opacity,
fearless series controls and the coach panel.

Run ``python run.py --window`` (server + window) or
``python -m draftcoach.window --url http://127.0.0.1:8765`` against a running server.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from tkinter import ttk

BG = "#0e1118"
BG2 = "#171c27"
TEXT = "#e9edf4"
MUTED = "#97a1b4"
GOLD = "#f2c14e"
GREEN = "#48d597"
AMBER = "#ffb547"
RED = "#ff4d5e"
BLUE = "#3b8bff"
ROLE_SHORT = {"Top": "TOP", "Jungle": "JG", "Mid": "MID", "ADC": "BOT", "Support": "SUP"}
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
FONT_H = ("Segoe UI", 13, "bold")
FONT_S = ("Segoe UI", 8)


class OverlayWindow:
    def __init__(self, url: str, poll_ms: int = 400, rows: int = 4, alpha: float = 0.93):
        self.url = url.rstrip("/")
        self.poll_ms = poll_ms
        self.rows = rows
        self.snapshot: dict | None = None
        self.error: str | None = None
        self._types_filled = False
        self._stop = False
        self._drag = (0, 0)

        self.root = tk.Tk()
        self.root.title("Draft overlay")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", alpha)
        self.root.configure(bg=BG)
        self.root.geometry("+40+120")
        self._build()
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.root.after(100, self._refresh)

    # ---- layout ------------------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 2}
        outer = tk.Frame(self.root, bg=BG, highlightbackground="#2a3140", highlightthickness=1)
        outer.pack(fill="both", expand=True)

        strip = tk.Frame(outer, bg=BG2, cursor="fleur")
        strip.pack(fill="x")
        self.status_var = tk.StringVar(value="connecting")
        self.dot = tk.Label(strip, text="●", fg=AMBER, bg=BG2, font=FONT)
        self.dot.pack(side="left", padx=(8, 2))
        tk.Label(strip, textvariable=self.status_var, fg=MUTED, bg=BG2, font=FONT_S).pack(side="left")
        tk.Button(strip, text="×", command=self.close, fg=MUTED, bg=BG2, bd=0, font=FONT, activebackground=BG2,
                  activeforeground=TEXT, cursor="arrow").pack(side="right", padx=4)
        self.type_var = tk.StringVar()
        self.type_box = ttk.Combobox(strip, textvariable=self.type_var, state="readonly", width=20, font=FONT_S)
        self.type_box.pack(side="right", padx=4, pady=3)
        self.type_box.bind("<<ComboboxSelected>>", self._on_type)
        for w in (strip, self.dot):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        strip.bind("<Button-3>", self._menu)
        outer.bind("<Button-3>", self._menu)

        self.lobby = tk.Label(outer, text="waiting for a lobby", fg=MUTED, bg=BG, font=FONT_S, justify="left", anchor="w", wraplength=360)
        self.lobby.pack(fill="x", **pad)
        self.headline = tk.Label(outer, text="Waiting for champion select", fg=TEXT, bg=BG, font=FONT_H, anchor="w", wraplength=360, justify="left")
        self.headline.pack(fill="x", padx=10, pady=(4, 2))
        self.change = tk.Label(outer, text="", fg=GOLD, bg="#2a2412", font=FONT_S, anchor="w", wraplength=360, justify="left", padx=6, pady=3)
        self.rows_frame = tk.Frame(outer, bg=BG)
        self.rows_frame.pack(fill="x", padx=10, pady=2)
        self.row_widgets: list[tuple[tk.Label, tk.Label, tk.Label, tk.Label]] = []
        for i in range(self.rows):
            n = tk.Label(self.rows_frame, text="", fg=MUTED, bg=BG, font=FONT_S, width=2, anchor="w")
            c = tk.Label(self.rows_frame, text="", fg=TEXT, bg=BG, font=FONT_B, anchor="w")
            t = tk.Label(self.rows_frame, text="", fg=MUTED, bg=BG, font=FONT_S, anchor="e")
            s = tk.Label(self.rows_frame, text="", fg=TEXT, bg=BG, font=FONT_B, width=4, anchor="e")
            n.grid(row=i, column=0, sticky="w")
            c.grid(row=i, column=1, sticky="w")
            t.grid(row=i, column=2, sticky="e", padx=(8, 4))
            s.grid(row=i, column=3, sticky="e")
            self.row_widgets.append((n, c, t, s))
        self.rows_frame.columnconfigure(1, weight=1)
        self.banline = tk.Label(outer, text="", fg=TEXT, bg=BG, font=FONT_S, anchor="w", wraplength=360, justify="left")
        self.banline.pack(fill="x", **pad)
        self.bans = tk.Label(outer, text="", fg=MUTED, bg=BG, font=FONT_S, anchor="w", wraplength=360, justify="left")
        self.bans.pack(fill="x", padx=10, pady=(2, 8))

    # ---- interaction ------------------------------------------------------------------
    def _drag_start(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg=BG2, fg=TEXT, activebackground=BLUE, activeforeground="white", font=FONT_S)
        snap = self.snapshot or {}
        for key, info in (snap.get("draft_types") or {}).items():
            menu.add_radiobutton(label=info["label"], value=key, variable=self.type_var, command=self._on_type)
        menu.add_separator()
        if snap.get("draft_type") == "fearless":
            menu.add_command(label=f"Fearless: record game & next ({len(snap.get('series') or [])} played)", command=lambda: self._post("/api/series/next"))
            menu.add_command(label="Fearless: clear series", command=lambda: self._post("/api/series/clear"))
            menu.add_separator()
        opacity = tk.Menu(menu, tearoff=0, bg=BG2, fg=TEXT, font=FONT_S)
        for a in (1.0, 0.93, 0.85, 0.7, 0.55):
            opacity.add_command(label=f"{int(a * 100)}%", command=lambda a=a: self.root.attributes("-alpha", a))
        menu.add_cascade(label="Opacity", menu=opacity)
        rows = tk.Menu(menu, tearoff=0, bg=BG2, fg=TEXT, font=FONT_S)
        for r in (3, 4, 5, 6):
            rows.add_command(label=f"{r} suggestions", command=lambda r=r: self._set_rows(r))
        menu.add_cascade(label="Rows", menu=rows)
        menu.add_command(label="Open coach panel (roster, loadfiles, board)", command=lambda: webbrowser.open(self.url + "/"))
        menu.add_separator()
        menu.add_command(label="Quit overlay", command=self.close)
        menu.tk_popup(event.x_root, event.y_root)

    def _set_rows(self, rows: int) -> None:
        self.rows = rows
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.row_widgets = []
        for i in range(self.rows):
            n = tk.Label(self.rows_frame, text="", fg=MUTED, bg=BG, font=FONT_S, width=2, anchor="w")
            c = tk.Label(self.rows_frame, text="", fg=TEXT, bg=BG, font=FONT_B, anchor="w")
            t = tk.Label(self.rows_frame, text="", fg=MUTED, bg=BG, font=FONT_S, anchor="e")
            s = tk.Label(self.rows_frame, text="", fg=TEXT, bg=BG, font=FONT_B, width=4, anchor="e")
            n.grid(row=i, column=0, sticky="w"); c.grid(row=i, column=1, sticky="w")
            t.grid(row=i, column=2, sticky="e", padx=(8, 4)); s.grid(row=i, column=3, sticky="e")
            self.row_widgets.append((n, c, t, s))

    def _on_type(self, *_):
        value = self.type_var.get()
        key = next((k for k, v in (self.snapshot or {}).get("draft_types", {}).items() if v["label"] == value or k == value), value)
        self._post("/api/draft/type", {"draft_type": key})

    def _post(self, path: str, body: dict | None = None) -> None:
        def run():
            try:
                data = json.dumps(body or {}).encode()
                req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
        threading.Thread(target=run, daemon=True).start()

    def close(self) -> None:
        self._stop = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---- data ----------------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while not self._stop:
            try:
                with urllib.request.urlopen(self.url + "/api/state", timeout=5) as response:
                    self.snapshot = json.loads(response.read())
                    self.error = None
            except Exception as exc:  # noqa: BLE001
                self.error = f"server unreachable ({exc.__class__.__name__})"
            time.sleep(self.poll_ms / 1000.0)

    def _refresh(self) -> None:
        if self._stop:
            return
        try:
            self._render()
        except tk.TclError:
            return
        self.root.after(self.poll_ms, self._refresh)

    def _render(self) -> None:
        snap = self.snapshot
        if self.error or snap is None:
            self.status_var.set(self.error or "connecting")
            self.dot.configure(fg=RED if self.error else AMBER)
            return
        status = snap.get("status") or {}
        conn = status.get("connection")
        self.dot.configure(fg=GREEN if conn in ("connected", "simulation") else RED if conn == "error" else AMBER)
        label = {"connected": "client connected", "simulation": "simulation", "manual": "manual board"}.get(conn, status.get("detail") or conn or "")
        self.status_var.set(label)

        types = snap.get("draft_types") or {}
        if types and not self._types_filled:
            self.type_box["values"] = [v["label"] for v in types.values()]
            self._types_filled = True
        current = types.get(snap.get("draft_type"), {}).get("label")
        if current and self.type_var.get() != current:
            self.type_var.set(current)

        lobby = snap.get("lobby") or []
        if lobby:
            parts = []
            for l in lobby:
                mark = {"pool": "✓", "partial": "△", "default": "○", "none": "✗"}.get(l["pool_status"], "?")
                parts.append(f"{mark} {l['name']}" + (f" ({ROLE_SHORT.get(l['role'], l['role'])})" if l.get("role") else ""))
            unknown = sum(1 for l in lobby if not l["matched"])
            text = "   ".join(parts)
            if unknown:
                text += f"\n{unknown} seat(s) not in the roster: link them in the coach panel"
            self.lobby.configure(text=text, fg=TEXT if not unknown else AMBER)
        else:
            self.lobby.configure(text="waiting for a lobby", fg=MUTED)

        g = snap.get("guidance")
        state = snap.get("state")
        kind = (g or {}).get("phase_kind", "idle")
        self.headline.configure(text=(g or {}).get("headline") or ("Scoring…" if state else "Waiting for champion select"),
                                fg={"pick": GOLD, "ban": RED, "complete": GREEN}.get(kind, TEXT))

        change = snap.get("last_change")
        if change and time.time() - float(change.get("at", 0)) < 25:
            self.change.configure(text=change["event"] + (" → " + change["effect"] if change.get("effect") else ""))
            if not self.change.winfo_ismapped():
                self.change.pack(fill="x", padx=10, pady=2, before=self.rows_frame)
        elif self.change.winfo_ismapped():
            self.change.pack_forget()

        rows: list[tuple[str, str, str, str, bool]] = []
        banline = ""
        if g and not g.get("draft_complete"):
            our_ban = state and state["active"]["kind"] == "ban" and state["active"]["ours"]
            if our_ban:
                for i, b in enumerate((g.get("bans") or [])[: self.rows]):
                    tag = (b.get("tags") or [None])[0] or ((b.get("reasons") or [""])[0])[:40]
                    rows.append((str(i + 1), b["champion"], tag, f"{b['score']:.1f}", i == 0))
                picks = g.get("picks") or []
                if picks:
                    banline = "Next pick: " + ", ".join(f"{p['champion']} ({ROLE_SHORT.get(p['role'], p['role'])})" for p in picks[:2])
            else:
                for i, p in enumerate((g.get("picks") or [])[: self.rows]):
                    rows.append((str(i + 1), f"{p['champion']}  {ROLE_SHORT.get(p['role'], p['role'])}", self._pick_tag(p), f"{p['score']:.0f}", i == 0))
                bans = g.get("bans") or []
                if bans:
                    banline = "Ban: " + " · ".join(b["champion"] + (f" ({b['tags'][0]})" if b.get("tags") else "") for b in bans[:3])
        elif g and g.get("draft_complete") and g.get("team"):
            team = g["team"]
            rows.append(("", team.get("team_plan") or "locked", (team.get("strengths") or [""])[0], f"{team['score']:.0f}", True))
        for i, (n, c, t, s) in enumerate(self.row_widgets):
            if i < len(rows):
                num, champ, tag, score, top = rows[i]
                n.configure(text=num); c.configure(text=champ, fg=GOLD if top else TEXT); t.configure(text=tag); s.configure(text=score)
            else:
                n.configure(text=""); c.configure(text=""); t.configure(text=""); s.configure(text="")
        self.banline.configure(text=banline)
        if state:
            text = "Bans  ours: " + (", ".join(state["our_bans"]) or "—") + "   theirs: " + (", ".join(state["enemy_bans"]) or "—")
            used = state.get("series_unavailable") or []
            if used:
                text += f"\nfearless: {len(used)} champions used earlier in the series"
            self.bans.configure(text=text)
        else:
            self.bans.configure(text="")

    @staticmethod
    def _pick_tag(p: dict) -> str:
        if p.get("priority") == 1:
            return "comfort"
        if p.get("lane"):
            e = p["lane"][0]
            return f"{e['advantage']:+.1f} vs {e['enemy']}"
        if p.get("counter_risk", 0) >= 1 and p.get("counter_risk_by"):
            return "counterable: " + ", ".join(r["enemy"] for r in p["counter_risk_by"][:2])
        if p.get("flex_roles"):
            return "flex " + "/".join(ROLE_SHORT.get(r, r) for r in p["flex_roles"])
        if p.get("blind_safe"):
            return "blind-safe"
        return p.get("team_plan") or ""

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Always-on-top draft overlay window")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--rows", type=int, default=4)
    parser.add_argument("--alpha", type=float, default=0.93)
    args = parser.parse_args(argv)
    OverlayWindow(args.url, rows=args.rows, alpha=args.alpha).run()


if __name__ == "__main__":
    main()
