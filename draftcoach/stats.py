"""Player review: each player's own account stats, pulled from their own League client.

The League client exposes the signed-in player's match history and ranked standing without an
API key (``/lol-match-history``, ``/lol-ranked``). This module fetches them, converts the client's
match format into the Riot match-v5 shape, runs the Matchup Master aggregates (``draftcoach.mmstats``)
and derives the OP.GG / Blitz / League of Graphs style review: per-champion and per-role records,
recent form, streaks, KDA, CS, damage, gold and vision per minute, kill participation, how you
compare with the same-role players in your own games, and plain-language insights.

Everything is cached under ``var/stats/<puuid>/`` so the review opens instantly and survives the
client being closed. Nothing leaves the machine; the coach page reads ``/api/stats/me``.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import VAR_DIR, champions as champ_map
from .mmstats import build_peer_stats

log = logging.getLogger("draftcoach.stats")

STATS_DIR = VAR_DIR / "stats"
QUEUE_NAMES = {420: "Ranked Solo/Duo", 440: "Ranked Flex", 400: "Normal Draft", 430: "Normal Blind", 450: "ARAM",
               490: "Quickplay", 700: "Clash", 0: "Custom", 1700: "Arena", 900: "URF"}
RANKED_QUEUES = {"RANKED_SOLO_5x5": "Solo/Duo", "RANKED_FLEX_SR": "Flex"}
POSITION = {("TOP", "SOLO"): "TOP", ("JUNGLE", "NONE"): "JUNGLE", ("MIDDLE", "SOLO"): "MIDDLE",
            ("BOTTOM", "DUO_CARRY"): "BOTTOM", ("BOTTOM", "DUO_SUPPORT"): "UTILITY", ("BOTTOM", "CARRY"): "BOTTOM",
            ("BOTTOM", "SUPPORT"): "UTILITY"}
ROLE_LABEL = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
TIER_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]


# ---- the client ---------------------------------------------------------------------------------
def fetch_from_client(rest, *, count: int = 40, puuid: str | None = None, cache_dir: Path | None = None) -> dict:
    """Pull the local player's account, ranked standing and recent games through the client."""
    me = rest.get("/lol-summoner/v1/current-summoner") or {}
    puuid = puuid or me.get("puuid")
    if not puuid:
        raise RuntimeError("the client did not report the signed-in player")
    account = {"puuid": puuid, "gameName": me.get("gameName") or me.get("displayName"), "tagLine": me.get("tagLine"),
               "summonerLevel": me.get("summonerLevel"), "profileIconId": me.get("profileIconId")}
    ranked = rest.get("/lol-ranked/v1/current-ranked-stats") or {}
    folder = (cache_dir or STATS_DIR) / puuid
    games_dir = folder / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    listing = rest.get(f"/lol-match-history/v1/products/lol/{puuid}/matches?begIndex=0&endIndex={int(count)}") or {}
    rows = ((listing.get("games") or {}).get("games") or []) if isinstance(listing, dict) else []
    games: list[dict] = []
    for row in rows:
        game_id = row.get("gameId")
        if game_id is None:
            continue
        file = games_dir / f"{int(game_id)}.json"
        full = None
        if file.is_file():
            try:
                full = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                full = None
        if full is None:
            try:
                full = rest.get(f"/lol-match-history/v1/games/{int(game_id)}")
            except Exception as exc:  # noqa: BLE001
                log.debug("game %s: %s", game_id, exc)
                full = None
            if not isinstance(full, dict) or len(full.get("participants") or []) < 10:
                full = row                     # the listing row carries at least our own participant
            else:
                try:
                    file.write_text(json.dumps(full), encoding="utf-8")
                except OSError:
                    pass
        games.append(full)
    payload = {"fetched_at": time.time(), "account": account, "ranked": ranked, "games": games}
    try:
        (folder / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return payload


def load_cached(puuid: str | None = None, cache_dir: Path | None = None) -> dict | None:
    root = cache_dir or STATS_DIR
    candidates = [root / puuid / "latest.json"] if puuid else sorted(root.glob("*/latest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for file in candidates:
        if file.is_file():
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return None


# ---- client match format -> match-v5 shape --------------------------------------------------------
def _position(participant: dict) -> str | None:
    stats = participant.get("stats") or {}
    for key in ("teamPosition", "individualPosition"):
        value = str(stats.get(key) or participant.get(key) or "").upper()
        if value in ROLE_LABEL:
            return value
    timeline = participant.get("timeline") or {}
    lane, role = str(timeline.get("lane") or "").upper(), str(timeline.get("role") or "").upper()
    return POSITION.get((lane, role)) or {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MIDDLE"}.get(lane)


def to_match_v5(game: dict) -> dict | None:
    """Riot's client lists games in the older match-v4 layout; the aggregates expect match-v5."""
    if not isinstance(game, dict) or game.get("gameId") is None:
        return None
    identities = {int(i.get("participantId", -1)): (i.get("player") or {}) for i in game.get("participantIdentities") or []}
    participants = []
    for p in game.get("participants") or []:
        pid = int(p.get("participantId", -1))
        stats = p.get("stats") or {}
        player = identities.get(pid, {})
        champion_id = int(p.get("championId") or 0)
        row = {
            "participantId": pid, "teamId": int(p.get("teamId") or 0), "puuid": player.get("puuid") or stats.get("puuid"),
            "riotIdGameName": player.get("gameName") or player.get("summonerName"), "riotIdTagline": player.get("tagLine"),
            "championId": champion_id, "championName": champ_map.name_for(champion_id) or f"Champion {champion_id}",
            "teamPosition": _position(p), "win": stats.get("win") if isinstance(stats.get("win"), bool) else None,
        }
        for key in ("kills", "deaths", "assists", "goldEarned", "totalMinionsKilled", "neutralMinionsKilled",
                    "totalDamageDealtToChampions", "visionScore", "wardsPlaced", "wardsKilled", "visionWardsBoughtInGame",
                    "physicalDamageDealtToChampions", "magicDamageDealtToChampions", "trueDamageDealtToChampions",
                    "totalDamageTaken", "totalHeal", "damageDealtToTurrets", "damageDealtToObjectives", "doubleKills",
                    "tripleKills", "quadraKills", "pentaKills", "champLevel", "firstBloodKill", "firstBloodAssist",
                    "gameEndedInSurrender", "gameEndedInEarlySurrender", "timeCCingOthers", "largestKillingSpree"):
            if key in stats:
                row[key] = stats[key]
        row.setdefault("detectorWardsPlaced", stats.get("visionWardsBoughtInGame"))
        participants.append(row)
    teams = []
    for t in game.get("teams") or []:
        teams.append({"teamId": int(t.get("teamId") or 0), "win": str(t.get("win") or "").lower() == "win",
                      "objectives": {"champion": {"first": bool(t.get("firstBlood"))}, "tower": {"first": bool(t.get("firstTower"))},
                                     "dragon": {"first": bool(t.get("firstDargon", t.get("firstDragon")))},
                                     "riftHerald": {"first": bool(t.get("firstRiftHerald"))}, "baron": {"first": bool(t.get("firstBaron"))},
                                     "inhibitor": {"first": bool(t.get("firstInhibitor"))}}})
    duration = game.get("gameDuration")
    return {"metadata": {"matchId": f"{game.get('platformId') or 'EUW1'}_{int(game['gameId'])}", "participants": [p.get("puuid") for p in participants]},
            "info": {"gameId": int(game["gameId"]), "gameCreation": game.get("gameCreation"), "gameStartTimestamp": game.get("gameCreation"),
                     "gameDuration": duration, "gameVersion": game.get("gameVersion") or "15.1.1", "queueId": game.get("queueId"),
                     "mapId": game.get("mapId"), "gameMode": game.get("gameMode"), "participants": participants, "teams": teams}}


# ---- the review ------------------------------------------------------------------------------------
def _fmt(value, digits=1):
    return None if value is None else round(float(value), digits)


def ranked_summary(ranked: dict) -> list[dict]:
    out = []
    for queue in (ranked or {}).get("queues") or []:
        qtype = queue.get("queueType")
        if qtype not in RANKED_QUEUES:
            continue
        wins, losses = int(queue.get("wins") or 0), int(queue.get("losses") or 0)
        tier = str(queue.get("tier") or "").upper()
        out.append({"queue": RANKED_QUEUES[qtype], "tier": tier.title() if tier and tier != "NONE" else "Unranked",
                    "division": queue.get("division") if queue.get("division") not in ("NA", None) else "",
                    "lp": int(queue.get("leaguePoints") or 0), "wins": wins, "losses": losses,
                    "winrate": _fmt(100.0 * wins / (wins + losses)) if wins + losses else None,
                    "tier_rank": TIER_ORDER.index(tier) if tier in TIER_ORDER else -1,
                    "streak": bool(queue.get("isProvisional")) and "placements" or None})
    return out


def build_review(payload: dict) -> dict:
    account = payload.get("account") or {}
    puuid = account.get("puuid")
    matches = [m for m in (to_match_v5(g) for g in payload.get("games") or []) if m]
    cache = {"account": account, "matches": matches, "timelines": {}}
    stats = build_peer_stats(cache, account=account)
    dash_all, dash_20 = stats["dashboard"]["all"], stats["dashboard"]["last20"]
    peers = stats["peers"]
    games = stats["games"]                     # last 20, chronological

    def group_rows(groups, role_labels=False, limit=None):
        rows = []
        for g in groups:
            s = g["stats"]
            v = lambda key, digits=1: _fmt((s.get(key) or {}).get("value"), digits)   # noqa: E731 - absent for grouped splits
            rows.append({"label": ROLE_LABEL.get(g["label"], g["label"]) if role_labels else g["label"],
                         "games": s["games"]["value"], "wins": s["wins"]["value"], "losses": s["losses"]["value"],
                         "winrate": v("winrate"), "kda": v("kda", 2), "kills": v("kills"), "deaths": v("deaths"), "assists": v("assists"),
                         "cs_per_min": v("cs_per_min"), "damage_per_min": v("damage_per_min", 0), "gold_per_min": v("gold_per_min", 0),
                         "vision_per_min": v("vision_per_min", 2), "kp": v("kp"), "damage_share": v("damage_share"),
                         "share": _fmt((g.get("share") or {}).get("value")), "low_sample": s["games"]["value"] < 5})
        return rows[:limit] if limit else rows

    summary = dash_all["summary"]
    recent = [{"match_id": g["match_id"], "date": g["date"], "champion": g["champion"], "role": ROLE_LABEL.get(g["role"], g["role"] or "?"),
               "win": g["win"], "kills": g["kills"], "deaths": g["deaths"], "assists": g["assists"], "kda": _fmt(g["kda"], 2),
               "cs_per_min": _fmt(g["cs_per_min"]), "damage_per_min": _fmt(g["damage_per_min"], 0), "kp": _fmt(g["kp"]),
               "opponent": g.get("opponent_champion"), "duration": g.get("duration")} for g in games]
    form = "".join("W" if g["win"] else "L" if g["win"] is False else "?" for g in games[-10:])

    # Matchups: same-role opponents across all cached games (OP.GG style "lane opponents").
    faced: dict[str, list] = defaultdict(list)
    for m in matches:
        info = m["info"]
        own = next((p for p in info["participants"] if p.get("puuid") == puuid), None)
        if not own or own.get("teamPosition") not in ROLE_LABEL:
            continue
        enemy = [p for p in info["participants"] if p.get("teamId") != own.get("teamId") and p.get("teamPosition") == own.get("teamPosition")]
        if len(enemy) == 1 and own.get("win") is not None:
            faced[f"{own['championName']} vs {enemy[0]['championName']}"].append(own["win"])
    matchups = sorted(({"label": k, "games": len(v), "wins": sum(v), "winrate": _fmt(100.0 * sum(v) / len(v))}
                       for k, v in faced.items()), key=lambda r: (-r["games"], -r["winrate"]))

    # Peer comparison: percentile among the same-role players in your own games (League of Graphs style).
    compare = []
    for key, label in (("cs_per_min", "CS / min"), ("gold_per_min", "Gold / min"), ("damage_per_min", "Damage / min"),
                       ("vision_per_min", "Vision / min"), ("kp", "Kill participation"), ("damage_share", "Damage share"),
                       ("deaths", "Deaths"), ("kda", "KDA")):
        m = peers["all"]["metrics"].get(key) or {}
        compare.append({"key": key, "label": label, "you": _fmt(m.get("you"), 2), "peers": _fmt(m.get("peers_mean"), 2),
                        "percentile": _fmt(m.get("percentile"), 0), "n": m.get("n_you"), "lower_is_better": m.get("lower_is_better")})

    insights = _insights(summary, dash_all, dash_20, peers, group_rows(dash_all["champions"]), group_rows(dash_all["roles"], True), form, matchups)
    return {
        "account": {"name": f"{account.get('gameName') or '?'}#{account.get('tagLine') or ''}".rstrip("#"), "level": account.get("summonerLevel"),
                    "puuid": puuid, "fetched_at": payload.get("fetched_at")},
        "ranked": ranked_summary(payload.get("ranked") or {}),
        "overview": {"games": summary["games"]["value"], "wins": summary["wins"]["value"], "losses": summary["losses"]["value"],
                     "winrate": _fmt(summary["winrate"]["value"]), "kda": _fmt(summary["kda"]["value"], 2),
                     "cs_per_min": _fmt(summary["cs_per_min"]["value"]), "damage_per_min": _fmt(summary["damage_per_min"]["value"], 0),
                     "gold_per_min": _fmt(summary["gold_per_min"]["value"], 0), "vision_per_min": _fmt(summary["vision_per_min"]["value"], 2),
                     "kp": _fmt(summary["kp"]["value"]), "form": form,
                     "streak": dash_all["streaks"], "main_role": ROLE_LABEL.get(peers["all"].get("role"), peers["all"].get("role"))},
        "champions": group_rows(dash_all["champions"], limit=12),
        "roles": group_rows(dash_all["roles"], role_labels=True),
        "queues": [{**r, "label": QUEUE_NAMES.get(int(r["label"]), r["label"]) if str(r["label"]).isdigit() else r["label"]} for r in group_rows(dash_all["queues"])],
        "recent": list(reversed(recent)),
        "matchups": matchups[:12],
        "compare": compare,
        "tilt": {"after_loss": group_rows(dash_all["after_result"]), "by_session": group_rows(dash_all["by_session"]),
                 "by_duration": group_rows(dash_all["by_duration"]), "by_side": group_rows(dash_all["by_side"])},
        "records": [{"key": r["key"], "value": _fmt(r["value"], 1 if r["key"] == "cs_per_min" else 0), "champion": r["champion"], "date": r["date"]}
                    for r in dash_all["records"]],
        "multikills": {k: v["value"] for k, v in dash_all["multikills"].items()},
        "insights": insights,
        "notes": dash_all["notes"],
    }


def _insights(summary, dash_all, dash_20, peers, champions, roles, form, matchups) -> list[dict]:
    out: list[dict] = []
    games = summary["games"]["value"] or 0
    if games < 3:
        return [{"level": "info", "text": "Play a few more games on this account and the review fills in."}]
    wr = summary["winrate"]["value"]
    if wr is not None:
        out.append({"level": "good" if wr >= 55 else "warn" if wr < 45 else "info",
                    "text": f"{wr:.0f}% win rate over {games} games" + (f", last ten {form.count('W')}-{form.count('L')}." if form else ".")})
    streak = dash_all["streaks"]
    if streak.get("current") and streak["current"] >= 3:
        out.append({"level": "good" if streak["result"] == "Win" else "warn",
                    "text": f"On a {streak['current']}-game {streak['result'].lower()} streak."})
    best = [c for c in champions if c["games"] >= 3 and c["winrate"] is not None]
    if best:
        top = max(best, key=lambda c: (c["winrate"], c["games"]))
        out.append({"level": "good", "text": f"Best champion: {top['label']} ({top['wins']}-{top['losses']}, {top['kda']} KDA)."})
        worst = min(best, key=lambda c: (c["winrate"], -c["games"]))
        if worst is not top and worst["winrate"] < 40:
            out.append({"level": "warn", "text": f"{worst['label']} is {worst['wins']}-{worst['losses']}: keep it out of ranked for now."})
    for role in roles:
        if role["games"] >= 3 and role["winrate"] is not None and role["winrate"] < 40 and role["share"] and role["share"] < 50:
            out.append({"level": "warn", "text": f"{role['label']} is {role['wins']}-{role['losses']} on this account: avoid autofilling there."})
    metrics = peers["all"]["metrics"]
    for key, label, good_high in (("cs_per_min", "CS per minute", True), ("damage_per_min", "damage per minute", True),
                                  ("vision_per_min", "vision per minute", True), ("deaths", "deaths per game", False)):
        m = metrics.get(key) or {}
        pct = m.get("percentile")
        if pct is None or (m.get("n_peers") or 0) < 8:
            continue
        if pct >= 75:
            out.append({"level": "good", "text": f"Top quarter for {label} among the same-role players in your games."})
        elif pct <= 25:
            out.append({"level": "warn", "text": f"Bottom quarter for {label} among the same-role players in your games."})
    after = {r["label"]: r for r in dash_all["after_result"]}
    loss = after.get("Loss")
    win = after.get("Win")
    if loss and win and loss["stats"]["games"]["value"] >= 4 and loss["stats"]["winrate"]["value"] is not None and win["stats"]["winrate"]["value"] is not None:
        gap = win["stats"]["winrate"]["value"] - loss["stats"]["winrate"]["value"]
        if gap >= 20:
            out.append({"level": "warn", "text": f"Win rate drops {gap:.0f} points in the game right after a loss: take a break after losing."})
    sessions = {r["label"]: r for r in dash_all["by_session"]}
    late = sessions.get("4+")
    if late and late["stats"]["games"]["value"] >= 4 and late["stats"]["winrate"]["value"] is not None and late["stats"]["winrate"]["value"] < 40:
        out.append({"level": "warn", "text": "Fourth game of a session and later is where the losses pile up."})
    hard = [m for m in matchups if m["games"] >= 3 and m["winrate"] is not None and m["winrate"] < 35]
    if hard:
        out.append({"level": "warn", "text": "Hard matchups on record: " + ", ".join(m["label"] for m in hard[:3]) + "."})
    return out[:8]
