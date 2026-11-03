"""This PC's player: who they are, and their champion pool per role (the overlay's My champs tab).

Edits address one exact roster player, never a display name: two people can share "Alex", and a
name-based import would overwrite the other one's pool. A new account gets its own player under a key
derived from its full Riot ID. Accounts are linked to at most one player: an account another player
already owns is not taken over.
"""

from __future__ import annotations

from .roster import ROLES, Player, PoolEntry, Roster, slug


def account_player(roster: Roster, riot_id: str | None, puuid: str | None, owner_key: str | None) -> Player | None:
    """The PC owner's player when this PC is linked, else the player owning this account exactly."""
    if owner_key and owner_key in roster.players:
        return roster.players[owner_key]
    if puuid:
        hit = next((p for p in roster.players.values() if puuid in p.puuids), None)
        if hit is not None:
            return hit
    if riot_id and "#" in riot_id:
        key = roster.riot_key(riot_id)
        return next((p for p in roster.players.values() if key in {roster.riot_key(r) for r in p.riot_ids}), None)
    return None


def owner_of(roster: Roster, riot_id: str | None, puuid: str | None) -> Player | None:
    """Whoever already holds this account (by PUUID or full Riot ID)."""
    return account_player(roster, riot_id, puuid, None)


def link_unique(roster: Roster, player: Player, riot_id: str | None, puuid: str | None) -> bool:
    """Record the account on ``player`` unless another player already owns it; True if anything changed."""
    holder = owner_of(roster, riot_id, puuid)
    if holder is not None and holder is not player:
        return False
    changed = False
    if puuid and puuid not in player.puuids:
        player.puuids.append(str(puuid))
        changed = True
    if riot_id and "#" in riot_id and roster.riot_key(riot_id) not in {roster.riot_key(r) for r in player.riot_ids}:
        player.riot_ids.append(str(riot_id))
        changed = True
    return changed


def new_player(roster: Roster, name: str, riot_id: str | None = None, puuid: str | None = None) -> Player:
    """A fresh player under a unique key: never adopts an existing player by name. The key carries a digest of
    the account (PUUID, else full Riot ID), so two PCs creating players for different accounts never collide."""
    import hashlib

    identity = (puuid or riot_id or "").strip()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8] if identity else ""
    base = slug(f"{slug(name)[:40]}-{digest}" if digest else name)
    key, n = base, 2
    while key in roster.players:
        key, n = f"{base}-{n}", n + 1
    player = Player(key=key, name=str(name)[:40] or key, source="this PC")
    roster.players[key] = player
    return player


def set_pool(player: Player, role: str, champions: list[str]) -> list[str]:
    """Replace ``role``'s pool; list order is the priority (first = first choice). Notes and familiarity
    of champions that stay are kept."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    keep = {e.champion: e for e in player.pools.get(role, [])}
    ordered = list(dict.fromkeys(c for c in champions if c))
    player.pools[role] = [PoolEntry(c, i + 1, keep[c].note if c in keep else "", keep[c].familiarity if c in keep else "")
                          for i, c in enumerate(ordered)]
    if ordered and role not in player.roles:
        player.roles.append(role)
    if not ordered:
        player.pools.pop(role, None)
    return ordered
