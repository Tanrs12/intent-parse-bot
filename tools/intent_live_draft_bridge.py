from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import league_comp_engine
from league_draft_backend import recommend_live_drafts
from league_pro_data import refresh_recent_pro_data

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]
COACH_STATE_URL = "http://127.0.0.1:8765/api/state"
NORTHFLANK = Path(os.environ.get("APPDATA", "")) / "npm" / "northflank.CMD"
SIGNUP_CACHE = REPO / "data" / "live_flex_signups.json"
RUNTIME_PRO_PATH = REPO / "data" / "live_league_recent_pro.json"
ARAM_QUEUE_IDS = {450, 2400}

# Keep runtime pro refreshes out of the tracked Git snapshot while still feeding the engine.
if not RUNTIME_PRO_PATH.exists():
    try:
        RUNTIME_PRO_PATH.write_bytes(league_comp_engine.RECENT_PRO_PATH.read_bytes())
    except Exception:
        pass
league_comp_engine.RECENT_PRO_PATH = RUNTIME_PRO_PATH
league_comp_engine._RECENT_PRO_CACHE = (None, {})

_cache_lock = threading.Lock()
_signups: list[dict] = []
_last_signup_refresh = 0.0
_last_pro_refresh = 0.0
_compute_lock = threading.Lock()
_response_cache_key = None
_response_cache_value = None
_response_cache_at = 0.0


def _json_get(url: str, *, headers: dict | None = None, context=None, timeout: float = 2.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
        return json.load(response)


def _lcu_credentials():
    candidates = [
        Path(r"C:\Riot Games\League of Legends\lockfile"),
        Path(r"D:\Riot Games\League of Legends\lockfile"),
    ]
    for path in candidates:
        try:
            parts = path.read_text(encoding="utf-8").strip().split(":")
            if len(parts) >= 5:
                return int(parts[2]), parts[3]
        except Exception:
            pass
    return None


def _gameflow():
    creds = _lcu_credentials()
    if not creds:
        return None
    port, password = creds
    auth = base64.b64encode(("riot:" + password).encode()).decode()
    try:
        return _json_get(
            f"https://127.0.0.1:{port}/lol-gameflow/v1/session",
            headers={"Authorization": "Basic " + auth},
            context=ssl._create_unverified_context(),
            timeout=1.5,
        )
    except Exception:
        return None


def _mode_gate():
    flow = _gameflow()
    if not isinstance(flow, dict):
        return {"disabled": False, "queue_id": None, "queue_name": None}
    queue = ((flow.get("gameData") or {}).get("queue") or {})
    queue_id = queue.get("id")
    name = " ".join(str(queue.get(k, "")) for k in ("name", "shortName", "description", "gameMode", "type")).casefold()
    map_data = flow.get("map") or {}
    map_name = " ".join(str(map_data.get(k, "")) for k in ("name", "gameModeName", "gameMode")).casefold()
    disabled = queue_id in ARAM_QUEUE_IDS or any(token in (name + " " + map_name) for token in ("aram", "mayhem", "kiwi", "howling abyss"))
    return {
        "disabled": bool(disabled),
        "queue_id": queue_id,
        "queue_name": queue.get("name") or queue.get("description") or queue.get("shortName"),
    }


def _load_cached_signups():
    global _signups
    try:
        raw = json.loads(SIGNUP_CACHE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            _signups = raw
    except Exception:
        pass


def _signup_export_script() -> str:
    return '''import os,asyncio,asyncpg,json\nasync def main():\n c=await asyncpg.connect(os.environ["DATABASE_URL"])\n rows=await c.fetch("select discord_user_id,display_name,roles_json,role_priority_json,champions_json from league_flex_signups order by display_name")\n out=[]\n for r in rows:\n  out.append({"user_id":int(r["discord_user_id"]),"display_name":r["display_name"],"roles":json.loads(r["roles_json"] or "[]"),"role_priority":json.loads(r["role_priority_json"] or "{}"),"champions":json.loads(r["champions_json"] or "{}")})\n print("SIGNUPS_JSON="+json.dumps(out,ensure_ascii=False))\n await c.close()\nasyncio.run(main())\n'''


def refresh_signups(force: bool = False):
    global _signups, _last_signup_refresh
    now = time.time()
    if not force and now - _last_signup_refresh < 600:
        return
    _last_signup_refresh = now
    if not NORTHFLANK.exists():
        return
    payload = base64.b64encode(_signup_export_script().encode()).decode()
    remote = f'''python -c 'import base64;exec(base64.b64decode("{payload}"))' '''
    try:
        cp = subprocess.run(
            ["cmd", "/c", str(NORTHFLANK), "exec", "service", "--project", "intent-parse-bot", "--service", "intent-parse-bot", "--shell-cmd", remote, "--quiet"],
            capture_output=True, text=True, timeout=25,
        )
        marker = "SIGNUPS_JSON="
        line = next((ln for ln in cp.stdout.splitlines() if ln.startswith(marker)), None)
        if not line:
            return
        parsed = json.loads(line[len(marker):])
        if isinstance(parsed, list) and parsed:
            with _cache_lock:
                _signups = parsed
            SIGNUP_CACHE.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def refresh_pro_snapshot(force: bool = False):
    global _last_pro_refresh
    now = time.time()
    if not force and now - _last_pro_refresh < 600:
        return
    _last_pro_refresh = now
    try:
        asyncio.run(refresh_recent_pro_data(path=RUNTIME_PRO_PATH))
    except Exception:
        pass


def _current_five(state: dict) -> list[dict]:
    ids = [int(row["user_id"]) for row in state.get("our_team", []) if row.get("user_id")]
    if len(ids) != 5:
        return []
    with _cache_lock:
        by_id = {int(row["user_id"]): row for row in _signups}
    return [by_id[uid] for uid in ids if uid in by_id]


def _locked_by_role(state: dict) -> dict[str, str]:
    return {row["role"]: row["locked"] for row in state.get("our_team", []) if row.get("role") in ROLES and row.get("locked")}


def _unavailable(state: dict) -> set[str]:
    names = set(state.get("our_bans") or []) | set(state.get("enemy_bans") or [])
    for row in (state.get("our_team") or []) + (state.get("enemy_team") or []):
        if row.get("locked"):
            names.add(row["locked"])
    return names


def _guidance_from_result(result: dict, state: dict) -> dict:
    recs = result.get("recommendations") or []
    locked_roles = {row["role"] for row in state.get("our_team", []) if row.get("locked") and row.get("role") in ROLES}
    picks = []
    seen = set()
    for rec in recs:
        next_row = next((row for row in rec.get("pick_order", []) if row.get("role") not in locked_roles), None)
        if not next_row:
            continue
        champ = next_row["champion"]
        role = next_row["role"]
        if (champ, role) in seen:
            continue
        seen.add((champ, role))
        player = (rec.get("players", {}).get(role) or {}).get("display_name", role)
        picks.append({
            "role": role,
            "champion": champ,
            "champion_id": None,
            "score": float(rec.get("score", rec.get("composition_score", 0.0)) or 0.0),
            "rank": float(rec.get("score", 0.0) or 0.0),
            "comp_score": float(rec.get("composition_score", rec.get("score", 0.0)) or 0.0),
            "familiarity": None,
            "risk_known": True,
            "flex_roles": [],
            "lane": [],
            "counter_risk": 0.0,
            "counter_risk_by": [],
            "blind_safe": True,
            "priority": int(((rec.get("players", {}).get(role) or {}).get("role_priority") or {}).get(role, 2)),
            "source": "intent",
            "player": player,
            "team_plan": rec.get("team_plan"),
            "why": ["same ranking brain as /league game"],
        })
        if len(picks) >= 6:
            break

    bans = []
    if recs:
        for item in (recs[0].get("counter_bans") or [])[:5]:
            if isinstance(item, str):
                bans.append({"champion": item, "champion_id": None, "score": 1.0, "reasons": ["protects the planned Intent draft"], "tags": ["intent"]})
            elif isinstance(item, dict):
                champ = item.get("champion") or item.get("name")
                if champ:
                    bans.append({"champion": champ, "champion_id": None, "score": float(item.get("score", 1.0) or 1.0), "reasons": item.get("reasons") or [item.get("reason") or "protects the planned Intent draft"], "tags": list(item.get("tags") or ["intent"])})

    comps = []
    if recs:
        top = recs[0]
        comps.append({
            "score": float(top.get("score", 0.0) or 0.0),
            "champions": top.get("champions") or {},
            "players": {role: (top.get("players", {}).get(role) or {}).get("display_name", role) for role in ROLES},
            "team_plan": top.get("team_plan"),
        })
    active = state.get("active") or {}
    roles_now = [row["role"] for row in state.get("our_team", []) if row.get("role") in ROLES and not row.get("locked")]
    return {
        "headline": ("Ban now: " + bans[0]["champion"]) if active.get("kind") == "ban" and active.get("ours") and bans else (("Pick " + picks[0]["champion"]) if picks else "Intent draft ready"),
        "phase_kind": active.get("kind") or "idle",
        "roles_now": roles_now,
        "picking_now": bool(active.get("kind") == "pick" and active.get("ours")),
        "draft_complete": len(locked_roles) == 5,
        "our_locked": _locked_by_role(state),
        "enemy_locked": {},
        "swaps": [],
        "picks": picks,
        "bans": bans,
        "comps": comps,
        "team": {"score": comps[0]["score"] if comps else 0.0, "team_plan": comps[0].get("team_plan") if comps else None, "strengths": [], "gaps": []},
        "enemy_team_score": None,
        "alerts": [{"level": "info", "text": "INTent /league game backend is controlling live recommendations."}],
        "plans": [],
        "likely_enemy": [],
        "flex": [],
        "counters": [],
    }


def build_response():
    gate = _mode_gate()
    if gate["disabled"]:
        return {"active": False, "disabled": True, "reason": f"Disabled in {gate['queue_name'] or 'ARAM'}", **gate}
    try:
        snap = _json_get(COACH_STATE_URL, timeout=2.0)
    except Exception as exc:
        return {"active": False, "disabled": False, "reason": "Draft Coach server unavailable", "error": str(exc)}
    status = snap.get("status") or {}
    state = snap.get("state") or {}
    if not status.get("lcu_in_champ_select") and state.get("source") != "manual":
        return {"active": False, "disabled": False, "reason": "Waiting for champion select", **gate}
    five = _current_five(state)
    if len(five) != 5:
        return {"active": False, "disabled": False, "reason": "Waiting for all five Discord signup profiles", "matched": len(five), **gate}
    locked = _locked_by_role(state)
    unavailable = _unavailable(state)
    cache_key = json.dumps({
        "side": state.get("side") or "blue",
        "active": state.get("active") or {},
        "our_bans": state.get("our_bans") or [],
        "enemy_bans": state.get("enemy_bans") or [],
        "locked": locked,
        "enemy_locked": [row.get("locked") for row in state.get("enemy_team", []) if row.get("locked")],
        "players": [int(row["user_id"]) for row in five],
    }, sort_keys=True)
    global _response_cache_key, _response_cache_value, _response_cache_at
    with _compute_lock:
        now = time.time()
        if cache_key == _response_cache_key and _response_cache_value is not None and now - _response_cache_at < 5:
            return _response_cache_value
        result = recommend_live_drafts(
            five,
            unavailable=unavailable,
            locked_champions=locked,
            side=state.get("side") or "blue",
            limit=6,
            rotation_token=None,
        )
        response = {"active": True, "disabled": False, "reason": "Intent backend active", "guidance": _guidance_from_result(result, state), **gate}
        _response_cache_key = cache_key
        _response_cache_value = response
        _response_cache_at = now
        return response


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] != "/api/guidance":
            self.send_response(404); self.end_headers(); return
        payload = json.dumps(build_response()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


def background_refresh(server):
    seen_coach = False
    down_since = None
    while True:
        refresh_signups()
        refresh_pro_snapshot()
        try:
            _json_get("http://127.0.0.1:8765/api/config", timeout=1.5)
            seen_coach = True
            down_since = None
        except Exception:
            if seen_coach:
                down_since = down_since or time.time()
                if time.time() - down_since > 180:
                    server.shutdown()
                    return
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    _load_cached_signups()
    refresh_signups(force=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=background_refresh, args=(server,), daemon=True).start()
    print(f"Intent live draft bridge: http://127.0.0.1:{args.port}/api/guidance")
    server.serve_forever()


if __name__ == "__main__":
    main()
