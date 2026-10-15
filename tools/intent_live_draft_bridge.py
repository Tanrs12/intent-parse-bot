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
_party_size_cache: int | None = None
_party_size_at = 0.0
PARTY_CACHE_SECONDS = 15 * 60


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


def _lcu_json(path: str):
    creds = _lcu_credentials()
    if not creds:
        return None
    port, password = creds
    auth = base64.b64encode(("riot:" + password).encode()).decode()
    return _json_get(
        f"https://127.0.0.1:{port}{path}",
        headers={"Authorization": "Basic " + auth},
        context=ssl._create_unverified_context(),
        timeout=1.5,
    )


def _gameflow():
    try:
        return _lcu_json("/lol-gameflow/v1/session")
    except Exception:
        return None


def _party_gate():
    """Require a confirmed five-player premade before enabling Flex guidance.

    League's lobby endpoint disappears once champion select begins, so remember the
    most recent lobby size briefly and carry that confirmation into champion select.
    """
    global _party_size_cache, _party_size_at
    now = time.time()
    live_size = None
    try:
        lobby = _lcu_json("/lol-lobby/v2/lobby")
        if isinstance(lobby, dict) and isinstance(lobby.get("members"), list):
            live_size = len(lobby["members"])
            _party_size_cache = live_size
            _party_size_at = now
    except Exception:
        pass

    fresh_cached = _party_size_cache is not None and now - _party_size_at <= PARTY_CACHE_SECONDS
    size = live_size if live_size is not None else (_party_size_cache if fresh_cached else None)
    return {
        "party_confirmed": size == 5,
        "party_size": size,
        "party_cached": live_size is None and fresh_cached,
    }


def _mode_gate():
    flow = _gameflow()
    if not isinstance(flow, dict):
        return {"disabled": False, "queue_id": None, "queue_name": None, "is_custom": False, "is_bot": False}
    game_data = flow.get("gameData") or {}
    queue = (game_data.get("queue") or {})
    queue_id = queue.get("id")
    category = str(queue.get("category", "")).casefold()
    qtype = str(queue.get("type", "")).casefold()
    is_custom = bool(game_data.get("isCustomGame") or queue.get("isCustom") or category == "custom")
    is_bot = category == "versusai" or "bot" in qtype
    name = " ".join(str(queue.get(k, "")) for k in ("name", "shortName", "description", "gameMode", "type")).casefold()
    map_data = flow.get("map") or {}
    map_name = " ".join(str(map_data.get(k, "")) for k in ("name", "gameModeName", "gameMode")).casefold()
    disabled = queue_id in ARAM_QUEUE_IDS or any(token in (name + " " + map_name) for token in ("aram", "mayhem", "kiwi", "howling abyss"))
    return {
        "disabled": bool(disabled),
        "queue_id": queue_id,
        "queue_name": queue.get("name") or queue.get("description") or queue.get("shortName"),
        "is_custom": is_custom,
        "is_bot": is_bot,
    }


def _queue_traits(queue: dict | None) -> dict:
    queue = queue or {}
    try:
        queue_id = int(queue.get("id")) if queue.get("id") is not None else None
    except (TypeError, ValueError):
        queue_id = None
    text = " ".join(str(queue.get(k, "")) for k in ("name", "shortName", "description", "gameMode", "type", "category")).casefold()
    is_custom = queue_id == 0 or "custom" in text
    is_bot = queue_id in {830, 840, 850, 870} or "versusai" in text or "co-op vs ai" in text or " bot" in (" " + text)
    disabled = queue_id in ARAM_QUEUE_IDS or any(token in text for token in ("aram", "mayhem", "kiwi", "howling abyss"))
    return {"disabled": disabled, "queue_id": queue_id, "queue_name": queue.get("name") or queue.get("description"), "is_custom": is_custom, "is_bot": is_bot}


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


def _test_lineup_ids() -> list[int]:
    """Stable five used only to make solo custom / versus-AI champ select testable.

    The profiles themselves still come from the live Discord signup cache.  The loadfile only
    chooses which five people stand in for the four bot ally seats that LCU does not identify as
    real Discord users.
    """
    try:
        data = json.loads((REPO / "loadfiles" / "flexers-team.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    by_name: dict[str, int] = {}
    for row in data.get("players") or []:
        uid = row.get("user_id")
        name = row.get("rename") or row.get("name")
        if uid and name:
            by_name[str(name).casefold()] = int(uid)
    out = []
    for name in data.get("lineup") or []:
        uid = by_name.get(str(name).casefold())
        if uid and uid not in out:
            out.append(uid)
    return out[:5]


TEST_LINEUP_IDS = _test_lineup_ids()


def _current_five(state: dict, *, fill_for_test: bool = False) -> list[dict]:
    ids = []
    for row in state.get("our_team", []) or []:
        if row.get("user_id"):
            uid = int(row["user_id"])
            if uid not in ids:
                ids.append(uid)
    with _cache_lock:
        by_id = {int(row["user_id"]): row for row in _signups}
    matched = [uid for uid in ids if uid in by_id]
    if fill_for_test and len(matched) < 5:
        for uid in TEST_LINEUP_IDS:
            if uid in by_id and uid not in matched:
                matched.append(uid)
            if len(matched) >= 5:
                break
        if len(matched) < 5:
            for uid in by_id:
                if uid not in matched:
                    matched.append(uid)
                if len(matched) >= 5:
                    break
    if len(matched) != 5:
        return []
    return [by_id[uid] for uid in matched]


def _locked_by_role(state: dict) -> dict[str, str]:
    return {row["role"]: row["locked"] for row in state.get("our_team", []) if row.get("role") in ROLES and row.get("locked")}


def _state_for_test(state: dict) -> dict:
    """Ignore auto-selected ally bots while preserving the local/known humans.

    Versus-AI reveals four ally bots immediately.  Those champions are useful for testing LCU
    ingestion, but they must not become fake locks in the five real-player draft we are previewing.
    Enemy bots stay intact so the live matchup layer can still be tested against all five reveals.
    """
    with _cache_lock:
        known_ids = {int(row["user_id"]) for row in _signups if row.get("user_id")}
    local_cell = state.get("local_cell_id")
    out = dict(state)
    team = []
    for original in state.get("our_team", []) or []:
        row = dict(original)
        uid = int(row["user_id"]) if row.get("user_id") else None
        known_human = uid in known_ids or (local_cell is not None and row.get("cell_id") == local_cell) or bool(row.get("is_local"))
        if not known_human:
            row["locked"] = None
            row["hover"] = None
        team.append(row)
    out["our_team"] = team
    return out


def _unavailable(state: dict) -> set[str]:
    names = set(state.get("our_bans") or []) | set(state.get("enemy_bans") or [])
    for row in (state.get("our_team") or []) + (state.get("enemy_team") or []):
        if row.get("locked"):
            names.add(row["locked"])
    return names


def _live_lane_adjustment(rec: dict, state: dict) -> tuple[float, list[dict]]:
    """Rat's live counter layer on top of the shared comp score.

    The shared backend decides *which complete drafts fit our five*.  Once the enemy has
    revealed a champion in a known/inferred role, apply the same calibrated lane matchup
    evidence used by Draft Coach so a good counter/composition completion rises above an
    otherwise similar draft.  This is deliberately bounded: lane counter data can break a
    close score, not override a much better five-man composition.
    """
    matchups = league_comp_engine.load_lane_matchups()
    champions = rec.get("champions") or {}
    edges: list[dict] = []
    total = 0.0
    for enemy in state.get("enemy_team") or []:
        role = enemy.get("role")
        enemy_champion = enemy.get("locked") or enemy.get("hover")
        our_champion = champions.get(role) if role in ROLES else None
        if not role or not enemy_champion or not our_champion:
            continue
        advantage, games = league_comp_engine.matchup_response(role, enemy_champion, our_champion, matchups)
        if advantage is None:
            continue
        residual, points = league_comp_engine.calibrated_matchup(role, advantage, games, quality=0.7)
        total += points
        edges.append({
            "role": role,
            "enemy": enemy_champion,
            "ours": our_champion,
            "advantage": round(float(advantage), 2),
            "adjusted": residual,
            "points": points,
            "games": games,
            "locked": bool(enemy.get("locked")),
        })
    return round(max(-6.0, min(6.0, total)), 3), edges


def _guidance_from_result(result: dict, state: dict) -> dict:
    raw_recs = result.get("recommendations") or []
    recs = []
    for original in raw_recs:
        rec = dict(original)
        lane_adjustment, lane_edges = _live_lane_adjustment(rec, state)
        rec["live_lane_adjustment"] = lane_adjustment
        rec["live_lane_edges"] = lane_edges
        rec["live_rank_score"] = float(rec.get("rank_score", rec.get("score", 0.0)) or 0.0) + lane_adjustment
        recs.append(rec)
    recs.sort(key=lambda rec: float(rec.get("live_rank_score", 0.0)), reverse=True)

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
        lane = [edge for edge in rec.get("live_lane_edges", []) if edge.get("role") == role]
        why = ["same comp/player ranking brain as /league game"]
        if lane:
            edge = lane[0]
            if edge["points"] > 0:
                why.append(f"counter edge into {edge['enemy']}")
            elif edge["points"] < 0:
                why.append(f"matchup cost into {edge['enemy']}")
        picks.append({
            "role": role,
            "champion": champ,
            "champion_id": None,
            "score": float(rec.get("score", rec.get("composition_score", 0.0)) or 0.0),
            "rank": float(rec.get("live_rank_score", rec.get("score", 0.0)) or 0.0),
            "comp_score": float(rec.get("composition_score", rec.get("score", 0.0)) or 0.0),
            "familiarity": None,
            "risk_known": True,
            "flex_roles": [],
            "lane": lane,
            "lane_adjustment": round(sum(float(edge.get("points", 0.0) or 0.0) for edge in lane), 3),
            "counter_risk": 0.0,
            "counter_risk_by": [],
            "blind_safe": True,
            "priority": int(((rec.get("players", {}).get(role) or {}).get("role_priority") or {}).get(role, 2)),
            "source": "intent",
            "player": player,
            "team_plan": rec.get("team_plan"),
            "why": why,
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
    for rec in recs[:6]:
        comps.append({
            "score": float(rec.get("score", rec.get("composition_score", 0.0)) or 0.0),
            "live_score": round(float(rec.get("live_rank_score", rec.get("score", 0.0)) or 0.0), 2),
            "lane_adjustment": float(rec.get("live_lane_adjustment", 0.0) or 0.0),
            "lane_edges": list(rec.get("live_lane_edges") or []),
            "champions": rec.get("champions") or {},
            "players": {role: (rec.get("players", {}).get(role) or {}).get("display_name", role) for role in ROLES},
            "team_plan": rec.get("team_plan"),
            "pick_order": list(rec.get("pick_order") or []),
        })
    active = state.get("active") or {}
    roles_now = [row["role"] for row in state.get("our_team", []) if row.get("role") in ROLES and not row.get("locked")]
    enemy_locked = {row["role"]: row["locked"] for row in state.get("enemy_team", []) if row.get("role") in ROLES and row.get("locked")}
    return {
        "headline": ("Ban now: " + bans[0]["champion"]) if active.get("kind") == "ban" and active.get("ours") and bans else (("Pick " + picks[0]["champion"]) if picks else "Intent draft ready"),
        "phase_kind": active.get("kind") or "idle",
        "roles_now": roles_now,
        "picking_now": bool(active.get("kind") == "pick" and active.get("ours")),
        "draft_complete": len(locked_roles) == 5,
        "our_locked": _locked_by_role(state),
        "enemy_locked": enemy_locked,
        "swaps": [],
        "picks": picks,
        "bans": bans,
        "comps": comps,
        "team": {"score": comps[0]["score"] if comps else 0.0, "team_plan": comps[0].get("team_plan") if comps else None, "strengths": [], "gaps": []},
        "enemy_team_score": None,
        "alerts": [{"level": "info", "text": "INTent comp/player ranking + Draft Coach live matchup layer."}],
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
        party = _party_gate()
        return {"active": False, "disabled": False, "reason": "Draft Coach server unavailable", "error": str(exc), **gate, **party}

    status = snap.get("status") or {}
    state = snap.get("state") or {}
    # The League gameflow endpoint can be sparse in custom / bot champ select.  The Draft Coach
    # watcher also records the queue, so use either source to recognise test lobbies and ARAM.
    qgate = _queue_traits(status.get("queue"))
    merged_gate = dict(gate)
    for key in ("is_custom", "is_bot", "disabled"):
        merged_gate[key] = bool(gate.get(key) or qgate.get(key))
    if merged_gate.get("queue_id") is None and qgate.get("queue_id") is not None:
        merged_gate["queue_id"] = qgate["queue_id"]
    if not merged_gate.get("queue_name") and qgate.get("queue_name"):
        merged_gate["queue_name"] = qgate["queue_name"]
    if merged_gate["disabled"]:
        return {"active": False, "disabled": True, "reason": f"Disabled in {merged_gate['queue_name'] or 'ARAM'}", **merged_gate}

    party = _party_gate()
    testing = bool(merged_gate.get("is_custom") or merged_gate.get("is_bot"))
    # Custom and versus-AI lobbies are allowed for solo bot testing. Real PvP matchmaking still
    # requires a confirmed five-player premade.
    if not testing and not party["party_confirmed"]:
        size = party.get("party_size")
        reason = "Draft Coach only runs in a 5-man party" if size is None else f"Draft Coach only runs in a 5-man party ({size}/5 currently)"
        return {"active": False, "disabled": True, "reason": reason, **merged_gate, **party}
    if not status.get("lcu_in_champ_select") and state.get("source") != "manual":
        return {"active": False, "disabled": False, "reason": "Waiting for champion select", **merged_gate, **party}
    five = _current_five(state, fill_for_test=testing)
    if len(five) != 5:
        label = "test lineup" if testing else "Discord signup profiles"
        return {"active": False, "disabled": False, "reason": f"Waiting for all five {label}", "matched": len(five), **merged_gate, **party}
    effective_state = _state_for_test(state) if testing else state
    locked = _locked_by_role(effective_state)
    unavailable = _unavailable(effective_state)
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
        response = {"active": True, "disabled": False, "reason": "Intent backend active", "guidance": _guidance_from_result(result, effective_state), **merged_gate, **party}
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
    # Serve the cached signup data immediately. Northflank refresh happens in the background;
    # champ-select overlays should not wait up to 25 seconds just to bind this local port.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=background_refresh, args=(server,), daemon=True).start()
    print(f"Intent live draft bridge: http://127.0.0.1:{args.port}/api/guidance")
    server.serve_forever()


if __name__ == "__main__":
    main()
