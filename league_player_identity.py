"""Stable player identity across Discord account changes.

Keep live Discord IDs untouched for signup authorization, role selection,
and mentions. Only gameplay modelling/history uses this canonical mapping.
"""
RAT_HISTORY_DISCORD_ID = 270517092703272960
RAT_CURRENT_DISCORD_ID = 439790899837272084
RAT_DISCORD_IDS = frozenset((RAT_HISTORY_DISCORD_ID, RAT_CURRENT_DISCORD_ID))


def canonical_player_id(user_id: int | str) -> int:
    uid = int(user_id)
    return RAT_HISTORY_DISCORD_ID if uid in RAT_DISCORD_IDS else uid


def is_rat(user_id: int | str) -> bool:
    return int(user_id) in RAT_DISCORD_IDS
