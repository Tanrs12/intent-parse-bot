"""Champion id <-> name mapping with forgiving name resolution.

The League client talks in numeric ids, the engine in display names
(``"Jarvan IV"``, ``"K'Sante"``). Loadfiles are typed by humans, so
``resolve`` also accepts loose spellings ("jarvan", "ksante", "j4", "MF") and
reports what it could not match instead of guessing silently.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
from pathlib import Path

_BUNDLED_PATH = Path(__file__).with_name("champion_ids.json")
_lock = threading.Lock()
_id_to_name: dict[int, str] = {}
_name_to_id: dict[str, int] = {}
_canonical: dict[str, str] = {}

# Common shorthand that no prefix/fuzzy rule would find.
ALIASES = {
    "j4": "Jarvan IV", "jarvan": "Jarvan IV", "jarvan4": "Jarvan IV", "jarvaniv": "Jarvan IV",
    "mf": "Miss Fortune", "tf": "Twisted Fate", "asol": "Aurelion Sol", "aurelion": "Aurelion Sol",
    "mundo": "Dr. Mundo", "drmundo": "Dr. Mundo", "kog": "Kog'Maw", "kogmaw": "Kog'Maw",
    "cho": "Cho'Gath", "chogath": "Cho'Gath", "velkoz": "Vel'Koz", "khazix": "Kha'Zix", "kha": "Kha'Zix",
    "reksai": "Rek'Sai", "ksante": "K'Sante", "kaisa": "Kai'Sa", "nunu": "Nunu & Willump",
    "willump": "Nunu & Willump", "wukong": "Wukong", "monkeyking": "Wukong", "yi": "Master Yi",
    "lb": "LeBlanc", "leblanc": "LeBlanc", "renata": "Renata Glasc", "glasc": "Renata Glasc",
    "tk": "Tahm Kench", "tahm": "Tahm Kench", "kench": "Tahm Kench", "tahmkench": "Tahm Kench",
    "xin": "Xin Zhao", "lee": "Lee Sin", "ez": "Ezreal", "cait": "Caitlyn", "ori": "Orianna",
    "naut": "Nautilus", "trist": "Tristana", "gp": "Gangplank", "malz": "Malzahar", "sej": "Sejuani",
    "kat": "Katarina", "morg": "Morgana", "vlad": "Vladimir", "sera": "Seraphine", "noc": "Nocturne",
    "belveth": "Bel'Veth", "ww": "Warwick", "blitz": "Blitzcrank", "fiddle": "Fiddlesticks",
    "heimer": "Heimerdinger", "panth": "Pantheon", "kass": "Kassadin", "voli": "Volibear",
    "zil": "Zilean", "ali": "Alistar", "sylas": "Sylas", "shyv": "Shyvana", "renek": "Renekton",
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).casefold())


def _rebuild(mapping: dict[int, str]) -> None:
    global _id_to_name, _name_to_id, _canonical
    _id_to_name = dict(mapping)
    _name_to_id = {name: cid for cid, name in mapping.items()}
    _canonical = {_norm(name): name for name in mapping.values()}


def _ensure_loaded() -> None:
    if _id_to_name:
        return
    with _lock:
        if _id_to_name:
            return
        data = json.loads(_BUNDLED_PATH.read_text(encoding="utf-8"))
        _rebuild({int(k): str(v) for k, v in data["champions"].items()})


def name_for(champion_id: int | None) -> str | None:
    """Backend display name for a client champion id (0/None -> None)."""
    if not champion_id or int(champion_id) <= 0:
        return None
    _ensure_loaded()
    return _id_to_name.get(int(champion_id)) or f"Champion {int(champion_id)}"


def id_for(name: str | None) -> int | None:
    if not name:
        return None
    _ensure_loaded()
    canonical = resolve(name)
    return _name_to_id.get(canonical) if canonical else None


def resolve(name: str | None) -> str | None:
    """Map a loose spelling onto a known champion name, or ``None`` if unknown.

    Exact (normalised) match, then alias, then unique prefix, then a close fuzzy
    match. Anything that stays ambiguous or distant returns ``None`` so callers
    can report it rather than silently inventing a champion.
    """
    if not name:
        return None
    _ensure_loaded()
    key = _norm(name)
    if not key:
        return None
    if key in _canonical:
        return _canonical[key]
    if key in ALIASES:
        return ALIASES[key]
    prefix = [full for norm, full in _canonical.items() if norm.startswith(key)]
    if len(prefix) == 1:
        return prefix[0]
    if len(key) >= 4:
        close = difflib.get_close_matches(key, list(_canonical), n=2, cutoff=0.8)
        if len(close) == 1:
            return _canonical[close[0]]
        if len(close) == 2:
            first = difflib.SequenceMatcher(None, key, close[0]).ratio()
            second = difflib.SequenceMatcher(None, key, close[1]).ratio()
            if first - second > 0.08:
                return _canonical[close[0]]
    return None


def canonical_name(name: str | None) -> str | None:
    """Like ``resolve`` but never drops a name: unknowns pass through untouched."""
    if not name:
        return None
    return resolve(name) or str(name).strip()


def refresh_from_client(summary: list[dict]) -> int:
    """Replace the map with ``/lol-game-data/assets/v1/champion-summary.json`` rows."""
    mapping = {}
    seen: dict[str, int] = {}
    for row in summary or []:
        try:
            cid = int(row.get("id", -1))
        except (TypeError, ValueError):
            continue
        name = str(row.get("name") or "").strip()
        if cid <= 0 or not name or name.casefold() == "none":
            continue
        # The client's summary can list a champion more than once (game-mode variants carry large
        # synthetic ids). Champion-select actions need the real id, which is always the smallest.
        if name in seen:
            if cid >= seen[name]:
                continue
            mapping.pop(seen[name], None)
        seen[name] = cid
        mapping[cid] = name
    if not mapping:
        return 0
    with _lock:
        _rebuild(mapping)
    return len(mapping)


def all_names() -> list[str]:
    _ensure_loaded()
    return sorted(_name_to_id)


def all_ids() -> dict[str, int]:
    _ensure_loaded()
    return dict(_name_to_id)
