"""Small-scale OP.GG official MCP top-three strong/weak counter collection.

Provider's MCP serves the selected-field response as a compact class-like
string, NOT JSON. Parse a fixed, narrow record grammar. NEVER eval/exec it.

One permitted private, noncommercial query collects up to 6 directional pairs.
No patch or rank filter is attested; values remain *rolling references* that
must not influence automatic picks. No site scraping or bulk requests.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from league_opgg_mcp_reference import (
    CACHE_PATH, MCP_ENDPOINT, SOURCE_URL, MIN_REQUEST_GAP_SECONDS,
    _api_name, _filename, _key, get_cached,
)

COUNTER_FIELD = "data.strong_counters[].{champion_id,champion_name,counter_win_rate,my_win_rate,play,win,win_rate}"
WEAK_FIELD = "data.weak_counters[].{champion_id,champion_name,counter_win_rate,my_win_rate,play,win,win_rate}"

_RE = re.compile(
    r'StrongCounter\((\d+),"([^"]{1,48})",(\d+),(\d+),'
    r'(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\)'
)


def parse_counter_summary(text: str, champion: str, role: str,
                          *, game_mode: str = "ranked") -> list[dict]:
    """Parse the exact official selected-fields response, with no code execution."""
    if not isinstance(text, str) or "LolGetChampionAnalysis(Data(" not in text:
        raise ValueError("OP.GG champion analysis did not return expected summary")
    tail = text.split("LolGetChampionAnalysis(Data(", 1)[1]
    arrays = re.findall(r"\[([^\[\]]*)\]", tail)
    if len(arrays) != 2:
        raise ValueError("OP.GG response has unexpected counter-list structure")
    now = time.time()
    found = []
    for group, section in zip(("strong", "weak"), arrays):
        matches = list(_RE.finditer(section))
        if (len(matches) != section.count("StrongCounter(") or
            re.sub(r"[,\s]+", "", _RE.sub("", section))):
            raise ValueError("OP.GG returned an unsupported counter record")
        for match in matches:
            champion_id, enemy, games, wins, my_wr, counter_wr, win_rate = match.groups()
            games, wins = int(games), int(wins)
            my_wr, counter_wr = float(my_wr), float(counter_wr)
            if (games < 1 or wins > games or wins < 0 or
                not 0 <= my_wr <= 1 or not 0 <= counter_wr <= 1 or
                abs(my_wr - wins / games) > 0.03 or
                _key(enemy) == _key(champion)):
                raise ValueError("OP.GG returned inconsistent matchup counts")
            found.append({
                "schema_version": 1,
                "source": "OP.GG official MCP",
                "source_method": "lol_get_champion_analysis",
                "source_url": SOURCE_URL,
                "source_patch": None,
                "rank_filter": None,
                "queue_filter": game_mode,
                "metadata_warning": "Rank/patch not attested by OP.GG; rolling reference only.",
                "role": role, "ours": champion, "enemy": enemy,
                "games": games, "wins": wins,
                "head_to_head_win_rate": round(100 * wins / games, 2),
                "role_baseline_win_rate": None,
                "residual_pp": None,
                "provider_group": group,
                "retrieved_epoch": now,
                "retrieved_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            })
    if len(found) > 6 or len({(_key(x["ours"]), _key(x["enemy"])) for x in found}) != len(found):
        raise ValueError("unexpected duplicate/oversized counter summary")
    return found


def request_summary(role: str, champion: str, *, game_mode="ranked",
                    timeout: int = 25) -> list[dict]:
    if role not in ("Top", "Jungle", "Mid", "ADC", "Support") or game_mode not in ("ranked", "flex"):
        raise ValueError("unsupported role or game mode")
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "lol_get_champion_analysis",
            "arguments": {
                "game_mode": game_mode, "champion": _api_name(champion),
                "position": role.lower(), "desired_output_fields": [COUNTER_FIELD, WEAK_FIELD],
            },
        },
    }).encode("utf8")
    request = urllib.request.Request(
        MCP_ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": "INTentDraftCoach-PrivateNonCommercial/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(2_000_000))
    message = (payload.get("result") or {})
    body = next((r.get("text") for r in message.get("content") or []
                 if r.get("type") == "text"), None)
    if not body:
        raise ValueError("OP.GG champion analysis returned no counters")
    return parse_counter_summary(body, champion, role, game_mode=game_mode)


def save_summary(rows: list[dict], *, folder: Path | None = None) -> tuple[int, int]:
    """Preserve richer pair-specific observations already cached."""
    folder = Path(folder) if folder else CACHE_PATH
    folder.mkdir(parents=True, exist_ok=True)
    saved = skipped = 0
    for row in rows:
        role, ours, enemy = row["role"], row["ours"], row["enemy"]
        if get_cached(role, ours, enemy, folder=folder):
            skipped += 1
            continue
        target = folder / _filename(role, ours, enemy)
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf8")
        temp.replace(target)
        saved += 1
    return saved, skipped
