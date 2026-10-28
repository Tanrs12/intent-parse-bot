from __future__ import annotations

import asyncio
import html
import itertools
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aiohttp

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
DATA_PATH = Path(__file__).with_name("data") / "league_recent_pro.json"
GOL_BASE = "https://gol.gg"
TOURNAMENTS = ("Demacia Cup Global Invitational 2026", "Worlds 2026 Play-In", "Worlds 2026 Main Event")
USER_AGENT = "Mozilla/5.0 (compatible; IntentLeagueBot/1.0)"
MAX_GAMES = 140
MAX_AGE_DAYS = 50

ALIASES = {
    "MonkeyKing": "Wukong",
    "KSante": "K'Sante",
    "Kaisa": "Kai'Sa",
    "RekSai": "Rek'Sai",
    "Belveth": "Bel'Veth",
    "Chogath": "Cho'Gath",
    "DrMundo": "Dr. Mundo",
    "JarvanIV": "Jarvan IV",
    "Khazix": "Kha'Zix",
    "KogMaw": "Kog'Maw",
    "Velkoz": "Vel'Koz",
}


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _normalise_champion(name: str) -> str:
    return ALIASES.get(name.strip(), name.strip())


def _parse_patch(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return ()


def _parse_matchlist(page: str, tournament: str) -> list[dict]:
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.I | re.S)
    matches: list[dict] = []
    for row in rows:
        link = re.search(r"/game/stats/(\d+)/(page-summary|page-game)/", row, re.I)
        if not link:
            continue
        raw_cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.I | re.S)
        if len(raw_cells) < 7:
            continue
        cells = [_text(body) for _, body in raw_cells]
        team1 = cells[1]
        team2 = cells[3]
        patch = cells[5]
        date_value = cells[6]
        team1_attrs = raw_cells[1][0].casefold()
        team2_attrs = raw_cells[3][0].casefold()
        if "text_victory" in team1_attrs:
            winner = team1
        elif "text_victory" in team2_attrs:
            winner = team2
        else:
            continue
        matches.append({
            "game_id": int(link.group(1)),
            "page_kind": link.group(2).casefold(),
            "tournament": tournament,
            "team1": team1,
            "team2": team2,
            "winner": winner,
            "patch": patch,
            "date": date_value,
        })
    return matches


def _parse_series_game_ids(page: str) -> list[int]:
    found: list[int] = []
    for raw in re.findall(r"/game/stats/(\d+)/page-game/", page, re.I):
        game_id = int(raw)
        if game_id not in found:
            found.append(game_id)
    return found


def _parse_game_picks(page: str) -> list[str]:
    picks: list[str] = []
    for tag in re.findall(r"<img\b[^>]*>", page, re.I | re.S):
        if "champions_icon/" not in tag:
            continue
        class_match = re.search(r"\bclass=[\"']([^\"']+)[\"']", tag, re.I)
        classes = set(class_match.group(1).split()) if class_match else set()
        # GoL renders bans with champion_icon_medium and the ten actual picks with
        # the exact champion_icon class. Filtering by class avoids learning bans.
        if "champion_icon" not in classes:
            continue
        alt = re.search(r"\balt=[\"']([^\"']+)[\"']", tag, re.I)
        if alt:
            picks.append(_normalise_champion(html.unescape(alt.group(1))))
        if len(picks) == 10:
            break
    return picks


def _parse_game_teams(page: str) -> tuple[str, str, str] | None:
    rows = re.findall(
        r"<a[^>]+href=[\"']\.\./teams/team-stats/[^\"']+[\"'][^>]*>(.*?)</a>\s*-\s*(WIN|LOSS)",
        page,
        re.I | re.S,
    )
    if len(rows) < 2:
        return None
    team1, result1 = _text(rows[0][0]), rows[0][1].upper()
    team2, result2 = _text(rows[1][0]), rows[1][1].upper()
    if result1 == result2:
        return None
    return team1, team2, team1 if result1 == "WIN" else team2


def _game_weight(game: dict, latest_patch: tuple[int, ...], now: datetime) -> float:
    try:
        played = datetime.strptime(game["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - played).total_seconds() / 86400.0)
    except (ValueError, TypeError, KeyError):
        age_days = 30.0
    recency = math.pow(0.5, age_days / 14.0)
    patch = _parse_patch(str(game.get("patch", "")))
    if patch and latest_patch and patch == latest_patch:
        patch_weight = 1.0
    elif patch and latest_patch and patch[0] == latest_patch[0] and abs(patch[-1] - latest_patch[-1]) == 1:
        patch_weight = 0.65
    else:
        patch_weight = 0.35
    tournament = str(game.get("tournament", "")).casefold()
    tournament_weight = 1.35 if "worlds" in tournament or "world championship" in tournament else 1.0
    return recency * patch_weight * tournament_weight


def _aggregate(games: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    patches = [_parse_patch(str(game.get("patch", ""))) for game in games]
    patches = [patch for patch in patches if patch]
    latest_patch = max(patches) if patches else ()
    role_rows = defaultdict(lambda: defaultdict(lambda: {"raw_games": 0, "weighted_games": 0.0, "weighted_wins": 0.0}))
    pair_rows = defaultdict(lambda: {"raw_games": 0, "weighted_games": 0.0, "weighted_wins": 0.0})

    for game in games:
        picks = game.get("picks") or []
        if len(picks) != 10:
            continue
        weight = _game_weight(game, latest_patch, now)
        sides = ((game["team1"], picks[:5]), (game["team2"], picks[5:10]))
        for team, champions in sides:
            won = 1.0 if team == game.get("winner") else 0.0
            for role, champion in zip(ROLES, champions):
                row = role_rows[role][champion]
                row["raw_games"] += 1
                row["weighted_games"] += weight
                row["weighted_wins"] += weight * won
            for (ia, role_a), (ib, role_b) in itertools.combinations(enumerate(ROLES), 2):
                key = f"{role_a}|{role_b}|{champions[ia]}|{champions[ib]}"
                row = pair_rows[key]
                row["raw_games"] += 1
                row["weighted_games"] += weight
                row["weighted_wins"] += weight * won

    role_stats: dict[str, dict] = {}
    for role, champions in role_rows.items():
        role_stats[role] = {}
        for champion, row in champions.items():
            wg = row["weighted_games"]
            role_stats[role][champion] = {
                **row,
                "win_rate": row["weighted_wins"] / wg if wg else 0.5,
                "bayes_win_rate": (row["weighted_wins"] + 1.5) / (wg + 3.0),
            }

    pair_stats: dict[str, dict] = {}
    for key, row in pair_rows.items():
        wg = row["weighted_games"]
        parts = key.split("|", 3)
        role_a, role_b, champion_a, champion_b = parts
        a = role_stats.get(role_a, {}).get(champion_a, {})
        b = role_stats.get(role_b, {}).get(champion_b, {})
        baseline = (float(a.get("bayes_win_rate", 0.5)) + float(b.get("bayes_win_rate", 0.5))) / 2.0
        bayes = (row["weighted_wins"] + 1.5) / (wg + 3.0)
        pair_stats[key] = {
            **row,
            "win_rate": row["weighted_wins"] / wg if wg else 0.5,
            "bayes_win_rate": bayes,
            "lift": bayes - baseline,
        }

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "source": {"name": "Games of Legends", "url": GOL_BASE, "tournaments": list(TOURNAMENTS)},
        "latest_patch": ".".join(str(part) for part in latest_patch),
        "game_count": len(games),
        "games": games,
        "role_stats": role_stats,
        "pair_stats": pair_stats,
    }


async def _fetch(session: aiohttp.ClientSession, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=25)) as response:
                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientResponseError as exc:
            last_error = exc
            if exc.status not in {429, 500, 502, 503, 504}:
                raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
        if attempt < 2:
            await asyncio.sleep(0.75 * (2 ** attempt))
    assert last_error is not None
    raise last_error


async def refresh_recent_pro_data(path: Path | None = None) -> dict:
    target = path or DATA_PATH
    cached: dict = {}
    if target.exists():
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
    cached_games = {int(game["game_id"]): game for game in cached.get("games", []) if game.get("game_id")}

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    discovered: dict[int, dict] = {}
    errors: list[str] = []
    async with aiohttp.ClientSession(headers=headers) as session:
        for tournament in TOURNAMENTS:
            url = f"{GOL_BASE}/tournament/tournament-matchlist/{quote(tournament, safe='')}/"
            try:
                page = await _fetch(session, url)
                series_rows = _parse_matchlist(page, tournament)

                async def expand(match: dict) -> list[dict]:
                    if match.get("page_kind") == "page-game":
                        return [match]
                    try:
                        summary = await _fetch(
                            session,
                            f"{GOL_BASE}/game/stats/{match['game_id']}/page-summary/",
                        )
                        game_ids = _parse_series_game_ids(summary)
                    except Exception:
                        game_ids = []
                    return [
                        {**match, "game_id": game_id, "page_kind": "page-game"}
                        for game_id in game_ids
                    ]

                expanded_batches = await asyncio.gather(*(expand(match) for match in series_rows))
                for batch in expanded_batches:
                    for match in batch:
                        discovered[match["game_id"]] = match
            except Exception as exc:
                errors.append(f"{tournament}: {type(exc).__name__}")

        ordered = sorted(discovered.values(), key=lambda row: (row.get("date", ""), row["game_id"]), reverse=True)
        cutoff = (datetime.now(timezone.utc).date().toordinal() - MAX_AGE_DAYS)
        ordered = [row for row in ordered if _safe_ordinal(row.get("date")) >= cutoff][:MAX_GAMES]
        sem = asyncio.Semaphore(5)

        async def hydrate(match: dict) -> dict | None:
            old = cached_games.get(match["game_id"])
            if old and len(old.get("picks") or []) == 10 and old.get("winner"):
                # Finished games are immutable. Preserve the per-game winner/team order
                # parsed from the game page; the match list only knows the series winner.
                return {
                    **match,
                    **old,
                    "tournament": match["tournament"],
                    "patch": match["patch"],
                    "date": match["date"],
                }
            try:
                async with sem:
                    page = await _fetch(session, f"{GOL_BASE}/game/stats/{match['game_id']}/page-game/")
                picks = _parse_game_picks(page)
                teams = _parse_game_teams(page)
                if len(picks) != 10 or not teams:
                    return None
                team1, team2, winner = teams
                return {**match, "team1": team1, "team2": team2, "winner": winner, "picks": picks}
            except Exception:
                return None

        hydrated = await asyncio.gather(*(hydrate(match) for match in ordered))

    games = [game for game in hydrated if game]
    if not games and cached.get("games"):
        result = cached
        result["last_refresh_errors"] = errors or ["No live games returned; retained cache"]
        return result
    result = _aggregate(games)
    if errors:
        result["last_refresh_errors"] = errors
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return result


def _safe_ordinal(value: str | None) -> int:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().toordinal()
    except ValueError:
        return 0


if __name__ == "__main__":
    data = asyncio.run(refresh_recent_pro_data())
    print(f"Refreshed {data.get('game_count', 0)} recent pro games; latest patch {data.get('latest_patch', 'unknown')}")
