"""2026 Worlds team-composition patterns, from completed professional drafts.
Only observational evidence; no live network access during champion select.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from league_source_stats import source_mtime_ns

PATH = Path(__file__).with_name("data") / "league_recent_pro.json"
ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
TANK = {"K'Sante", "Ornn", "Sion", "Shen", "Malphite", "Gragas", "Poppy", "Maokai", "Cho'Gath"}
TOP_CARRY = {"Camille", "Jax", "Gwen", "Fiora", "Ambessa", "Jayce", "Yone", "Rumble", "Olaf", "Renekton", "Gnar", "Aatrox"}
HYPER = {"Jinx", "Aphelios", "Kog'Maw", "Zeri", "Yunara", "Twitch", "Vayne", "Smolder", "Kai'Sa", "Sivir"}
ENCHANT = {"Lulu", "Milio", "Janna", "Nami", "Soraka", "Sona", "Yuumi"}
ENGAGE = {"Nautilus", "Leona", "Alistar", "Thresh", "Rell", "Rakan", "Braum", "Poppy", "Maokai"}
ASSASSIN = {"Qiyana", "Kha'Zix", "Rengar", "Evelynn", "Ekko", "Diana", "Nocturne", "Talon"}
SETUP = {"Galio", "Lissandra", "Taliyah", "Annie", "Ahri", "Orianna", "Sylas", "Vex"}
CONTROL = {"Orianna", "Viktor", "Azir", "Syndra", "Taliyah", "Hwei", "Anivia", "Galio", "Lissandra"}
DIVE_JG = {"Nocturne", "Vi", "Jarvan IV", "Lee Sin", "Wukong", "Xin Zhao", "Viego", "Diana", "Qiyana"}
DIVE_ADC = {"Kai'Sa", "Kalista", "Samira", "Lucian", "Xayah", "Tristana"}

def identify_archetypes(champions):
    top, jg, mid, adc, sup = (champions.get(role, "") for role in ROLES)
    out = []
    if top in TANK and adc in HYPER and sup in ENCHANT:
        out.append("tank-top / protect-hypercarry")
    if top in TOP_CARRY and sup in ENGAGE:
        out.append("carry-top / engage-support")
    if jg in ASSASSIN and mid in SETUP and (top in TANK or sup in ENGAGE):
        out.append("assassin-jungle / setup-mid")
    if jg in DIVE_JG and mid in SETUP and (sup in ENGAGE or adc in DIVE_ADC):
        out.append("mid-jungle dive / follow-up")
    if mid in CONTROL and adc in HYPER and (top in TANK or sup in ENGAGE):
        out.append("front-to-back / control-mid")
    if adc in DIVE_ADC and sup in ENGAGE and jg in DIVE_JG:
        out.append("three-role engage / dive")
    return out

@lru_cache(maxsize=4)
def _read(path, stamp):
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    counts = {}
    worlds = 0
    for game in snapshot.get("games", []):
        if not str(game.get("date", "")).startswith("2026-") or not game.get("winner"):
            continue
        picks = game.get("picks", [])
        if len(picks) != 10 or not all(isinstance(c, str) and c for c in picks):
            continue
        if "world" not in str(game.get("tournament", "")).lower():
            continue
        worlds += 1
        for team, five in ((game.get("team1"), picks[:5]), (game.get("team2"), picks[5:])):
            if not team:
                continue
            for label in identify_archetypes(dict(zip(ROLES, five))):
                row = counts.setdefault(label, {"games": 0, "wins": 0})
                row["games"] += 1
                row["wins"] += int(team == game.get("winner"))
    return {"counts": counts, "worlds_games": worlds}

def pro_archetype_signal(champions, *, path=None):
    target = Path(path) if path else PATH
    try:
        data = _read(str(target), source_mtime_ns(target))
    except OSError:
        data = {}
    evidence = []
    for label in identify_archetypes(champions):
        row = data.get("counts", {}).get(label, {})
        if row.get("games", 0) >= 3:
            evidence.append({"name": label, "worlds_games": row["games"],
                             "worlds_wins": row["wins"],
                             "source": "Games of Legends / Worlds 2026", "patch": "26.20"})
    n = max((r["worlds_games"] for r in evidence), default=0)
    return {"adjustment": round(min(0.6, 0.6*n/(n+12)), 3) if n else 0.0,
            "labels": [r["name"] for r in evidence],
            "evidence": sorted(evidence, key=lambda r: -r["worlds_games"])[:3],
            "worlds_games_seen": data.get("worlds_games", 0)}
