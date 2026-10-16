"""Tiny local proxy that feeds Rat's native HUD the shared INTent recommendations.

The packaged Draft Coach remains responsible for LCU state and client actions on :8765.  The
shared backend lives on :8766.  This proxy merges them on :8767 so the familiar bottom-right
Hover / Lock-in card cannot disagree with the six composition cards.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COACH = "http://127.0.0.1:8765"
BRIDGE = "http://127.0.0.1:8766"


def _json(url: str, timeout: float = 12.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def merged_state() -> dict:
    snap = _json(COACH + "/api/state")
    try:
        bridge = _json(BRIDGE + "/api/guidance")
    except Exception:
        bridge = {}
    if not bridge.get("active") or not bridge.get("guidance"):
        # Never fall back to the packaged advisor: if INTent is gated off (ARAM, non-5-man PvP,
        # waiting for champ select) the action HUD must be gated off too.
        snap["guidance"] = None
        local = dict(snap.get("local") or {})
        local["can_act"] = False
        local["suggestion"] = None
        snap["local"] = local
        return snap

    guidance = bridge["guidance"]
    snap["guidance"] = guidance
    local = dict(snap.get("local") or {})
    active = (snap.get("state") or {}).get("active") or {}
    ours = active.get("kind") != "none" and active.get("ours")
    can_act = bool(local.get("can_act"))
    suggestion = None

    local_role = local.get("picking_role") or local.get("role")
    local_hover = local.get("hover")
    confirmed_hovers = guidance.get("our_hovers") or {}

    if ours and active.get("kind") == "ban" and guidance.get("bans"):
        b = guidance["bans"][0]
        suggestion = {
            "kind": "ban", "champion": b.get("champion"), "champion_id": b.get("champion_id"),
            "role": None, "reason": (b.get("reasons") or ["protects the planned INTent draft"])[0],
        }
    elif (ours and active.get("kind") == "pick" and local_role and local_hover and confirmed_hovers.get(local_role) == local_hover):
        suggestion = {
            "kind": "pick", "champion": local_hover,
            "champion_id": (guidance.get("our_hover_ids") or {}).get(local_role),
            "role": local_role, "reason": "All six plans re-ranked around your hover",
            "source": "human-hover",
        }
        local["picking_role"] = local_role
    elif ours and active.get("kind") == "pick" and guidance.get("picks"):
        p = guidance["picks"][0]
        suggestion = {
            "kind": "pick", "champion": p.get("champion"), "champion_id": p.get("champion_id"),
            "role": p.get("role"), "reason": "; ".join(p.get("why") or []),
        }
        local["picking_role"] = p.get("role")

    local["can_act"] = can_act
    local["suggestion"] = suggestion if can_act else None
    snap["local"] = local
    return snap


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict, status: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            try:
                self._send_json(merged_state())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 503)
            return
        if path == "/api/config":
            try:
                self._send_json(_json(COACH + "/api/config"))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 503)
            return
        if path.startswith("/api/champion-icon/"):
            try:
                with urllib.request.urlopen(COACH + path, timeout=5) as response:
                    payload = response.read()
                    content_type = response.headers.get("Content-Type", "image/png")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(payload)
            except Exception:
                self.send_response(404); self.end_headers()
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/client/hover", "/api/client/lock"):
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length)
        req = urllib.request.Request(
            COACH + path, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            body, status = exc.read(), exc.code
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main() -> None:
    instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        instance_lock.bind(("127.0.0.1", 18768))
    except OSError:
        instance_lock.close()
        return
    ThreadingHTTPServer(("127.0.0.1", 8767), Handler).serve_forever()


if __name__ == "__main__":
    main()
