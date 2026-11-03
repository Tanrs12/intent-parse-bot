"""Team-wide, persistent player profiles: one teammate's champion changes reach every other app.

Each app keeps every player's profile (pool per role, roles) and the team's account ownership in
``var/profiles.json``. While online, apps exchange them through a dedicated room on the team relay: read
the room, merge, write the merge back. An edit made while a teammate is away reaches them the next time
they are online together with anyone who has it, because every app keeps and re-shares what it has seen.

Ordering never trusts PC clocks: every register carries a Lamport version ``[counter, device]``; an edit
takes ``max(counter seen) + 1``; the higher version wins and the device id breaks ties deterministically.

* ``pools.<Role>`` and ``roles`` are registers per player (an ordered pool is replaced, never unioned).
* Account ownership is a register per account (Riot ID or PUUID -> player key): one owner team-wide, the
  latest claim wins, and the account moves off any other player.

The relay keeps rooms in memory for a few minutes, so it is a meeting point, not storage: the durable
copies live on every PC. The relay has no accounts, so an author cannot be authenticated; instead a whole
document is validated (types, sizes, known champions and roles, bounded counters) before anything changes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import champions as champ_map
from .roster import ROLES, Player, PoolEntry, Roster, slug

log = logging.getLogger("draftcoach.profiles")

SCHEMA = "flexers-profiles/2"
MAX_PLAYERS = 200          # records kept in total, local plus everything merged
MAX_CLAIMS = 2000
MAX_POOL = 20
MAX_COUNTER = 2 ** 53      # one admissible range everywhere: generated, saved, exchanged
MAX_JUMP = 2 ** 40         # a document more than this far ahead of what this PC has seen is refused: real edits move
                           # the counter one step at a time, and exhausting 2**53 would take thousands of hostile jumps
ROOM_SUFFIX = "-profiles"


class InvalidDocument(ValueError):
    pass


def account_key(account: str) -> str:
    """One spelling per account: Riot IDs compare like the roster does (no spaces, case-folded)."""
    account = str(account).strip()
    return Roster.riot_key(account) if "#" in account else account


def _newer(a: list | None, b: list | None) -> bool:
    """Is version ``a`` strictly newer than ``b``? Versions are [counter, device]."""
    if not a:
        return False
    if not b:
        return True
    return (int(a[0]), str(a[1])) > (int(b[0]), str(b[1]))


def _version(v) -> list:
    if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and not isinstance(v[0], bool)
            and 0 <= v[0] <= MAX_COUNTER and isinstance(v[1], str) and 0 < len(v[1]) <= 64):
        raise InvalidDocument("bad version")
    return [v[0], v[1]]


def _key(k) -> str:
    if not (isinstance(k, str) and 0 < len(k) <= 64 and slug(k) == k):
        raise InvalidDocument("bad player key")
    return k


def _clean_field(field: str, value) -> dict:
    if not isinstance(value, dict):
        raise InvalidDocument("bad field")
    v = _version(value.get("v"))
    items = value.get("value")
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise InvalidDocument("bad field value")
    if field == "roles":
        if len(items) > len(ROLES) or any(r not in ROLES for r in items):
            raise InvalidDocument("unknown role")
        return {"v": v, "value": list(dict.fromkeys(items))}
    if field.startswith("pools.") and field[6:] in ROLES:
        if len(items) > MAX_POOL:
            raise InvalidDocument("pool too long")
        names = [champ_map.resolve(x) for x in items]
        if any(n is None for n in names):
            raise InvalidDocument("unknown champion")
        return {"v": v, "value": list(dict.fromkeys(names))}
    raise InvalidDocument(f"unknown field {field[:20]}")


def validate(document) -> dict:
    """The document with every value checked and normalised, or InvalidDocument. Nothing is applied here."""
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise InvalidDocument("not a profile document")
    records, claims = document.get("records", {}), document.get("claims", {})
    if not isinstance(records, dict) or not isinstance(claims, dict):
        raise InvalidDocument("bad containers")
    if len(records) > MAX_PLAYERS or len(claims) > MAX_CLAIMS:
        raise InvalidDocument("too many entries")
    out_records = {}
    for key, rec in records.items():
        key = _key(key)
        if not isinstance(rec, dict) or not isinstance(rec.get("fields"), dict) or len(rec["fields"]) > len(ROLES) + 1:
            raise InvalidDocument("bad record")
        name = rec.get("name")
        out_records[key] = {"name": str(name)[:40] if isinstance(name, str) and name.strip() else key,
                            "fields": {f: _clean_field(f, v) for f, v in rec["fields"].items()}}
    out_claims = {}
    for account, claim in claims.items():
        if not (isinstance(account, str) and 0 < len(account.strip()) <= 120) or not isinstance(claim, dict):
            raise InvalidDocument("bad claim")
        clean = {"v": _version(claim.get("v")), "player": _key(claim.get("player"))}
        key = account_key(account)
        label = claim.get("label")
        if isinstance(label, str) and 0 < len(label) <= 120 and account_key(label) == key:
            clean["label"] = label        # the account as its owner spells it
        if key not in out_claims or _newer(clean["v"], out_claims[key]["v"]):   # case/space aliases: newest wins
            out_claims[key] = clean
    return {"records": out_records, "claims": out_claims}


class ProfileStore:
    """Local, persisted profile records plus the merge rules. Thread-safe."""

    def __init__(self, path: Path | None, device: str):
        """``path`` None keeps the store in memory only (tests, throwaway sessions)."""
        self.path, self.device = (Path(path) if path else None), str(device)
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}
        self.claims: dict[str, dict] = {}
        self.clock = 0
        self._load()

    # ---- persistence ----------------------------------------------------------------------------
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            clean = validate(data)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("profile store unreadable (%s): starting fresh, old file kept as .corrupt", exc)
            try:
                self.path.replace(self.path.with_name(self.path.name + ".corrupt"))
            except OSError:
                pass
            return
        self.records, self.claims = clean["records"], clean["claims"]
        self.clock = min(MAX_COUNTER, int(data.get("clock") or 0)) if isinstance(data.get("clock"), int) else 0
        self.clock = max([self.clock] + [f["v"][0] for r in self.records.values() for f in r["fields"].values()]
                         + [c["v"][0] for c in self.claims.values()])

    def save(self) -> None:
        if self.path is None:
            return
        with self.lock:   # serialise, write and swap under one lock: the newest state is the one on disk
            payload = json.dumps({"schema": SCHEMA, "clock": self.clock, "records": self.records, "claims": self.claims},
                                 ensure_ascii=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.path)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    def document(self) -> dict:
        with self.lock:
            return {"schema": SCHEMA, "records": json.loads(json.dumps(self.records)), "claims": dict(self.claims)}

    # ---- local edits ------------------------------------------------------------------------------
    def _bump(self) -> list:
        if self.clock >= MAX_COUNTER:
            raise RuntimeError("profile clock exhausted")
        self.clock += 1
        return [self.clock, self.device]

    def _room_for(self, key: str) -> bool:
        if key in self.records or len(self.records) < MAX_PLAYERS:
            return True
        log.warning("profile store full (%d players): %s not shared", MAX_PLAYERS, key)
        return False

    def record_pool(self, player: Player, role: str) -> None:
        with self.lock:
            if not self._room_for(player.key):
                return
            rec = self.records.setdefault(player.key, {"name": player.name[:40], "fields": {}})
            rec["name"] = player.name[:40]
            rec["fields"][f"pools.{role}"] = {"v": self._bump(), "value": list(player.champions(role))[:MAX_POOL]}
            rec["fields"]["roles"] = {"v": self._bump(), "value": [r for r in player.roles if r in ROLES]}
            self.save()

    def record_accounts(self, player: Player) -> None:
        """Claim this player's accounts team-wide (only accounts not already claimed by them)."""
        with self.lock:
            if not self._room_for(player.key):
                return
            changed = False
            for raw in list(player.riot_ids) + list(player.puuids):
                account = account_key(raw) if raw else ""
                if not account or len(account) > 120:
                    continue
                if (self.claims.get(account) or {}).get("player") != player.key and (account in self.claims or len(self.claims) < MAX_CLAIMS):
                    self.claims[account] = {"v": self._bump(), "player": player.key, "label": str(raw).strip()[:120]}
                    changed = True
            if changed:
                self.records.setdefault(player.key, {"name": player.name[:40], "fields": {}})
                self.save()

    # ---- merging --------------------------------------------------------------------------------------
    def merge(self, document) -> list[tuple[str, str]]:
        """Merge a remote document; returns the (player, field) pairs that changed here.
        An invalid document changes nothing at all."""
        try:
            clean = validate(document)
            top = max([f["v"][0] for r in clean["records"].values() for f in r["fields"].values()]
                      + [c["v"][0] for c in clean["claims"].values()] + [0])
            if top > self.clock + MAX_JUMP:
                raise InvalidDocument(f"versions jump {top - self.clock} ahead")
        except InvalidDocument as exc:
            log.info("ignored a profile document: %s", exc)
            return []
        changed: list[tuple[str, str]] = []
        with self.lock:
            for key, remote in clean["records"].items():
                if key not in self.records and len(self.records) >= MAX_PLAYERS:
                    continue
                local = self.records.setdefault(key, {"name": remote["name"], "fields": {}})
                for field, value in remote["fields"].items():
                    if _newer(value["v"], (local["fields"].get(field) or {}).get("v")):
                        local["fields"][field] = value
                        changed.append((key, field))
                    self.clock = max(self.clock, value["v"][0])
            for account, claim in clean["claims"].items():
                mine = self.claims.get(account)
                if _newer(claim["v"], (mine or {}).get("v")) and (account in self.claims or len(self.claims) < MAX_CLAIMS):
                    if mine and mine["player"] != claim["player"]:
                        changed.append((mine["player"], "accounts"))
                    self.claims[account] = claim
                    changed.append((claim["player"], "accounts"))
                self.clock = max(self.clock, claim["v"][0])
            if changed:
                self.save()
        return changed

    def differs_from(self, document) -> bool:
        """Does the room lack anything we have (so we should write the merge back)?"""
        try:
            theirs = validate(document)
        except InvalidDocument:
            return bool(self.records or self.claims)
        with self.lock:
            for key, rec in self.records.items():
                other = (theirs["records"].get(key) or {}).get("fields") or {}
                if any(_newer(v["v"], (other.get(f) or {}).get("v")) for f, v in rec["fields"].items()):
                    return True
            return any(_newer(c["v"], (theirs["claims"].get(a) or {}).get("v")) for a, c in self.claims.items())

    # ---- roster ----------------------------------------------------------------------------------------
    def apply_to(self, roster: Roster, only: set[str] | None = None) -> int:
        """Write the stored profiles into the roster (store wins). Returns how many players changed."""
        with self.lock:
            records = json.loads(json.dumps(self.records))
            claims = dict(self.claims)
        touched: set[str] = set()

        def fingerprint(p: Player) -> str:
            return json.dumps([p.roles, {r: p.champions(r) for r in p.pools}, p.riot_ids, p.puuids])

        def ensure(key: str) -> Player:
            player = roster.players.get(key)
            if player is None:
                player = Player(key=key, name=str((records.get(key) or {}).get("name") or key)[:40], source="team sync")
                roster.players[key] = player
            return player

        for key, rec in records.items():
            if only is not None and key not in only:
                continue
            player = ensure(key)
            before = fingerprint(player)
            for role in ROLES:
                f = rec["fields"].get(f"pools.{role}")
                if f is None:
                    continue
                keep = {e.champion: e for e in player.pools.get(role, [])}
                if f["value"]:
                    player.pools[role] = [PoolEntry(c, i + 1, keep[c].note if c in keep else "",
                                                    keep[c].familiarity if c in keep else "") for i, c in enumerate(f["value"])]
                else:
                    player.pools.pop(role, None)
            if rec["fields"].get("roles"):
                player.roles = list(rec["fields"]["roles"]["value"])
            if fingerprint(player) != before:
                touched.add(key)
        for account, claim in claims.items():
            if only is not None and claim["player"] not in only:
                continue
            owner = ensure(claim["player"])
            is_riot = "#" in account
            for other in roster.players.values():   # one owner: the account leaves anyone else
                if other is owner:
                    continue
                if is_riot and roster.riot_key(account) in {roster.riot_key(r) for r in other.riot_ids}:
                    other.riot_ids = [r for r in other.riot_ids if roster.riot_key(r) != roster.riot_key(account)]
                    touched.add(other.key)
                elif not is_riot and account in other.puuids:
                    other.puuids = [p for p in other.puuids if p != account]
                    touched.add(other.key)
            if is_riot and roster.riot_key(account) not in {roster.riot_key(r) for r in owner.riot_ids}:
                owner.riot_ids.append(claim.get("label") or account)
                touched.add(owner.key)
            elif not is_riot and account not in owner.puuids:
                owner.puuids.append(account)
                touched.add(owner.key)
        return len(touched)


class RelaySync:
    """Exchange the profile document with the team relay's profiles room."""

    def __init__(self, store: ProfileStore, relay: str, room_key: str, *, interval: float = 20.0, timeout: float = 8.0):
        self.store = store
        self.url = f"{relay.rstrip('/')}/draft-sync/{room_key[:110]}{ROOM_SUFFIX}"
        self.interval, self.timeout = interval, timeout
        self.status: dict = {"state": "starting", "last_ok": None, "last_error": None, "received": 0, "sent": 0}

    def _get(self) -> dict | None:
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, headers={"User-Agent": "FlexersDraftCoach/1.0"}),
                                        timeout=self.timeout) as r:
                return json.loads(r.read(2_000_000) or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 503:          # the room is empty right now: nothing to merge
                return None
            raise

    def _put(self, document: dict) -> None:
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        if len(body) > 1_800_000:
            raise ValueError("profile document too large for the relay")
        req = urllib.request.Request(self.url, data=body, method="PUT",
                                     headers={"Content-Type": "application/json", "User-Agent": "FlexersDraftCoach/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            r.read()

    def exchange(self) -> list[tuple[str, str]]:
        """One round: pull and merge (raises when the room cannot be read), then share back if the room lacks
        anything. A failed share never loses what was just received: the changes are still returned."""
        remote = self._get()
        changed = self.store.merge(remote) if remote else []
        self.status["received"] += len(changed)
        try:
            if self.store.differs_from(remote):
                self._put(self.store.document())
                self.status["sent"] += 1
            self.status.update(state="online", last_ok=time.time(), last_error=None)
        except Exception as exc:  # noqa: BLE001
            self.status.update(state="offline", last_error=f"{type(exc).__name__}: {str(exc)[:120]}")
        return changed
