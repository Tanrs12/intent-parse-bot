"""Which champion selects the live coach drafts for.

Every Summoner's Rift queue is a five-role draft the coach can rank: Ranked Solo/Duo and Flex,
Normal Draft and Blind, Quickplay/Swiftplay, Clash, customs and versus-AI. ARAM (random
champions, no roles) and Arena (two-player teams) are not, so they always stand by, whatever the
queue list in the settings says.
"""

from __future__ import annotations

# Queue ids where the queue dict carries no game mode (lobby fallback, older clients).
ARAM_QUEUE_IDS = {65, 100, 450, 720, 2400}
ARENA_QUEUE_IDS = {1700, 1710}
QUEUE_NAMES = {0: "Custom", 400: "Normal Draft", 420: "Ranked Solo/Duo", 430: "Normal Blind", 440: "Ranked Flex",
               450: "ARAM", 480: "Swiftplay", 490: "Quickplay", 700: "Clash", 1700: "Arena", 1900: "URF"}


def _text(queue: dict) -> str:
    return " ".join(str(queue.get(k) or "") for k in ("gameMode", "type", "name", "shortName", "description")).casefold()


def unsupported_mode(queue: dict | None) -> str | None:
    """'ARAM' / 'Arena' when this queue is not a role draft; None when the coach can draft it."""
    if not queue:
        return None
    try:
        queue_id = int(queue.get("id", -1))
    except (TypeError, ValueError):
        queue_id = -1
    text = _text(queue)
    try:
        map_id = int(queue.get("mapId") or 0)
    except (TypeError, ValueError):
        map_id = 0
    if queue_id in ARAM_QUEUE_IDS or map_id == 12 or "aram" in text:
        return "ARAM"
    if queue_id in ARENA_QUEUE_IDS or map_id == 30 or "cherry" in text or "arena" in text:
        return "Arena"
    return None


def queue_label(queue: dict | None) -> str:
    if not queue:
        return "this queue"
    try:
        queue_id = int(queue.get("id", -1))
    except (TypeError, ValueError):
        queue_id = -1
    return str(queue.get("name") or QUEUE_NAMES.get(queue_id) or f"queue {queue_id}")
