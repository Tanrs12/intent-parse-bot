from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PLAYER_PROFILE_PATH = Path(__file__).with_name("data") / "league_player_profiles.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36"
DEFAULT_MATCH_LIMITS = {"RANKED": 30, "NORMAL": 15}
MAX_STORED_MATCHES = {"RANKED": 600, "NORMAL": 300}
MATCH_HISTORY_SLEEP = 0.18
ACCOUNT_SLEEP = 0.20
_GAMES_ACTION_ID: str | None = None


def fetch(url: str, retries: int = 3) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last = exc
            time.sleep(0.7 * (attempt + 1))
    raise last


def _normalise_profile_url(raw_url: str) -> str:
    parsed = urllib.parse.urlsplit(str(raw_url))
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/-._~")
    return urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc or "op.gg", path, parsed.query, parsed.fragment))


def _account_label(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1])


def decoded_rsc(html: str) -> str:
    parts = []
    pattern = re.compile(r'<script>self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
    for match in pattern.finditer(html):
        try:
            parts.append(json.loads('"' + match.group(1) + '"'))
        except Exception:
            continue
    return "\n".join(parts)


def extract_puuid(html: str) -> str | None:
    text = decoded_rsc(html)
    match = re.search(r'"puuid":"([^"]+)"', text)
    return match.group(1) if match else None


def discover_games_action_id(html: str, page_url: str) -> str:
    global _GAMES_ACTION_ID
    if _GAMES_ACTION_ID:
        return _GAMES_ACTION_ID
    script_srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    for src in reversed(script_srcs):
        try:
            js = fetch(urllib.parse.urljoin(page_url, src), retries=1)
        except Exception:
            continue
        for match in re.finditer(r'["\']getGames["\']', js):
            window = js[max(0, match.start() - 1200): match.end() + 100]
            ids = re.findall(r'["\']([0-9a-f]{40,64})["\']', window)
            if ids:
                _GAMES_ACTION_ID = ids[-1]
                return _GAMES_ACTION_ID
    raise RuntimeError("could not discover OP.GG getGames action id")


def _post_games_action(page_url: str, action_id: str, puuid: str, game_type: str, ended_at: str = "") -> dict:
    payload = [{
        "locale": "en",
        "region": "euw",
        "puuid": puuid,
        "gameType": game_type,
        "endedAt": ended_at,
        "champion": "",
    }]
    req = urllib.request.Request(
        page_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Next-Action": action_id,
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "text/x-component",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        body = response.read().decode("utf-8", errors="replace")
    for line in body.splitlines():
        if ":" not in line:
            continue
        _, candidate = line.split(":", 1)
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("data"), list) and isinstance(obj.get("meta"), dict):
            return obj
    raise RuntimeError("could not parse OP.GG getGames response")


def _compact_match(game: dict, puuid: str, account: str) -> dict | None:
    team = (game.get("team_blue") or []) + (game.get("team_red") or [])
    participant = next(
        (row for row in team if (row.get("summoner") or {}).get("puuid") == puuid),
        None,
    ) or {}
    pstats = participant.get("stats") or {}
    display_stats = game.get("stats") or {}
    kda = display_stats.get("kda") or {}
    cs = display_stats.get("cs") or {}
    duration_min = max(1.0, float(game.get("game_length", 0) or 0) / 60.0)
    damage = float(pstats.get("total_damage_dealt_to_champions", 0) or 0)
    own_team_key = participant.get("team_key")
    own_team = [row for row in team if row.get("team_key") == own_team_key]
    team_damage = sum(float((row.get("stats") or {}).get("total_damage_dealt_to_champions", 0) or 0) for row in own_team)
    kills = int(kda.get("kill", pstats.get("kill", 0)) or 0)
    deaths = int(kda.get("death", pstats.get("death", 0)) or 0)
    assists = int(kda.get("assist", pstats.get("assist", 0)) or 0)
    champion_name = str((game.get("champion") or {}).get("name") or (participant.get("champion") or {}).get("name") or "")
    position = game.get("position") or participant.get("position")
    if not champion_name or not position:
        return None
    queue = str((game.get("game_type") or {}).get("game_type") or "")
    queue_group = "RANKED" if queue in {"RANKED", "SOLORANKED", "FLEXRANKED", "RANKED5S"} else queue
    return {
        "account": account,
        "id": game.get("id"),
        "created_at": game.get("created_at"),
        "queue": queue,
        "queue_group": queue_group,
        "map": game.get("game_map"),
        "result": game.get("game_result"),
        "champion": champion_name,
        "position": position,
        "game_length_seconds": int(game.get("game_length", 0) or 0),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(1, deaths), 2),
        "kp": float(display_stats.get("killParticipation", 0) or 0),
        "op_score": float(display_stats.get("op_score", pstats.get("op_score", 0)) or 0),
        "lane_score": float(display_stats.get("lane_score", pstats.get("lane_score", 0)) or 0),
        "cs_per_min": round(float(cs.get("csPerMin", 0) or 0), 2),
        "gold_per_min": round(float(pstats.get("gold_earned", 0) or 0) / duration_min, 1),
        "dpm": round(damage / duration_min, 1),
        "damage_share": round(damage / team_damage * 100.0, 1) if team_damage > 0 else 0.0,
        "vision_score": float(pstats.get("vision_score", 0) or 0),
    }


def fetch_match_history(page_url: str, html: str, account: str, game_type: str, max_games: int) -> list[dict]:
    global _GAMES_ACTION_ID
    puuid = extract_puuid(html)
    if not puuid:
        return []
    action_id = discover_games_action_id(html, page_url)
    ended_at = ""
    rows: list[dict] = []
    seen_ids = set()
    seen_cursors = set()
    retried_action = False
    while len(rows) < max_games:
        try:
            payload = _post_games_action(page_url, action_id, puuid, game_type, ended_at)
        except Exception:
            if ended_at or retried_action:
                raise
            _GAMES_ACTION_ID = None
            action_id = discover_games_action_id(html, page_url)
            retried_action = True
            payload = _post_games_action(page_url, action_id, puuid, game_type, ended_at)
        games = payload.get("data") or []
        meta = payload.get("meta") or {}
        if games:
            for game in games:
                game_id = game.get("id")
                if game_id in seen_ids:
                    continue
                seen_ids.add(game_id)
                compact = _compact_match(game, puuid, account)
                if compact and compact.get("map") == "SUMMONERS_RIFT":
                    rows.append(compact)
                    if len(rows) >= max_games:
                        break
            next_cursor = str(games[-1].get("created_at") or "")
        else:
            if meta.get("first_game_created_at"):
                break
            next_cursor = str(meta.get("last_game_created_at") or "")
            if not next_cursor:
                break
        if not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        ended_at = next_cursor
        time.sleep(MATCH_HISTORY_SLEEP)
    return rows[:max_games]


def aggregate_match_history(matches: list[dict]) -> dict:
    queues: dict[str, dict] = {}
    champions: dict[str, dict] = {}
    positions: dict[str, dict] = {}

    def add_bucket(bucket: dict, row: dict):
        bucket["games"] = bucket.get("games", 0) + 1
        bucket["wins"] = bucket.get("wins", 0) + int(row.get("result") == "WIN")
        for key in ("kda", "kp", "op_score", "lane_score", "cs_per_min", "gold_per_min", "dpm", "damage_share", "vision_score"):
            bucket[key + "_sum"] = bucket.get(key + "_sum", 0.0) + float(row.get(key, 0) or 0)

    for row in matches:
        queue = str(row.get("queue_group") or row.get("queue") or "UNKNOWN")
        champion = str(row.get("champion") or "Unknown")
        position = str(row.get("position") or "UNKNOWN")
        add_bucket(queues.setdefault(queue, {}), row)
        add_bucket(champions.setdefault(champion, {"positions": {}, "queues": {}}), row)
        champions[champion]["positions"][position] = champions[champion]["positions"].get(position, 0) + 1
        champions[champion]["queues"][queue] = champions[champion]["queues"].get(queue, 0) + 1
        add_bucket(positions.setdefault(position, {}), row)

    def finish(bucket: dict) -> dict:
        games = max(1, int(bucket.get("games", 0) or 0))
        out = {"games": int(bucket.get("games", 0) or 0), "wins": int(bucket.get("wins", 0) or 0)}
        out["win_rate"] = round(out["wins"] / games * 100.0, 1) if out["games"] else None
        for key in ("kda", "kp", "op_score", "lane_score", "cs_per_min", "gold_per_min", "dpm", "damage_share", "vision_score"):
            out[key] = round(float(bucket.get(key + "_sum", 0.0)) / games, 2)
        return out

    champion_rows = []
    for champion, bucket in champions.items():
        base = finish(bucket)
        base.update({"champion": champion, "positions": bucket.get("positions", {}), "queues": bucket.get("queues", {})})
        champion_rows.append(base)
    champion_rows.sort(key=lambda row: row["games"], reverse=True)
    return {
        "queues": {queue: finish(bucket) for queue, bucket in queues.items()},
        "positions": {position: finish(bucket) for position, bucket in positions.items()},
        "champions": champion_rows,
    }


def _match_key(row: dict) -> str:
    game_id = row.get("id")
    if game_id is not None:
        return f"id:{game_id}"
    return "fallback:" + "|".join(
        str(row.get(key) or "") for key in ("account", "created_at", "champion", "position", "queue_group")
    )


def _merge_recent_matches(old_rows: list[dict], fresh_rows: list[dict]) -> list[dict]:
    merged = {_match_key(row): row for row in old_rows if isinstance(row, dict)}
    for row in fresh_rows:
        merged[_match_key(row)] = row
    ordered = sorted(merged.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True)
    counts = {"RANKED": 0, "NORMAL": 0}
    kept = []
    for row in ordered:
        queue = str(row.get("queue_group") or row.get("queue") or "")
        if queue in {"SOLORANKED", "FLEXRANKED", "RANKED5S"}:
            queue = "RANKED"
            row["queue_group"] = queue
        limit = MAX_STORED_MATCHES.get(queue)
        if limit is None or counts[queue] >= limit:
            continue
        counts[queue] += 1
        kept.append(row)
    return kept


def refresh_recent_player_data(
    path: str | Path | None = None,
    match_limits: dict[str, int] | None = None,
) -> dict:
    """Refresh recent Ranked/Normal SR games for every OP.GG account in the player snapshot.

    Historical season aggregates/mastery stay intact.  New games are merged into the existing
    profile and only recent-match summaries are rebuilt, keeping this safe to run frequently.
    """
    global _GAMES_ACTION_ID
    _GAMES_ACTION_ID = None
    target = Path(path) if path else PLAYER_PROFILE_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    players = data.get("players", {})
    limits = dict(DEFAULT_MATCH_LIMITS)
    if match_limits:
        limits.update({str(k).upper(): max(0, int(v)) for k, v in match_limits.items()})

    refreshed_players = 0
    refreshed_accounts = 0
    new_game_rows = 0
    errors: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    for uid, profile in players.items():
        fresh_rows: list[dict] = []
        successful_accounts = 0
        account_errors = []
        urls = list(dict.fromkeys(str(url) for url in profile.get("opgg_urls", []) if url))
        for raw_url in urls:
            url = _normalise_profile_url(raw_url)
            account = _account_label(url)
            try:
                html = fetch(url)
                if not extract_puuid(html):
                    raise RuntimeError("OP.GG page did not expose a PUUID")
            except Exception as exc:
                msg = f"{profile.get('discord_name', uid)} / {account}: {type(exc).__name__}: {exc}"
                account_errors.append(msg)
                errors.append(msg)
                time.sleep(ACCOUNT_SLEEP)
                continue

            account_rows = []
            queue_successes = 0
            for queue, max_games in limits.items():
                if max_games <= 0:
                    continue
                try:
                    account_rows.extend(fetch_match_history(url, html, account, queue, max_games))
                    queue_successes += 1
                except Exception as exc:
                    msg = (
                        f"{profile.get('discord_name', uid)} / {account} / {queue}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    account_errors.append(msg)
                    errors.append(msg)
            if queue_successes:
                fresh_rows.extend(account_rows)
                successful_accounts += 1
                refreshed_accounts += 1
            time.sleep(ACCOUNT_SLEEP)

        if not successful_accounts:
            continue

        old_rows = list(profile.get("recent_matches", []))
        old_keys = {_match_key(row) for row in old_rows if isinstance(row, dict)}
        merged = _merge_recent_matches(old_rows, fresh_rows)
        new_game_rows += sum(1 for row in fresh_rows if _match_key(row) not in old_keys)
        profile["recent_matches"] = merged
        profile["recent_match_summary"] = aggregate_match_history(merged)
        summary = profile.setdefault("summary", {})
        queues = profile["recent_match_summary"].get("queues", {})
        summary["individual_ranked_games"] = int(queues.get("RANKED", {}).get("games", 0) or 0)
        summary["individual_normal_games"] = int(queues.get("NORMAL", {}).get("games", 0) or 0)
        profile["recent_refresh"] = {
            "refreshed_at": now,
            "accounts_ok": successful_accounts,
            "accounts_total": len(urls),
            "fresh_rows_seen": len(fresh_rows),
            "errors": account_errors,
        }
        refreshed_players += 1

    if refreshed_players:
        data["recent_matches_refreshed_at"] = now
        source = data.setdefault("source", {})
        source["recent_match_auto_refresh"] = {
            "site": "OP.GG",
            "match_limits_per_account": limits,
            "stored_match_caps_per_player": MAX_STORED_MATCHES,
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    return {
        "refreshed_at": now,
        "players": refreshed_players,
        "accounts": refreshed_accounts,
        "new_games": new_game_rows,
        "errors": errors,
    }


if __name__ == "__main__":
    result = refresh_recent_player_data()
    print(json.dumps(result, indent=2, ensure_ascii=False))
