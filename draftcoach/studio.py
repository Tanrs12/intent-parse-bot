"""Draft Studio: build team drafts outside champion select.

Everything ranks through the same brain as live drafting (``league_draft_backend.recommend_live_drafts``):

* ``generate``      - drafts for a chosen lineup with role/champion locks, bans, an identity (dive, poke, ...)
                      and an optional creative seed that rotates the search into less obvious drafts.
* ``reroll_lane``   - "not that champion": keeps the other four champions and re-solves one lane without it.
* ``learn``         - champions worth learning next for a role: close in kit to the player's pool (class, damage
                      type, range, ratings) and strong in solo queue, not already played.
* ``weaknesses``    - what a pool lacks: common enemies with no answer, a one-sided damage profile, no blind-safe
                      pick, one-trick ban exposure, missing engage/peel in the team's combined pools.
"""

from __future__ import annotations

import random

from . import assist, backend
from . import champions as champ_map
from .roster import ROLES, Roster

import league_comp_engine as engine  # type: ignore  # noqa: E402

PUG_BASE = 950_000_000_000


def _pool(player, role: str) -> list[str]:
    return [e.champion for e in player.pools.get(role, [])] if player is not None else []


def lineup_signups(roster: Roster, lineup: dict[str, str | None], tier: dict, excluded: dict[str, set[str]]
                   ) -> tuple[list[dict], dict[str, int]]:
    """Signups for role -> player key (None = "anyone": a solo-queue meta pool for that role).

    ``excluded`` removes champions from one role's options only (a rejected suggestion)."""
    signups, role_players = [], {}
    for index, role in enumerate(ROLES):
        player = roster.players.get(lineup.get(role) or "")
        blocked = set(excluded.get(role, ()))
        if player is not None:
            champions = {r: [c for c in _pool(player, r) if not (r == role and c in blocked)] for r in ROLES if _pool(player, r)}
            mine = champions.get(role) or []
            if not mine:
                # A player with nothing prepared here still gets a legal pool: their best meta picks for the role.
                mine = [p["champion"] for p in assist.comfort_picks(role, blocked, tier, {}, limit=8)]
            champions[role] = mine
            uid = int(player.user_id) if player.user_id else PUG_BASE + index
            name = player.name
        else:
            uid, name = PUG_BASE + index, f"Any {role}"
            champions = {role: [p["champion"] for p in assist.comfort_picks(role, blocked, tier, {}, limit=8)]}
        signups.append({"user_id": uid, "display_name": name, "roles": [role], "role_priority": {role: 1},
                        "champions": champions})
        role_players[role] = uid
    return signups, role_players


def _draft_rows(result: dict | None) -> list[dict]:
    out = []
    for rec in (result or {}).get("recommendations") or []:
        champions = rec.get("champions") or {}
        if not all(champions.get(r) for r in ROLES):
            continue
        out.append({
            "champions": {r: champions[r] for r in ROLES},
            "champion_ids": {r: champ_map.id_for(champions[r]) for r in ROLES},
            "players": {r: ((rec.get("players") or {}).get(r) or {}).get("display_name") for r in ROLES},
            "score": round(float(rec.get("score") or 0.0), 1),
            "plan": rec.get("team_plan") or rec.get("plan") or "",
            "strengths": list(rec.get("strengths") or [])[:4],
            "gaps": list(rec.get("gaps") or [])[:3],
            "pick_order": [{"role": p.get("role"), "champion": p.get("champion")} for p in rec.get("pick_order") or []],
            "signature": "|".join(champions[r] for r in ROLES),
        })
    return out


def generate(roster: Roster, tier: dict, *, lineup: dict[str, str | None], locks: dict[str, str] | None = None,
             bans: list[str] | None = None, excluded: dict[str, list[str]] | None = None, identity: str | None = None,
             creative: bool = False, seed: str | None = None, count: int = 6, side: str = "blue",
             exclude_signatures: list[str] | None = None, hard_exclude: bool = False) -> dict:
    """Ranked drafts for this lineup and these constraints.

    Pins are hard: a draft without every pinned champion in its role is dropped, and a pin that is also
    banned (or pinned twice) is reported before searching instead of being quietly relaxed.
    ``hard_exclude`` removes already-shown drafts outright ("show different drafts")."""
    if not backend.available():
        return {"drafts": [], "error": "the draft engine is not available in this build"}
    excluded_sets = {r: set(v or ()) for r, v in (excluded or {}).items() if r in ROLES}
    locks_in = dict(locks or {})
    locks = {r: champ_map.resolve(c) for r, c in locks_in.items() if r in ROLES and c}
    locks = {r: c for r, c in locks.items() if c}
    unavailable = {c for c in (champ_map.resolve(b) for b in bans or []) if c}
    chosen = [k for k in (lineup.get(r) for r in ROLES) if k]
    doubled = sorted({k for k in chosen if chosen.count(k) > 1})
    if doubled:
        names = ", ".join(roster.players[k].name if k in roster.players else k for k in doubled)
        return {"drafts": [], "error": f"{names} is chosen for two roles: one player per role"}
    unknown = [c for r, c in (locks_in or {}).items() if r in ROLES and c and not champ_map.resolve(c)]
    if unknown:
        return {"drafts": [], "error": "unknown champion: " + ", ".join(unknown)}
    clash = sorted(set(locks.values()) & unavailable)
    if clash:
        return {"drafts": [], "error": ", ".join(clash) + " is pinned and also banned: remove one of the two"}
    twice = sorted({c for c in locks.values() if list(locks.values()).count(c) > 1})
    if twice:
        return {"drafts": [], "error": ", ".join(twice) + " is pinned in two roles"}
    for role, champion in locks.items():
        if champion in excluded_sets.get(role, ()):
            return {"drafts": [], "error": f"{champion} is pinned for {role} and also rejected there"}
    signups, role_players = lineup_signups(roster, lineup, tier, excluded_sets)
    for signup in signups:   # a locked champion must be in its role's pool for the search to place it
        role = signup["roles"][0]
        if role in locks and locks[role] not in signup["champions"].get(role, []):
            signup["champions"][role] = [locks[role]] + signup["champions"].get(role, [])
    token = seed or (f"creative-{random.randrange(1 << 30)}" if creative else None)
    try:
        result = backend._recommend_live_drafts(  # noqa: SLF001 - the studio needs the full keyword surface
            signups, unavailable=unavailable, role_players=role_players, side=side, limit=max(1, min(24, int(count) + (len(exclude_signatures or []) if hard_exclude else 0))),
            rotation_token=token, required_champions=locks, identity=identity,
            exclude_signatures=list(exclude_signatures or []))
    except Exception as exc:  # noqa: BLE001
        return {"drafts": [], "error": f"draft engine failed: {exc}"}
    drafts = [d for d in _draft_rows(result)
              if all(d["champions"][r] == c for r, c in locks.items())                       # every pin honoured
              and not any(d["champions"][r] in excluded_sets.get(r, ()) for r in ROLES)     # rejections stay out
              and not set(d["champions"].values()) & unavailable]
    if hard_exclude:
        seen = {str(s) for s in exclude_signatures or []}
        drafts = [d for d in drafts if d["signature"] not in seen][:max(1, int(count))]
    return {"drafts": drafts, "seed": token,
            "identities": sorted(getattr(__import__("league_draft_backend"), "IDENTITIES", {}).keys())}


def reroll_lane(roster: Roster, tier: dict, *, lineup: dict[str, str | None], draft: dict[str, str], role: str,
                reject: list[str], bans: list[str] | None = None, side: str = "blue") -> dict:
    """Keep the other four champions, re-solve ``role`` without the rejected champions."""
    if role not in ROLES:
        return {"drafts": [], "error": f"unknown role {role}"}
    keep = {r: c for r, c in draft.items() if r in ROLES and r != role and c}
    return generate(roster, tier, lineup=lineup, locks=keep, bans=bans, excluded={role: list(reject)}, count=4,
                    side=side, seed=f"reroll-{role}-{'-'.join(sorted(reject))}")


# --------------------------------------------------------------------------- pool insight

def _traits(champion: str) -> dict:
    return (engine.load_meta().get("champion_traits") or {}).get(champion) or {}


def similarity(a: str, b: str) -> float:
    """0..1 closeness of two champions' kits: class tags, damage type, melee/ranged and the five ratings."""
    ta, tb = _traits(a), _traits(b)
    if not ta or not tb:
        return 0.0
    ca, cb = set(ta.get("roles") or []), set(tb.get("roles") or [])
    classes = len(ca & cb) / len(ca | cb) if ca | cb else 0.0
    damage = 1.0 if ta.get("adaptive_type") == tb.get("adaptive_type") else 0.0
    attack = 1.0 if ta.get("attack_type") == tb.get("attack_type") else 0.0
    ra, rb = ta.get("ratings") or {}, tb.get("ratings") or {}
    keys = [k for k in ("damage", "toughness", "control", "mobility", "utility") if k in ra and k in rb]
    ratings = 1.0 - (sum(abs(ra[k] - rb[k]) for k in keys) / (3.0 * len(keys))) if keys else 0.5
    return round(0.45 * classes + 0.2 * damage + 0.15 * attack + 0.2 * max(0.0, ratings), 3)


def learn(pool: list[str], role: str, tier: dict, *, limit: int = 6, exclude: set[str] | None = None) -> list[dict]:
    """Champions to learn next for ``role``: like what the player already plays, and good in solo queue."""
    rows = (tier.get("roles") or {}).get(role) or {}
    have = set(pool) | set(exclude or ())
    out = []
    for champion, row in rows.items():
        if champion in have or float(row.get("pick") or 0) < 0.8:
            continue
        close = max((similarity(champion, p) for p in pool), default=0.0)
        like = max(pool, key=lambda p: similarity(champion, p)) if pool else None
        win = float(row.get("win") or 50.0)
        score = (2.5 * close if pool else 0.0) + (win - 50.0) * 0.6 + min(2.0, float(row.get("pick") or 0) / 4)
        out.append({"champion": champion, "champion_id": champ_map.id_for(champion), "role": role,
                    "similar_to": like if close >= 0.5 else None, "similarity": close, "win_rate": win,
                    "score": round(score, 2)})
    out.sort(key=lambda r: (-r["score"], r["champion"]))
    return out[:limit]


def weaknesses(pool: list[str], role: str, tier: dict, matchups: dict) -> list[dict]:
    """What this role pool is missing, most important first."""
    notes: list[dict] = []
    pool = [c for c in pool if c]
    if not pool:
        return [{"level": "warn", "kind": "empty", "text": f"No {role} pool yet: add at least three champions."}]
    if len(pool) < 3:
        notes.append({"level": "warn", "kind": "ban-exposure",
                      "text": f"Only {len(pool)} {role} champion{'s' if len(pool) > 1 else ''}: one or two bans leave you without a prepared pick."})
    common = [row.get("champion") for row in (matchups.get("common_enemies") or {}).get(role, [])][:12]
    table = (matchups.get("matchups") or {}).get(role) or {}
    uncovered = []
    for enemy in common:
        answers = ((table.get(enemy) or {}).get("responses") or {})
        if enemy and not any(float((answers.get(c) or {}).get("response_advantage") or 0) > 0 for c in pool):
            uncovered.append(enemy)
    if uncovered:
        notes.append({"level": "warn", "kind": "uncovered", "champions": uncovered[:5],
                      "text": "No good answer in your pool to " + ", ".join(uncovered[:5]) + "."})
    damage = {(_traits(c).get("adaptive_type") or "") for c in pool}
    if len(pool) >= 3 and len(damage - {""}) == 1:
        kind = "magic" if "MAGIC_DAMAGE" in damage else "physical"
        notes.append({"level": "info", "kind": "damage", "text": f"Every {role} pick deals {kind} damage: enemies can stack one resistance."})
    rows = (tier.get("roles") or {}).get(role) or {}
    blind = [c for c in pool if float((rows.get(c) or {}).get("win") or 0) >= 50.5 and len(_counter_list(table, c)) <= 2]
    if not blind:
        notes.append({"level": "info", "kind": "blind", "text": f"No clear blind-pick-safe {role} champion: keep one stable pick for early rotations."})
    weak = [c for c in pool if rows.get(c) and float(rows[c].get("win") or 50) < 48.5]
    if weak:
        notes.append({"level": "info", "kind": "meta", "text": ", ".join(weak[:3]) + " under 48.5% in solo queue this patch."})
    return notes


def _counter_list(table: dict, champion: str) -> list[str]:
    answers = ((table.get(champion) or {}).get("responses") or {})
    return [c for c, row in answers.items() if float(row.get("response_advantage") or 0) >= 3.0]


def team_gaps(pools: dict[str, list[str]]) -> list[dict]:
    """Across the five role pools: is there engage, peel and a mix of damage at all?"""
    flat = [c for role in ROLES for c in pools.get(role, [])]
    if not flat:
        return []
    out = []
    control = max((_traits(c).get("ratings") or {}).get("control", 0) for c in flat)
    tough = max((_traits(c).get("ratings") or {}).get("toughness", 0) for c in flat)
    if control < 3:
        out.append({"level": "warn", "kind": "engage", "text": "Little hard crowd control across your pools: add an engage or pick champion."})
    if tough < 3:
        out.append({"level": "warn", "kind": "frontline", "text": "No tanky frontline in any pool: add a tank or bruiser."})
    damage = {(_traits(c).get("adaptive_type") or "") for c in flat} - {""}
    if len(damage) == 1:
        out.append({"level": "info", "kind": "damage", "text": "All pools lean one damage type."})
    return out
