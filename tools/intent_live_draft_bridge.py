from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import league_comp_engine
from league_draft_backend import _pick_group_meta, recommend_live_drafts
from league_pro_data import refresh_recent_pro_data

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]
COACH_STATE_URL = "http://127.0.0.1:8765/api/state"
NORTHFLANK = Path(os.environ.get("APPDATA", "")) / "npm" / "northflank.CMD"
SIGNUP_CACHE = REPO / "data" / "live_flex_signups.json"
RUNTIME_PRO_PATH = REPO / "data" / "live_league_recent_pro.json"
ARAM_QUEUE_IDS = {450, 2400}


def _load_champion_ids() -> dict[str, int]:
    # Source checkout: REPO/draftcoach. Packaged onedir build: champion_ids.json lives under
    # sys._MEIPASS/draftcoach while this bridge is imported from _MEIPASS/engine/tools.
    candidates = [
        REPO / "draftcoach" / "champion_ids.json",
        Path(getattr(sys, "_MEIPASS", REPO)) / "draftcoach" / "champion_ids.json",
        REPO.parent / "draftcoach" / "champion_ids.json",
    ]
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            mapping = {str(name).casefold(): int(cid) for cid, name in (raw.get("champions") or {}).items()}
            if mapping:
                return mapping
        except Exception:
            continue
    return {}


CHAMPION_IDS = _load_champion_ids()


def _champion_id(name: str | None) -> int | None:
    return CHAMPION_IDS.get(str(name or "").casefold())


def configure_upstream(base: str | None) -> None:
    """Follower mode: serve the coach PC's authoritative guidance on local :8766."""
    global _upstream_base
    _upstream_base = str(base or "").rstrip("/") or None


def configure_cloud(base: str | None, key: str | None, mode: str | None) -> None:
    global _cloud_base, _cloud_key, _cloud_mode
    _cloud_base = str(base or "").rstrip("/") or None
    _cloud_key = str(key or "").strip() or None
    _cloud_mode = str(mode or "").strip().casefold() or None
    if _cloud_mode not in (None, "host", "follow", "auto"):
        raise ValueError("cloud sync mode must be host, follow or auto")
    if _cloud_mode and (not _cloud_base or not _cloud_key or len(_cloud_key) < 16):
        raise ValueError("cloud sync needs a relay URL and a 16+ character session key")


def _cloud_url(suffix: str = "") -> str:
    if not (_cloud_base and _cloud_key):
        raise RuntimeError("cloud sync is not configured")
    return f"{_cloud_base}/draft-sync/{_cloud_key}{suffix}"


def _cloud_get() -> dict:
    data = _json_get(_cloud_url(), timeout=6.0)
    if isinstance(data, dict):
        data = dict(data)
        data["sync"] = {"mode": "cloud-follower", "source": _cloud_base}
    return data


def _cloud_preference(signature: str | None) -> dict:
    payload = json.dumps({"signature": signature}).encode("utf-8")
    req = urllib.request.Request(_cloud_url("/preference"), data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=6.0) as response:
        return json.loads(response.read())


def cloud_publish_loop() -> None:
    global _cloud_pref_version
    while _cloud_mode == "host":
        try:
            payload = build_response()
            payload = dict(payload)
            payload["sync"] = {"mode": "cloud-host", "source": _cloud_base}
            control = _cloud_publish(payload)
            _apply_cloud_control(control)
        except Exception:
            pass
        time.sleep(0.8)



def _room_label() -> str:
    if not _cloud_key:
        return ""
    return "FLEX-" + hashlib.sha256(_cloud_key.encode("utf-8")).hexdigest()[:6].upper()


def _team_loadfile() -> Path | None:
    candidates = [
        REPO / "loadfiles" / "flexers-team.json",
        REPO.parent.parent / "loadfiles" / "flexers-team.json",
        Path(sys.executable).resolve().parent / "loadfiles" / "flexers-team.json",
    ]
    return next((path for path in candidates if path.exists()), None)


def _team_host_priorities() -> dict[int, int]:
    try:
        path = _team_loadfile()
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
        return {int(row["user_id"]): i for i, row in enumerate(data.get("players") or []) if row.get("user_id")}
    except Exception:
        return {}


_HOST_PRIORITIES = _team_host_priorities()


def _team_confirmed_pool_additions() -> dict[int, dict[str, list[str]]]:
    """Honor verified role picks in the team loadfile even before Discord signup sync.

    Unlike raw account history, only entries explicitly marked live_confirmed
    may augment a saved pool. This lets a new player-confirmed pick survive the
    periodic live signup cache refresh without assuming all hovers are mastery.
    """
    try:
        path = _team_loadfile()
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
        return {
            int(player["user_id"]): {
                role: [str(pick["champion"]) for pick in choices
                       if isinstance(pick, dict) and pick.get("live_confirmed") and pick.get("champion")]
                for role, choices in (player.get("pools") or {}).items()
            }
            for player in data.get("players") or [] if player.get("user_id")
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {}


_CONFIRMED_TEAM_POOLS = _team_confirmed_pool_additions()


def _with_confirmed_team_picks(profile: dict) -> dict:
    additions = _CONFIRMED_TEAM_POOLS.get(int(profile.get("user_id") or 0), {})
    if not additions:
        return profile
    champions = {role: list(picks) for role, picks in (profile.get("champions") or {}).items()}
    for role, picks in additions.items():
        if role not in profile.get("roles", []):
            continue
        pool = champions.setdefault(role, [])
        for champion in picks:
            if champion not in pool:
                pool.append(champion)
    profile["champions"] = champions
    return profile


def _auto_identity() -> dict:
    try:
        snap = _json_get(COACH_STATE_URL, timeout=1.5)
    except Exception:
        snap = {}
    status = (snap or {}).get("status") or {}
    state = (snap or {}).get("state") or {}
    local_cell = state.get("local_cell_id")
    local = next((row for row in (state.get("our_team") or [])
                  if row.get("is_local") or (local_cell is not None and row.get("cell_id") == local_cell)), {})
    try:
        player_id = int(local.get("user_id") or 0)
    except (TypeError, ValueError):
        player_id = 0
    return {
        "client_id": _auto_client_id,
        "player_id": player_id,
        "player_name": str(local.get("display_name") or local.get("riot_id") or socket.gethostname()),
        "priority": int(_HOST_PRIORITIES.get(player_id, 9999)),
        "in_champ_select": bool(status.get("lcu_in_champ_select")),
    }


def _auto_sync_meta(mode: str) -> dict:
    with _auto_lock:
        return {
            "mode": mode,
            "source": _cloud_base,
            "room": _room_label(),
            "leader": _auto_leader_name,
            "participants": _auto_participant_count,
        }


def _cloud_heartbeat(identity: dict) -> dict:
    payload = json.dumps(identity).encode("utf-8")
    req = urllib.request.Request(_cloud_url("/heartbeat"), data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5.0) as response:
        return json.loads(response.read())


def _cloud_publish(payload: dict, client_id: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if client_id:
        headers["X-Intent-Client"] = client_id
    req = urllib.request.Request(_cloud_url(), data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=6.0) as response:
        return json.loads(response.read())


def _apply_cloud_control(control: dict) -> None:
    global _cloud_pref_version
    version = int((control or {}).get("preference_version") or 0)
    if version > _cloud_pref_version:
        _cloud_pref_version = version
        _set_preference((control or {}).get("preference"))


def cloud_auto_loop() -> None:
    global _auto_is_host, _auto_leader_id, _auto_leader_name, _auto_participant_count
    while _cloud_mode == "auto":
        try:
            identity = _auto_identity()
            control = _cloud_heartbeat(identity)
            leader_id = control.get("leader_id")
            with _auto_lock:
                _auto_leader_id = str(leader_id) if leader_id else None
                _auto_leader_name = str(control.get("leader_name") or "") or None
                _auto_participant_count = int(control.get("participant_count") or 0)
                _auto_is_host = bool(leader_id and leader_id == _auto_client_id)
            _apply_cloud_control(control)
            if leader_id == _auto_client_id:
                payload = dict(build_response())
                payload["sync"] = _auto_sync_meta("auto-host")
                published = _cloud_publish(payload, _auto_client_id)
                _apply_cloud_control(published)
        except Exception:
            with _auto_lock:
                _auto_is_host = False
        time.sleep(0.8)

def _comp_signature(champions: dict | None) -> str:
    champions = champions or {}
    return "|".join(str(champions.get(role) or "?") for role in ROLES)


def _get_preference() -> str | None:
    with _preference_lock:
        return _preferred_signature


def _set_preference(signature: str | None) -> str | None:
    global _preferred_signature, _response_cache_key
    clean = str(signature or "").strip() or None
    with _preference_lock:
        _preferred_signature = clean
    _response_cache_key = None
    return clean


def _upstream_get() -> dict:
    if not _upstream_base:
        raise RuntimeError("not in follower mode")
    data = _json_get(_upstream_base + "/api/intent-guidance", timeout=6.0)
    if isinstance(data, dict):
        data = dict(data)
        data["sync"] = {"mode": "follower", "source": _upstream_base}
    return data


def _upstream_preference(signature: str | None) -> dict:
    if not _upstream_base:
        raise RuntimeError("not in follower mode")
    payload = json.dumps({"signature": signature}).encode("utf-8")
    req = urllib.request.Request(_upstream_base + "/api/intent-preference", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=6.0) as response:
        return json.loads(response.read())


_test_lock = threading.Lock()
_test_overrides = {"bans": [], "enemy": {}, "ours": {}}

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
# Cached data is good enough to start champ select instantly; slow network refreshes happen later
# in the background instead of competing with League / the overlay during launch.
_last_signup_refresh = time.time() if SIGNUP_CACHE.exists() else 0.0
_last_pro_refresh = time.time() if RUNTIME_PRO_PATH.exists() else 0.0
SIGNUP_REFRESH_SECONDS = 10 * 60
PRO_REFRESH_SECONDS = 6 * 60 * 60
_compute_lock = threading.Lock()
_upstream_base: str | None = None
_cloud_base: str | None = None
_cloud_key: str | None = None
_cloud_mode: str | None = None
_cloud_pref_version = 0
_auto_lock = threading.Lock()
_auto_client_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
_auto_is_host = False
_auto_leader_id: str | None = None
_auto_leader_name: str | None = None
_auto_participant_count = 0
_preference_lock = threading.Lock()
_preferred_signature: str | None = None
PREFERENCE_MAX_GAP = 2.5
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
    """Allow a 3-5 player premade in normal Flex while keeping one/two-player normal PvP gated off.

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
        "party_confirmed": size in (3, 4, 5),
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
    global _signups, _last_signup_refresh, _response_cache_key
    now = time.time()
    if not force and now - _last_signup_refresh < SIGNUP_REFRESH_SECONDS:
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
                changed = parsed != _signups
                _signups = parsed
            if changed:
                _response_cache_key = None
            SIGNUP_CACHE.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def refresh_pro_snapshot(force: bool = False):
    global _last_pro_refresh, _response_cache_key
    now = time.time()
    if not force and now - _last_pro_refresh < PRO_REFRESH_SECONDS:
        return
    _last_pro_refresh = now
    try:
        asyncio.run(refresh_recent_pro_data(path=RUNTIME_PRO_PATH))
        _response_cache_key = None
    except Exception:
        pass


def _test_lineup_ids() -> list[int]:
    """Stable five used only to make solo custom / versus-AI champ select testable.

    The profiles themselves still come from the live Discord signup cache.  The loadfile only
    chooses which five people stand in for the four bot ally seats that LCU does not identify as
    real Discord users.
    """
    try:
        path = _team_loadfile()
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
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

_PUG_FALLBACK = {
    "Top": ["Ornn", "Gnar", "K'Sante"],
    "Jungle": ["Sejuani", "Wukong", "Vi"],
    "Mid": ["Orianna", "Ahri", "Ryze"],
    "ADC": ["Ezreal", "Jhin", "Kai'Sa"],
    "Support": ["Nautilus", "Braum", "Karma"],
}
_PUG_POOL_CACHE: dict[str, list[str]] = {}

_TEST_VISUAL_OPTIONS = {
    "Top": ["Camille", "Gnar", "Ornn"],
    "Jungle": ["Wukong", "Vi", "Sejuani"],
    "Mid": ["Orianna", "Ahri", "Ryze"],
    "ADC": ["Ezreal", "Kai'Sa", "Jhin"],
    "Support": ["Nautilus", "Karma", "Braum"],
}
_TEST_VISUAL_BANS = ["Rumble", "Skarner", "Aurora", "Ashe", "Neeko"]
_TEST_VISUAL_COUNTS = {"Top": 3, "Jungle": 1, "Mid": 2, "ADC": 3, "Support": 1}



def _pug_role_pool(role: str, limit: int = 3) -> list[str]:
    """Small neutral meta pool for an uncontrolled teammate before they hover.

    We never present these as instructions to the PUG; they only let the scorer estimate the
    unknown slot until Riot reveals the teammate's real hover/lock.  A deliberately tiny pool
    keeps 3/4-man recomputes cheap and avoids pretending we know the random player's mastery.
    """
    if role in _PUG_POOL_CACHE:
        return list(_PUG_POOL_CACHE[role][:limit])
    pool: list[str] = []
    try:
        index = league_comp_engine.load_solo_queue_winrates().get(role, {})
        blocked = set(getattr(league_comp_engine, "BLOCKED_ROLE_PICKS", set()) or set())
        excluded = set(getattr(league_comp_engine, "DISPLAY_EXCLUDED_CHAMPIONS", set()) or set())
        rows = []
        for champion, data in index.items():
            champion = str(champion)
            if (role, champion) in blocked or champion in excluded:
                continue
            weight = float((data or {}).get("weight", 0) or 0)
            win_rate = float((data or {}).get("win_rate", 50.0) or 50.0)
            rows.append((weight, -abs(win_rate - 50.0), champion))
        rows.sort(reverse=True)
        pool = [row[2] for row in rows[:limit]]
    except Exception:
        pool = []
    if not pool:
        pool = list(_PUG_FALLBACK.get(role, []))[:limit]
    _PUG_POOL_CACHE[role] = list(pool)
    return list(pool)


def _known_live_profile_count(state: dict) -> int:
    with _cache_lock:
        known = {int(row["user_id"]) for row in _signups if row.get("user_id")}
    return len({int(row["user_id"]) for row in (state.get("our_team") or []) if row.get("user_id") and int(row["user_id"]) in known})


def _live_pug_signup(row: dict, index: int) -> dict | None:
    role = row.get("role")
    if role not in ROLES:
        return None
    live_champion = row.get("locked") or row.get("hover")
    pool = [live_champion] if live_champion else _pug_role_pool(role)
    if not pool:
        return None
    try:
        cell_id = int(row.get("cell_id"))
    except (TypeError, ValueError):
        cell_id = index
    account = str(row.get("riot_id") or row.get("display_name") or f"PUG {index + 1}")
    return {
        "user_id": -100000 - abs(cell_id) - index * 100,
        "display_name": account,
        "roles": [role],
        "role_priority": {role: 1},
        "champions": {role: list(pool)},
        "_uncontrolled": True,
        "_live_account": account,
        "_profile_display_name": None,
        "_live_champion_confirmed": bool(live_champion),
    }


def _current_five(state: dict, *, fill_for_test: bool = False) -> list[dict]:
    """Resolve the five live seats to known profiles plus honest PUG placeholders.

    Known people are matched by the roster's Riot-account mapping and keep their saved pools, but
    their *display label* is the actual account currently in champ select.  In 3/4-man normal Flex,
    unmatched real seats become neutral, role-fixed PUG profiles.  Custom/bot testing keeps using
    the explicit five-person test lineup rather than treating Riot bots as people.
    """
    team = list(state.get("our_team") or [])
    with _cache_lock:
        by_id = {int(row["user_id"]): row for row in _signups if row.get("user_id")}

    resolved: list[dict] = []
    used_ids: set[int] = set()
    for row in team:
        if not row.get("user_id"):
            continue
        uid = int(row["user_id"])
        if uid in used_ids or uid not in by_id:
            continue
        profile = _with_confirmed_team_picks(dict(by_id[uid]))
        account = str(row.get("riot_id") or row.get("display_name") or profile.get("display_name") or uid)
        profile["_profile_display_name"] = profile.get("display_name")
        profile["_live_account"] = account
        profile["_uncontrolled"] = False
        profile["display_name"] = account
        resolved.append(profile)
        used_ids.add(uid)

    if fill_for_test:
        for uid in TEST_LINEUP_IDS:
            if uid in by_id and uid not in used_ids:
                resolved.append(_with_confirmed_team_picks(dict(by_id[uid])))
                used_ids.add(uid)
            if len(resolved) >= 5:
                break
        if len(resolved) < 5:
            for uid, profile in by_id.items():
                if uid not in used_ids:
                    resolved.append(_with_confirmed_team_picks(dict(profile)))
                    used_ids.add(uid)
                if len(resolved) >= 5:
                    break
        return resolved[:5] if len(resolved) >= 5 else []

    for index, row in enumerate(team):
        uid = int(row["user_id"]) if row.get("user_id") else None
        if uid is not None and uid in used_ids:
            continue
        # Ignore synthetic padded seats; normal PvP champ select supplies five real LCU rows.
        try:
            if int(row.get("cell_id")) < 0 and not row.get("riot_id"):
                continue
        except (TypeError, ValueError):
            pass
        pug = _live_pug_signup(row, index)
        if pug is not None:
            resolved.append(pug)

    return resolved[:5] if len(resolved) == 5 else []

def _locked_by_role(state: dict) -> dict[str, str]:
    return {row["role"]: row["locked"] for row in state.get("our_team", []) if row.get("role") in ROLES and row.get("locked")}


def _role_players_for_state(state: dict, five: list[dict], *, testing: bool) -> dict[str, int]:
    """Hard role ownership from genuine live seats, never synthetic custom-test placeholders."""
    five_ids = {int(row["user_id"]) for row in five if row.get("user_id")}
    local_cell = state.get("local_cell_id")
    result: dict[str, int] = {}
    for row in state.get("our_team", []) or []:
        if row.get("role") not in ROLES or not row.get("user_id"):
            continue
        uid = int(row["user_id"])
        if uid not in five_ids:
            continue
        if testing:
            # Custom/Practice visual testing is intentionally single-captain. Riot may attach
            # puuid/identity-like data to bot/custom seats, so only this machine's local seat is
            # authoritative. Real Flex (testing=False) still hard-pins every genuine live player.
            is_local = bool(row.get("is_local")) or (local_cell is not None and row.get("cell_id") == local_cell)
            if not is_local:
                continue
        result[row["role"]] = uid
    return result


def _hovered_by_role(state: dict) -> dict[str, str]:
    """Current ally pick intent. A lock always wins and ban-phase selections are ignored."""
    active = state.get("active") or {}
    if active.get("kind") == "ban":
        return {}
    return {row["role"]: row["hover"] for row in state.get("our_team", []) if row.get("role") in ROLES and row.get("hover") and not row.get("locked")}


def _state_for_test(state: dict) -> dict:
    """Ignore auto-selected ally bots while preserving the local/known humans.

    Versus-AI reveals four ally bots immediately.  Those champions are useful for testing LCU
    ingestion, but they must not become fake locks in the five real-player draft we are previewing.
    Enemy bots stay intact so the live matchup layer can still be tested against all five reveals.
    """
    local_cell = state.get("local_cell_id")
    out = dict(state)
    team = []
    for original in state.get("our_team", []) or []:
        row = dict(original)
        is_local = bool(row.get("is_local")) or (local_cell is not None and row.get("cell_id") == local_cell)
        # In solo custom/practice testing, every non-local ally seat is scaffolding. Riot can
        # populate puuid/identity-like fields for bots, so identity fields are not proof of a human.
        if not is_local:
            row["locked"] = None
            row["hover"] = None
        team.append(row)
    out["our_team"] = team
    return out



def _apply_test_overrides(state: dict) -> dict:
    """Overlay-only custom/bot test events on a copy of the live state.

    These inputs are never used in Ranked Flex. They let a solo custom/bot lobby exercise the
    exact same backend with sequential bans/picks that Riot's bots normally reveal all at once.
    """
    out = dict(state)
    out["our_team"] = [dict(row) for row in (state.get("our_team") or [])]
    out["enemy_team"] = [dict(row) for row in (state.get("enemy_team") or [])]
    out["our_bans"] = list(state.get("our_bans") or [])
    out["enemy_bans"] = list(state.get("enemy_bans") or [])
    with _test_lock:
        test = {
            "bans": list(_test_overrides.get("bans") or []),
            "enemy": dict(_test_overrides.get("enemy") or {}),
            "ours": dict(_test_overrides.get("ours") or {}),
        }
    for champ in test["bans"]:
        if champ and champ not in out["enemy_bans"]:
            out["enemy_bans"].append(champ)

    def set_lock(team: list[dict], role: str, champion: str) -> None:
        row = next((item for item in team if item.get("role") == role), None)
        if row is None:
            row = {"role": role, "cell_id": 100 + len(team), "display_name": f"Test {role}"}
            team.append(row)
        row["locked"] = champion or None
        row["hover"] = None

    for role, champion in test["enemy"].items():
        if role in ROLES:
            set_lock(out["enemy_team"], role, champion)
    for role, champion in test["ours"].items():
        if role in ROLES:
            set_lock(out["our_team"], role, champion)
    out["test_overrides"] = test
    return out


def _set_test_overrides(payload: dict) -> dict:
    with _test_lock:
        if payload.get("clear"):
            _test_overrides["bans"] = []
            _test_overrides["enemy"] = {}
            _test_overrides["ours"] = {}
        if "ban" in payload:
            champ = str(payload.get("ban") or "").strip()
            if champ and champ not in _test_overrides["bans"]:
                _test_overrides["bans"].append(champ)
        if "unban" in payload:
            champ = str(payload.get("unban") or "").strip().casefold()
            _test_overrides["bans"] = [x for x in _test_overrides["bans"] if str(x).casefold() != champ]
        for key in ("enemy_pick", "our_pick"):
            item = payload.get(key)
            if isinstance(item, dict):
                role = str(item.get("role") or "")
                champion = str(item.get("champion") or "").strip()
                target = _test_overrides["enemy" if key == "enemy_pick" else "ours"]
                if role in ROLES:
                    if champion:
                        target[role] = champion
                    else:
                        target.pop(role, None)
        return {
            "bans": list(_test_overrides["bans"]),
            "enemy": dict(_test_overrides["enemy"]),
            "ours": dict(_test_overrides["ours"]),
        }

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
        player = (rec.get("players", {}).get(role) or {}) if role in ROLES else {}
        if player.get("_uncontrolled") and not player.get("_live_champion_confirmed"):
            continue
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

    # A captain may softly prefer one complete plan.  It only becomes #1 while it remains close
    # to the objectively best live score; once the gap grows, the engine wins automatically.
    preferred = _get_preference()
    preference_info = {"signature": preferred, "active": False, "promoted": False,
                       "gap": None, "max_gap": PREFERENCE_MAX_GAP}
    if preferred and recs:
        pref_index = next((i for i, rec in enumerate(recs) if _comp_signature(rec.get("champions")) == preferred), None)
        if pref_index is not None:
            preference_info["active"] = True
            best_score = float(recs[0].get("live_rank_score", 0.0) or 0.0)
            pref_score = float(recs[pref_index].get("live_rank_score", 0.0) or 0.0)
            gap = max(0.0, best_score - pref_score)
            preference_info["gap"] = round(gap, 2)
            recs[pref_index]["_preferred"] = True
            if pref_index and gap <= PREFERENCE_MAX_GAP:
                recs.insert(0, recs.pop(pref_index))
                preference_info["promoted"] = True

    locked_roles = {row["role"] for row in state.get("our_team", []) if row.get("locked") and row.get("role") in ROLES}
    picks = []
    seen = set()
    for rec in recs:
        next_row = next((
            row for row in rec.get("pick_order", [])
            if row.get("role") not in locked_roles
            and not (rec.get("players", {}).get(row.get("role")) or {}).get("_uncontrolled")
        ), None)
        if not next_row:
            continue
        champ = next_row["champion"]
        role = next_row["role"]
        if (champ, role) in seen:
            continue
        seen.add((champ, role))
        player_data = rec.get("players", {}).get(role) or {}
        player = player_data.get("display_name", role)
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
            "champion_id": _champion_id(champ),
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
            "player_id": int(player_data.get("user_id") or 0),
            "team_plan": rec.get("team_plan"),
            "why": why,
        })
        if len(picks) >= 6:
            break

    visual_test = "test_overrides" in state
    bans = []
    if recs:
        for item in (recs[0].get("counter_bans") or [])[:5]:
            if isinstance(item, str):
                bans.append({"champion": item, "champion_id": _champion_id(item), "score": 1.0, "reasons": ["protects the planned Intent draft"], "tags": ["intent"]})
            elif isinstance(item, dict):
                champ = item.get("champion") or item.get("name")
                if champ:
                    bans.append({"champion": champ, "champion_id": _champion_id(champ), "score": float(item.get("score", 1.0) or 1.0), "reasons": item.get("reasons") or [item.get("reason") or "protects the planned Intent draft"], "tags": list(item.get("tags") or ["intent"])})
    if visual_test:
        seen_bans = {str(row.get("champion") or "").casefold() for row in bans}
        for champ in _TEST_VISUAL_BANS:
            if champ.casefold() not in seen_bans:
                bans.append({"champion": champ, "champion_id": _champion_id(champ), "score": 1.0,
                             "reasons": ["visual test preview"], "tags": ["test-preview"]})
                seen_bans.add(champ.casefold())
            if len(bans) >= 5:
                break
        bans = bans[:5]

    live_by_role = {
        row["role"]: (row.get("locked") or row.get("hover"))
        for row in state.get("our_team", [])
        if row.get("role") in ROLES and (row.get("locked") or row.get("hover"))
    }
    def role_options_for(rec: dict, role: str) -> list[str]:
        options = [champion for champion in dict.fromkeys([
            (rec.get("champions") or {}).get(role),
            *((rec.get("role_alternatives") or {}).get(role) or [])[:2],
        ]) if champion]
        if visual_test:
            # Mixed custom/practice preview deliberately exercises all three layouts at once:
            # Top/ADC = 3 choices, Mid = 2, Jungle/Support = 1. Presentation only.
            wanted = int(_TEST_VISUAL_COUNTS.get(role, 3))
            if len(options) < wanted:
                player = (rec.get("players") or {}).get(role) or {}
                pool = list(((player.get("champions") or {}).get(role) or [])) + list(_TEST_VISUAL_OPTIONS.get(role, []))
                for champion in pool:
                    if champion and champion not in options:
                        options.append(champion)
                    if len(options) >= wanted:
                        break
            return options[:wanted]
        return options[:3]

    def counter_bans_for(rec: dict) -> list[dict]:
        rows = []
        for item in (rec.get("counter_bans") or [])[:5]:
            champ = item if isinstance(item, str) else (item.get("champion") or item.get("name"))
            if not champ:
                continue
            rows.append({
                "champion": champ,
                "champion_id": _champion_id(champ),
                "score": 1.0 if isinstance(item, str) else float(item.get("score", 1.0) or 1.0),
            })
        if visual_test:
            seen = {str(row["champion"]).casefold() for row in rows}
            for champ in _TEST_VISUAL_BANS:
                if champ.casefold() not in seen:
                    rows.append({"champion": champ, "champion_id": _champion_id(champ), "score": 1.0})
                    seen.add(champ.casefold())
                if len(rows) >= 5:
                    break
        return rows[:5]

    comps = []
    side_key = str(result.get("side") or state.get("side") or "blue").casefold()
    for rec in recs[:6]:
        annotated_order = []
        for index, original_row in enumerate(rec.get("pick_order") or []):
            row = dict(original_row)
            meta = _pick_group_meta(side_key, index + 1)
            row.setdefault("slot", index + 1)
            for key, value in meta.items():
                row.setdefault(key, value)
            annotated_order.append(row)
        comps.append({
            "score": float(rec.get("score", rec.get("composition_score", 0.0)) or 0.0),
            "live_score": round(float(rec.get("live_rank_score", rec.get("score", 0.0)) or 0.0), 2),
            "lane_adjustment": float(rec.get("live_lane_adjustment", 0.0) or 0.0),
            "lane_edges": list(rec.get("live_lane_edges") or []),
            "enemy_draft_adjustment": float(rec.get("live_enemy_adjustment", 0.0) or 0.0),
            "enemy_draft_reasons": list(rec.get("live_enemy_reasons") or []),
            "champions": rec.get("champions") or {},
            "champion_ids": {role: _champion_id((rec.get("champions") or {}).get(role)) for role in ROLES},
            "role_options": {role: role_options_for(rec, role) for role in ROLES},
            "role_option_ids": {
                role: [_champion_id(champion) for champion in role_options_for(rec, role)]
                for role in ROLES
            },
            "players": {role: (rec.get("players", {}).get(role) or {}).get("display_name", role) for role in ROLES},
            "player_ids": {role: int((rec.get("players", {}).get(role) or {}).get("user_id") or 0) for role in ROLES},
            "player_meta": {
                role: {
                    "uncontrolled": bool((rec.get("players", {}).get(role) or {}).get("_uncontrolled")),
                    "profile_name": (rec.get("players", {}).get(role) or {}).get("_profile_display_name"),
                    "live_account": (rec.get("players", {}).get(role) or {}).get("_live_account")
                                    or (rec.get("players", {}).get(role) or {}).get("display_name"),
                }
                for role in ROLES
            },
            "uncontrolled_roles": [
                role for role in ROLES if (rec.get("players", {}).get(role) or {}).get("_uncontrolled")
            ],
            "live_uncontrolled_champions": {
                role: live_by_role.get(role)
                for role in ROLES
                if (rec.get("players", {}).get(role) or {}).get("_uncontrolled") and live_by_role.get(role)
            },
            "live_uncontrolled_champion_ids": {
                role: _champion_id(live_by_role.get(role))
                for role in ROLES
                if (rec.get("players", {}).get(role) or {}).get("_uncontrolled") and live_by_role.get(role)
            },
            "team_plan": rec.get("team_plan"),
            "team_plan_grade": rec.get("team_plan_grade"),
            "team_plan_reasons": list(rec.get("team_plan_reasons") or []),
            "strengths": list(rec.get("strengths") or []),
            "gaps": list(rec.get("gaps") or []),
            "bot_lane_grade": rec.get("bot_lane_grade"),
            "bot_lane_reasons": list(rec.get("bot_lane_reasons") or []),
            "carry_plan": dict(rec.get("carry_plan") or {}),
            "player_fit_notes": list(rec.get("player_fit_notes") or []),
            "signature": _comp_signature(rec.get("champions")),
            "preferred": bool(rec.get("_preferred")),
            "pick_order": annotated_order,
            # Counter bans are calculated against this exact composition/pick order by the same
            # shared backend. The overlay shows only our side's five bans, never both teams' plans.
            "counter_bans": counter_bans_for(rec),
        })
    hover_now = dict(result.get("hovered_champions") or _hovered_by_role(state))
    current_pick_group = None
    if comps:
        locked_now = {
            row.get("role") for row in state.get("our_team", [])
            if row.get("role") in ROLES and row.get("locked")
        }
        order = comps[0].get("pick_order") or []
        remaining = [row for row in order if row.get("role") not in locked_now]
        if remaining:
            first = remaining[0]
            group_id = first.get("pick_group")
            group_rows = [row for row in order if row.get("pick_group") == group_id]
            members = []
            for row in group_rows:
                role = row.get("role")
                champion = row.get("champion")
                if role not in ROLES or not champion:
                    continue
                members.append({
                    "slot": row.get("slot"),
                    "role": role,
                    "champion": champion,
                    "champion_id": _champion_id(champion),
                    "status": "locked" if role in locked_now else ("hovered" if role in hover_now else "remaining"),
                })
            current_pick_group = {
                "group": group_id,
                "label": first.get("pick_group_label") or str(first.get("slot") or ""),
                "slots": list(first.get("pick_group_slots") or [first.get("slot")]),
                "interchangeable": len(first.get("pick_group_slots") or []) > 1,
                "members": members,
            }

    if visual_test and not picks and comps:
        local_cell = state.get("local_cell_id")
        sample_role = next((
            row.get("role") for row in state.get("our_team", [])
            if row.get("role") in ROLES and (row.get("is_local") or (local_cell is not None and row.get("cell_id") == local_cell))
        ), None) or "Jungle"
        sample_options = (comps[0].get("role_options") or {}).get(sample_role) or _TEST_VISUAL_OPTIONS.get(sample_role) or []
        if sample_options:
            champ = sample_options[0]
            picks.append({
                "role": sample_role, "champion": champ, "champion_id": _champion_id(champ),
                "score": comps[0].get("score", 0.0), "rank": comps[0].get("live_score", 0.0),
                "comp_score": comps[0].get("score", 0.0), "familiarity": "comfort", "risk_known": True,
                "flex_roles": [], "lane": [], "lane_adjustment": 0.0, "counter_risk": 0.0,
                "counter_risk_by": [], "blind_safe": True, "priority": 1, "source": "test-preview",
                "player": (comps[0].get("players") or {}).get(sample_role, sample_role), "player_id": 0,
                "team_plan": comps[0].get("team_plan"), "why": ["visual test preview"],
            })

    active = state.get("active") or {}
    roles_now = [row["role"] for row in state.get("our_team", []) if row.get("role") in ROLES and not row.get("locked")]
    enemy_locked = {row["role"]: row["locked"] for row in state.get("enemy_team", []) if row.get("role") in ROLES and row.get("locked")}

    # Visual-test HUD preview follows the #1 comp's planned reveal order, not this machine's
    # assigned role. Bot/custom ally locks are already stripped by _state_for_test(), so the HUD
    # behaves like a real draft: show pick #1 first, then advance as our test picks are locked.
    visual_test_pick = None
    if visual_test and comps:
        locked_test_roles = {
            row.get("role") for row in state.get("our_team", [])
            if row.get("role") in ROLES and row.get("locked")
        }
        next_row = next((
            row for row in (comps[0].get("pick_order") or [])
            if row.get("role") in ROLES
            and row.get("role") not in locked_test_roles
            and row.get("champion")
        ), None)
        if next_row:
            role = next_row["role"]
            champ = next_row["champion"]
            visual_test_pick = {
                "role": role,
                "champion": champ,
                "champion_id": _champion_id(champ),
                "source": "test-next-team-pick",
            }

    return {
        "headline": ("Ban now: " + bans[0]["champion"]) if active.get("kind") == "ban" and active.get("ours") and bans else (("Pick " + picks[0]["champion"]) if picks else "Intent draft ready"),
        "phase_kind": active.get("kind") or "idle",
        "side": side_key,
        "current_pick_group": current_pick_group,
        "roles_now": roles_now,
        "picking_now": bool(active.get("kind") == "pick" and active.get("ours")),
        "draft_complete": len(locked_roles) == 5,
        "our_locked": _locked_by_role(state),
        "our_hovers": hover_now,
        "our_hover_ids": {role: _champion_id(champion) for role, champion in hover_now.items()},
        "enemy_locked": enemy_locked,
        "our_bans": list(state.get("our_bans") or []),
        "enemy_bans": list(state.get("enemy_bans") or []),
        "test_overrides": dict(state.get("test_overrides") or {}),
        "visual_test": visual_test,
        "visual_test_pick": visual_test_pick,
        "preference": preference_info,
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
    known_profiles = _known_live_profile_count(state)
    party_inferred = party.get("party_size") is None and known_profiles >= 3
    # Custom and versus-AI lobbies are allowed for solo visual testing. Normal PvP supports
    # 3/4/5-player premades; one/two-player queues remain off. If the lobby endpoint vanished
    # before this helper started, three matched live profiles are sufficient evidence to continue.
    if not testing and not (party["party_confirmed"] or party_inferred):
        size = party.get("party_size")
        reason = "Draft Coach normal PvP needs a 3-5 player premade" if size is None else f"Draft Coach normal PvP needs a 3-5 player premade ({size}/5 currently)"
        return {"active": False, "disabled": True, "reason": reason, "known_profiles": known_profiles,
                "party_inferred": party_inferred, **merged_gate, **party}
    # Live helpers are champion-select-only. The server keeps a manual board state while idle;
    # never score that in the background or let it keep the overlays alive over the normal client.
    in_champ_select = bool(status.get("lcu_in_champ_select")) or state.get("source") == "simulation"
    if not in_champ_select:
        return {"active": False, "disabled": False, "reason": "Waiting for champion select", **merged_gate, **party}
    five = _current_five(state, fill_for_test=testing)
    if len(five) != 5:
        label = "test lineup" if testing else "live seats/roles"
        return {"active": False, "disabled": False, "reason": f"Waiting for all five {label}", "matched": len(five),
                "known_profiles": known_profiles, "party_inferred": party_inferred, **merged_gate, **party}
    effective_state = _apply_test_overrides(_state_for_test(state)) if testing else state
    locked = _locked_by_role(effective_state)
    unavailable = _unavailable(effective_state)
    hovered = _hovered_by_role(effective_state)
    role_players = _role_players_for_state(effective_state, five, testing=testing)
    cache_key = json.dumps({
        "side": state.get("side") or "blue",
        "active": state.get("active") or {},
        "our_bans": effective_state.get("our_bans") or [],
        "enemy_bans": effective_state.get("enemy_bans") or [],
        "locked": locked,
        "hovered": hovered,
        "role_players": role_players,
        "enemy_locked": [row.get("locked") for row in effective_state.get("enemy_team", []) if row.get("locked")],
        "enemy_hovers": [(row.get("role"), row.get("hover")) for row in effective_state.get("enemy_team", []) if row.get("hover")],
        "our_hovers": [(row.get("role"), row.get("hover"), row.get("user_id")) for row in effective_state.get("our_team", []) if row.get("hover")],
        "players": [int(row["user_id"]) for row in five],
        "pug_pools": [
            (int(row["user_id"]), row.get("champions") or {}) for row in five if row.get("_uncontrolled")
        ],
        "preferred_signature": _get_preference(),
    }, sort_keys=True)
    global _response_cache_key, _response_cache_value, _response_cache_at
    with _compute_lock:
        now = time.time()
        if cache_key == _response_cache_key and _response_cache_value is not None:
            return _response_cache_value
        result = recommend_live_drafts(
            five,
            unavailable=unavailable,
            locked_champions=locked,
            hovered_champions=hovered,
            role_players=role_players,
            enemy_champions=[
                row["locked"] for row in effective_state.get("enemy_team", [])
                if row.get("locked")
            ],
            side=state.get("side") or "blue",
            limit=6,
            rotation_token=None,
        )
        response = {"active": True, "disabled": False, "reason": "Intent backend active", "testing": testing,
                    "known_profiles": known_profiles, "party_inferred": party_inferred,
                    "guidance": _guidance_from_result(result, effective_state), **merged_gate, **party}
        _response_cache_key = cache_key
        _response_cache_value = response
        _response_cache_at = now
        return response


class Handler(BaseHTTPRequestHandler):
    def _reply(self, obj: dict, status: int = 200) -> None:
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/guidance":
            if _cloud_mode == "auto":
                with _auto_lock:
                    auto_host = _auto_is_host
                if auto_host:
                    data = dict(build_response())
                    data["sync"] = _auto_sync_meta("auto-host")
                    self._reply(data)
                elif not _auto_identity().get("in_champ_select"):
                    data = dict(build_response())
                    data["sync"] = _auto_sync_meta("auto-idle")
                    self._reply(data)
                else:
                    try:
                        data = _cloud_get()
                        data["sync"] = _auto_sync_meta("auto-follower")
                        self._reply(data)
                    except Exception as exc:
                        self._reply({"active": False, "disabled": False,
                                     "reason": "Waiting for the Flexers draft captain",
                                     "sync": _auto_sync_meta("auto-follower"), "error": str(exc)}, 503)
            elif _cloud_mode == "follow":
                try:
                    self._reply(_cloud_get())
                except Exception as exc:
                    self._reply({"active": False, "disabled": False, "reason": f"Internet draft sync unavailable: {exc}",
                                 "sync": {"mode": "cloud-follower", "source": _cloud_base}}, 503)
            elif _upstream_base:
                try:
                    self._reply(_upstream_get())
                except Exception as exc:
                    self._reply({"active": False, "disabled": False, "reason": f"Coach sync unavailable: {exc}",
                                 "sync": {"mode": "follower", "source": _upstream_base}}, 503)
            else:
                data = build_response()
                if _cloud_mode == "host":
                    data = dict(data); data["sync"] = {"mode": "cloud-host", "source": _cloud_base}
                self._reply(data)
            return
        if path == "/api/preference":
            self._reply({"signature": _get_preference(), "max_gap": PREFERENCE_MAX_GAP})
            return
        if path == "/api/test":
            with _test_lock:
                current = {"bans": list(_test_overrides["bans"]), "enemy": dict(_test_overrides["enemy"]), "ours": dict(_test_overrides["ours"])}
            self._reply({"ok": True, "test": current})
            return
        self._reply({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("expected object")
        except Exception as exc:
            self._reply({"error": str(exc)}, 400)
            return
        if path == "/api/preference":
            signature = payload.get("signature")
            try:
                if _cloud_mode in ("follow", "auto"):
                    self._reply(_cloud_preference(signature))
                elif _upstream_base:
                    self._reply(_upstream_preference(signature))
                else:
                    current = _set_preference(signature)
                    self._reply({"ok": True, "signature": current, "max_gap": PREFERENCE_MAX_GAP})
            except Exception as exc:
                self._reply({"error": str(exc)}, 503)
            return
        if path != "/api/test":
            self._reply({"error": "not found"}, 404)
            return
        global _response_cache_key
        current = _set_test_overrides(payload)
        _response_cache_key = None
        self._reply({"ok": True, "test": current})

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
    # Separate singleton lock: protects against double-clicks / stale launcher races even on
    # Windows builds where the HTTP listener can briefly be reused.
    instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        instance_lock.bind(("127.0.0.1", 18766))
    except OSError:
        instance_lock.close()
        return
    _load_cached_signups()
    # Serve the cached signup data immediately. Northflank refresh happens in the background;
    # champ-select overlays should not wait up to 25 seconds just to bind this local port.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=background_refresh, args=(server,), daemon=True).start()
    print(f"Intent live draft bridge: http://127.0.0.1:{args.port}/api/guidance")
    server.serve_forever()


if __name__ == "__main__":
    main()
