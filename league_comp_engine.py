from __future__ import annotations

from league_source_stats import source_mtime_ns, source_stat_scope
from league_patch_live import (
    LIVE_PATCH, live_patch_active, live_patch_role_adjustment,
    live_patch_draft_adjustment,
)

import hashlib
import itertools
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from league_multi_source import open_pro_pair_signal
from league_aggregate_sources import approved_matchups, approved_champions, approved_pair_signal

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
DATA_PATH = Path(__file__).with_name("data") / "league_comp_meta.json"
MATCHUP_PATH = Path(__file__).with_name("data") / "league_lane_matchups.json"
PLAYER_PROFILE_PATH = Path(__file__).with_name("data") / "league_player_profiles.json"
RECENT_PRO_PATH = Path(__file__).with_name("data") / "league_recent_pro.json"
_RECENT_PRO_CACHE: tuple[int | None, dict] = (None, {})

ENGAGE_TAGS = {"VANGUARD", "DIVER", "CATCHER"}
PEEL_TAGS = {"ENCHANTER", "WARDEN", "CATCHER", "SUPPORT"}
DIVE_TAGS = {"DIVER", "ASSASSIN"}
TANK_TAGS = {"TANK", "VANGUARD", "WARDEN", "JUGGERNAUT"}
POKE_CHAMPS = {
    "Jayce", "Ezreal", "Jhin", "Varus", "Ziggs", "Xerath", "Vel'Koz",
    "Lux", "Zoe", "Caitlyn", "Nidalee", "Corki", "Hwei",
}
SPLIT_CHAMPS = {
    "Camille", "Fiora", "Gwen", "Jax", "Kayle", "Nasus", "Quinn",
    "Riven", "Trundle", "Tryndamere", "Yorick", "Yone",
}

# Explicit confirmations supplement (but do not replace) Discord submitted
# selections. A confirmed champion is PLAYABLE; it isn't automatically meta,
# a good synergy pick, or a priority pick on the current patch.
EXPLICIT_CHAMPION_CONFIRMATIONS = Path(__file__).resolve().parent / "data" / "league_player_confirmations.json"


@lru_cache(maxsize=1)
def _read_explicit_champion_confirmations() -> dict:
    try:
        data = json.loads(EXPLICIT_CHAMPION_CONFIRMATIONS.read_text(encoding="utf-8"))
        return data.get("players", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _explicitly_confirmed_player_pick(uid: int, role: str, champion: str) -> bool:
    per_role = _read_explicit_champion_confirmations().get(str(uid), {})
    return champion in (per_role.get(role) or [])

# Temporary bridge from Demacia Cup 26.18 evidence to the Worlds 26.20 patch.
# These are deliberately small priors, not fake win rates. Once real Worlds games
# arrive, the bridge fades over a meaningful sample instead of disappearing after
# a single game.
WORLDS_PATCH_BRIDGE_FADE_GAMES = 20
WORLDS_PATCH_ROLE_ADJUSTMENTS = {
    # K'Sante remained near-perma contested in the opening Worlds series, so the
    # balance-change penalty is deliberately softer than the first bridge version.
    ("Top", "K'Sante"): -0.010,
    ("Top", "Ambessa"): -0.018,
    ("ADC", "Ashe"): -0.018,
    ("Jungle", "Vi"): -0.018,
    ("Jungle", "Nocturne"): -0.015,
    ("Mid", "Ryze"): -0.018,
    ("Top", "Rumble"): -0.016,
    ("ADC", "Lucian"): 0.012,
    ("Top", "Kennen"): 0.012,
    ("Jungle", "Kindred"): 0.015,
    ("Jungle", "Lillia"): 0.012,
}
HYBRID_CHAMPS = {"Kai'Sa", "Kog'Maw", "Corki", "Varus", "Smolder"}
HYPERCARRY_ADCS = {"Jinx", "Kog'Maw", "Aphelios", "Twitch", "Vayne", "Smolder", "Kai'Sa", "Yunara"}
UTILITY_ADCS = {"Ashe", "Jhin", "Senna"}
PROTECT_CARRY_ADCS = {"Jinx", "Kog'Maw", "Aphelios", "Twitch", "Vayne", "Smolder", "Sivir", "Yunara", "Zeri"}

# User-facing draft exclusions. A champion can remain in a player's saved pool/history
# without ever being surfaced by the composition recommender.
DISPLAY_EXCLUDED_CHAMPIONS = {"Teemo"}
# Role-specific niche picks can still exist in account history, but should not be
# forced into normal prepared drafts unless we deliberately reopen them later.
BLOCKED_ROLE_PICKS = {("Top", "Varus")}
CREDIBLE_DAMAGE_MIN = 1.75

# Ranged/carry Top consumes a team resource slot. These champions are useful signals
# that Jungle can supply low-economy frontline while Mid supplies dependable waveclear.
LOW_ECON_FRONTLINE_JUNGLES = {
    "Skarner", "Maokai", "Sejuani", "Poppy", "Amumu", "Zac", "Rammus",
    "Nunu & Willump", "Ivern",
}
STRONG_WAVECLEAR_MIDS = {
    "Galio", "Orianna", "Syndra", "Viktor", "Taliyah", "Hwei", "Azir",
    "Anivia", "Ryze", "Ziggs", "Xerath", "Corki", "Aurelion Sol",
    "Malzahar", "Swain", "Lissandra",
}
MID_FRONTLINE_CONTROL = {"Galio", "Swain", "Lissandra"}

POKE_ADCS = {"Ezreal", "Caitlyn", "Varus", "Jhin", "Ashe", "Ziggs", "Corki"}
ALL_IN_ADCS = {"Kai'Sa", "Samira", "Nilah", "Kalista", "Tristana", "Draven", "Lucian"}
ENCHANTER_SUPPORTS = {"Lulu", "Milio", "Yuumi", "Nami", "Janna", "Soraka", "Sona"}
POKE_SUPPORTS = {"Karma", "Lux", "Xerath", "Vel'Koz", "Zyra", "Morgana", "Bard", "Seraphine"}
ENGAGE_SUPPORTS = {"Nautilus", "Leona", "Rell", "Alistar", "Thresh", "Blitzcrank", "Rakan", "Braum", "Maokai", "Poppy", "Pantheon"}
PEEL_SUPPORTS = ENCHANTER_SUPPORTS | {"Thresh", "Braum", "Renata Glasc", "Rakan"}

# Qualitative, matchup-dependent EARLY wave-access priors, not fabricated win
# rates or lane-victory predictions. Neutral defaults preserve unfamiliar picks.
# Kept deliberately bounded so true champion synergy/counters still outrank them.
EARLY_TOP_PRIORITY = {
    "Renekton": .72, "Rumble": .70, "Jayce": .73, "Darius": .69,
    "Gnar": .66, "Olaf": .67, "Kennen": .62, "Nasus": .22,
    "Kayle": .26, "Vladimir": .31, "Dr. Mundo": .38,
}
EARLY_MID_PRIORITY = {
    "Orianna": .66, "Syndra": .68, "Taliyah": .66, "Ahri": .59,
    "Viktor": .62, "Hwei": .65, "Malzahar": .65, "Galio": .55,
    "Kassadin": .24, "Kayle": .30, "Veigar": .38, "Aurelion Sol": .35,
}
EARLY_BOT_ADC_PRIORITY = {
    "Caitlyn": .78, "Draven": .75, "Varus": .71, "Sivir": .74,
    "Ashe": .67, "Kalista": .69, "Miss Fortune": .68, "Ziggs": .74,
    "Xayah": .60, "Yunara": .49, "Aphelios": .37, "Jinx": .43,
    "Kog'Maw": .29, "Smolder": .28, "Ezreal": .40, "Jhin": .50,
}
EARLY_BOT_SUPPORT_PRIORITY = {
    "Karma": .80, "Zyra": .78, "Lux": .75, "Brand": .73,
    "Seraphine": .67, "Morgana": .57, "Nami": .55, "Lulu": .50,
    "Janna": .41, "Milio": .38, "Yuumi": .28, "Braum": .36,
    "Leona": .45, "Nautilus": .51, "Rell": .43, "Thresh": .52,
}
ROAMING_SUPPORT_THREATS = {
    "Leona", "Nautilus", "Rell", "Pyke", "Bard", "Alistar",
    "Rakan", "Thresh", "Blitzcrank", "Pantheon",
}
JUNGLE_NEEDS_LANE_ACCESS = {
    "Vi", "Lee Sin", "Nidalee", "Kindred", "Xin Zhao", "Viego",
    "Elise", "Rek'Sai", "Jarvan IV", "Graves",
}

# Draft-specific counters that are important across lanes rather than ordinary
# same-lane matchup data. These are intentionally sparse/high-confidence rules.
# Example: a planned Malphite materially increases Sylas value regardless of who
# Sylas lanes into, so that consequence must follow Malphite as an alternate pick.
DRAFT_SPECIFIC_COUNTERS = {
    "Malphite": [
        {
            "champion": "Sylas",
            "score": 4.5,
            "reason": "Sylas can steal Malphite's fight-defining engage ultimate",
        },
    ],
}

# Organised-play priors. Current pro pair_stats can move these up/down,
# but these prevent individually strong champions forming a strategically weak lane.
BOT_DUO_PRIORS = {
    ("Ezreal", "Karma"): 1.00, ("Ezreal", "Bard"): 0.78,
    ("Caitlyn", "Lux"): 1.00, ("Caitlyn", "Morgana"): 0.95, ("Caitlyn", "Karma"): 0.88,
    # Prefer Milio when both are genuinely in the support player's available pool.
    ("Lucian", "Milio"): 1.12, ("Lucian", "Nami"): 0.84,
    ("Xayah", "Rakan"): 1.00,
    ("Kai'Sa", "Nautilus"): 0.98, ("Kai'Sa", "Rell"): 0.92, ("Kai'Sa", "Leona"): 0.85,
    ("Samira", "Nautilus"): 0.98, ("Samira", "Rell"): 0.90, ("Samira", "Leona"): 0.88, ("Samira", "Alistar"): 0.84,
    ("Jinx", "Lulu"): 0.98, ("Jinx", "Milio"): 0.96, ("Jinx", "Thresh"): 0.90, ("Jinx", "Braum"): 0.82,
    ("Kog'Maw", "Lulu"): 1.00, ("Twitch", "Lulu"): 0.96,
    ("Aphelios", "Thresh"): 0.98, ("Aphelios", "Lulu"): 0.88, ("Aphelios", "Braum"): 0.84,
    # Yunara/Lulu is an established current-patch high-volume/pro pairing,
    # not just a generic hypercarry+enchanter. OP.GG 16.20 EUW Emerald+
    # tracked 5,582 ranked appearances together; this is a suitability prior,
    # not an automatically imported win rate or a claim it always wins.
    ("Yunara", "Lulu"): 1.00, ("Yunara", "Milio"): 0.88,
    ("Kalista", "Renata Glasc"): 0.96, ("Kalista", "Nautilus"): 0.90, ("Kalista", "Rell"): 0.88,
    ("Zeri", "Lulu"): 0.94, ("Zeri", "Yuumi"): 0.90, ("Zeri", "Milio"): 0.88,
    ("Ashe", "Seraphine"): 0.94,
    ("Jhin", "Nautilus"): 0.88, ("Jhin", "Xerath"): 0.86, ("Jhin", "Bard"): 0.82, ("Jhin", "Leona"): 0.80,
    ("Sivir", "Yuumi"): 0.86,
}
# Player-requested duo candidates must survive the *shortlisting* stage if
# both players submitted them. This is candidate coverage, NOT a score bonus
# or an unconditional recommendation. Every five-man still gets rescored.
# Aphelios/Thresh remains legal through real scoring, not forcibly shortlisted.
EXPLICIT_BOT_PAIR_COVERAGE = (
    ("Yunara", "Lulu"),
    ("Kalista", "Renata Glasc"),
)

# High-value organised-play two-man packages outside bot lane. These are priors,
# then current pair_stats can move them up/down.
COORDINATED_DUO_PRIORS = {
    ("Jungle", "Mid", "Vi", "Ahri"): 1.00,
    ("Jungle", "Mid", "Jarvan IV", "Galio"): 1.00,
    ("Jungle", "Mid", "Jarvan IV", "Orianna"): 0.96,
    ("Jungle", "Mid", "Nocturne", "Orianna"): 0.96,
    ("Jungle", "Mid", "Diana", "Yasuo"): 1.00,
    ("Jungle", "Mid", "Zac", "Yasuo"): 1.00,
    ("Jungle", "Mid", "Vi", "Taliyah"): 0.94,
    ("Jungle", "Mid", "Vi", "Lissandra"): 0.92,
    ("Jungle", "Mid", "Lee Sin", "LeBlanc"): 0.90,
    ("Jungle", "Mid", "Wukong", "Annie"): 0.88,
    ("Jungle", "Mid", "Sejuani", "Sylas"): 0.86,
    ("Top", "Jungle", "Renekton", "Nidalee"): 1.00,
    ("Top", "Jungle", "Renekton", "Elise"): 0.96,
    ("Top", "Jungle", "Camille", "Sejuani"): 0.92,
    ("Top", "Jungle", "Jax", "Sejuani"): 0.88,
    ("Jungle", "Support", "Vi", "Nautilus"): 0.94,
    ("Jungle", "Support", "Jarvan IV", "Rakan"): 0.92,
    ("Jungle", "Support", "Wukong", "Rakan"): 0.92,
    ("Jungle", "Support", "Sejuani", "Rell"): 0.90,
    ("Jungle", "Support", "Maokai", "Rell"): 0.88,
}



@lru_cache(maxsize=1)
def load_player_profiles(path: str | None = None) -> dict:
    target = Path(path) if path else PLAYER_PROFILE_PATH
    if not target.exists():
        return {"players": {}}
    return json.loads(target.read_text(encoding="utf-8"))


OPGG_POSITION_TO_ROLE = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MID": "Mid",
    "MIDDLE": "Mid",
    "ADC": "ADC",
    "BOTTOM": "ADC",
    "BOT": "ADC",
    "SUPPORT": "Support",
    "UTILITY": "Support",
}
NORMAL_MATCH_WEIGHT = 0.55


@lru_cache(maxsize=1)
def load_recent_match_index() -> dict[int, dict]:
    """Index recent individual OP.GG Ranked + Normal SR matches by player/role/champion.

    Ranked gets full weight. Normal Summoner's Rift gets a lower weight so it can
    establish comfort/playstyle without being treated as equivalent competitive evidence.
    ARAM/Arena/etc never enter this index.
    """
    players = load_player_profiles().get("players", {})
    output: dict[int, dict] = {}
    metric_keys = ("dpm", "damage_share", "cs_per_min", "gold_per_min", "kda", "op_score", "lane_score", "kp", "vision_score")

    def add(bucket: dict, row: dict, weight: float):
        bucket["raw_games"] = bucket.get("raw_games", 0) + 1
        bucket["effective_games"] = bucket.get("effective_games", 0.0) + weight
        bucket["weighted_wins"] = bucket.get("weighted_wins", 0.0) + weight * int(row.get("result") == "WIN")
        for key in metric_keys:
            bucket[key + "_sum"] = bucket.get(key + "_sum", 0.0) + float(row.get(key, 0) or 0) * weight

    def finish(bucket: dict) -> dict:
        weight = float(bucket.get("effective_games", 0.0) or 0.0)
        result = {
            "raw_games": int(bucket.get("raw_games", 0) or 0),
            "effective_games": round(weight, 2),
            "win_rate": round(float(bucket.get("weighted_wins", 0.0)) / weight * 100.0, 2) if weight else None,
        }
        for key in metric_keys:
            result[key] = round(float(bucket.get(key + "_sum", 0.0)) / weight, 2) if weight else 0.0
        return result

    for uid_s, profile in players.items():
        uid = int(uid_s)
        global_bucket: dict = {}
        by_role: dict[str, dict] = {}
        by_champion: dict[str, dict] = {}
        by_role_champion: dict[str, dict[str, dict]] = {}
        queue_counts = {"RANKED": 0, "NORMAL": 0}
        matches = sorted(profile.get("recent_matches", []), key=lambda row: str(row.get("created_at") or ""), reverse=True)
        for idx, row in enumerate(matches):
            queue = str(row.get("queue_group") or row.get("queue") or "")
            if queue in {"SOLORANKED", "FLEXRANKED", "RANKED5S"}:
                queue = "RANKED"
            if queue == "RANKED":
                mode_weight = 1.0
            elif queue == "NORMAL":
                mode_weight = NORMAL_MATCH_WEIGHT
            else:
                continue
            role = OPGG_POSITION_TO_ROLE.get(str(row.get("position") or "").upper())
            champion = str(row.get("champion") or "")
            if not role or not champion:
                continue
            # Current form matters a little more, but older scraped games still retain value.
            recency_weight = max(0.45, 0.992 ** idx)
            weight = mode_weight * recency_weight
            queue_counts[queue] = queue_counts.get(queue, 0) + 1
            add(global_bucket, row, weight)
            add(by_role.setdefault(role, {}), row, weight)
            add(by_champion.setdefault(champion, {}), row, weight)
            add(by_role_champion.setdefault(role, {}).setdefault(champion, {}), row, weight)

        output[uid] = {
            "global": finish(global_bucket),
            "by_role": {role: finish(bucket) for role, bucket in by_role.items()},
            "by_champion": {champion: finish(bucket) for champion, bucket in by_champion.items()},
            "by_role_champion": {
                role: {champion: finish(bucket) for champion, bucket in champions.items()}
                for role, champions in by_role_champion.items()
            },
            "queue_counts": queue_counts,
        }
    return output


@lru_cache(maxsize=1)
def load_meta(path: str | None = None) -> dict:
    target = Path(path) if path else DATA_PATH
    return json.loads(target.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_lane_matchups(path: str | None = None) -> dict:
    target = Path(path) if path else MATCHUP_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    # Partner exports may only override recorded matchups when the exact patch
    # matches. Never combine differently ranked/dated samples as independent
    # games. Each row retains its own approved source attribution.
    if path is None:
        patch = next(iter(data.get("source", {}).get("patches_detected", ())), None)
        imported = approved_matchups(patch)
        if imported:
            data = dict(data)
            matchups = {role: dict(block) for role,block in data.get("matchups", {}).items()}
            for row in imported:
                role, enemy, own = row["role"], row["enemy"], row["response"]
                role_block = matchups.setdefault(role, {})
                enemy_block = dict(role_block.get(enemy, {"responses": {}}))
                responses = dict(enemy_block.get("responses", {}))
                # A new provider's response delta cannot inherit win rates,
                # baselines, or other fields from an older LoLalytics row.
                # Mixing these would silently corrupt the aggregate index.
                response_row = {
                    "response_advantage": float(row["response_advantage"]),
                    "games": int(row["games"]),
                    "source": str(row["source"]),
                }
                responses[own] = response_row
                enemy_block["responses"] = responses
                role_block[enemy] = enemy_block
            data["matchups"] = matchups
            data["licensed_imports"] = len(imported)
    return data


@lru_cache(maxsize=1)
def load_solo_queue_winrates(path: str | None = None) -> dict[str, dict[str, dict]]:
    """Build a cached role/champion SoloQ WR index from LoLalytics matchup data."""
    data = load_lane_matchups(path)
    samples: dict[str, dict[str, list[tuple[float, int]]]] = {role: {} for role in ROLES}
    for role in ROLES:
        for enemy_data in data.get("matchups", {}).get(role, {}).values():
            for champion, response in enemy_data.get("responses", {}).items():
                win_rate = response.get("opponent_baseline_win_rate")
                games = int(response.get("games", 0) or 0)
                if win_rate is None:
                    continue
                # FLEXERS: in LoLalytics snapshots the stored baseline is the opponents' expected
                # win rate into this champion, i.e. 100 - the champion's own win rate (it equals
                # enemy_win_rate - enemy_delta1 on every row). Complement it unless the snapshot
                # declares response-side semantics.
                if str(data.get("source", {}).get("baseline_semantics", "opponent")) != "response":
                    win_rate = 100.0 - float(win_rate)
                samples[role].setdefault(champion, []).append((float(win_rate), max(games, 1)))

    index: dict[str, dict[str, dict]] = {role: {} for role in ROLES}
    for role, champions in samples.items():
        for champion, values in champions.items():
            total_weight = sum(min(games, 5000) for _, games in values)
            if not total_weight:
                continue
            weighted_wr = sum(wr * min(games, 5000) for wr, games in values) / total_weight
            index[role][champion] = {
                "win_rate": round(weighted_wr, 2),
                "samples": len(values),
                "weight": total_weight,
            }
    if path is None:
        patch = next(iter(data.get("source", {}).get("patches_detected", ())), None)
        for row in approved_champions(patch):
            index[row["role"]][row["champion"]] = {
                "win_rate": float(row["win_rate"]),
                "samples": 1,
                "weight": min(int(row["games"]), 100_000),
                "source": row["source"],
            }
    return index


def _solo_queue_value(role: str, champion: str) -> tuple[float | None, int]:
    entry = load_solo_queue_winrates().get(role, {}).get(champion)
    if not entry:
        return None, 0
    return float(entry["win_rate"]), int(entry.get("weight", 0))


def _rank_value(profile: dict) -> float:
    order = {
        "iron": 1, "bronze": 2, "silver": 3, "gold": 4, "platinum": 5,
        "emerald": 6, "diamond": 7, "master": 8, "grandmaster": 9, "challenger": 10,
    }
    best = 0.0
    for queue_rows in profile.get("rank_history", {}).values():
        for row in queue_rows:
            tier = str(row.get("tier", "")).lower()
            for name, value in order.items():
                if tier.startswith(name):
                    best = max(best, float(value))
                    break
    return best


def _normalise(value: float, values: list[float]) -> float:
    if not values:
        return 50.0
    lo, hi = min(values), max(values)
    if hi <= lo:
        return 50.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


@lru_cache(maxsize=1)
def load_player_score_index() -> dict[int, dict]:
    players = load_player_profiles().get("players", {})
    recent_index = load_recent_match_index()

    recent_to_summary = {
        "damage_dealt_per_min": "dpm",
        "damage_dealt_share": "damage_share",
        "cs_per_min": "cs_per_min",
        "gold_per_min": "gold_per_min",
        "kda": "kda",
        "op_score": "op_score",
        "lane_lead": "lane_score",
        "kill_participation": "kp",
        "win_rate": "win_rate",
    }

    summaries: dict[int, dict] = {}
    for uid_s, profile in players.items():
        uid = int(uid_s)
        historical = dict(profile.get("summary", {}))
        recent = recent_index.get(uid, {}).get("global", {})
        recent_eff = float(recent.get("effective_games", 0) or 0)
        # Recent individual games can adjust form/playstyle, but deep ranked season
        # history remains the anchor. Normal games are already discounted inside
        # recent_eff by NORMAL_MATCH_WEIGHT.
        blend = min(0.35, recent_eff / 80.0 * 0.35)
        blended = dict(historical)
        for historical_key, recent_key in recent_to_summary.items():
            recent_value = recent.get(recent_key)
            historical_value = historical.get(historical_key)
            if isinstance(recent_value, (int, float)) and recent_eff > 0:
                if isinstance(historical_value, (int, float)):
                    blended[historical_key] = float(historical_value) * (1.0 - blend) + float(recent_value) * blend
                else:
                    blended[historical_key] = float(recent_value)
        blended["_recent_blend"] = blend
        blended["_recent_effective_games"] = recent_eff
        summaries[uid] = blended

    # Newly linked or returning players can have no rank/performance evidence
    # while OP.GG is being refreshed. Treat that as UNKNOWN, not as zero skill;
    # otherwise they become the artificial minimum in every team percentile.
    evidence_uids = {
        uid for uid, summary in summaries.items()
        if float(summary.get('ranked_games_observed', 0) or 0) > 0
        or float(summary.get('_recent_effective_games', 0) or 0) >= 1.0
        or any(float(summary.get(k, 0) or 0) > 0 for k in
               ('damage_dealt_per_min', 'gold_per_min', 'op_score'))
    }
    evidence = [summaries[uid] for uid in evidence_uids]

    metrics = {
        "damage_dealt_per_min": [float(s.get("damage_dealt_per_min", 0) or 0) for s in evidence],
        "damage_dealt_share": [float(s.get("damage_dealt_share", 0) or 0) for s in evidence],
        "cs_per_min": [float(s.get("cs_per_min", 0) or 0) for s in evidence],
        "gold_per_min": [float(s.get("gold_per_min", 0) or 0) for s in evidence],
        "kda": [float(s.get("kda", 0) or 0) for s in evidence],
        "op_score": [float(s.get("op_score", 0) or 0) for s in evidence],
        "lane_lead": [float(s.get("lane_lead", 0) or 0) for s in evidence],
        "kill_participation": [float(s.get("kill_participation", 0) or 0) for s in evidence],
        "win_rate": [float(s.get("win_rate", 0) or 0) for s in evidence],
        "rank": [_rank_value(players[str(uid)]) for uid in evidence_uids],
        "experience": [math.log1p(float(s.get("ranked_games_observed", 0) or 0)) for s in evidence],
    }

    # Role-aware recent percentiles stop a Support's low CS/DPM from being compared
    # directly with a Mid/ADC when we're deciding who fits a specific role/job.
    role_metric_keys = {
        "dpm": "dpm",
        "damage_share": "damage_share",
        "cs": "cs_per_min",
        "gold": "gold_per_min",
        "kda": "kda",
        "op": "op_score",
        "lane": "lane_score",
        "kp": "kp",
        "wr": "win_rate",
    }
    role_values: dict[str, dict[str, list[float]]] = {
        role: {metric: [] for metric in role_metric_keys}
        for role in ROLES
    }
    for uid in summaries:
        for role in ROLES:
            r = recent_index.get(uid, {}).get("by_role", {}).get(role, {})
            if float(r.get("effective_games", 0) or 0) < 2.0:
                continue
            for metric, source_key in role_metric_keys.items():
                role_values[role][metric].append(float(r.get(source_key, 0) or 0))

    output = {}
    for uid, summary in summaries.items():
        # Missing data is explicitly UNKNOWN, never the weakest team's score.
        # Avoid contaminating percentile ranges and assigning low carry potential
        # to a returning player who has not played in the recorded period.
        if uid not in evidence_uids:
            output[uid] = {
                "carry_index": 50.0,
                "reliability_index": 50.0,
                "metrics": {key: 50.0 for key in
                            ("dpm", "damage_share", "cs", "gold", "kda",
                             "op", "lane", "kp", "wr", "rank", "experience")},
                "roles": {},
                "recent_blend": 0.0,
                "recent_effective_games": 0.0,
                "data_quality": "insufficient_history",
            }
            continue
        profile = players[str(uid)]
        pct = {
            "dpm": _normalise(float(summary.get("damage_dealt_per_min", 0) or 0), metrics["damage_dealt_per_min"]),
            "damage_share": _normalise(float(summary.get("damage_dealt_share", 0) or 0), metrics["damage_dealt_share"]),
            "cs": _normalise(float(summary.get("cs_per_min", 0) or 0), metrics["cs_per_min"]),
            "gold": _normalise(float(summary.get("gold_per_min", 0) or 0), metrics["gold_per_min"]),
            "kda": _normalise(float(summary.get("kda", 0) or 0), metrics["kda"]),
            "op": _normalise(float(summary.get("op_score", 0) or 0), metrics["op_score"]),
            "lane": _normalise(float(summary.get("lane_lead", 0) or 0), metrics["lane_lead"]),
            "kp": _normalise(float(summary.get("kill_participation", 0) or 0), metrics["kill_participation"]),
            "wr": _normalise(float(summary.get("win_rate", 0) or 0), metrics["win_rate"]),
            "rank": _normalise(_rank_value(profile), metrics["rank"]),
            "experience": _normalise(math.log1p(float(summary.get("ranked_games_observed", 0) or 0)), metrics["experience"]),
        }
        carry = (
            pct["dpm"] * 0.24 + pct["damage_share"] * 0.20 + pct["cs"] * 0.14
            + pct["gold"] * 0.10 + pct["kda"] * 0.12 + pct["op"] * 0.10
            + pct["lane"] * 0.05 + pct["rank"] * 0.05
        )
        reliability = (
            pct["wr"] * 0.22 + pct["kda"] * 0.20 + pct["op"] * 0.20
            + pct["kp"] * 0.13 + pct["rank"] * 0.10 + pct["experience"] * 0.15
        )

        role_profiles = {}
        for role in ROLES:
            recent_role = recent_index.get(uid, {}).get("by_role", {}).get(role, {})
            eff = float(recent_role.get("effective_games", 0) or 0)
            if eff < 2.0:
                continue
            rpct = {}
            for metric, source_key in role_metric_keys.items():
                values = role_values[role][metric]
                rpct[metric] = _normalise(float(recent_role.get(source_key, 0) or 0), values) if len(values) >= 2 else 50.0
            rpct["rank"] = pct["rank"]
            rpct["experience"] = min(100.0, eff / 20.0 * 100.0)
            role_carry = (
                rpct["dpm"] * 0.24 + rpct["damage_share"] * 0.20 + rpct["cs"] * 0.14
                + rpct["gold"] * 0.10 + rpct["kda"] * 0.12 + rpct["op"] * 0.10
                + rpct["lane"] * 0.05 + rpct["rank"] * 0.05
            )
            role_reliability = (
                rpct["wr"] * 0.22 + rpct["kda"] * 0.20 + rpct["op"] * 0.20
                + rpct["kp"] * 0.13 + rpct["rank"] * 0.10 + rpct["experience"] * 0.15
            )
            role_profiles[role] = {
                "effective_games": round(eff, 2),
                "carry_index": round(role_carry, 1),
                "reliability_index": round(role_reliability, 1),
                "metrics": rpct,
            }

        output[uid] = {
            "carry_index": round(carry, 1),
            "reliability_index": round(reliability, 1),
            "metrics": pct,
            "roles": role_profiles,
            "recent_blend": round(float(summary.get("_recent_blend", 0) or 0), 3),
            "recent_effective_games": round(float(summary.get("_recent_effective_games", 0) or 0), 2),
        }
    return output


def _profile_for(user_id: int) -> dict:
    return load_player_profiles().get("players", {}).get(str(user_id), {})


@lru_cache(maxsize=None)
def role_experience_score(user_id: int, role: str) -> float:
    """Soft role affinity from OP.GG position hints + recent role games.

    This never makes a role legal by itself; Discord role selection remains the hard boundary.
    It only breaks ties between roles a player already said they are willing to play.
    """
    profile = _profile_for(user_id)
    role_key = str(role).upper()
    hint_score = 0.0
    for hint in profile.get("position_hints", []):
        if str(hint.get("position", "")).upper() != role_key:
            continue
        tag = str(hint.get("tag", "")).upper()
        hint_score = max(hint_score, 4.0 if tag == "MAIN" else 2.0 if tag == "SUB" else 1.0)
    recent = load_recent_match_index().get(user_id, {}).get("by_role", {}).get(role, {})
    eff = float(recent.get("effective_games", 0) or 0)
    recent_score = min(4.0, 4.0 * math.log1p(eff) / math.log1p(30)) if eff else 0.0
    return round(hint_score + recent_score, 3)


def _champion_history_for(user_id: int, champion: str) -> dict | None:
    for row in _profile_for(user_id).get("champion_history", []):
        if row.get("champion") == champion:
            return row
    return None


def _historical_pool(user_id: int, role: str, meta: dict, limit: int = 10) -> list[str]:
    profile = _profile_for(user_id)
    role_index = load_solo_queue_winrates().get(role, {})
    recent_role = load_recent_match_index().get(user_id, {}).get("by_role_champion", {}).get(role, {})
    mastery_map = {
        row.get("champion_name"): int(row.get("points", 0) or 0)
        for row in profile.get("masteries", [])
    }
    history_map = {
        row.get("champion"): row
        for row in profile.get("champion_history", [])
        if row.get("champion")
    }

    def supported_in_role(champion: str) -> bool:
        role_row = role_index.get(champion)
        if not role_row:
            return False
        recent = recent_role.get(champion, {})
        if float(recent.get("effective_games", 0) or 0) >= 1.5:
            return True
        # Avoid silly database-technically-valid suggestions such as a champion with
        # only a few hundred off-role SoloQ games. A historical suggestion without
        # player-specific role evidence needs meaningful current role population or
        # organised-play evidence.
        solo_weight = int(role_row.get("weight", 0) or 0)
        _, pro_games = _meta_value(role, champion, meta)
        # Global ownership alone should not migrate a champion into a random role,
        # but strong personal mastery plus credible current evidence in that role is
        # enough to infer a genuine niche pick. Explicit Discord selections always
        # bypass this gate as well.
        mastery = int(mastery_map.get(champion, 0) or 0)
        if mastery >= 50000 and (solo_weight >= 2000 or pro_games >= 10):
            return True
        return solo_weight >= 12000 or pro_games >= 40

    candidates = []
    for champion in set(history_map) | set(recent_role):
        if not supported_in_role(champion):
            continue
        row = history_map.get(champion, {})
        recent = recent_role.get(champion, {})
        games = int(row.get("games", 0) or 0)
        mastery = int(row.get("mastery_points", mastery_map.get(champion, 0)) or 0)
        recent_eff = float(recent.get("effective_games", 0) or 0)
        recent_raw = int(recent.get("raw_games", 0) or 0)

        # Submitted Discord picks bypass this entirely. For suggestions, require either
        # some real history/mastery or enough Ranked/Normal SR evidence to be meaningful.
        if games < 3 and mastery < 50000 and recent_eff < 1.5:
            continue

        wr = float(row.get("recent_weighted_win_rate", 50) or 50)
        op_score = float(row.get("recent_weighted_op_score", 5) or 5)
        confidence = min(1.0, games / 15.0)
        sample_quality = min(1.0, games / 20.0)
        comfort = (
            math.log1p(games) * 3.0 + math.log1p(mastery) / 2.8
            + (wr - 50.0) * 0.07 * confidence + (op_score - 5.0) * 0.45 * confidence
            + sample_quality * 1.5
        )
        if recent_eff:
            recent_conf = min(1.0, recent_eff / 10.0)
            recent_wr = float(recent.get("win_rate", 50) or 50)
            recent_op = float(recent.get("op_score", 5) or 5)
            comfort += (
                math.log1p(recent_eff) * 2.2
                + (recent_wr - 50.0) * 0.045 * recent_conf
                + (recent_op - 5.0) * 0.35 * recent_conf
            )
        candidates.append((comfort, games + recent_eff, recent_raw, mastery, champion))

    candidates.sort(reverse=True)
    if candidates:
        return [champion for _, _, _, _, champion in candidates[:limit]]

    # Last-resort fallback: use high-mastery champions that are currently valid in this role.
    mastery_rows = sorted(profile.get("masteries", []), key=lambda row: int(row.get("points", 0) or 0), reverse=True)
    inferred = [row.get("champion_name") for row in mastery_rows if row.get("champion_name") and supported_in_role(row.get("champion_name"))]
    if inferred:
        return inferred[:limit]

    # If there is no account history at all, keep the draft moving with current role strength.
    ranked = sorted(
        (
            (champion, row)
            for champion, row in role_index.items()
            if int(row.get("weight", 0) or 0) >= 12000
            or _meta_value(role, champion, meta)[1] >= 40
        ),
        key=lambda item: (float(item[1].get("win_rate", 50)), int(item[1].get("weight", 0))),
        reverse=True,
    )
    return [champion for champion, _ in ranked[:limit]]


def _traits(champion: str, meta: dict) -> dict:
    return meta.get("champion_traits", {}).get(champion, {})


def _rating(champion: str, key: str, meta: dict) -> int:
    return int(_traits(champion, meta).get("ratings", {}).get(key, 0) or 0)


def _tags(champion: str, meta: dict) -> set[str]:
    return {str(tag).upper() for tag in _traits(champion, meta).get("roles", [])}


def load_recent_pro(path: str | None = None) -> dict:
    global _RECENT_PRO_CACHE
    target = Path(path) if path else RECENT_PRO_PATH
    try:
        mtime = source_mtime_ns(target)
    except OSError:
        return {}
    if path is None and _RECENT_PRO_CACHE[0] == mtime:
        return _RECENT_PRO_CACHE[1]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if path is None:
        _RECENT_PRO_CACHE = (mtime, data)
    return data


def _recent_pro_patch_minor() -> int | None:
    raw = str(load_recent_pro().get("latest_patch", "")).strip()
    try:
        return int(raw.rsplit(".", 1)[-1])
    except (TypeError, ValueError):
        return None


def _worlds_patch_bridge_factor() -> float:
    """How much pre-Worlds patch priors should still matter (1.0 -> 0.0).

    The first one or two Worlds games are far too small a sample to erase known
    patch-direction information. Fade the bridge across 20 completed current-patch
    Worlds games, then let live tournament evidence stand on its own.
    """
    recent = load_recent_pro()
    minor = _recent_pro_patch_minor()
    if minor is None or minor < 20:
        return 1.0
    worlds_games = 0
    for game in recent.get("games", []):
        tournament = str(game.get("tournament", "")).casefold()
        patch = str(game.get("patch", "")).strip()
        try:
            game_minor = int(patch.rsplit(".", 1)[-1])
        except (TypeError, ValueError):
            continue
        if "world" in tournament and game_minor >= 20:
            worlds_games += 1
    return max(0.0, 1.0 - worlds_games / float(WORLDS_PATCH_BRIDGE_FADE_GAMES))


def _worlds_patch_bridge_active() -> bool:
    return _worlds_patch_bridge_factor() > 0.0


def _apply_worlds_patch_role_adjustment(role: str, champion: str, value: float) -> float:
    factor = _worlds_patch_bridge_factor()
    if factor <= 0.0:
        return value
    delta = WORLDS_PATCH_ROLE_ADJUSTMENTS.get((role, champion), 0.0) * factor
    return max(0.35, min(0.65, value + delta))


def _meta_value(role: str, champion: str, meta: dict) -> tuple[float, int]:
    entry = meta.get("role_stats", {}).get(role, {}).get(champion)
    historical_wr = float(entry.get("bayes_win_rate", 0.5)) if entry else 0.5
    historical_games = int(entry.get("games", 0)) if entry else 0
    recent = load_recent_pro().get("role_stats", {}).get(role, {}).get(champion)
    if not recent:
        return _apply_worlds_patch_role_adjustment(role, champion, historical_wr), historical_games
    recent_wr = float(recent.get("bayes_win_rate", 0.5))
    recent_weighted_games = max(0.0, float(recent.get("weighted_games", 0.0)))
    current_weight = min(24.0, recent_weighted_games * 4.0)
    old_weight = min(10.0, float(historical_games))
    if current_weight <= 0:
        return _apply_worlds_patch_role_adjustment(role, champion, historical_wr), historical_games
    blended = ((historical_wr * old_weight) + (recent_wr * current_weight)) / max(1e-9, old_weight + current_weight)
    blended = _apply_worlds_patch_role_adjustment(role, champion, blended)
    return blended, historical_games + int(round(current_weight))


@lru_cache(maxsize=128)
def _adc_field_strength_adjustment(adc: str) -> float:
    """Small, confidence-weighted ADC strength versus its own SoloQ sample.

    Historical pro duo priors must not drown out current high-elo ADC strength.
    Use the median of the same sampled ADC field, not a fixed 50% threshold:
    high-rank LoLalytics champion win rates need not centre on exactly 50%.
    Missing/sparse samples are neutral. Bounded so valid pairs remain playable.
    """
    # Do not treat last patch's D2+ snapshot as current-patch evidence.
    if live_patch_active():
        patches = load_lane_matchups().get("source", {}).get("patches_detected", ())
        if not any(str(patch).split(".")[-1] == LIVE_PATCH.split(".")[-1] for patch in patches):
            return 0.0
    index = load_solo_queue_winrates().get("ADC", {})
    row = index.get(adc) or {}
    weight = int(row.get("weight", 0) or 0)
    if weight < 1000 or row.get("win_rate") is None:
        return 0.0
    peers = sorted(
        float(value["win_rate"]) for value in index.values()
        if int(value.get("weight", 0) or 0) >= 1500
        and value.get("win_rate") is not None
    )
    if len(peers) < 8:
        return 0.0
    n = len(peers)
    baseline = (peers[(n - 1) // 2] + peers[n // 2]) / 2.0
    differential = float(row["win_rate"]) - baseline
    confidence = min(1.0, weight / 5000.0)
    return round(max(-1.35, min(1.35, differential * 0.65 * confidence)), 3)


def _bot_lane_duo_score(adc: str, support: str, meta: dict) -> dict:
    """Score ADC/support as one lane unit, using pro evidence plus kit/lane identity."""
    score = 5.0
    reasons = []

    prior = BOT_DUO_PRIORS.get((adc, support))
    if prior is not None:
        score += 3.2 * prior
        reasons.append("proven organised-play pairing")

    if adc in HYPERCARRY_ADCS and support in PEEL_SUPPORTS:
        score += 1.25
        reasons.append("carry + peel")
    if adc in ALL_IN_ADCS and support in ENGAGE_SUPPORTS:
        score += 1.35
        reasons.append("all-in timing matches")
    if adc in POKE_ADCS and support in POKE_SUPPORTS:
        score += 1.15
        reasons.append("lane pressure / poke matches")
    if adc == "Ashe" and support in (POKE_SUPPORTS | ENGAGE_SUPPORTS):
        score += 0.65
        reasons.append("utility / pick lane")

    if adc in {"Ezreal", "Jhin"} and support in {"Lulu", "Yuumi", "Soraka"}:
        score -= 2.2
        reasons.append("low combined lane pressure")
    if adc == "Sivir" and support in {"Morgana", "Bard", "Xerath", "Vel'Koz"}:
        score -= 1.15
        reasons.append("limited direct 2v2 synergy")

    lift, games = _pair_value("ADC", adc, "Support", support, meta)
    if games:
        confidence = min(1.0, games / 16.0)
        score += max(-1.8, min(1.8, lift * 30.0)) * confidence
        if games >= 5:
            reasons.append(f"{games} recent pro pair games")
    elif prior is None:
        score -= 0.45
        reasons.append("little organised-play evidence")

    # Independent licensed pro dataset; never presented as SoloQ evidence.
    # Bounded to < 0.8/10 so practiced duo comfort + botlane gameplan dominate.
    open_pair = open_pro_pair_signal("ADC", adc, "Support", support)
    score += open_pair["adjustment"]
    current_patch = next(iter(load_lane_matchups().get("source", {}).get("patches_detected", ())), None)
    solo_pair = approved_pair_signal("ADC", adc, "Support", support, current_patch)
    score += solo_pair["adjustment"]

    # Historical synergy cannot keep a weaker current ADC at the top by itself.
    # This is sampled evidence, not a hard-coded patch 26.21 win rate.
    adc_field_adjustment = _adc_field_strength_adjustment(adc)
    if adc_field_adjustment <= -0.30:
        reasons.insert(0, "ADC below current sampled role median")
    elif adc_field_adjustment >= 0.30:
        reasons.insert(0, "ADC above current sampled role median")
    if solo_pair["games"] >= 500 and solo_pair["adjustment"] > 0.1:
        reasons.append(f"{solo_pair['games']} approved SoloQ duo observations")
    if open_pair["games"] >= 8 and open_pair["adjustment"] > 0.12:
        reasons.append(f"{open_pair['games']} ChainCC pro duo games (CC BY 4.0)")

    score = max(0.0, min(10.0, score))
    grade = "excellent" if score >= 8.2 else "strong" if score >= 7.0 else "playable" if score >= 5.5 else "weak" if score >= 4.0 else "avoid"
    return {"score": round(score, 1), "grade": grade, "reasons": reasons[:3], "pro_games": games}



def _early_map_resilience(
    champions: dict[str, str],
    *,
    enemy_roles: dict[str, str] | None = None,
    player_fit: dict | None = None,
) -> dict:
    """Bounded draft-stage wave access and enemy-support roam response.

    This is a qualitative *risk screen*, not observed priority or a substitute
    for matchup/early gold data. Unknown enemy roles stay uncertain; no support
    is assumed to have roamed unless a caller supplies that actual evidence.
    """
    enemy_roles = enemy_roles or {}
    roles = (player_fit or {}).get("role_details") or {}
    top = EARLY_TOP_PRIORITY.get(champions["Top"], .51)
    mid = EARLY_MID_PRIORITY.get(champions["Mid"], .52)
    adc = EARLY_BOT_ADC_PRIORITY.get(champions["ADC"], .52)
    support = EARLY_BOT_SUPPORT_PRIORITY.get(champions["Support"], .52)
    bot = (adc + support) / 2.0

    # Reliable lane partners more consistently convert favourable kit-based
    # wave access into real pressure. Unknown players are neutral.
    bot_reliability = min(
        float(roles.get("ADC", {}).get("reliability_index", 50.0)),
        float(roles.get("Support", {}).get("reliability_index", 50.0)),
    )
    bot -= max(0.0, 46.0 - bot_reliability) / 100.0 * .34
    bot = max(.15, min(.85, bot))

    pressure = {"Top": round(top, 2), "Mid": round(mid, 2), "Bot": round(bot, 2)}
    notes = []
    penalty = 0.0
    support_reliability = float(
        roles.get("Support", {}).get("reliability_index", 50.0)
    )
    if support_reliability < 35.0 and bot < .60:
        penalty += min(1.4, .65 + (35.0 - support_reliability) / 35.0 * .90)
        notes.append("bot lane needs more self-sufficient wave control")
    low_lanes = sum(value < .43 for value in (top, mid, bot))
    if low_lanes >= 2:
        penalty += 1.3 + .55 * (low_lanes - 2)
        notes.append("multiple lanes may concede early wave access")
    if champions["Jungle"] in JUNGLE_NEEDS_LANE_ACCESS:
        if mid < .46 and bot < .50:
            penalty += 1.7
            notes.append("jungle skirmishes lack reliable mid/bot first move")
        elif low_lanes >= 2:
            penalty += .6

    # Protect a strong solo-lane/jungle win condition from total isolation,
    # but do not assign a permanent 'carry player' or role.
    carry_options = (player_fit or {}).get("carry_options") or []
    if carry_options:
        main = carry_options[0]
        main_role = main.get("role")
        if main_role == "Top" and mid < .46 and bot < .50:
            penalty += 1.2
            notes.append("topside win condition may be isolated without cross-map pressure")
        elif main_role == "Jungle" and mid < .48 and bot < .48:
            penalty += 1.2
            notes.append("jungle win condition lacks dependable lane access")

    enemy_support = enemy_roles.get("Support")
    known_roamer = enemy_support in ROAMING_SUPPORT_THREATS
    if known_roamer and bot < .58:
        # Stronger warning only when the enemy support is actually revealed.
        # A priority advantage is merely the *ability* to punish a roam.
        penalty += min(2.8, (.58 - bot) * 8.0 + .4)
        notes.append(f"{enemy_support} can roam while bot may struggle to punish the empty lane")
    if known_roamer and mid < .45:
        penalty += .5
        notes.append("limited mid cover against support rotations")

    # Modest reward for multiple options to answer roams, capped to prevent
    # automatically sidelining organised-play scaling pairs (e.g. Yunara/Lulu).
    bonus = .0
    if bot >= .65 and mid >= .55:
        bonus = .65
        notes.append("bot wave access plus mid cover can punish support rotations")
    adjustment = round(max(-5.8, min(.65, bonus - penalty)), 2)
    return {
        "adjustment": adjustment,
        "bot_priority": round(bot, 2),
        "lane_priority": pressure,
        "enemy_roam_threat": enemy_support if known_roamer else None,
        "reasons": notes[:4],
    }


def _coordinated_duo_score(role_a: str, champ_a: str, role_b: str, champ_b: str, meta: dict) -> dict:
    """Score non-bot 2-man packages, prioritising current pro evidence and known setup/follow-up pairs."""
    role_index = {role: i for i, role in enumerate(ROLES)}
    if role_index[role_a] > role_index[role_b]:
        role_a, role_b = role_b, role_a
        champ_a, champ_b = champ_b, champ_a
    score = 5.0
    reasons = []
    prior = COORDINATED_DUO_PRIORS.get((role_a, role_b, champ_a, champ_b))
    if prior is not None:
        score += 2.8 * prior
        reasons.append("proven organised-play package")
    lift, games = _pair_value(role_a, champ_a, role_b, champ_b, meta)
    if games:
        confidence = min(1.0, games / 14.0)
        score += max(-2.0, min(2.0, lift * 30.0)) * confidence
        if games >= 5:
            reasons.append(f"{games} recent pro pair games")
    elif prior is None:
        score -= 0.25
    # Generic setup/follow-up signal: CC-heavy initiator plus damage/control follow-up.
    control = (_rating(champ_a, "control", meta) + _rating(champ_b, "control", meta)) / 6.0
    damage = (_rating(champ_a, "damage", meta) + _rating(champ_b, "damage", meta)) / 6.0
    if control >= 0.67 and damage >= 0.60:
        score += 0.65
        reasons.append("setup + follow-up fit")
    open_pair = open_pro_pair_signal(role_a, champ_a, role_b, champ_b)
    score += open_pair["adjustment"]
    current_patch = next(iter(load_lane_matchups().get("source", {}).get("patches_detected", ())), None)
    solo_pair = approved_pair_signal(role_a, champ_a, role_b, champ_b, current_patch)
    score += solo_pair["adjustment"]
    if solo_pair["games"] >= 500 and solo_pair["adjustment"] > 0.1:
        reasons.append(f"{solo_pair['games']} approved SoloQ pair observations")
    if open_pair["games"] >= 8 and open_pair["adjustment"] > 0.12:
        reasons.append(f"{open_pair['games']} ChainCC pro pair games (CC BY 4.0)")
    score = max(0.0, min(10.0, score))
    grade = "excellent" if score >= 8.2 else "strong" if score >= 7.0 else "playable" if score >= 5.5 else "weak" if score >= 4.0 else "avoid"
    return {"score": round(score, 1), "grade": grade, "reasons": reasons[:3], "pro_games": games}


def _ranged_top_structure_fit(champions: dict[str, str], meta: dict) -> dict:
    """Score whether the other four picks pay the structural cost of a ranged carry Top."""
    top = champions.get("Top", "")
    top_tags = _tags(top, meta)
    top_traits = _traits(top, meta)
    ranged_carry = (
        str(top_traits.get("attack_type") or "").upper() == "RANGED"
        and bool(top_tags & {"MARKSMAN", "ARTILLERY"})
        and not bool(top_tags & TANK_TAGS)
    )
    if not ranged_carry:
        return {"active": False, "plan_adjustment": 0.0, "score_adjustment": 0.0, "reason": "", "missing": ""}

    setup_roles = ("Jungle", "Mid", "Support")
    reliable_setup = []
    frontline = []
    for role in setup_roles:
        champion = champions[role]
        tags = _tags(champion, meta)
        if tags & ENGAGE_TAGS or _rating(champion, "control", meta) >= 3:
            reliable_setup.append(role)
        if tags & TANK_TAGS or _rating(champion, "toughness", meta) >= 3:
            frontline.append(role)

    jungle = champions["Jungle"]
    mid = champions["Mid"]
    adc = champions["ADC"]
    support = champions["Support"]
    jungle_low_econ_frontline = (
        jungle in LOW_ECON_FRONTLINE_JUNGLES
        or (bool(_tags(jungle, meta) & TANK_TAGS) and _rating(jungle, "damage", meta) <= 2)
    )
    mid_waveclear = mid in STRONG_WAVECLEAR_MIDS
    mid_structure = mid in MID_FRONTLINE_CONTROL or _rating(mid, "control", meta) >= 3

    non_top_poke = 0
    for role in ("Jungle", "Mid", "ADC", "Support"):
        champion = champions[role]
        if champion in POKE_CHAMPS or (role == "Support" and champion in POKE_SUPPORTS):
            non_top_poke += 1
    coherent_siege = (
        non_top_poke >= 3
        and bool(frontline or reliable_setup)
        and (jungle_low_econ_frontline or mid_waveclear)
    )

    # A double-ranged poke bot beside a ranged Top is especially greedy: it needs
    # Jungle to absorb low-econ frontline duty and Mid to provide waveclear/control.
    greedy_poke_shell = adc in POKE_ADCS and support in POKE_SUPPORTS
    if greedy_poke_shell:
        if jungle_low_econ_frontline and mid_waveclear and mid_structure:
            return {
                "active": True, "plan_adjustment": 0.75, "score_adjustment": 1.6,
                "reason": f"low-econ Jungle + waveclear/control Mid unlock {top} Top", "missing": "",
            }
        missing_parts = []
        if not jungle_low_econ_frontline:
            missing_parts.append("low-econ frontline Jungle")
        if not mid_waveclear:
            missing_parts.append("strong Mid waveclear")
        if not mid_structure:
            missing_parts.append("Mid frontline/control")
        return {
            "active": True, "plan_adjustment": -3.0, "score_adjustment": -7.5,
            "reason": "", "missing": f"{', '.join(missing_parts)} for ranged Top ({top})",
        }

    if coherent_siege:
        return {
            "active": True, "plan_adjustment": 0.45, "score_adjustment": 1.0,
            "reason": f"the other lanes complete {top}'s ranged siege shell", "missing": "",
        }
    if len(reliable_setup) >= 2 and frontline and (jungle_low_econ_frontline or mid_waveclear):
        return {
            "active": True, "plan_adjustment": 0.45, "score_adjustment": 1.0,
            "reason": f"multiple setup/frontline sources unlock {top} Top", "missing": "",
        }
    if not frontline:
        return {
            "active": True, "plan_adjustment": -2.8, "score_adjustment": -7.0,
            "reason": "", "missing": f"frontline outside ranged Top ({top})",
        }
    if not reliable_setup:
        return {
            "active": True, "plan_adjustment": -2.6, "score_adjustment": -6.5,
            "reason": "", "missing": f"reliable setup for ranged Top ({top})",
        }
    return {
        "active": True, "plan_adjustment": -2.2, "score_adjustment": -5.5,
        "reason": "", "missing": f"second setup/frontline source for ranged Top ({top})",
    }


def _team_plan_completion(champions: dict[str, str], meta: dict) -> dict:
    """Infer a team plan from strong 2-man packages and score whether the other three picks complete it."""
    adc, support = champions["ADC"], champions["Support"]
    top, jungle, mid = champions["Top"], champions["Jungle"], champions["Mid"]
    bot = _bot_lane_duo_score(adc, support, meta)
    mid_jungle = _coordinated_duo_score("Jungle", jungle, "Mid", mid, meta)
    shell = (top, jungle, mid)
    shell_tags = [_tags(c, meta) for c in shell]
    tanky = sum(bool(tags & TANK_TAGS) for tags in shell_tags)
    engage = sum(bool(tags & ENGAGE_TAGS) for tags in shell_tags)
    dive = sum(bool(tags & DIVE_TAGS) for tags in shell_tags)
    poke = sum(c in POKE_CHAMPS for c in shell)
    control = sum(_rating(c, "control", meta) for c in shell) / 3.0
    mobility = sum(_rating(c, "mobility", meta) for c in shell) / 3.0
    damage = sum(_rating(c, "damage", meta) for c in shell) / 3.0

    plan, score = "balanced", 5.0
    reasons, missing = [], []

    if adc in HYPERCARRY_ADCS and support in PEEL_SUPPORTS:
        plan = "scaling front-to-back"
        if tanky >= 1:
            score += 1.2; reasons.append("frontline protects scaling bot")
        else:
            score -= 1.8; missing.append("frontline")
        if engage >= 1:
            score += 0.9; reasons.append("reliable fight start")
        else:
            score -= 1.2; missing.append("engage")
        if control >= 1.8:
            score += 0.8; reasons.append("control buys carry space")
        if damage >= 1.7:
            score += 0.4; reasons.append("non-ADC damage agency")
        if tanky == 0 and engage == 0:
            score -= 0.8

    elif adc == "Lucian" and support in {"Nami", "Milio"}:
        plan = "tempo skirmish"
        if float(mid_jungle["score"]) >= 7.0:
            score += 1.3; reasons.append("mid-jungle can drive tempo")
        else:
            score -= 0.9; missing.append("strong mid-jungle tempo")
        if engage >= 1 or mobility >= 2.0:
            score += 1.0; reasons.append("access for mid-game fights")
        else:
            score -= 1.0; missing.append("fight access")
        if control >= 1.7:
            score += 0.7; reasons.append("setup for Lucian burst window")
        if tanky >= 1:
            score += 0.5
        if tanky == 0 and control < 1.5:
            score -= 0.8

    elif (
        adc in POKE_ADCS
        and adc != "Ashe"
        and support in POKE_SUPPORTS
        and (
            sum(c in POKE_CHAMPS for c in champions.values())
            + int(support in POKE_SUPPORTS and support not in POKE_CHAMPS)
        ) >= 3
    ):
        # A poke bot lane does not automatically make the whole composition siege.
        # Require genuine team-wide ranged pressure and avoid calling heavy-engage
        # shells (e.g. Poppy + Amumu + Ashe/Seraphine) "poke / siege".
        plan = "poke / siege"
        if poke >= 1:
            score += 1.0; reasons.append("shell extends poke pressure")
        else:
            score -= 0.6; missing.append("secondary ranged pressure")
        if control >= 1.8:
            score += 0.9; reasons.append("control protects siege space")
        if tanky >= 1 or engage >= 1:
            score += 0.7; reasons.append("frontline/engage stops free collapse")
        else:
            score -= 1.2; missing.append("anti-dive/frontline")
        # A true split-push/diver top can coexist with poke, but it is a variant
        # win condition rather than another siege piece. Do not present Camille-
        # style tops as the default pure-poke answer just because they are strong.
        if top in SPLIT_CHAMPS and top not in POKE_CHAMPS:
            plan = "poke + side-lane"
            score -= 1.0
            reasons.append(f"{top} adds side-lane/flank pressure rather than direct siege")
            missing.append("top does not directly extend poke pressure")

    elif adc in ALL_IN_ADCS and support in ENGAGE_SUPPORTS:
        plan = "dive / all-in"
        if dive >= 1:
            score += 1.0; reasons.append("shell follows bot all-in")
        if engage >= 2:
            score += 1.0; reasons.append("multiple reliable engage angles")
        elif engage == 0:
            score -= 1.4; missing.append("engage")
        # Opening Worlds 26.20 validated Kai'Sa as the follow-up carry in layered
        # engage shells. Reward the structure, not the one-game result itself.
        if adc == "Kai'Sa":
            layered_access = engage + dive
            if layered_access >= 2:
                score += 0.50
                if not (top in SPLIT_CHAMPS and bool(_tags(top, meta) & DIVE_TAGS)):
                    reasons.append("layered engage gives Kai'Sa reliable backline access")
            elif layered_access >= 1:
                score += 0.25
        if mobility >= 1.8:
            score += 0.7
        if tanky >= 1:
            score += 0.5
        if top in SPLIT_CHAMPS and bool(_tags(top, meta) & DIVE_TAGS):
            score += 0.8
            reasons.append("top adds side-lane threat and a second backline access angle")
        if control < 1.3:
            score -= 0.7; missing.append("setup/control")

    elif adc == "Ashe" or support in {"Bard", "Neeko", "Rakan", "Nautilus", "Thresh"}:
        plan = "pick / control"
        if control >= 1.8:
            score += 1.0; reasons.append("shell chains pick tools")
        else:
            score -= 0.7; missing.append("follow-up control")
        if engage >= 1:
            score += 0.8
        if damage >= 1.8:
            score += 0.7; reasons.append("enough damage behind utility bot")
        else:
            score -= 0.8; missing.append("damage follow-up")

    else:
        mj = float(mid_jungle["score"])
        if mj >= 8.2:
            plan = "mid-jungle led skirmish"
            score += 1.2; reasons.append("excellent mid-jungle package")
            if tanky >= 1: score += 0.5
            if control >= 1.7: score += 0.5
        elif mj >= 7.0:
            score += 0.5; reasons.append("strong mid-jungle package")

    # Patch 26.19 lowered top-role Teleport cooldowns specifically to support
    # side-lane strategies. Give those structures a modest bridge bump until
    # actual Worlds 26.20 games are present in the live feed.
    bridge_factor = _worlds_patch_bridge_factor()
    if bridge_factor > 0.0 and top in SPLIT_CHAMPS:
        score += 0.25 * bridge_factor
        reasons.append("Worlds patch gives side-lane/Teleport plans more room")

    top_structure = _ranged_top_structure_fit(champions, meta)
    score += float(top_structure["plan_adjustment"])
    if top_structure.get("reason"):
        reasons.insert(0, str(top_structure["reason"]))
    if top_structure.get("missing"):
        missing.insert(0, str(top_structure["missing"]))

    score += (float(bot["score"]) - 5.0) * 0.18
    score += (float(mid_jungle["score"]) - 5.0) * 0.10
    score = max(0.0, min(10.0, score))
    grade = "excellent" if score >= 8.2 else "strong" if score >= 7.0 else "playable" if score >= 5.5 else "weak" if score >= 4.0 else "poor fit"
    return {"plan": plan, "score": round(score, 1), "grade": grade, "reasons": reasons[:3], "missing": missing[:3]}


def _pair_value(role_a: str, champ_a: str, role_b: str, champ_b: str, meta: dict) -> tuple[float, int]:
    role_index = {role: i for i, role in enumerate(ROLES)}
    if role_index[role_a] > role_index[role_b]:
        role_a, role_b = role_b, role_a
        champ_a, champ_b = champ_b, champ_a
    key = f"{role_a}|{role_b}|{champ_a}|{champ_b}"
    entry = meta.get("pair_stats", {}).get(key)
    historical_lift = float(entry.get("lift", 0.0)) if entry else 0.0
    historical_games = int(entry.get("games", 0)) if entry else 0
    recent = load_recent_pro().get("pair_stats", {}).get(key)
    if not recent:
        return historical_lift, historical_games
    recent_lift = float(recent.get("lift", 0.0))
    recent_weighted_games = max(0.0, float(recent.get("weighted_games", 0.0)))
    current_weight = min(16.0, recent_weighted_games * 4.0)
    old_weight = min(8.0, float(historical_games))
    if current_weight <= 0:
        return historical_lift, historical_games
    blended = ((historical_lift * old_weight) + (recent_lift * current_weight)) / max(1e-9, old_weight + current_weight)
    return blended, historical_games + int(round(current_weight))


def _prune_pool(role: str, champions: Iterable[str], meta: dict, limit: int = 8, *, enemy_roles: dict[str, str] | None = None, user_id: int | None = None, linked_bot_pool: Iterable[str] | None = None, linked_role_pool: Iterable[str] | None = None, rotation_token: str | None = None, pinned_champion: str | None = None) -> list[str]:
    """Keep the search small without turning it into a pure pro-meta shortlist."""
    unique = list(dict.fromkeys(champions))
    if len(unique) <= limit:
        return unique

    def meta_rank(champion: str):
        bayes, games = _meta_value(role, champion, meta)
        confidence = min(1.0, games / 12.0)
        known = 1 if _traits(champion, meta) else 0
        # Never choose the ten available champions *solely* by historical pro
        # win rate. The actual person's comfort/role experience is fundamental.
        # An unusually broad saved pool does not mean the player has practised
        # 40 top champions to the same standard.
        comfort = 0.0
        if user_id is not None and user_id > 0:
            own = _champion_comfort(user_id, champion, role)
            effective = float(load_recent_match_index().get(user_id, {})
                              .get("by_role_champion", {}).get(role, {})
                              .get(champion, {}).get("effective_games", 0) or 0)
            # Form helps order eligible picks, but must not monopolise the
            # short list after two recent high-scoring games.
            comfort = (own - 5.0) * 0.35 + min(3.0, effective) * 0.20
        meta_support = confidence * (bayes - 0.5) * 4.5 + min(1.0, games / 30.0)
        # Patch-day directional evidence can break close ties, not displace comfort.
        meta_support += live_patch_role_adjustment(role, champion) * 0.30
        return (known, round(comfort + meta_support, 3), comfort, math.log1p(games), champion.lower())

    selected: list[str] = []

    def add(champion: str):
        if champion not in selected and len(selected) < limit:
            selected.append(champion)

    # A previously chosen /league plan deserves consideration on the live
    # client unless the pick was banned or removed from the player's own pool.
    if pinned_champion in unique:
        add(pinned_champion)

    # Explicitly confirmed picks must survive candidate compression, especially
    # for a flexible player's secondary ADC role. This preserves eligibility,
    # NOT a ranking bonus: bad pairings and full-team plans can still lose.
    if user_id is not None:
        for champion in unique:
            if _explicitly_confirmed_player_pick(int(user_id), role, champion):
                add(champion)

    # Preserve known favourable responses from the player's *actual pool* before
    # pruning it to the ten candidates the live search can afford.
    if enemy_roles and enemy_roles.get(role):
        counters = sorted(unique, key=lambda champ: live_lane_matchup_adjustment(
            {role: champ}, enemy_roles
        )["adjustment"], reverse=True)
        for champion in counters[:2]:
            if live_lane_matchup_adjustment({role: champion}, enemy_roles)["adjustment"] > 0.35:
                add(champion)

    # Preserve strong *complete* ADC+Support options before recent personal
    # results shrink a broad 30+ champion pool to ten. Otherwise a player who
    # owns Yunara+Lulu loses that package before the combination is ever scored.
    # Include diverse duo styles rather than ten nearly identical enchanter pairs.
    if role in {"ADC", "Support"} and linked_bot_pool:
        partners = list(dict.fromkeys(linked_bot_pool))
        # Preserve explicitly discussed multi-champion packages without
        # declaring that they beat every other available bot pair.
        for adc, support in EXPLICIT_BOT_PAIR_COVERAGE:
            champ, partner = (adc, support) if role == "ADC" else (support, adc)
            if champ in unique and partner in partners:
                add(champ)
        pair_rows = []
        for champion in unique:
            for partner in partners:
                adc, support = (champion, partner) if role == "ADC" else (partner, champion)
                duo_score = float(_bot_lane_duo_score(adc, support, meta)["score"])
                pair_rows.append((duo_score, meta_rank(champion), champion, partner))
        # The top role pairings are viable prepared draft packages, not
        # single-player win-rate recommendations.
        for duo_score, _rank, champion, partner in sorted(pair_rows, reverse=True):
            if duo_score < 7.4:
                break
            if len(selected) >= min(4, limit):
                break
            add(champion)

    # Mid/Jungle is a unit just like ADC/Support. Its pro/meta synergies
    # deserve candidate coverage even if the assigned player has recently
    # carried on a different champion. Do not assume either player can pilot
    # an unsubmitted champion.
    if role in {"Jungle", "Mid"} and linked_role_pool:
        for candidate in sorted(
            unique,
            key=lambda champ: (
                max((
                    _coordinated_duo_score(
                        "Jungle", champ if role == "Jungle" else partner,
                        "Mid", partner if role == "Jungle" else champ, meta
                    )["score"] for partner in linked_role_pool
                ), default=0.0),
                meta_rank(champ),
            ), reverse=True,
        ):
            best = max((
                _coordinated_duo_score(
                    "Jungle", candidate if role == "Jungle" else partner,
                    "Mid", partner if role == "Jungle" else candidate, meta
                )["score"] for partner in linked_role_pool
            ), default=0.0)
            if best < 7.35 or len(selected) >= min(4, limit):
                break
            add(candidate)

    # Preserve several currently supported/meta choices.
    for champion in sorted(unique, key=meta_rank, reverse=True)[:3]:
        add(champion)

    # Then preserve structurally different comfort picks so a lower-meta tank,
    # control pick or utility pick can still rescue an otherwise bad draft.
    for trait in ("toughness", "control", "utility", "damage", "mobility"):
        ranked = sorted(
            unique,
            key=lambda champion: (
                _rating(champion, trait, meta),
                meta_rank(champion),
            ),
            reverse=True,
        )
        if ranked:
            add(ranked[0])

    # Fill any remaining slots with the broader meta ordering.
    for champion in sorted(unique, key=meta_rank, reverse=True):
        add(champion)
        if len(selected) >= limit:
            break

    # Prepared drafts should not evaluate exactly the same subset of a 40-pick
    # self-declared pool game after game. Keep the important counter/synergy/
    # top-meta core, then sample a *bounded* tail from the same selected pool.
    # The scorer still rejects poor pairs, role fit and whole-team structure.
    if (rotation_token and user_id and
        role in TRUST_DECLARED_POOL_ROLES.get(int(user_id), ()) and
        len(unique) > limit):
        protected = {champ for champ in selected[:min(6, limit)]}
        if pinned_champion:
            protected.add(pinned_champion)
        reserve = min(4, max(1, limit // 4))
        kept = list(selected[:limit - reserve])
        for champ in protected:
            if champ in unique and champ not in kept:
                kept[-1:] = [champ]
        alternatives = [champ for champ in unique if champ not in kept]
        alternatives.sort(key=lambda champ: hashlib.sha256(
            f"{rotation_token}|{user_id}|{role}|{champ}".encode("utf-8")
        ).digest())
        for champ in alternatives:
            if len(kept) >= limit:
                break
            kept.append(champ)
        return kept[:limit]
    return selected


def _damage_mix(champions: dict[str, str], meta: dict) -> tuple[float, str]:
    physical = 0.0
    magic = 0.0
    unknown = 0
    for champion in champions.values():
        if champion in HYBRID_CHAMPS:
            physical += 0.5
            magic += 0.5
            continue
        adaptive = str(_traits(champion, meta).get("adaptive_type") or "")
        if adaptive == "PHYSICAL_DAMAGE":
            physical += 1
        elif adaptive == "MAGIC_DAMAGE":
            magic += 1
        else:
            unknown += 1
    known = physical + magic
    if known == 0:
        return 5.0, "damage mix unknown"
    minority = min(physical, magic)
    balance = minority / known
    score = min(10.0, 2.0 + balance * 20.0)
    if physical == 0:
        label = "magic-heavy"
    elif magic == 0:
        label = "physical-heavy"
    elif balance >= 0.35:
        label = "balanced physical/magic"
    elif physical > magic:
        label = "physical-leaning"
    else:
        label = "magic-leaning"
    if unknown:
        label += f" ({unknown} unknown)"
    return score, label


def _credible_damage_profile(champions: dict[str, str], meta: dict) -> dict:
    """Require both damage types to come from a meaningful threat, not token support AP."""
    totals = {"physical": 0.0, "magic": 0.0}
    sources = {"physical": [], "magic": []}
    for role, champion in champions.items():
        damage_rating = float(_rating(champion, "damage", meta) or 0)
        economy = 0.40 if role == "Support" else 1.0
        contribution = damage_rating * economy
        if champion in HYBRID_CHAMPS:
            physical = contribution * 0.5
            magic = contribution * 0.5
            totals["physical"] += physical
            totals["magic"] += magic
            if physical >= CREDIBLE_DAMAGE_MIN:
                sources["physical"].append(champion)
            if magic >= CREDIBLE_DAMAGE_MIN:
                sources["magic"].append(champion)
            continue
        adaptive = str(_traits(champion, meta).get("adaptive_type") or "")
        if adaptive == "PHYSICAL_DAMAGE":
            totals["physical"] += contribution
            if contribution >= CREDIBLE_DAMAGE_MIN:
                sources["physical"].append(champion)
        elif adaptive == "MAGIC_DAMAGE":
            totals["magic"] += contribution
            if contribution >= CREDIBLE_DAMAGE_MIN:
                sources["magic"].append(champion)

    return {
        "physical": round(totals["physical"], 2),
        "magic": round(totals["magic"], 2),
        "physical_sources": sources["physical"],
        "magic_sources": sources["magic"],
        "credible_mixed": (
            totals["physical"] >= CREDIBLE_DAMAGE_MIN
            and totals["magic"] >= CREDIBLE_DAMAGE_MIN
        ),
    }


def live_lane_matchup_adjustment(
    champions: dict[str, str],
    enemy_roles: dict[str, str] | None,
    matchup_data: dict | None = None,
) -> dict:
    """Calibrated *same-role* matchup evidence, evaluated while assembling five-man drafts.

    This is a secondary signal, not a command to abandon team composition or player
    comfort. Unknown matches yield zero rather than an invented counter advantage.
    The LoLalytics snapshot is limited; never interpret missing data as neutral proof.
    """
    if not enemy_roles:
        return {"adjustment": 0.0, "edges": []}
    data = matchup_data if matchup_data is not None else load_lane_matchups()
    edges = []
    total = 0.0
    observed_patches = {str(p).split('.')[-1] for p in data.get('source', {}).get('patches_detected', [])}
    older_snapshot = live_patch_active() and bool(observed_patches) and '21' not in observed_patches
    quality = 0.85 if older_snapshot else 1.0
    for role, enemy in enemy_roles.items():
        own = champions.get(role)
        if role not in ROLES or not enemy or not own:
            continue
        advantage, games = matchup_response(role, enemy, own, data)
        if advantage is None:
            continue
        residual, points = calibrated_matchup(role, advantage, games, quality=quality)
        edges.append({
            "role": role, "enemy": enemy, "ours": own,
            "advantage": round(float(advantage), 2),
            "adjusted": residual, "points": points, "games": games,
            "locked": True,
        })
        total += points
    # Prevent patch-specific matchup swings from overwhelming the better team plan.
    return {"adjustment": round(max(-4.5, min(4.5, total)), 3), "edges": edges}


def live_enemy_draft_adjustment(champions: dict[str, str], enemies: Iterable[str], meta: dict) -> dict:
    """Small live-draft adjustment for revealed *locked* enemies, not speculative hovers.

    Counter ultimates and damage-type concentration are team-wide draft decisions:
    they must be scored before the engine discards lower-ranked candidate comps.
    """
    opponents = {str(champion) for champion in enemies if champion}
    if not opponents:
        return {"adjustment": 0.0, "reasons": []}

    frontline = sum(
        bool(_tags(champion, meta) & TANK_TAGS) or _rating(champion, "toughness", meta) >= 3
        for champion in opponents
    )
    adjustment = 0.0
    reasons: list[str] = []

    # Cross-role ultimate-steal value: Malphite/Ornn/Rell are useful Sylas
    # targets regardless of whether Sylas is assigned jungle, mid or top.
    if "Sylas" in champions.values():
        steal = (9.0 if "Malphite" in opponents else 0.0)
        steal += 3.0 if "Ornn" in opponents else 0.0
        steal += 1.5 if "Rell" in opponents else 0.0
        if steal:
            adjustment += min(13.5, steal)
            reasons.append("Sylas can steal high-impact enemy engage ultimates")

    if frontline >= 2:
        profile = _credible_damage_profile(champions, meta)
        physical = float(profile["physical"])
        magic = float(profile["magic"])
        total = physical + magic
        if total > 0:
            fraction = max(physical, magic) / total
            # Both all-AD and all-AP drafts give multiple tanks an easy resist purchase.
            if fraction >= 0.67:
                exposure = min(1.0, (fraction - 0.55) / 0.40)
                penalty = (5.0 + 4.0 * min(1.0, (frontline - 2) / 1.0)) * exposure
                adjustment -= penalty
                dominant = "magic" if magic > physical else "physical"
                reasons.append(f"{dominant}-heavy damage lets {frontline} enemy frontliners stack resistances")

        # A hybrid ADC can supply both damage types, but is not a substitute for
        # another sustained physical threat when the rest of the five are all AP.
        sustained_physical = any(
            role != "Support"
            and _traits(champion, meta).get("adaptive_type") == "PHYSICAL_DAMAGE"
            and _rating(champion, "damage", meta) >= 2
            and "ASSASSIN" not in _tags(champion, meta)
            for role, champion in champions.items()
        )
        if not sustained_physical:
            adjustment -= 3.0 if frontline == 2 else 5.0
            reasons.append("no reliable sustained physical damage threat into enemy tanks")

        if champions.get("Mid") in {"Zed", "Qiyana", "Talon", "Naafiri"}:
            adjustment -= 4.0 if frontline == 2 else 7.0
            reasons.append("burst-assassin mid is poor at cutting through multiple tanks")

    return {"adjustment": round(adjustment, 3), "reasons": reasons}


def _archetypes(champions: dict[str, str], meta: dict) -> list[str]:
    champs = list(champions.values())
    tags = [_tags(champ, meta) for champ in champs]
    engage = sum(bool(t & ENGAGE_TAGS) for t in tags)
    dive = sum(bool(t & DIVE_TAGS) for t in tags)
    peel = sum(bool(t & PEEL_TAGS) for t in tags)
    control = sum(_rating(c, "control", meta) for c in champs) / max(1, len(champs))
    mobility = sum(_rating(c, "mobility", meta) for c in champs) / max(1, len(champs))
    poke = sum(c in POKE_CHAMPS for c in champs)
    split = sum(c in SPLIT_CHAMPS for c in champs)

    labels: list[str] = []
    if engage >= 2 and control >= 1.8:
        labels.append("front-to-back engage")
    if dive >= 2 and mobility >= 1.8:
        labels.append("dive")
    if poke >= 2:
        labels.append("poke")
    if peel >= 2 and champions["ADC"] in PROTECT_CARRY_ADCS:
        labels.append("protect-the-carry")
    if control >= 2.0 and engage >= 1:
        labels.append("pick")
    if split >= 1:
        labels.append("1-3-1 / split-pressure option")
    if not labels:
        labels.append("balanced skirmish")
    return labels[:3]


def _champion_carry_demand(role: str, champion: str, meta: dict) -> float:
    """0..1 estimate of how much this champion should be treated as a primary win condition."""
    tags = _tags(champion, meta)
    damage = _rating(champion, "damage", meta) / 3.0
    toughness = _rating(champion, "toughness", meta) / 3.0
    utility = _rating(champion, "utility", meta) / 3.0
    demand = damage * 0.65
    if champion in HYPERCARRY_ADCS:
        demand += 0.30
    if role == "ADC" and champion in UTILITY_ADCS:
        demand -= 0.32
    if champion in SPLIT_CHAMPS:
        demand += 0.18
    if tags & {"ASSASSIN", "MARKSMAN", "SKIRMISHER", "DIVER"}:
        demand += 0.12
    if tags & {"TANK", "VANGUARD", "WARDEN"}:
        demand -= 0.35
    if role == "Support":
        demand -= 0.35
    demand -= utility * 0.08
    demand -= toughness * 0.05
    return max(0.05, min(1.0, demand))


@lru_cache(maxsize=None)
def _mastery_points_for(user_id: int, champion: str) -> int:
    history = _champion_history_for(user_id, champion) or {}
    mastery = int(history.get("mastery_points", 0) or 0)
    if mastery:
        return mastery
    return max(
        [
            int(row.get("points", 0) or 0)
            for row in _profile_for(user_id).get("masteries", [])
            if row.get("champion_name") == champion
        ]
        or [0]
    )


@lru_cache(maxsize=None)
def _champion_ownership(user_id: int, champion: str, role: str | None = None) -> float:
    """Long-term champion ownership, distinct from recent performance.

    Mastery is deliberately allowed to create a meaningful gap between a signature
    champion and a small recent sample. Recent role games still confirm that the
    ownership is relevant to the role being drafted.
    """
    history = _champion_history_for(user_id, champion) or {}
    mastery = _mastery_points_for(user_id, champion)
    historical_games = int(history.get("games", 0) or 0)
    recent_index = load_recent_match_index().get(user_id, {})
    recent = (
        recent_index.get("by_role_champion", {}).get(role, {}).get(champion, {})
        if role
        else recent_index.get("by_champion", {}).get(champion, {})
    )
    role_games = float(recent.get("effective_games", 0) or 0)

    if not mastery and not historical_games and not role_games:
        return 0.0

    # sqrt scaling keeps very high mastery meaningful instead of compressing
    # 500k and 30k into nearly the same logarithmic score.
    mastery_score = min(10.0, 10.0 * math.sqrt(max(0.0, mastery) / 500000.0)) if mastery else 0.0
    history_score = min(10.0, 10.0 * math.log1p(historical_games) / math.log1p(50)) if historical_games else 0.0
    role_score = min(10.0, 10.0 * math.log1p(role_games) / math.log1p(20)) if role_games else 0.0
    score = mastery_score * 0.65 + history_score * 0.15 + role_score * 0.20
    return max(0.0, min(10.0, score))


@lru_cache(maxsize=None)
def _champion_comfort(user_id: int, champion: str, role: str | None = None) -> float:
    history = _champion_history_for(user_id, champion) or {}
    mastery = _mastery_points_for(user_id, champion)

    recent_index = load_recent_match_index().get(user_id, {})
    if role:
        recent = recent_index.get("by_role_champion", {}).get(role, {}).get(champion, {})
    else:
        recent = recent_index.get("by_champion", {}).get(champion, {})

    games = int(history.get("games", 0) or 0)
    if not history and not recent and not mastery:
        return 5.0

    score = 4.0
    if history:
        win_rate = float(history.get("recent_weighted_win_rate", 50) or 50)
        op_score = float(history.get("recent_weighted_op_score", 5.0) or 5.0)
        confidence = min(1.0, games / 12.0)
        games_term = min(1.0, math.log1p(games) / math.log1p(40))
        score += (
            games_term * 2.4
            + (win_rate - 50.0) * 0.075 * confidence
            + (op_score - 5.5) * 0.70 * confidence
        )

    if mastery:
        mastery_term = min(1.0, math.log1p(mastery) / math.log1p(300000))
        score += mastery_term * 0.9

    recent_eff = float(recent.get("effective_games", 0) or 0)
    if recent_eff:
        recent_conf = min(1.0, recent_eff / 10.0)
        recent_games_term = min(1.0, math.log1p(recent_eff) / math.log1p(20))
        recent_wr = float(recent.get("win_rate", 50) or 50)
        recent_op = float(recent.get("op_score", 5.0) or 5.0)
        score += (
            recent_games_term * 1.5
            + (recent_wr - 50.0) * 0.04 * recent_conf
            + (recent_op - 5.5) * 0.45 * recent_conf
        )

    return max(0.0, min(10.0, score))


def _player_fit_adjustment(
    assignment: dict[str, dict],
    champions: dict[str, str],
    archetypes: list[str],
    meta: dict,
) -> dict:
    scores = load_player_score_index()
    total = 0.0
    role_details = {}
    carry_values = {}
    demand_values = {}
    win_condition_values = {}

    for role in ROLES:
        player = assignment[role]
        uid = int(player["user_id"])
        player_score = scores.get(uid, {"carry_index": 50.0, "reliability_index": 50.0, "metrics": {}})
        role_score = player_score.get("roles", {}).get(role, {})
        global_carry = float(player_score.get("carry_index", 50.0))
        global_reliability = float(player_score.get("reliability_index", 50.0))
        global_metrics = player_score.get("metrics", {})
        role_eff = float(role_score.get("effective_games", 0) or 0)
        if role_eff >= 2.0:
            # Role evidence ramps in with sample size and tops out at 70%; even a large
            # recent Normal sample cannot erase years of ranked history by itself.
            role_weight = min(0.70, role_eff / 15.0 * 0.70)
            carry = global_carry * (1.0 - role_weight) + float(role_score.get("carry_index", global_carry)) * role_weight
            reliability = global_reliability * (1.0 - role_weight) + float(role_score.get("reliability_index", global_reliability)) * role_weight
            role_metrics = role_score.get("metrics", {})
            metrics = {
                key: float(global_metrics.get(key, 50.0)) * (1.0 - role_weight)
                + float(role_metrics.get(key, global_metrics.get(key, 50.0))) * role_weight
                for key in set(global_metrics) | set(role_metrics)
            }
        else:
            carry = global_carry
            reliability = global_reliability
            metrics = global_metrics
        champion = champions[role]
        comfort = _champion_comfort(uid, champion, role)
        ownership = _champion_ownership(uid, champion, role)
        comfort, ownership = _declared_pool_competence_floor(
            player, role, champion, comfort, ownership,
        )
        # Comfort captures results/form; ownership captures long-term champion identity.
        # Keep them separate so a short hot sample does not erase a genuine signature pick.
        # Comfort is necessary for playability but two recent great games
        # shouldn't eclipse better synergy or overall composition completion.
        adjustment = (comfort - 5.0) * 0.25 + (ownership - 5.0) * 0.18
        if not _profile_for(uid):
            # FLEXERS: no profile means no evidence, not a penalty.
            adjustment = 0.0

        if role == "ADC":
            # ADC is not automatically the team's main win condition. Utility/poke ADCs
            # such as Ashe, Jhin and Ezreal should not drag the strongest player bot every game.
            adjustment += (carry - 50.0) / 50.0 * 0.55
            if champion in HYPERCARRY_ADCS:
                adjustment += (carry - 50.0) / 50.0 * 2.6
        elif role == "Mid":
            adjustment += (carry - 50.0) / 50.0 * 1.1
        elif role == "Top":
            tags = _tags(champion, meta)
            low_econ_frontline = bool(tags & {"TANK", "VANGUARD", "WARDEN"})
            if low_econ_frontline:
                # Reliable players can still be good tank pilots, but don't spend a
                # clearly stronger carry profile on a low-economy Top by default.
                # This is deliberately modest: a much better five-man comp can still
                # justify the tank, while close drafts should preserve that player's agency.
                adjustment += (reliability - 50.0) / 50.0 * 0.30
                if carry > 50.0:
                    adjustment -= (carry - 50.0) / 50.0 * 1.25
            else:
                adjustment += (carry - 50.0) / 50.0 * 0.75
        elif role == "Jungle":
            adjustment += (reliability - 50.0) / 50.0 * 0.8
            adjustment += (float(metrics.get("kp", 50.0)) - 50.0) / 50.0 * 0.4
        elif role == "Support":
            adjustment += (reliability - 50.0) / 50.0 * 0.8
            adjustment += (float(metrics.get("kp", 50.0)) - 50.0) / 50.0 * 0.4

        total += adjustment
        carry_values[role] = carry
        demand = _champion_carry_demand(role, champion, meta)
        demand_values[role] = demand
        win_condition_values[role] = carry * demand
        role_details[role] = {
            "carry_index": round(carry, 1),
            "reliability_index": round(reliability, 1),
            "champion_comfort": round(comfort, 1),
            "champion_ownership": round(ownership, 1),
            "mastery_points": _mastery_points_for(uid, champion),
            "agency_demand": round(demand, 2),
            "adjustment": round(adjustment, 2),
        }

    notes = []

    # WHO carries matters as much as which champions can carry. A structurally neat
    # draft is still wrong for this five if it spends the strongest player on a very
    # low-agency job and asks materially weaker carry profiles to be the damage engines.
    team_carry_avg = sum(carry_values.values()) / max(1, len(carry_values))
    carry_order = sorted(ROLES, key=lambda role: carry_values[role], reverse=True)
    strongest_player_role = carry_order[0]
    strongest_player_carry = carry_values[strongest_player_role]
    strongest_player_demand = demand_values[strongest_player_role]
    allocation_adjustment = 0.0
    if strongest_player_carry >= team_carry_avg + 6.0:
        if strongest_player_demand < 0.30:
            carry_gap = strongest_player_carry - team_carry_avg
            sacrifice_penalty = min(
                5.0,
                2.0
                + carry_gap / 25.0 * 1.5
                + (0.30 - strongest_player_demand) * 3.5,
            )
            total -= sacrifice_penalty
            allocation_adjustment -= sacrifice_penalty
            notes.append(
                f"highest carry-profile player is sacrificing agency on {champions[strongest_player_role]}"
            )
        elif strongest_player_demand >= 0.72:
            leverage_bonus = min(
                0.8,
                0.25 + (strongest_player_carry - team_carry_avg) / 70.0,
            )
            total += leverage_bonus
            allocation_adjustment += leverage_bonus
            notes.append("highest carry-profile player is on a high-agency win condition")

    # Bot lane shares early-lane agency with another player. If the lineup's strongest
    # snowball/carry profile is only flexing ADC (P2/P3), charge an opportunity cost for
    # giving up a solo lane/jungle where that player can build a lead independently.
    # Do NOT apply this to a genuine P1 ADC: a true ADC main should be allowed to own the
    # role (e.g. Jack) without the portfolio trying to rotate him out for fairness.
    adc_player = assignment["ADC"]
    adc_priority = adc_player.get("_role_priority_level")
    if adc_priority not in {1, 2, 3}:
        try:
            adc_priority = int((adc_player.get("role_priority") or {}).get("ADC"))
        except (TypeError, ValueError):
            adc_priority = None
    if strongest_player_role == "ADC" and adc_priority in {2, 3}:
        carry_gap = strongest_player_carry - team_carry_avg
        support_gap = strongest_player_carry - carry_values.get("Support", 50.0)
        if carry_gap >= 8.0:
            shared_lane_penalty = min(
                4.8,
                1.6
                + max(0.0, carry_gap - 8.0) / 18.0 * 1.4
                + max(0.0, support_gap - 10.0) / 30.0 * 1.5,
            )
            total -= shared_lane_penalty
            allocation_adjustment -= shared_lane_penalty
            notes.append(
                "strongest carry-profile player loses solo-lane snowball agency by flexing ADC"
            )

    # Do not stack the two strongest carry profiles into bot lane unless the quality
    # gain is large enough to justify surrendering a strong solo-lane/jungle resource.
    top_two_roles = set(carry_order[:2])
    if top_two_roles == {"ADC", "Support"}:
        support = champions["Support"]
        lane_stack_penalty = 2.8 if support in ENCHANTER_SUPPORTS else 2.0
        total -= lane_stack_penalty
        allocation_adjustment -= lane_stack_penalty
        notes.append("two strongest carry profiles are concentrated in bot lane")

    # Penalise agency inversions more meaningfully: a clearly stronger carry profile
    # should not be on a low-agency job while a much weaker profile gets the demanding
    # carry champion, unless the composition itself is substantially better.
    inversion_penalty = 0.0
    for strong_role in ROLES:
        for weak_role in ROLES:
            if strong_role == weak_role:
                continue
            carry_gap = carry_values[strong_role] - carry_values[weak_role]
            demand_gap = demand_values[weak_role] - demand_values[strong_role]
            if carry_gap >= 15.0 and demand_gap >= 0.30:
                inversion_penalty += min(
                    0.9,
                    (carry_gap - 10.0) / 45.0 * demand_gap * 1.35,
                )
    inversion_penalty = min(3.0, inversion_penalty)
    if inversion_penalty > 0:
        total -= inversion_penalty
        allocation_adjustment -= inversion_penalty

    credible_threat_roles = [
        role
        for role in ROLES
        if demand_values[role] >= 0.46
        and float(role_details[role]["champion_comfort"]) >= 4.0
    ]
    if len(credible_threat_roles) >= 3:
        total += 0.35
        allocation_adjustment += 0.35
        notes.append("three credible player/champion carry angles reduce damage concentration")
    adc_uid = int(assignment["ADC"]["user_id"])
    adc_carry = carry_values.get("ADC", 50.0)
    if "protect-the-carry" in archetypes:
        protect_adjustment = (adc_carry - 50.0) / 50.0 * 5.0
        if adc_carry <= min(carry_values.values()):
            protect_adjustment -= 2.5
            notes.append("protect-the-carry burden is on the lineup's lowest carry-profile player")
        elif adc_carry >= max(carry_values.values()):
            protect_adjustment += 1.0
            notes.append("protect-the-carry is centred on a strong historical carry profile")
        total += protect_adjustment

    adc_role_score = scores.get(adc_uid, {}).get("roles", {}).get("ADC", {})
    adc_role_eff = float(adc_role_score.get("effective_games", 0) or 0)
    adc_role_carry = float(adc_role_score.get("carry_index", adc_carry) or adc_carry)
    if champions["ADC"] in HYPERCARRY_ADCS:
        carry_signal = adc_role_carry if adc_role_eff >= 2.0 else adc_carry
        if carry_signal < 35:
            total -= 2.8
            notes.append(
                f"{champions['ADC']} is a high-resource ADC on weak role-specific carry evidence"
            )

    # Fit the inferred team plan to the actual people playing it. A pro-style shell
    # is only valuable if our players are suited to the jobs the plan asks of them.
    team_plan = _team_plan_completion(champions, meta)
    plan = team_plan.get("plan", "balanced")

    def metric(role: str, name: str, default: float = 50.0) -> float:
        uid = int(assignment[role]["user_id"])
        return float(scores.get(uid, {}).get("metrics", {}).get(name, default) or default)

    if plan == "scaling front-to-back":
        # The ADC is the resource sink; support/frontline players must be reliable enough
        # to create space. Reward actual farm/damage/carry history, not just role label.
        adc_resource = (carry_values["ADC"] * 0.45 + metric("ADC", "cs") * 0.20
                        + metric("ADC", "dpm") * 0.20 + metric("ADC", "gold") * 0.15)
        support_reliability = role_details["Support"]["reliability_index"]
        total += (adc_resource - 50.0) / 50.0 * 3.2
        total += (support_reliability - 50.0) / 50.0 * 0.9
        if adc_resource < 42:
            total -= 2.5
            notes.append("scaling front-to-back is centred on a weak historical resource/carry profile")
        elif adc_resource >= 70:
            notes.append("scaling plan is centred on a proven high-resource damage profile")

    elif plan == "tempo skirmish":
        # Mid/jungle need to move first and turn setup into fights; ADC is not treated as
        # the only win condition just because Lucian is in the comp.
        tempo = (metric("Jungle", "kp") + metric("Mid", "kp")
                 + role_details["Jungle"]["reliability_index"]
                 + role_details["Mid"]["reliability_index"]) / 4.0
        total += (tempo - 50.0) / 50.0 * 2.4
        if tempo < 38:
            total -= 1.5
            notes.append("tempo plan asks too much of a low-KP/low-reliability mid-jungle")
        elif tempo >= 65:
            notes.append("mid-jungle player history fits a tempo/skirmish plan")

    elif plan in {"poke / siege", "poke + side-lane"}:
        pressure = (metric("ADC", "dpm") + metric("ADC", "cs")
                    + metric("Mid", "dpm") + metric("Mid", "cs")) / 4.0
        total += (pressure - 50.0) / 50.0 * 1.8
        if pressure < 38:
            total -= 1.2
            notes.append("poke plan lacks strong historical lane/damage pressure from its ranged cores")

    elif plan == "dive / all-in":
        dive_fit = (carry_values["Top"] + carry_values["Jungle"] + carry_values["Mid"]
                    + metric("Jungle", "kp") + metric("Mid", "kp")) / 5.0
        total += (dive_fit - 50.0) / 50.0 * 2.0
        if dive_fit < 38:
            total -= 1.2
            notes.append("dive plan is assigned to players with weak historical carry/participation signals")

    elif plan == "pick / control":
        pick_fit = (metric("Jungle", "kp") + metric("Support", "kp")
                    + role_details["Jungle"]["reliability_index"]
                    + role_details["Support"]["reliability_index"]) / 4.0
        total += (pick_fit - 50.0) / 50.0 * 1.8
        if pick_fit < 38:
            total -= 1.0
            notes.append("pick/control plan is on low-participation setup players")

    ranked_carry_roles = sorted(
        ROLES,
        key=lambda role: win_condition_values[role],
        reverse=True,
    )
    carry_options = []
    for role in ranked_carry_roles[:3]:
        player = assignment[role]
        carry_options.append({
            "role": role,
            "user_id": int(player["user_id"]),
            "display_name": player["display_name"],
            "champion": champions[role],
            "score": round(win_condition_values[role], 1),
            "player_carry_index": round(carry_values[role], 1),
            "agency_demand": round(demand_values[role], 2),
        })
    win_condition = carry_options[0]
    return {
        "adjustment": round(max(-12.0, min(12.0, total)), 2),
        "role_details": role_details,
        "notes": notes[:3],
        "carry_plan": win_condition,
        "carry_options": carry_options,
        "agency_allocation_adjustment": round(allocation_adjustment, 2),
        "credible_threat_roles": credible_threat_roles,
        "adc_carry_index": round(adc_carry, 1),
        "adc_user_id": adc_uid,
    }


def score_comp(champions: dict[str, str], meta: dict | None = None) -> dict:
    meta = meta or load_meta()
    champs = list(champions.values())
    tags = [_tags(champ, meta) for champ in champs]

    tanky = sum(
        _rating(champ, "toughness", meta) >= 3 or bool(_tags(champ, meta) & TANK_TAGS)
        for champ in champs
    )
    frontline_score = 15.0 if tanky >= 2 else 10.0 if tanky == 1 else 2.0

    control_avg = sum(_rating(c, "control", meta) for c in champs) / 5.0
    control_score = min(15.0, control_avg / 3.0 * 15.0)

    engage = sum(bool(t & ENGAGE_TAGS) for t in tags)
    engage_score = 12.0 if engage >= 2 else 8.0 if engage == 1 else 1.0

    utility_avg = sum(_rating(c, "utility", meta) for c in champs) / 5.0
    utility_score = min(8.0, utility_avg / 3.0 * 8.0)

    damage_avg = sum(_rating(c, "damage", meta) for c in champs) / 5.0
    damage_score = min(10.0, damage_avg / 3.0 * 10.0)

    mobility_avg = sum(_rating(c, "mobility", meta) for c in champs) / 5.0
    mobility_score = min(6.0, mobility_avg / 3.0 * 6.0)

    ranged = sum(str(_traits(c, meta).get("attack_type")) == "RANGED" for c in champs)
    range_score = {0: 1.0, 1: 4.0, 2: 7.0, 3: 8.0, 4: 8.0, 5: 6.0}.get(ranged, 5.0)
    mix_score, mix_label = _damage_mix(champions, meta)
    damage_profile = _credible_damage_profile(champions, meta)
    credible_mixed_damage = bool(damage_profile["credible_mixed"])

    solo_wr_terms = []
    solo_wr_display = []
    for role, champ in champions.items():
        win_rate, weight = _solo_queue_value(role, champ)
        if win_rate is None:
            continue
        confidence = min(1.0, weight / 1500.0)
        solo_wr_terms.append((win_rate - 50.0) * confidence)
        solo_wr_display.append(win_rate)
    # FLEXERS: average over all five roles (missing = neutral) and cap the contribution at 4 points,
    # so one covered champion cannot carry the whole solo-queue term.
    solo_signal = sum(solo_wr_terms) / 5.0 if solo_wr_terms else 0.0
    solo_queue_score = max(0.0, min(4.0, 2.0 + solo_signal * 0.35 * 5.0 / max(1, len(solo_wr_terms))))
    avg_solo_wr = round(sum(solo_wr_display) / len(solo_wr_display), 2) if solo_wr_display else None

    meta_terms = []
    for role, champ in champions.items():
        bayes, games = _meta_value(role, champ, meta)
        confidence = min(1.0, games / 12.0)
        meta_terms.append((bayes - 0.5) * confidence)
    meta_signal = sum(meta_terms) / len(meta_terms)
    meta_score = max(0.0, min(3.0, 1.5 + meta_signal * 17.0))

    pair_terms = []
    supported_pairs = 0
    for role_a, role_b in itertools.combinations(ROLES, 2):
        if {role_a, role_b} == {"ADC", "Support"}:
            continue
        lift, games = _pair_value(
            role_a, champions[role_a], role_b, champions[role_b], meta
        )
        if games:
            supported_pairs += 1
            confidence = min(1.0, games / 10.0)
            pair_terms.append(lift * confidence)
    pair_signal = sum(pair_terms) / len(pair_terms) if pair_terms else 0.0
    synergy_score = max(0.0, min(3.0, 1.5 + pair_signal * 26.0))

    bot_lane = _bot_lane_duo_score(champions["ADC"], champions["Support"], meta)
    bot_score = float(bot_lane["score"])
    bot_lane_adjustment = (bot_score - 5.0) * 1.75
    if bot_score < 5.5:
        # Bot lane is one shared lane: a strategically weak 2v2 should not be
        # rescued by unrelated strengths elsewhere in the composition.
        bot_lane_adjustment -= 2.0 + (5.5 - bot_score) * 2.5
    team_plan = _team_plan_completion(champions, meta)
    team_plan_adjustment = (float(team_plan["score"]) - 5.0) * 1.45
    top_structure = _ranged_top_structure_fit(champions, meta)
    top_structure_adjustment = float(top_structure["score_adjustment"])
    mid_jungle = _coordinated_duo_score("Jungle", champions["Jungle"], "Mid", champions["Mid"], meta)
    jungle_support = _coordinated_duo_score("Jungle", champions["Jungle"], "Support", champions["Support"], meta)
    top_jungle = _coordinated_duo_score("Top", champions["Top"], "Jungle", champions["Jungle"], meta)
    mid_support = _coordinated_duo_score("Mid", champions["Mid"], "Support", champions["Support"], meta)
    package_adjustment = (
        (float(mid_jungle["score"]) - 5.0) * 1.35
        + (float(jungle_support["score"]) - 5.0) * 0.85
        + (float(top_jungle["score"]) - 5.0) * 0.70
        + (float(mid_support["score"]) - 5.0) * 0.45
    )

    # Ranged top follow-through is not interchangeable with a dive champion.
    # A TF-style map/utility top can complement a poke or split-map plan, but
    # does not create another reliable backline dive angle just because Zac +
    # Leona supply engage. In a fixed Kai'Sa/Leona all-in shell that distinction
    # matters more than generic ranged-top structure bonuses.
    top = champions["Top"]
    top_tags = _tags(top, meta)
    top_dive_mismatch = 0.0
    if (team_plan["plan"] == "dive / all-in"
        and str(_traits(top, meta).get("attack_type", "")).upper() == "RANGED"
        and top not in SPLIT_CHAMPS
        and not (top_tags & (DIVE_TAGS | ENGAGE_TAGS | TANK_TAGS))
        and _rating(top, "control", meta) < 3):
        top_dive_mismatch = -3.5

    # Sparse/old pro-game appearances cannot establish that a niche Top is
    # current-patch SoloQ meta. Do not discard it: a favourable revealed enemy
    # matchup can still rescue the pick in the live drafting layer.
    top_solo_wr, top_solo_games = _solo_queue_value("Top", top)
    _, top_pro_games = _meta_value("Top", top, meta)
    top_sparse_evidence = -1.5 if (top_solo_wr is None
                                    and top_solo_games == 0
                                    and top_pro_games < 25) else 0.0

    # Official 26.21 balance directions are deliberately small, do not use
    # patch-day WR guesses, and automatically retire for patch 26.22.
    live_patch_delta = live_patch_draft_adjustment(champions)
    total = (
        frontline_score + control_score + engage_score + utility_score
        + damage_score + mobility_score + range_score + mix_score
        + solo_queue_score + meta_score + synergy_score + bot_lane_adjustment + package_adjustment
        + team_plan_adjustment + top_structure_adjustment + live_patch_delta
        + top_dive_mismatch + top_sparse_evidence
        + (0.0 if credible_mixed_damage else -16.0)
    )
    raw_total = total
    total = max(0.0, min(100.0, raw_total))

    strengths = []
    gaps = []
    if tanky >= 2:
        strengths.append("reliable frontline")
    elif tanky == 0:
        gaps.append("no obvious frontline")
    if engage >= 2:
        strengths.append("multiple engage tools")
    elif engage == 0:
        gaps.append("limited hard engage")
    if control_avg >= 2.0:
        strengths.append("strong crowd control")
    elif control_avg < 1.3:
        gaps.append("light crowd control")
    if credible_mixed_damage:
        strengths.append("credible mixed damage")
    else:
        missing_type = "magic" if damage_profile["magic"] < CREDIBLE_DAMAGE_MIN else "physical"
        gaps.append(f"no credible {missing_type} damage threat")
    if ranged <= 1:
        gaps.append("very short-ranged")
    if supported_pairs >= 5 and synergy_score >= 5:
        strengths.append("recent pro pairing support")

    difficulty = sum(_rating(c, "difficulty", meta) for c in champs) / 5.0
    return {
        "score": round(total, 1),
        "rank_score": round(raw_total, 3),  # Unclamped score for ordering
        "live_patch": LIVE_PATCH if live_patch_active() else None,
        "live_patch_adjustment": round(live_patch_delta, 2),
        "archetypes": _archetypes(champions, meta),
        "strengths": strengths[:4],
        "gaps": gaps[:4],
        "damage_mix": mix_label,
        "credible_mixed_damage": credible_mixed_damage,
        "damage_profile": damage_profile,
        "pro_pair_samples": supported_pairs,
        "solo_queue_score": round(solo_queue_score, 1),
        "average_solo_queue_win_rate": avg_solo_wr,
        "solo_queue_champions_covered": len(solo_wr_display),
        "pro_meta_score": round(meta_score, 1),
        "pro_synergy_score": round(synergy_score, 1),
        "bot_lane_score": bot_lane["score"],
        "bot_lane_grade": bot_lane["grade"],
        "bot_lane_reasons": bot_lane["reasons"],
        "team_plan": team_plan["plan"],
        "team_plan_score": team_plan["score"],
        "team_plan_grade": team_plan["grade"],
        "team_plan_reasons": team_plan["reasons"],
        "team_plan_missing": team_plan["missing"],
        "top_structure_adjustment": round(top_structure_adjustment, 1),
        "top_dive_mismatch": round(top_dive_mismatch, 1),
        "top_sparse_evidence": round(top_sparse_evidence, 1),
        "top_structure_reason": top_structure.get("reason") or top_structure.get("missing") or "",
        "mid_jungle_score": mid_jungle["score"],
        "mid_jungle_grade": mid_jungle["grade"],
        "mid_jungle_reasons": mid_jungle["reasons"],
        "jungle_support_score": jungle_support["score"],
        "top_jungle_score": top_jungle["score"],
        "mid_support_score": mid_support["score"],
        "bot_lane_pro_games": bot_lane["pro_games"],
        "execution_difficulty": round(difficulty, 1),
    }



def _ranked_with_coverage(rows: list[dict], base_limit: int, coverage_fields: tuple[str, ...]) -> list[dict]:
    """Keep the strongest package rows while guaranteeing every pool champion gets a route."""
    ranked = sorted(rows, key=lambda row: float(row["heuristic"]), reverse=True)
    chosen = list(ranked[:base_limit])
    seen_ids = {id(row) for row in chosen}
    for field in coverage_fields:
        values = {row[field] for row in ranked}
        for value in values:
            best = next((row for row in ranked if row[field] == value), None)
            if best is not None and id(best) not in seen_ids:
                chosen.append(best)
                seen_ids.add(id(best))
    return chosen


def _bounded_package_products(
    pools: dict[str, list[str]],
    assignment: dict[str, dict],
    meta: dict,
    history_suggestions: dict[str, list[str]],
    enemy_roles: dict[str, str] | None = None,
) -> list[tuple[str, str, str, str, str]]:
    """Package-first search for large 10-champion pools.

    Bot and Mid-Jungle are formed first, then a Top/Jungle/Mid shell. This keeps
    every champion eligible while avoiding a 10^5 Cartesian explosion per role assignment.
    """
    total = math.prod(max(1, len(pools[role])) for role in ROLES)
    if total <= 5000:
        return list(itertools.product(*(pools[role] for role in ROLES)))

    suggestion_sets = {role: set(history_suggestions.get(role, [])) for role in ROLES}
    player_scores = load_player_score_index()
    lane_scores = {
        (role, champion): live_lane_matchup_adjustment(
            {role: champion}, enemy_roles
        )["adjustment"]
        for role in ROLES for champion in pools[role]
    }

    def individual(role: str, champion: str) -> float:
        uid = int(assignment[role]["user_id"])
        comfort = _champion_comfort(uid, champion, role)
        ownership = _champion_ownership(uid, champion, role)
        comfort, ownership = _declared_pool_competence_floor(
            assignment[role], role, champion, comfort, ownership,
        )
        solo_wr, solo_weight = _solo_queue_value(role, champion)
        bayes, pro_games = _meta_value(role, champion, meta)
        # The pair/game plan must outrank a short individual hot streak.
        # Personal champion experience remains a modest tiebreaker.
        value = comfort * 0.22 + ownership * 0.15
        # Apply matchup evidence during package pruning, not only after the
        # best 300 shells have been retained. Otherwise a real counter vanishes.
        value += lane_scores.get((role, champion), 0.0) * 1.35
        player_carry = float(player_scores.get(uid, {}).get("carry_index", 50.0) or 50.0)
        agency_demand = _champion_carry_demand(role, champion, meta)
        # Carry/agency fit belongs in the cheap package heuristic too; otherwise
        # the bounded search can prune the right high-agency pick before full scoring.
        value += ((player_carry - 50.0) / 50.0) * (agency_demand - 0.35) * 0.45
        if solo_wr is not None:
            value += (solo_wr - 50.0) * min(1.0, solo_weight / 1500.0) * 0.12
        if pro_games:
            value += (bayes - 0.5) * min(1.0, pro_games / 12.0) * 8.0
        # Explicitly submitted champions remain the preferred comfort assumption;
        # OP.GG additions are suggestions, not silent replacements for the player's pool.
        if champion not in suggestion_sets[role]:
            value += 0.65
        return value

    bot_rows = []
    for adc in pools["ADC"]:
        for support in pools["Support"]:
            if adc == support:
                continue
            duo = _bot_lane_duo_score(adc, support, meta)
            bot_rows.append({
                "adc": adc,
                "support": support,
                "heuristic": (
                    float(duo["score"]) * 1.95 + individual("ADC", adc)
                    + individual("Support", support)
                    + ((EARLY_BOT_ADC_PRIORITY.get(adc, .52)
                        + EARLY_BOT_SUPPORT_PRIORITY.get(support, .52)) / 2.0 - .5) * 1.7
                ),
            })
    bot_rows = _ranked_with_coverage(bot_rows, 28, ("adc", "support"))

    mj_rows = []
    for jungle in pools["Jungle"]:
        for mid in pools["Mid"]:
            if jungle == mid:
                continue
            duo = _coordinated_duo_score("Jungle", jungle, "Mid", mid, meta)
            mj_rows.append({
                "jungle": jungle,
                "mid": mid,
                "heuristic": (
                    float(duo["score"]) * 1.95 + individual("Jungle", jungle)
                    + individual("Mid", mid)
                    + (EARLY_MID_PRIORITY.get(mid, .52) - .5)
                    * (1.4 if jungle in JUNGLE_NEEDS_LANE_ACCESS else .6)
                ),
            })
    mj_rows = _ranked_with_coverage(mj_rows, 28, ("jungle", "mid"))

    shell_rows = []
    for top in pools["Top"]:
        for mj in mj_rows:
            jungle, mid = mj["jungle"], mj["mid"]
            if len({top, jungle, mid}) < 3:
                continue
            top_jungle = _coordinated_duo_score("Top", top, "Jungle", jungle, meta)
            shell_rows.append({
                "top": top,
                "jungle": jungle,
                "mid": mid,
                "heuristic": float(mj["heuristic"]) + float(top_jungle["score"]) * 0.65 + individual("Top", top),
            })
    shell_rows = _ranked_with_coverage(shell_rows, 70, ("top", "jungle", "mid"))

    product_rows = []
    seen = set()
    for shell in shell_rows:
        for bot in bot_rows:
            picks = (shell["top"], shell["jungle"], shell["mid"], bot["adc"], bot["support"])
            if len(set(picks)) != 5 or picks in seen:
                continue
            seen.add(picks)
            product_rows.append({
                "top": picks[0],
                "jungle": picks[1],
                "mid": picks[2],
                "adc": picks[3],
                "support": picks[4],
                "heuristic": float(shell["heuristic"]) + float(bot["heuristic"]),
            })

    # Full comp scoring is substantially more expensive than package scoring. Keep
    # the strongest complete shells, but retain at least one route for every champion
    # in every role so a lower-ranked comfort pick is never silently made impossible.
    product_rows = _ranked_with_coverage(
        product_rows,
        300,
        ("top", "jungle", "mid", "adc", "support"),
    )
    # Individual champion coverage is not enough. Guarantee an evaluation
    # route for multiple credible complete duo packages in BOTH bot lane and
    # mid+jungle. This is bounded candidate coverage, NOT a rank bonus.
    present = {(r["top"], r["jungle"], r["mid"], r["adc"], r["support"])
               for r in product_rows}

    def add_product(top: str, jungle: str, mid: str,
                    adc: str, support: str) -> None:
        key = (top, jungle, mid, adc, support)
        if len(set(key)) < 5 or key in present:
            return
        product_rows.append({"top": top, "jungle": jungle, "mid": mid,
                             "adc": adc, "support": support, "heuristic": 0.0})
        present.add(key)

    # Return strong pairs across a variety of champions; do not overweight
    # any one player based on a tiny sample of recent high-scoring games.
    bot_coverage = [(a, s) for a, s in EXPLICIT_BOT_PAIR_COVERAGE
                    if a in pools["ADC"] and s in pools["Support"]]
    for row in sorted(bot_rows, key=lambda r: (
        _bot_lane_duo_score(r["adc"], r["support"], meta)["score"],
        float(r["heuristic"])), reverse=True):
        pair = (row["adc"], row["support"])
        if pair in bot_coverage:
            continue
        if _bot_lane_duo_score(*pair, meta)["score"] < 7.5:
            break
        if len(bot_coverage) >= 14:
            break
        bot_coverage.append(pair)
    for adc, support in bot_coverage:
        added = 0
        for shell in shell_rows:
            if len({shell["top"], shell["jungle"], shell["mid"], adc, support}) < 5:
                continue
            add_product(shell["top"], shell["jungle"], shell["mid"], adc, support)
            added += 1
            if added >= 2:
                break

    mj_coverage = []
    for row in sorted(mj_rows, key=lambda r: (
        _coordinated_duo_score(
            "Jungle", r["jungle"], "Mid", r["mid"], meta
        )["score"], float(r["heuristic"])), reverse=True):
        pair = (row["jungle"], row["mid"])
        if pair in mj_coverage:
            continue
        if _coordinated_duo_score("Jungle", pair[0], "Mid", pair[1], meta)["score"] < 7.4:
            break
        if len(mj_coverage) >= 12:
            break
        mj_coverage.append(pair)
    for jungle, mid in mj_coverage:
        candidate_tops = sorted(
            (top for top in pools["Top"] if top not in (jungle, mid)),
            key=lambda top: (
                _coordinated_duo_score("Top", top, "Jungle", jungle, meta)["score"]
                + individual("Top", top) * 0.35
            ), reverse=True)
        for top in candidate_tops[:2]:
            for bot in bot_rows[:3]:
                add_product(top, jungle, mid, bot["adc"], bot["support"])

    return [
        (row["top"], row["jungle"], row["mid"], row["adc"], row["support"])
        for row in product_rows
    ]


def _credible_broad_pool_pick(player: dict, role: str, champion: str, meta: dict) -> bool:
    """A player selecting 30-50 champions is not declaring them all comfort picks.

    Preserve straightforward low-economy picks; more demanding picks need
    same-role practice, meaningful mastery, or recorded champion experience.
    A real live LOCK bypasses this just like any other draft hard constraint.
    """
    if player.get("_forced_live_champion") == champion:
        return True
    uid = int(player["user_id"])
    if _explicitly_confirmed_player_pick(uid, role, champion):
        return True
    if _champion_carry_demand(role, champion, meta) < 0.40:
        return True
    recent = (load_recent_match_index().get(uid, {})
              .get("by_role_champion", {}).get(role, {}).get(champion) or {})
    if int(recent.get("raw_games", 0) or 0) >= 3 or float(recent.get("effective_games", 0) or 0) >= 1.5:
        return True
    history = _champion_history_for(uid, champion) or {}
    mastery = _mastery_points_for(uid, champion)
    return mastery >= 80_000 or int(history.get("games", 0) or 0) >= 5


# These players have directly confirmed their role-selected pool is playable.
# This is an ELIGIBILITY rule, never a confidence-weighted score bonus, and
# never permission to suggest an unselected or banned champion.
TRUST_DECLARED_POOL_ROLES = {
    139837324438536193: frozenset(ROLES),  # Cian: all voluntarily selected roles
    286592835850076162: frozenset({"Mid"}),  # Ste: broad self-selected Mid pool
}


def _declared_pool_competence_floor(
    player: dict, role: str, champion: str, comfort: float, ownership: float,
) -> tuple[float, float]:
    """Avoid treating a trusted *selected* champion with no OP.GG history as unplayable.

    Unknown match samples are neutral (5/10), never proof of incompetence;
    strong real historical samples remain positive. No unsubmitted option gains
    any floor, and no full-team objective or champion match-up is bypassed.
    """
    uid = int(player.get("user_id") or 0)
    if (role in TRUST_DECLARED_POOL_ROLES.get(uid, ())
        and champion in (player.get("champions") or {}).get(role, ())):
        return max(5.0, comfort), max(5.0, ownership)
    return comfort, ownership


def _verified_secondary_role_pick(player: dict, role: str, champion: str, meta: dict) -> bool:
    """Avoid recommending highly demanding picks to an unpractised role flex.

    This is NOT a ban on playing the champion: explicit live LOCKS are accepted
    by the draft bridge. It only affects speculative prepared-draft choices.
    The person's deliberate first-priority role keeps its complete pool.
    """
    user_id = int(player.get("user_id") or 0)
    if role in TRUST_DECLARED_POOL_ROLES.get(user_id, ()):
        return True
    priorities = player.get("role_priority") or {}
    try:
        priority = int(priorities.get(role, 1))
    except (ValueError, TypeError):
        priority = 1
    if priority <= 1 or player.get("_forced_live_champion") == champion:
        return True
    # An unknown new teammate isn't automatically unskilled. The gate needs
    # real account history before rejecting that person's own selections.
    user_id = int(player["user_id"])
    # First-hand confirmation is stronger evidence of playable comfort than
    # the absence of recent games in a secondary role. This does NOT boost
    # draft score or imply current patch strength.
    if _explicitly_confirmed_player_pick(user_id, role, champion):
        return True
    recent_index = load_recent_match_index().get(user_id)
    if not recent_index:
        return True
    # A simple weak-side champion can be a reasonable secondary-role fallback.
    # Demanding/technical picks must have actual evidence IN THIS ROLE; generic
    # mastery on other lanes doesn't establish Vayne/Irelia Top proficiency.
    if (_champion_carry_demand(role, champion, meta) < 0.40
        and _rating(champion, "difficulty", meta) <= 1):
        return True
    role_record = (recent_index
                   .get("by_role_champion", {}).get(role, {}).get(champion) or {})
    raw = int(role_record.get("raw_games", 0) or 0)
    effective = float(role_record.get("effective_games", 0) or 0)
    return raw >= 3 or effective >= 1.5


def _role_search_pool_limit(player: dict, role: str, default: int) -> int:
    """Wider selected-pool coverage while preserving bounded package search."""
    uid = int(player.get("user_id") or 0)
    if uid == 286592835850076162 and role == "Mid":
        return max(int(default), 16)
    if uid == 139837324438536193:
        return max(int(default), 18 if role in {"Jungle", "Mid", "ADC"} else 14)
    return int(default)


@source_stat_scope
def recommend_comps(
    assignments: list[dict[str, dict]],
    *,
    limit: int = 5,
    pool_limit: int = 10,
    meta: dict | None = None,
    live_enemy_champions: Iterable[str] | None = None,
    live_enemy_roles: dict[str, str] | None = None,
    rotation_token: str | None = None,
    preferred_champions: dict[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    meta = meta or load_meta()
    revealed_enemies = set(live_enemy_champions or ())
    enemy_roles = dict(live_enemy_roles or {})
    recommendations: list[dict] = []
    missing: set[str] = set()

    for assignment_index, assignment in enumerate(assignments, start=1):
        pools: dict[str, list[str]] = {}
        pool_sources: dict[str, str] = {}
        history_suggestions: dict[str, list[str]] = {}
        valid = True
        for role in ROLES:
            player = assignment[role]
            selected = list(dict.fromkeys(player.get("champions", {}).get(role, [])))
            inferred = _historical_pool(
                int(player["user_id"]),
                role,
                meta,
                limit=max(pool_limit, 10),
            )

            # Explicit champion choices are the hard primary-pick boundary. Account
            # history/mastery can rank those choices and can be retained as optional
            # suggestion data, but it must not silently become a primary recommendation.
            selected = [
                champ for champ in selected
                if champ not in DISPLAY_EXCLUDED_CHAMPIONS
                and _verified_secondary_role_pick(player, role, champ, meta)
            ]
            inferred = [champ for champ in inferred if champ not in DISPLAY_EXCLUDED_CHAMPIONS]
            role_limit = _role_search_pool_limit(player, role, pool_limit)
            # Once a player has declared a role and champion selection,
            # recent wins/losses must NEVER hard-delete those selections.
            # Off-role safety is checked above. Candidate compression below
            # balances pro synergy, composition roles, counters and confidence.
            if selected:
                partner_role = {
                    "ADC": "Support", "Support": "ADC",
                    "Jungle": "Mid", "Mid": "Jungle",
                }.get(role)
                partner_pool = (
                    assignment[partner_role].get("champions", {}).get(partner_role, [])
                    if partner_role else None
                )
                chosen = _prune_pool(
                    role, selected, meta, role_limit,
                    enemy_roles=enemy_roles, user_id=int(player["user_id"]),
                    linked_bot_pool=partner_pool if role in {"ADC", "Support"} else None,
                    linked_role_pool=partner_pool if role in {"Jungle", "Mid"} else None,
                    rotation_token=rotation_token,
                    pinned_champion=(preferred_champions or {}).get(role),
                ) if len(selected) > role_limit else list(selected)
                # Preserve the known cross-role counter in an explicit player pool
                # even if a large generic pool was reduced to the top ten.
                if "Malphite" in revealed_enemies and role in {"Top", "Jungle", "Mid"}:
                    if "Sylas" in selected and "Sylas" not in chosen:
                        chosen = [*chosen[:max(0, role_limit - 1)], "Sylas"]
                # Discord submissions are the primary pool. Inferred mastery must
                # never silently overwrite a player's explicit champion choices.
                suggestions = [champ for champ in inferred if champ not in chosen][:role_limit]
                pools[role] = chosen[:role_limit]
                history_suggestions[role] = suggestions
                pool_sources[role] = "submitted"
                continue

            # Mastery is evidence, not permission to assign an unsubmitted pick.
            # When no Discord pool exists, surface alternatives for learning but
            # do not silently declare an unknown role champion playable.
            history_suggestions[role] = inferred[:pool_limit]
            missing.add(f"{player['display_name']} ({role})")
            valid = False
        if not valid:
            continue

        best_for_assignment: list[dict] = []
        candidate_products = _bounded_package_products(pools, assignment, meta, history_suggestions, enemy_roles=enemy_roles)
        # A captain-selected opening plan is considered in full-team scoring,
        # even when it would otherwise miss the bounded heuristic candidate set.
        # It is never forced: ranked fitness/counters still determine viability.
        preferred = preferred_champions or {}
        if all(preferred.get(role) in pools[role] for role in ROLES):
            pick_tuple = tuple(preferred[role] for role in ROLES)
            if pick_tuple not in candidate_products:
                candidate_products.append(pick_tuple)
        for picks in candidate_products:
            if len(set(picks)) != len(picks):
                continue
            champions = dict(zip(ROLES, picks))
            if any((role, champions[role]) in BLOCKED_ROLE_PICKS for role in ROLES):
                continue
            scored = score_comp(champions, meta)
            enemy_response = live_enemy_draft_adjustment(champions, revealed_enemies, meta)
            lane_response = live_lane_matchup_adjustment(champions, enemy_roles)
            enemy_score = float(enemy_response["adjustment"]) + float(lane_response["adjustment"])
            player_fit = _player_fit_adjustment(
                assignment,
                champions,
                scored.get("archetypes", []),
                meta,
            )
            composition_score = float(scored["score"])
            early_map = _early_map_resilience(
                champions, enemy_roles=enemy_roles, player_fit=player_fit,
            )
            suggested_pick_count = sum(
                1
                for role in ROLES
                if pool_sources.get(role) == "history"
            )
            suggestion_penalty = suggested_pick_count * 0.25
            # Mastery-only options are credible but not equivalent to player-
            # submitted champion selections. Real locks are never penalised.
            for role in ROLES:
                if (champions[role] not in assignment[role].get("champions", {}).get(role, [])
                    and not assignment[role].get("_forced_live_lock")):
                    suggestion_penalty += 2.5
                elif (champions[role] == assignment[role].get("_hover_experience_only")
                      and not assignment[role].get("_forced_live_lock")):
                    suggestion_penalty += 2.5
            role_preference_penalty = sum(
                float(assignment[role].get("_role_preference_penalty", 0.0) or 0.0)
                for role in ROLES
            )
            # Draft quality is primary. Player fit matters, but the specific
            # carry/agency allocation layer is only a tie-breaker among otherwise
            # good compositions; it must not rescue a structurally worse five-man.
            agency_adjustment = float(player_fit.get("agency_allocation_adjustment", 0.0) or 0.0)
            # Positive agency bonuses stay a light tie-breaker so we never force a bad
            # composition just to put the strongest player on a carry. Negative agency
            # mistakes count more strongly: wasting a top carry profile on low agency
            # while weaker players get the demanding jobs should lose close drafts.
            agency_weight = 0.55 if agency_adjustment < 0.0 else 0.35
            weighted_player_fit = (
                float(player_fit["adjustment"]) - agency_adjustment
                + agency_adjustment * agency_weight
            )
            # Player strength modifies a sound draft; it must not rescue a shell with
            # poor strategic completion. Positive player-fit is progressively capped
            # as team-plan quality falls, while negative fit still counts in full.
            plan_score = float(scored.get("team_plan_score", 5.0) or 5.0)
            if weighted_player_fit > 0:
                plan_fit_multiplier = max(0.20, min(1.0, (plan_score - 4.5) / 2.5))
                weighted_player_fit *= plan_fit_multiplier
            draft_structure_penalty = max(0.0, 5.5 - plan_score) * 2.0
            final_score = max(
                0.0,
                min(
                    100.0,
                    composition_score
                    + weighted_player_fit
                    - suggestion_penalty
                    - role_preference_penalty
                    - draft_structure_penalty
                    + enemy_score
                    + float(early_map["adjustment"]),
                ),
            )
            item = {
                "assignment_index": assignment_index,
                "players": {
                    role: {
                        "user_id": assignment[role]["user_id"],
                        "display_name": assignment[role]["display_name"],
                        "roles": list(assignment[role].get("roles", [])),
                        "role_priority": dict(assignment[role].get("role_priority", {})),
                    }
                    for role in ROLES
                },
                "champions": champions,
                "pool_sources": dict(pool_sources),
                "history_suggestions": {role: list(history_suggestions.get(role, [])) for role in ROLES},
                "submitted_pools": {
                    role: list(dict.fromkeys(assignment[role].get("champions", {}).get(role, [])))
                    for role in ROLES
                },
                "pick_sources": {
                    role: (
                        "experienced"
                        if (champions[role] not in assignment[role].get("champions", {}).get(role, [])
                            or champions[role] == assignment[role].get("_hover_experience_only"))
                        else "submitted"
                    )
                    for role in ROLES
                },
                **scored,
                "gaps": (
                    (["early map: " + early_map["reasons"][0]] + list(scored["gaps"]))[:4]
                    if early_map["adjustment"] <= -1.0 and early_map["reasons"]
                    else list(scored["gaps"])
                ),
                "composition_score": round(composition_score, 1),
                "rank_score": round(float(scored.get("rank_score", composition_score)) + weighted_player_fit - suggestion_penalty
                                    - role_preference_penalty - draft_structure_penalty + enemy_score
                                    + float(early_map["adjustment"]), 3),
                "early_map_adjustment": early_map["adjustment"],
                "early_map_bot_priority": early_map["bot_priority"],
                "early_map_lane_priority": early_map["lane_priority"],
                "early_map_enemy_roam_threat": early_map["enemy_roam_threat"],
                "early_map_reasons": early_map["reasons"],
                "live_enemy_adjustment": float(enemy_response["adjustment"]),
                "live_enemy_reasons": list(enemy_response["reasons"]),
                "live_lane_adjustment": float(lane_response["adjustment"]),
                "live_lane_edges": list(lane_response["edges"]),
                "player_fit_adjustment": round(weighted_player_fit, 2),
                "raw_player_fit_adjustment": player_fit["adjustment"],
                "draft_structure_penalty": round(draft_structure_penalty, 2),
                "history_suggestion_penalty": round(suggestion_penalty, 2),
                "role_preference_penalty": round(role_preference_penalty, 2),
                "player_fit_details": player_fit["role_details"],
                "player_fit_notes": player_fit["notes"],
                "carry_plan": player_fit["carry_plan"],
                "carry_options": player_fit["carry_options"],
                "agency_allocation_adjustment": player_fit["agency_allocation_adjustment"],
                "credible_threat_roles": player_fit["credible_threat_roles"],
                "adc_carry_index": player_fit["adc_carry_index"],
                "score": round(final_score, 1),
            }
            best_for_assignment.append(item)

        best_for_assignment.sort(
            key=lambda item: (item["score"], item["rank_score"]), reverse=True
        )
        # Retain the best representative of each eligible ADC and bot package.
        # The original flat top-600 could contain hundreds of the same strong
        # duo, leaving Kai'Sa/Jinx/Kog'Maw out before diversity was even tested.
        # Coverage does NOT award points or accept structurally weak drafts.
        leader = float(best_for_assignment[0]["score"]) if best_for_assignment else 0.0
        shortlist = list(best_for_assignment[: max(limit * 2, 8)])
        seen_adc = {
            (int(row["players"]["ADC"]["user_id"]), row["champions"]["ADC"])
            for row in shortlist
        }
        seen_pairs = {
            (int(row["players"]["ADC"]["user_id"]),
             row["champions"]["ADC"], row["champions"]["Support"])
            for row in shortlist
        }
        for row in best_for_assignment:
            if float(row["score"]) < leader - 10.0:
                break
            if (float(row.get("team_plan_score", 0)) < 6.0
                or float(row.get("bot_lane_score", 0)) < 7.0
                or float(row.get("mid_jungle_score", 0)) < 7.0):
                continue
            adc_key = (int(row["players"]["ADC"]["user_id"]), row["champions"]["ADC"])
            pair_key = (*adc_key, row["champions"]["Support"])
            if adc_key not in seen_adc or (pair_key not in seen_pairs and len(seen_pairs) < 50):
                shortlist.append(row)
                seen_adc.add(adc_key)
                seen_pairs.add(pair_key)
        covered = {
            (role, int(row["players"][role]["user_id"]), row["champions"][role])
            for row in shortlist for role in ROLES
        }
        added = 0
        for row in best_for_assignment:
            if float(row["score"]) < leader - 10.0 or added >= 40:
                break
            if (float(row.get("team_plan_score", 0)) < 6.0
                or float(row.get("bot_lane_score", 0)) < 7.0
                or float(row.get("mid_jungle_score", 0)) < 7.0):
                continue
            keys = {
                (role, int(row["players"][role]["user_id"]), row["champions"][role])
                for role in ROLES
            }
            if keys - covered:
                shortlist.append(row)
                covered.update(keys)
                added += 1
        recommendations.extend(shortlist)

    recommendations.sort(
        key=lambda item: (item["score"], item["rank_score"]), reverse=True
    )
    # Keep a coverage representative per credible ADC before returning the
    # top 300 to the *real* composition selector. No weak or unsubmitted
    # champion is forced into the ten visible plans by this candidate step.
    coverage = []
    seen_adc = set()
    seen_other_roles = set()
    best_score = float(recommendations[0]["score"]) if recommendations else 0.0
    for row in recommendations:
        if float(row["score"]) < best_score - 10.0:
            break
        if (float(row.get("team_plan_score", 0)) < 6.0
            or float(row.get("bot_lane_score", 0)) < 7.0
            or float(row.get("mid_jungle_score", 0)) < 7.0):
            continue
        key = (int(row["players"]["ADC"]["user_id"]), row["champions"]["ADC"])
        other = {
            (role, int(row["players"][role]["user_id"]), row["champions"][role])
            for role in ("Top", "Jungle", "Mid", "Support")
        }
        if key not in seen_adc or other - seen_other_roles:
            seen_adc.add(key)
            seen_other_roles.update(other)
            coverage.append(row)
    coverage_ids = {id(row) for row in coverage}
    reserved = min(len(coverage), max(0, limit // 3))
    # Champion representatives compete on objective score, not a fixed ADC list.
    candidates = recommendations[:max(0, limit - reserved)] + coverage[:reserved]
    candidates += [row for row in recommendations if id(row) not in coverage_ids]
    unique: list[dict] = []
    seen = set()
    for item in candidates:
        key = tuple(
            (
                role,
                item["players"][role]["user_id"],
                item["champions"][role],
            )
            for role in ROLES
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique, sorted(missing)


def _same_job_candidate(role: str, base: str, candidate: str, meta: dict) -> bool:
    """Reject superficially similar swaps that change the champion's team job."""
    base_tags = _tags(base, meta)
    cand_tags = _tags(candidate, meta)
    base_tanky = bool(base_tags & TANK_TAGS)
    cand_tanky = bool(cand_tags & TANK_TAGS)
    if base_tanky and not cand_tanky:
        return False

    base_engage = bool(base_tags & ENGAGE_TAGS) or _rating(base, "control", meta) >= 2
    cand_engage = bool(cand_tags & ENGAGE_TAGS) or _rating(candidate, "control", meta) >= 2
    if base_engage and not cand_engage:
        return False

    base_demand = _champion_carry_demand(role, base, meta)
    cand_demand = _champion_carry_demand(role, candidate, meta)
    if abs(base_demand - cand_demand) > 0.38:
        return False

    base_damage = _rating(base, "damage", meta)
    cand_damage = _rating(candidate, "damage", meta)
    if base_damage >= 2 and cand_damage < base_damage - 1:
        return False
    return True


def attach_learn_suggestions(recommendations: list[dict], meta: dict | None = None, max_per_role: int = 1) -> list[dict]:
    """Attach low-ownership champions worth learning when they preserve the exact draft shell.

    These are advisory only. They are never used as the primary recommendation unless the
    player later adds them to their pool / builds enough history for them to become owned picks.
    """
    meta = meta or load_meta()
    role_index = load_solo_queue_winrates()
    for rec in recommendations:
        base_champions = dict(rec.get("champions", {}))
        base_plan = rec.get("team_plan", "balanced")
        base_plan_score = float(rec.get("team_plan_score", 5.0) or 5.0)
        base_comp_score = float(rec.get("composition_score", rec.get("score", 0.0)) or 0.0)
        base_mix_score, _ = _damage_mix(base_champions, meta)
        base_carry_role = (rec.get("carry_plan") or {}).get("role")
        assignment = {
            role: {
                "user_id": rec["players"][role]["user_id"],
                "display_name": rec["players"][role]["display_name"],
            }
            for role in ROLES
        }
        submitted = rec.get("submitted_pools", {})
        known_history = rec.get("history_suggestions", {})
        learn: dict[str, list[str]] = {}

        for role in ROLES:
            uid = int(rec["players"][role]["user_id"])
            # Learn suggestions are for genuine pool gaps, not homework for players
            # who already submitted a broad set of picks in this role.
            if len(submitted.get(role, [])) > 3:
                continue
            base = base_champions[role]
            owned = set(submitted.get(role, [])) | set(known_history.get(role, [])) | {base}
            candidate_rows = []
            candidate_names = set(meta.get("role_stats", {}).get(role, {})) | set(role_index.get(role, {}))
            for candidate in candidate_names:
                if candidate in DISPLAY_EXCLUDED_CHAMPIONS:
                    continue
                if candidate in owned or candidate in base_champions.values():
                    continue
                # If they already have meaningful ownership, this is not a "learn" pick.
                if _champion_ownership(uid, candidate, role) >= 4.0:
                    continue
                solo = role_index.get(role, {}).get(candidate, {})
                solo_weight = int(solo.get("weight", 0) or 0)
                pro_games = int(meta.get("role_stats", {}).get(role, {}).get(candidate, {}).get("games", 0) or 0)
                if pro_games < 5 and solo_weight < 4000:
                    continue
                if not _same_job_candidate(role, base, candidate, meta):
                    continue

                swapped = dict(base_champions)
                swapped[role] = candidate
                scored = score_comp(swapped, meta)
                if scored.get("team_plan") != base_plan:
                    continue
                if float(scored.get("team_plan_score", 5.0)) < base_plan_score - 0.6:
                    continue
                mix_score, _ = _damage_mix(swapped, meta)
                # Damage profile is part of the champion's job in the shell. This is what
                # prevents e.g. Sejuani -> Skarner when Sejuani is supplying needed AP.
                if mix_score < base_mix_score - 1.0:
                    continue
                if role in {"ADC", "Support"} and float(scored.get("bot_lane_score", 5.0)) < float(rec.get("bot_lane_score", 5.0)) - 0.7:
                    continue
                if role in {"Jungle", "Mid"} and float(scored.get("mid_jungle_score", 5.0)) < float(rec.get("mid_jungle_score", 5.0)) - 0.7:
                    continue

                fit = _player_fit_adjustment(assignment, swapped, scored.get("archetypes", []), meta)
                if (fit.get("carry_plan") or {}).get("role") != base_carry_role:
                    continue
                total = float(scored.get("score", 0.0)) + float(fit.get("adjustment", 0.0))
                if base_comp_score - float(scored.get("score", 0.0)) > 4.0:
                    continue

                # When a low-resource player has a narrow pool, prefer learn targets
                # that add reliable utility/frontline rather than another demanding carry.
                player_score = load_player_score_index().get(uid, {})
                role_score = player_score.get("roles", {}).get(role, {})
                carry_profile = float(role_score.get("carry_index", player_score.get("carry_index", 50.0)) or 50.0)
                cand_tags = _tags(candidate, meta)
                demand = _champion_carry_demand(role, candidate, meta)
                if carry_profile < 40:
                    if cand_tags & {"TANK", "VANGUARD", "WARDEN"}:
                        total += 1.4
                    if demand > 0.65:
                        total -= 1.2
                    # For a low-resource role player, prefer a practical addition to the
                    # pool when two unfamiliar champs solve the same strategic problem.
                    difficulty = _rating(candidate, "difficulty", meta)
                    total -= max(0, difficulty - 1) * 0.75
                candidate_rows.append((total, candidate))

            if candidate_rows:
                chosen = []
                for _, champion in sorted(candidate_rows, reverse=True):
                    if champion not in chosen:
                        chosen.append(champion)
                    if len(chosen) >= max_per_role:
                        break
                if chosen:
                    learn[role] = chosen
        rec["learn_suggestions"] = learn
    return recommendations


def counter_ban_recommendations(
    champions: dict[str, str],
    pick_order: list[tuple[str, str]],
    side: str,
    *,
    unavailable: set[str] | None = None,
    matchup_data: dict | None = None,
    meta: dict | None = None,
    limit: int = 5,
) -> list[dict]:
    """Rank bans by how hard they counter picks we expose before the opponent can answer.

    Blue has opponent responses after B1, B2/B3 and B4/B5; Red has the true R5
    last pick, so an R5 lane gets zero direct counter-ban pressure.
    """
    matchup_data = matchup_data or load_lane_matchups()
    meta = meta or load_meta()
    unavailable_lower = {str(x).casefold() for x in (unavailable or set())}
    own_lower = {str(x).casefold() for x in champions.values()}
    side_key = str(side or "blue").casefold()
    exposure_by_slot = (
        [1.00, 0.66, 0.66, 0.30, 0.30]
        if side_key == "blue"
        else [0.88, 0.88, 0.46, 0.46, 0.0]
    )
    role_weight = {"Top": 1.12, "Jungle": 0.78, "Mid": 1.18, "ADC": 1.00, "Support": 0.88}
    pick_slots = {role: idx for idx, (role, _) in enumerate(pick_order)}
    aggregated: dict[str, dict] = {}

    for role, own_champion in champions.items():
        slot_index = pick_slots.get(role, 4)
        exposure = exposure_by_slot[min(slot_index, 4)]
        if exposure <= 0:
            continue
        role_matchups = matchup_data.get("matchups", {}).get(role, {})
        for enemy_champion, enemy_data in role_matchups.items():
            enemy_key = str(enemy_champion).casefold()
            if enemy_key in own_lower or enemy_key in unavailable_lower:
                continue
            response = enemy_data.get("responses", {}).get(own_champion)
            if not response:
                continue
            advantage = float(response.get("response_advantage", 0.0) or 0.0)
            # Negative response advantage means our planned champion loses this matchup.
            counter_edge = max(0.0, -advantage)
            if counter_edge < 0.35:
                continue
            games = int(response.get("games", 0) or 0)
            confidence = min(1.0, math.log1p(max(games, 1)) / math.log1p(600))
            pro_games = float(meta.get("role_stats", {}).get(role, {}).get(enemy_champion, {}).get("games", 0) or 0)
            pro_priority = min(1.0, math.log1p(pro_games) / math.log1p(20)) if pro_games else 0.0
            score = counter_edge * confidence * exposure * role_weight[role] * (0.90 + 0.10 * pro_priority)
            if score <= 0:
                continue
            row = aggregated.setdefault(enemy_champion, {"champion": enemy_champion, "score": 0.0, "reasons": []})
            row["score"] += score
            row["reasons"].append({
                "role": role,
                "our_champion": own_champion,
                "pick_number": slot_index + 1,
                "counter_edge": round(counter_edge, 2),
                "games": games,
                "exposure": exposure,
                "score": score,
            })

    # Cross-role / draft-shape counters follow the champion itself. This is what
    # makes an alternate Malphite branch recommend Sylas even when Sylas is not the
    # opposing top-lane matchup in the lane dataset.
    for role, own_champion in champions.items():
        slot_index = pick_slots.get(role, 4)
        for rule in DRAFT_SPECIFIC_COUNTERS.get(own_champion, []):
            enemy_champion = str(rule["champion"])
            enemy_key = enemy_champion.casefold()
            if enemy_key in own_lower or enemy_key in unavailable_lower:
                continue
            score = float(rule.get("score", 0.0) or 0.0)
            if score <= 0:
                continue
            row = aggregated.setdefault(
                enemy_champion,
                {"champion": enemy_champion, "score": 0.0, "reasons": []},
            )
            row["score"] += score
            row["reasons"].append({
                "role": role,
                "our_champion": own_champion,
                "pick_number": slot_index + 1,
                "counter_edge": round(score, 2),
                "games": 0,
                "exposure": 1.0,
                "score": score,
                "reason": str(rule.get("reason") or "draft-specific counter"),
            })

    ranked = sorted(aggregated.values(), key=lambda row: row["score"], reverse=True)
    for row in ranked:
        row["score"] = round(float(row["score"]), 2)
        row["reasons"] = sorted(row["reasons"], key=lambda x: x["score"], reverse=True)[:2]
    return ranked[:limit]


# FLEXERS: one calibrated matchup signal. The snapshot is D2+ game-outcome evidence conditioned
# on role, not a measured lane advantage: shrink by sample size, cap, and weight by how much the
# role's matchup actually decides the game.
_MATCHUP_K = {"Top": 400.0, "Mid": 400.0, "Jungle": 600.0, "ADC": 600.0, "Support": 600.0}
_MATCHUP_POINTS = {"Top": 0.90, "Mid": 0.75, "Jungle": 0.35, "ADC": 0.35, "Support": 0.25}


def calibrated_matchup(role: str, advantage: float | None, games: int, *, quality: float = 1.0) -> tuple[float, float]:
    """Return (shrunk percentage-point residual, ranking contribution) for a raw response advantage."""
    if advantage is None or games <= 0 or not math.isfinite(float(advantage)):
        return 0.0, 0.0
    quality = max(0.0, min(1.0, float(quality)))
    shrunk = float(advantage) * games / (games + _MATCHUP_K.get(role, 500.0))
    shrunk = max(-2.5, min(2.5, shrunk))
    return round(shrunk, 3), round(shrunk * quality * _MATCHUP_POINTS.get(role, 0.5), 3)


def matchup_response(
    role: str,
    enemy_champion: str,
    response_champion: str,
    matchup_data: dict | None = None,
) -> tuple[float | None, int]:
    matchup_data = matchup_data or load_lane_matchups()
    entry = (
        matchup_data.get("matchups", {})
        .get(role, {})
        .get(enemy_champion, {})
        .get("responses", {})
        .get(response_champion)
    )
    if not entry:
        return None, 0
    return float(entry.get("response_advantage", 0.0)), int(entry.get("games", 0))


def build_draft_book(
    assignments: list[dict[str, dict]],
    *,
    base_limit: int = 20,
    branches_per_role: int = 3,
    meta: dict | None = None,
    matchup_data: dict | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    meta = meta or load_meta()
    matchup_data = matchup_data or load_lane_matchups()
    base, missing = recommend_comps(
        assignments,
        limit=base_limit,
        pool_limit=8,
        meta=meta,
    )
    if not base:
        return [], [], missing

    branches: list[dict] = []
    common = matchup_data.get("common_enemies", {})
    for role in ROLES:
        enemies = common.get(role, [])[:branches_per_role]
        for enemy_info in enemies:
            enemy = enemy_info["champion"]
            candidates = []
            for index, rec in enumerate(base, start=1):
                response = rec["champions"][role]
                advantage, games = matchup_response(
                    role,
                    enemy,
                    response,
                    matchup_data,
                )
                if advantage is None:
                    continue
                clamped = max(-6.0, min(6.0, advantage))
                adjusted = float(rec["score"]) + clamped * 1.35
                candidates.append(
                    {
                        "draft_index": index,
                        "enemy_role": role,
                        "enemy_champion": enemy,
                        "response_champion": response,
                        "response_advantage": round(advantage, 2),
                        "matchup_games": games,
                        "adjusted_score": round(adjusted, 1),
                        "player": rec["players"][role],
                    }
                )
            if not candidates:
                continue
            candidates.sort(
                key=lambda item: (
                    item["adjusted_score"],
                    item["response_advantage"],
                    item["matchup_games"],
                ),
                reverse=True,
            )
            branches.append(candidates[0])

    return base, branches, missing


def clear_player_profile_caches() -> None:
    """Drop only caches derived from league_player_profiles.json after a live refresh."""
    for cached in (
        load_player_profiles,
        load_recent_match_index,
        load_player_score_index,
        role_experience_score,
        _mastery_points_for,
        _champion_ownership,
        _champion_comfort,
    ):
        cached.cache_clear()
