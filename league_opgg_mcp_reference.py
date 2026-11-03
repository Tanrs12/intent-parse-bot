"""Small-scale OP.GG *official MCP* matchup reference, on demand and cached.

Requires no scraping. OP.GG's written reply for this private noncommercial
project permits small attributed use, not an unrestricted bulk mirror.
The MCP lane guide returns a recorded W/N vs a named enemy but no trustworthy
explicit patch/rank/queue label in this response; NEVER call it 26.21 data.
The resulting observation is a reference, NOT an auto-draft scoring signal.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MCP_ENDPOINT = "https://mcp-api.op.gg/mcp"
SOURCE_URL = "https://op.gg/open-source/opgg-mcp"
CACHE_PATH = Path(os.getenv("DRAFTCOACH_OPGG_CACHE_DIR")
                  or (Path(os.getenv("LOCALAPPDATA") or Path.home()) /
                      "FlexersDraftCoach" / "opgg-mcp"))
TTL_SECONDS = 24 * 3600
MIN_REQUEST_GAP_SECONDS = 3.0
ROLES = {"Top": "top", "Jungle": "jungle", "Mid": "mid",
         "ADC": "adc", "Support": "support"}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _api_name(value: str) -> str:
    # OP.GG's MCP champion keys follow compact Riot champion IDs for
    # punctuation-heavy names. CHO_GATH was silently rejected by its service.
    compact = _key(value)
    aliases = {
        "chogath": "CHOGATH", "drmundo": "DR_MUNDO",
        "kogmaw": "KOGMAW", "kaisa": "KAISA",
        "reksai": "REKSAI", "khazix": "KHAZIX",
        "ksante": "KSANTE", "velkoz": "VELKOZ",
        "missfortune": "MISSFORTUNE",
    }
    return aliases.get(compact, re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_"))


def _filename(role: str, ours: str, enemy: str) -> str:
    return "-".join((_key(role), _key(ours), _key(enemy))) + ".json"


def get_cached(role: str, ours: str, enemy: str, *,
               folder: Path | None = None, max_age: int = TTL_SECONDS) -> dict:
    folder = Path(folder) if folder else CACHE_PATH
    path = folder / _filename(role, ours, enemy)
    try:
        row = json.loads(path.read_text(encoding="utf8"))
        if (row.get("source") != "OP.GG official MCP" or
            _key(row.get("role", "")) != _key(role) or
            _key(row.get("ours", "")) != _key(ours) or
            _key(row.get("enemy", "")) != _key(enemy)):
            return {}
        if time.time() - float(row.get("retrieved_epoch", 0)) > max_age:
            return {}
        return row
    except (ValueError, OSError, TypeError):
        return {}


def cached_for_enemy(role: str, enemy: str, *,
                     folder: Path | None = None, max_age: int = TTL_SECONDS) -> dict[str, dict]:
    """Read locally cached OP.GG references, NEVER make HTTP requests.

    Returned keys are normalized champion names. These rolling, unversioned
    references are permitted only in the advice presentation, not the scorer.
    """
    if role not in ROLES or not enemy:
        return {}
    folder = Path(folder) if folder else CACHE_PATH
    rows = {}
    try:
        paths = sorted(folder.glob(f"{_key(role)}-*-{_key(enemy)}.json"))
    except OSError:
        return {}
    for path in paths[:300]:
        try:
            content = json.loads(path.read_text(encoding="utf8"))
            name = str(content.get("ours") or "")
            if not name:
                continue
            verified = get_cached(role, name, enemy, folder=folder, max_age=max_age)
            if verified:
                rows[_key(name)] = verified
        except (OSError, ValueError, TypeError):
            continue
    return rows


def extract_record(payload: dict, role: str, ours: str, enemy: str) -> dict:
    if (role not in ROLES or _key(payload.get("position", "")) != _key(ROLES[role])
        or _key(payload.get("my_champion", "")) != _key(ours)
        or _key(payload.get("opponent_champion", "")) != _key(enemy)):
        raise ValueError("OP.GG returned a different champion or position")
    data = payload.get("data") or {}
    row = next((v for v in data.get("counters") or []
                if _key(v.get("champion_name", "")) == _key(enemy)), None)
    if not row:
        raise ValueError("OP.GG did not return a numeric head-to-head observation")
    games = int(row.get("play") or 0)
    wins = int(row.get("win") or 0)
    if games < 1 or wins < 0 or wins > games:
        raise ValueError("invalid OP.GG matchup count")
    baseline = next((p.get("stats", {}).get("win_rate") for p in
                     (data.get("summary") or {}).get("positions") or []
                     if _key(p.get("name", "")) == _key(ROLES[role])), None)
    baseline = (round(100 * float(baseline), 2)
                if baseline is not None and 0 <= float(baseline) <= 1 else None)
    tip = str(data.get("opponent_champion_tip") or "").strip()
    # Attribution/link rather than copying the whole guide.
    return {
        "schema_version": 1,
        "source": "OP.GG official MCP",
        "source_url": SOURCE_URL,
        "source_patch": None,
        "rank_filter": None,
        "queue_filter": None,
        "metadata_warning": "Patch/rank/queue not attested by the lane-guide response; rolling reference only.",
        "role": role, "ours": ours, "enemy": enemy,
        "games": games, "wins": wins, "head_to_head_win_rate": round(100 * wins / games, 2),
        "role_baseline_win_rate": baseline,
        "residual_pp": round(100 * wins / games - baseline, 2) if baseline is not None else None,
        "tip_excerpt": tip[:160],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "retrieved_epoch": time.time(),
    }


def request_pair(role: str, ours: str, enemy: str, *, timeout: int = 25) -> dict:
    if role not in ROLES:
        raise ValueError("unknown role")
    args = {"position": ROLES[role], "my_champion": _api_name(ours),
            "opponent_champion": _api_name(enemy), "lang": "en_US"}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "lol_get_lane_matchup_guide",
                                  "arguments": args}}).encode("utf8")
    req = urllib.request.Request(
        MCP_ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": "INTentDraftCoach-PrivateNonCommercial/1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read(2_000_000))
    response = result.get("result") or {}
    contents = response.get("content") or []
    text = next((r.get("text") for r in contents if r.get("type") == "text"), None)
    if not text:
        message = str((result.get("error") or {}).get("message")
                      or response.get("message") or "empty MCP content")[:140]
        raise ValueError("OP.GG MCP returned no matchup result: " + message)
    payload = json.loads(text)
    return extract_record(payload, role, ours, enemy)


def fetch_cached(role: str, ours: str, enemy: str, *,
                 folder: Path | None = None) -> dict:
    folder = Path(folder) if folder else CACHE_PATH
    prior = get_cached(role, ours, enemy, folder=folder)
    if prior:
        return prior
    result = request_pair(role, ours, enemy)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / _filename(role, ours, enemy)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf8")
    temp.replace(target)
    return result
