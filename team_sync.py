"""The Flexers draft room: where it is, and how a chosen draft is pushed into it.

Both the Discord bot and the packaged Draft Coach build import the same constants, so a draft locked
in Discord lands in the same relay room the five apps are already listening to. The room key is
coordination metadata rather than a credential: anyone with the app already has it.
"""
from __future__ import annotations

import os
from typing import Any

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")

TEAM_SYNC_RELAY = os.getenv("INTENT_SYNC_RELAY") or "https://draft--intent-parse-bot--cdzz6bjv54yz.code.run"
TEAM_SYNC_ROOM_KEY = os.getenv("INTENT_SYNC_KEY") or "flexers-intent-ranked-flex-room-v1"

# Engine plan labels -> draft identity families (league_draft_backend.IDENTITIES keys).
_PLAN_IDENTITY = {
    "dive / all-in": "dive",
    "poke / siege": "poke",
    "poke + side-lane": "poke",
    "scaling front-to-back": "front-to-back",
    "tempo skirmish": "tempo",
    "mid-jungle led skirmish": "tempo",
    "pick / control": "pick",
}


def identity_for_plan(plan: str | None, archetypes: list[str] | None = None) -> str | None:
    """The identity family a Discord draft belongs to, or None when it is just 'balanced'."""
    key = str(plan or "").strip().casefold()
    if key in _PLAN_IDENTITY:
        return _PLAN_IDENTITY[key]
    for arche in archetypes or []:
        a = str(arche or "").casefold()
        if "protect" in a:
            return "protect"
        if a == "pick":
            return "pick"
        if "dive" in a:
            return "dive"
        if "poke" in a:
            return "poke"
        if "front-to-back" in a:
            return "front-to-back"
    return None


def comp_signature(champions: dict | None) -> str:
    champions = champions or {}
    return "|".join(str(champions.get(role) or "?") for role in ROLES)


def draft_control_payload(rec: dict, *, label: str, source: str = "discord") -> dict[str, Any]:
    """The relay /preference body that makes every Draft Coach follow one Discord draft.

    Roles are pinned to the players, but unpicked champions are soft preferences.
    Counter swaps are permitted from submitted pools when the revealed enemy lane justifies them.
    the draft's identity keeps the replacement in the same style. ``chosen`` carries the label and
    the original five so the apps can say what changed and why.
    """
    champions = {role: str((rec.get("champions") or {}).get(role) or "") for role in ROLES}
    players = rec.get("players") or {}
    role_locks = {}
    names = {}
    for role in ROLES:
        row = players.get(role) or {}
        uid = row.get("user_id")
        if uid and str(uid).isdigit():
            role_locks[role] = int(uid)
        if row.get("display_name"):
            names[role] = str(row["display_name"])[:32]
    identity = identity_for_plan(rec.get("team_plan"), rec.get("archetypes"))
    return {
        "signature": comp_signature(champions),
        "control": {
            "identity": identity,
            "role_locks": role_locks,
            "champion_locks": {},  # real in-client locks are applied by the live backend
            "shake": 0,
            "chosen": {
                "mode": "adaptive",
                "label": str(label)[:40],
                "source": str(source)[:12],
                "plan": str(rec.get("team_plan") or "")[:40],
                "champions": champions,
                "players": names,
            },
        },
    }


def clear_control_payload() -> dict[str, Any]:
    """Release a chosen draft: no preference, no locks, back to automatic drafts."""
    return {"signature": None, "control": None}


def clean_chosen(raw: Any) -> dict | None:
    """Validate a ``chosen`` block from the network (relay and bridge share this)."""
    if not isinstance(raw, dict):
        return None
    champions = {role: str(v)[:40] for role, v in (raw.get("champions") or {}).items() if role in ROLES and v}
    if not champions:
        return None
    players = {role: str(v)[:32] for role, v in (raw.get("players") or {}).items() if role in ROLES and v}
    return {
        "mode": "adaptive" if raw.get("mode") == "adaptive" else "locked",
        "label": str(raw.get("label") or "Team draft")[:40],
        "source": str(raw.get("source") or "")[:12],
        "plan": str(raw.get("plan") or "")[:40],
        "champions": champions,
        "players": players,
    }


def chosen_status(chosen: dict | None, lead: dict | None, *, our_bans: list[str], enemy_bans: list[str],
                  enemy_locked: list[str]) -> dict | None:
    """What the live lead draft did to the chosen five: which seats were replaced and why."""
    if not chosen:
        return None
    lead_champs = (lead or {}).get("champions") or {}
    gone = {str(c).casefold() for c in (our_bans or []) + (enemy_bans or [])}
    taken = {str(c).casefold() for c in enemy_locked or []}
    replaced = {}
    for role in ROLES:
        was = chosen["champions"].get(role)
        now = lead_champs.get(role)
        if not was or not now or was == now:
            continue
        key = was.casefold()
        adaptive_reason = (lead or {}).get("adaptive_reasons", {}).get(role)
        why = ("banned" if key in gone else "taken by the enemy" if key in taken
               else adaptive_reason or "swapped")
        replaced[role] = {"was": was, "now": now, "why": why}
    return {**chosen, "replaced": replaced, "intact": not replaced, "lead_matches": bool(lead_champs)}
