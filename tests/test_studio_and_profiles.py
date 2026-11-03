"""Draft Studio and team profile sync (Will, 3 Nov 2026)."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draftcoach import assist, studio
from draftcoach import loadfiles as lf
from draftcoach.profile_sync import SCHEMA, ProfileStore, RelaySync
from draftcoach.roster import ROLES, Player, Roster

TEAM_FILE = Path(__file__).resolve().parent.parent / "loadfiles" / "flexers-team.json"


@pytest.fixture(scope="module")
def team() -> Roster:
    r = Roster(auto_profiles=True)
    assert lf.import_path(r, TEAM_FILE).ok
    return r


@pytest.fixture(scope="module")
def tier() -> dict:
    return assist.load_tierlist()


@pytest.fixture(scope="module")
def lineup(team) -> dict:
    return dict(zip(ROLES, list(team.players)[:5]))


# --------------------------------------------------------------------------- Studio

def test_generate_honours_every_pin(team, tier, lineup):
    out = studio.generate(team, tier, lineup=lineup, locks={"Mid": "ahri"}, count=4)
    assert out["drafts"] and all(d["champions"]["Mid"] == "Ahri" for d in out["drafts"])
    assert all(len(set(d["champions"].values())) == 5 for d in out["drafts"])


@pytest.mark.parametrize("kwargs,message", [
    ({"locks": {"Mid": "Ahri"}, "bans": ["Ahri"]}, "pinned and also banned"),
    ({"locks": {"Mid": "Ahri", "Top": "Ahri"}}, "pinned in two roles"),
    ({"locks": {"Mid": "Notachamp"}}, "unknown champion"),
    ({"locks": {"Mid": "Ahri"}, "excluded": {"Mid": ["Ahri"]}}, "also rejected"),
])
def test_contradictory_constraints_are_reported_before_searching(team, tier, lineup, kwargs, message):
    out = studio.generate(team, tier, lineup=lineup, **kwargs)
    assert out["drafts"] == [] and message in out["error"]


def test_bans_never_appear(team, tier, lineup):
    first = studio.generate(team, tier, lineup=lineup, count=3)["drafts"][0]["champions"]
    banned = [first["Top"], first["ADC"]]
    out = studio.generate(team, tier, lineup=lineup, bans=banned, count=4)
    assert out["drafts"] and not any(set(banned) & set(d["champions"].values()) for d in out["drafts"])


def test_reroll_changes_only_the_rejected_lane(team, tier, lineup):
    draft = studio.generate(team, tier, lineup=lineup, count=1)["drafts"][0]["champions"]
    out = studio.reroll_lane(team, tier, lineup=lineup, draft=draft, role="Mid", reject=[draft["Mid"]])
    assert out["drafts"]
    for d in out["drafts"]:
        assert d["champions"]["Mid"] != draft["Mid"]
        assert all(d["champions"][r] == draft[r] for r in ROLES if r != "Mid")
    assert studio.reroll_lane(team, tier, lineup=lineup, draft=draft, role="Lane", reject=[])["error"]


def test_show_different_never_repeats(team, tier, lineup):
    seen = []
    for _ in range(3):
        out = studio.generate(team, tier, lineup=lineup, count=3, exclude_signatures=seen, hard_exclude=bool(seen), creative=bool(seen))
        new = [d["signature"] for d in out["drafts"]]
        assert new and not set(new) & set(seen)
        seen += new


def test_open_seats_use_meta_picks(team, tier):
    out = studio.generate(team, tier, lineup={r: None for r in ROLES}, creative=True, count=3)
    assert out["drafts"] and all(d["players"]["Top"] == "Any Top" for d in out["drafts"])


def test_learn_suggests_new_similar_champions(tier):
    pool = ["Darius", "Garen"]
    rows = studio.learn(pool, "Top", tier)
    assert rows and not {r["champion"] for r in rows} & set(pool)
    assert any(r["similar_to"] in pool for r in rows)
    assert studio.similarity("Darius", "Darius") == 1.0 and studio.similarity("Darius", "Unknownchamp") == 0.0


def test_weaknesses_spot_thin_and_empty_pools(tier):
    import league_comp_engine as engine

    m = engine.load_lane_matchups()
    assert studio.weaknesses([], "Top", tier, m)[0]["kind"] == "empty"
    kinds = {w["kind"] for w in studio.weaknesses(["Garen"], "Top", tier, m)}
    assert "ban-exposure" in kinds
    assert any(g["kind"] == "frontline" for g in studio.team_gaps({"Mid": ["Zed"], "ADC": ["Jinx"]}))


@pytest.fixture
def studio_client(tmp_path):
    from draftcoach import server

    app = server.create_app({}, enable_lcu=False, roster_path=tmp_path / "roster.json", watch_dir=None, load_files=[TEAM_FILE])
    with TestClient(app) as client:
        yield client


def test_studio_api(studio_client):
    setup = studio_client.get("/api/studio/setup").json()
    assert setup["roles"] == list(ROLES) and setup["players"] and any(i["key"] == "dive" for i in setup["identities"])
    lineup = {r: setup["players"][i]["key"] for i, r in enumerate(ROLES)}
    out = studio_client.post("/api/studio/generate", json={"lineup": lineup, "count": 2}).json()
    assert out["drafts"]
    d = out["drafts"][0]["champions"]
    rr = studio_client.post("/api/studio/reroll", json={"lineup": lineup, "draft": d, "role": "Top", "reject": [d["Top"]]}).json()
    assert all(x["champions"]["Top"] != d["Top"] for x in rr["drafts"])
    assert studio_client.post("/api/studio/reroll", json={"lineup": lineup, "draft": d, "role": "X"}).status_code == 400
    ins = studio_client.post("/api/studio/insight", json={"lineup": lineup}).json()
    assert set(ins["seats"]) == set(ROLES)
    assert studio_client.get("/studio").status_code == 200
    # Unknown player keys mean "anyone", never an error or another player's pool.
    assert studio_client.post("/api/studio/generate", json={"lineup": {"Top": "nobody"}, "count": 1}).json()["drafts"]


# --------------------------------------------------------------------------- profile sync: merge rules

def _player(key="will", pools=None):
    p = Player(key=key, name=key.title())
    from draftcoach.my_champs import set_pool

    for role, champs in (pools or {}).items():
        set_pool(p, role, champs)
    return p


def test_local_edits_bump_a_lamport_clock_and_persist(tmp_path):
    s = ProfileStore(tmp_path / "p.json", "dev-a")
    p = _player(pools={"Mid": ["Ahri", "Yone"]})
    s.record_pool(p, "Mid")
    v1 = s.records["will"]["fields"]["pools.Mid"]["v"]
    s.record_pool(p, "Mid")
    assert s.records["will"]["fields"]["pools.Mid"]["v"][0] > v1[0]
    again = ProfileStore(tmp_path / "p.json", "dev-a")
    assert again.records == s.records and again.clock == s.clock


def test_newer_wins_older_is_ignored_and_ties_break_by_device(tmp_path):
    a, b = ProfileStore(None, "dev-a"), ProfileStore(None, "dev-b")
    a.record_pool(_player(pools={"Top": ["Garen"]}), "Top")
    b.merge(a.document())
    b.record_pool(_player(pools={"Top": ["Darius"]}), "Top")       # b saw a's counter, so b's edit is newer
    assert a.merge(b.document()) == [("will", "pools.Top"), ("will", "roles")]
    assert a.records["will"]["fields"]["pools.Top"]["value"] == ["Darius"]
    assert b.merge(a.document()) == []                               # nothing older comes back
    x, y = ProfileStore(None, "dev-x"), ProfileStore(None, "dev-y")
    x.record_pool(_player(pools={"Top": ["Garen"]}), "Top")
    y.record_pool(_player(pools={"Top": ["Sett"]}), "Top")           # same counter on both
    x.merge(y.document()), y.merge(x.document())
    assert x.records["will"]["fields"]["pools.Top"] == y.records["will"]["fields"]["pools.Top"]
    assert x.records["will"]["fields"]["pools.Top"]["value"] == ["Sett"]   # "dev-y" > "dev-x"


@pytest.mark.parametrize("bad", [
    {"schema": "other", "records": {}},
    {"schema": SCHEMA, "records": {"Bad Key!": {"fields": {}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Mid": {"v": [9, "d"], "value": ["Notachamp"]}}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Lane": {"v": [9, "d"], "value": ["Ahri"]}}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Mid": {"v": ["x", "d"], "value": ["Ahri"]}}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Mid": "nope"}}}},
    "not a dict",
])
def test_invalid_remote_input_changes_nothing(bad):
    s = ProfileStore(None, "dev-a")
    s.record_pool(_player(pools={"Mid": ["Ahri"]}), "Mid")
    before = json.dumps(s.records, sort_keys=True)
    assert s.merge(bad) == []
    assert json.dumps(s.records, sort_keys=True) == before


def test_accounts_are_claimed_team_wide_and_apply_to_the_roster():
    a, b = ProfileStore(None, "dev-a"), ProfileStore(None, "dev-b")
    p = _player(pools={"Jungle": ["Vi", "Lee Sin"]})
    p.riot_ids, p.puuids = ["Will#EUW"], ["main"]
    a.record_pool(p, "Jungle"), a.record_accounts(p)
    p2 = _player()
    p2.riot_ids, p2.puuids = ["Cylybria#EUW"], ["smurf"]
    b.record_accounts(p2)
    a.merge(b.document()), b.merge(a.document())
    for store in (a, b):
        assert {k for k, c in store.claims.items() if c["player"] == "will"} == {"will#euw", "cylybria#euw", "main", "smurf"}
    roster = Roster(auto_profiles=False)
    assert b.apply_to(roster) == 1
    will = roster.players["will"]
    assert will.champions("Jungle") == ["Vi", "Lee Sin"] and "smurf" in will.puuids and "Jungle" in will.roles
    assert b.apply_to(roster) == 0                                    # idempotent


class FakeRoom:
    """The relay's semantics: one opaque document, gone 30 s after its last write."""
    def __init__(self):
        self.doc, self.at, self.now = None, 0.0, 0.0

    def get(self):
        return None if self.doc is None or self.now - self.at > 30 else json.loads(json.dumps(self.doc))

    def put(self, doc):
        self.doc, self.at = json.loads(json.dumps(doc)), self.now


def _sync(store, room):
    rs = RelaySync(store, "http://unused", "flexers-room-key-0001")
    rs._get, rs._put = room.get, room.put
    return rs


def test_an_offline_teammate_converges_through_whoever_is_online():
    room = FakeRoom()
    will, cian, jack = ProfileStore(None, "pc-will"), ProfileStore(None, "pc-cian"), ProfileStore(None, "pc-jack")
    will.record_pool(_player(pools={"Mid": ["Akshan", "Ahri"]}), "Mid")
    _sync(will, room).exchange()                     # Will online, shares, then logs off
    room.now += 10
    _sync(cian, room).exchange()                     # Cian was online with him: receives it
    room.now += 3600                                 # everyone offline for an hour: the room expires
    assert room.get() is None
    _sync(cian, room).exchange()                     # next evening Cian opens the app first: re-shares
    room.now += 5
    _sync(jack, room).exchange()                     # Jack, who was away, now gets Will's change
    assert jack.records["will"]["fields"]["pools.Mid"]["value"] == ["Akshan", "Ahri"]


def test_concurrent_writers_converge_after_a_few_rounds():
    room = FakeRoom()
    stores = [ProfileStore(None, f"pc-{i}") for i in range(4)]
    for i, s in enumerate(stores):
        s.record_pool(_player(key=f"p{i}", pools={"Top": [["Garen", "Darius", "Sett", "Fiora"][i]]}), "Top")
    for _ in range(3):
        for s in stores:
            _sync(s, room).exchange()
    keys = {json.dumps(s.records, sort_keys=True) for s in stores}
    assert len(keys) == 1 and len(stores[0].records) == 4


# --------------------------------------------------------------------------- profile sync: through the real relay code

@pytest.fixture
def relay_url():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import asyncio

    from aiohttp import web
    import local_relay

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(local_relay.build_app())

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
        loop.run_forever()
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.5)
    yield f"http://127.0.0.1:{port}"
    loop.call_soon_threadsafe(loop.stop)


def test_two_apps_exchange_through_the_bot_relay_handlers(relay_url):
    a, b = ProfileStore(None, "pc-a"), ProfileStore(None, "pc-b")
    a.record_pool(_player(pools={"Support": ["Nami", "Lulu"]}), "Support")
    ra, rb = RelaySync(a, relay_url, "flexers-test-room-key-01"), RelaySync(b, relay_url, "flexers-test-room-key-01")
    ra.exchange()
    changed = rb.exchange()
    assert ("will", "pools.Support") in changed and b.records["will"]["fields"]["pools.Support"]["value"] == ["Nami", "Lulu"]
    assert ra.status["state"] == "online" and ra.status["sent"] == 1 and rb.status["sent"] == 0
    # The draft room itself is untouched: profiles live in their own room.
    import urllib.error
    import urllib.request

    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(relay_url + "/draft-sync/flexers-test-room-key-01", timeout=5)


def test_unreachable_relay_keeps_the_local_copy(tmp_path):
    s = ProfileStore(tmp_path / "p.json", "pc-a")
    s.record_pool(_player(pools={"Top": ["Garen"]}), "Top")
    with pytest.raises(OSError):
        RelaySync(s, "http://127.0.0.1:9", "flexers-room-key-0001", timeout=1).exchange()
    assert ProfileStore(tmp_path / "p.json", "pc-a").records["will"]["fields"]["pools.Top"]["value"] == ["Garen"]


def test_one_player_in_two_roles_is_reported(team, tier, lineup):
    doubled = {**lineup, "ADC": lineup["Top"]}
    out = studio.generate(team, tier, lineup=doubled)
    assert out["drafts"] == [] and "chosen for two roles" in out["error"]


# --------------------------------------------------------------------------- Codex round-three regressions (367)

from draftcoach.profile_sync import MAX_COUNTER, MAX_PLAYERS, validate, InvalidDocument  # noqa: E402


@pytest.mark.parametrize("bad", [
    {"schema": SCHEMA, "records": {"will": {"fields": {"roles": {"v": [3, "d"], "value": [{"x": 1}]}}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Mid": {"v": [MAX_COUNTER + 1, "d"], "value": ["Ahri"]}}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Mid": {"v": [True, "d"], "value": ["Ahri"]}}}}},
    {"schema": SCHEMA, "records": {f"p{i}": {"fields": {}} for i in range(MAX_PLAYERS + 1)}},
    {"schema": SCHEMA, "claims": {"Acc#1": {"v": [1, "d"], "player": "Not A Key"}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Mid": {"v": [5, "d"], "value": ["Ahri"]},
                                                       "roles": {"v": [6, "d"], "value": ["Lane"]}}}}},
])
def test_a_bad_document_is_refused_whole_before_anything_changes(bad):
    s = ProfileStore(None, "dev-a")
    s.record_pool(_player(pools={"Mid": ["Ahri"]}), "Mid")
    before = json.dumps([s.records, s.claims, s.clock], sort_keys=True)
    with pytest.raises(InvalidDocument):
        validate(bad)
    assert s.merge(bad) == [] and json.dumps([s.records, s.claims, s.clock], sort_keys=True) == before


def test_concurrent_edits_and_merges_leave_the_newest_state_on_disk(tmp_path):
    s = ProfileStore(tmp_path / "p.json", "dev-a")
    other = ProfileStore(None, "dev-b")
    def edit(i):
        s.record_pool(_player(key=f"p{i % 5}", pools={"Top": [["Garen", "Darius", "Sett"][i % 3]]}), "Top")
    def merge(i):
        other.record_pool(_player(key=f"q{i % 5}", pools={"Mid": ["Ahri"]}), "Mid")
        s.merge(other.document())
    threads = [threading.Thread(target=edit if i % 2 else merge, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    disk = ProfileStore(tmp_path / "p.json", "dev-a")
    assert disk.records == s.records and disk.clock == s.clock
    assert not list(tmp_path.glob("*.tmp"))


def test_received_changes_survive_a_failed_share():
    room = FakeRoom()
    a, b = ProfileStore(None, "pc-a"), ProfileStore(None, "pc-b")
    a.record_pool(_player(pools={"Top": ["Garen"]}), "Top")
    _sync(a, room).exchange()
    b.record_pool(_player(key="cian", pools={"Jungle": ["Vi"]}), "Jungle")
    rs = _sync(b, room)
    def broken(_doc):
        raise OSError("relay refused")
    rs._put = broken
    changed = rs.exchange()
    assert ("will", "pools.Top") in changed and rs.status["state"] == "offline"


def test_new_players_for_different_accounts_never_share_a_key():
    from draftcoach import my_champs

    r1, r2 = Roster(auto_profiles=False), Roster(auto_profiles=False)
    a = my_champs.new_player(r1, "Alex", "Alex#ONE", "puuid-one")
    b = my_champs.new_player(r2, "Alex", "Alex#TWO", "puuid-two")
    assert a.key != b.key and a.key.startswith("alex-") and b.key.startswith("alex-")


def test_one_account_has_one_owner_across_pcs():
    a, b = ProfileStore(None, "pc-a"), ProfileStore(None, "pc-b")
    pa, pb = _player(key="will"), _player(key="cian")
    pa.riot_ids = pb.riot_ids = ["Shared#EUW"]
    a.record_accounts(pa)
    b.merge(a.document())
    b.record_accounts(pb)                                     # later claim on another PC wins
    a.merge(b.document())
    assert a.claims["shared#euw"]["player"] == b.claims["shared#euw"]["player"] == "cian"
    roster = Roster(auto_profiles=False)
    roster.players["will"] = Player(key="will", name="Will", riot_ids=["Shared#EUW"])
    a.apply_to(roster)
    assert roster.players["will"].riot_ids == [] and roster.players["cian"].riot_ids == ["Shared#EUW"]


def test_imports_never_overwrite_versioned_profile_edits(tmp_path):
    from draftcoach.hub import Hub

    roster = Roster(auto_profiles=False)
    hub = Hub(roster)
    p = _player(pools={"Mid": ["Akshan", "Ahri"]})
    roster.players["will"] = p
    hub.profiles.record_pool(p, "Mid")
    doc = {"format": "flexers-loadfile", "version": 1,
           "players": [{"name": "Will", "pools": {"Mid": ["Zed"]}, "mode": "replace"}]}

    import asyncio
    asyncio.run(hub.import_document(doc, "old export"))
    assert roster.players["will"].champions("Mid") == ["Akshan", "Ahri"]


def test_hub_draft_source_has_one_authority(tmp_path):
    from draftcoach.hub import Hub

    roster = Roster(auto_profiles=False)
    hub = Hub(roster)
    assert hub.effective_source() == "app"                                    # auto, nothing from the bridge
    hub.intent_guidance = {"picks": [], "control": {}}
    assert hub.effective_source() == "app"                                    # auto, no affirmative presence
    hub.intent_guidance = {"picks": [], "control": {"team_present": True}}
    assert hub.effective_source() == "discord"
    roster.settings["draft_source"] = "app"
    assert hub.effective_source() == "app" and hub.shared_guidance() is None
    roster.settings["draft_source"] = "discord"
    hub.intent_guidance = None
    assert hub.effective_source() == "app"                                    # nothing to follow


def test_bridge_always_says_whether_the_team_is_here():
    from tools import intent_live_draft_bridge as bridge

    solo = [{"user_id": -1, "_uncontrolled": True}] * 4 + [{"user_id": 7, "_uncontrolled": False, "_local_unsigned": True}]
    assert bridge._control_for_lobby({}, solo, set())["team_present"] is False
    team = [{"user_id": u, "_uncontrolled": False} for u in (1, 2, 3)] + solo[:2]
    assert bridge._control_for_lobby({}, team, {1, 2, 3})["team_present"] is True


def test_old_boolean_setting_migrates():
    for old, new in ((True, "discord"), (False, "app")):
        r = Roster(auto_profiles=False)
        r.load_dict({"settings": {"discord_drafts": old}})
        assert r.settings["draft_source"] == new
    r = Roster(auto_profiles=False)
    r.load_dict({"settings": {"draft_source": "weird"}})
    assert r.settings["draft_source"] == "auto"


def test_studio_rerolls_one_at_a_time_and_replaces_the_whole_card():
    html = (Path(__file__).resolve().parent.parent / "draftcoach" / "static" / "studio.html").read_text(encoding="utf-8")
    assert "if (card.busy)" in html and "card.replaceWith(fresh)" in html


# --------------------------------------------------------------------------- Codex round-three re-check (368)

from draftcoach.profile_sync import MAX_CLAIMS, MAX_JUMP, account_key  # noqa: E402


@pytest.mark.parametrize("first,second", [("Shared#EUW", "shared #euw"), ("shared #euw", "Shared#EUW")])
def test_case_and_space_aliases_of_one_account_have_one_owner(first, second):
    a, b = ProfileStore(None, "pc-a"), ProfileStore(None, "pc-b")
    pa, pb = _player(key="will"), _player(key="cian")
    pa.riot_ids, pb.riot_ids = [first], [second]
    a.record_accounts(pa)
    b.merge(a.document())
    b.record_accounts(pb)
    a.merge(b.document())
    assert list(a.claims) == [account_key(first)] and a.claims == b.claims and a.claims[account_key(first)]["player"] == "cian"
    roster = Roster(auto_profiles=False)
    roster.players["will"] = Player(key="will", name="Will", riot_ids=[first])
    a.apply_to(roster)
    assert roster.players["will"].riot_ids == [] and roster.players["cian"].riot_ids == [second]


@pytest.mark.parametrize("bad", [
    {"schema": SCHEMA, "records": []},
    {"schema": SCHEMA, "claims": False},
    {"schema": SCHEMA, "records": {"will": {"fields": {"roles": {"v": [1, "d"], "value": ["Top"] * 6}}}}},
    {"schema": SCHEMA, "records": {"will": {"fields": {"pools.Top": {"v": [MAX_COUNTER + 1, "d"], "value": ["Garen"]}}}}},
])
def test_malformed_containers_and_exhausting_counters_are_refused(bad):
    with pytest.raises(InvalidDocument):
        validate(bad)


def test_a_document_jumping_far_ahead_is_refused_and_normal_ones_stay_valid(tmp_path):
    s = ProfileStore(tmp_path / "p.json", "pc-a")
    far = {"schema": SCHEMA, "records": {"cian": {"fields": {"pools.Top": {"v": [MAX_JUMP + 1, "pc-b"], "value": ["Garen"]}}}}}
    assert s.merge(far) == [] and s.clock == 0
    near = {"schema": SCHEMA, "records": {"cian": {"fields": {"pools.Top": {"v": [5000, "pc-b"], "value": ["Garen"]}}}}}
    assert s.merge(near) and s.clock == 5000
    p = _player(key="cian")
    p.puuids = ["acct"]
    s.record_accounts(p)
    s.record_pool(_player(pools={"Mid": ["Ahri"]}), "Mid")
    validate(s.document())                                   # what we share is valid for every peer
    peer = ProfileStore(None, "pc-c")
    assert peer.merge(s.document())                           # a fresh peer accepts a normal team document
    reloaded = ProfileStore(tmp_path / "p.json", "pc-a")      # and our own file reloads
    assert reloaded.records == s.records


def test_local_records_respect_the_cap_peers_enforce():
    s = ProfileStore(None, "pc-a")
    for i in range(MAX_PLAYERS + 5):
        s.record_pool(_player(key=f"p{i}", pools={"Top": ["Garen"]}), "Top")
    assert len(s.records) == MAX_PLAYERS
    validate(s.document())                                   # what we share is always acceptable to peers


def test_an_account_can_move_even_when_claims_are_full():
    s = ProfileStore(None, "pc-a")
    s.claims = {f"acct-{i}": {"v": [1, "pc-z"], "player": "someone"} for i in range(MAX_CLAIMS)}
    p = _player(key="will")
    p.puuids = ["acct-7"]
    s.record_accounts(p)
    assert s.claims["acct-7"]["player"] == "will"


def test_a_corrupt_store_file_is_set_aside(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text('{"schema": "flexers-profiles/2", "records": []}', encoding="utf-8")
    s = ProfileStore(path, "pc-a")
    assert s.records == {} and (tmp_path / "profiles.json.corrupt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_the_window_exits_when_its_server_dies_even_unbound(tmp_path):
    import subprocess

    root = str(Path(__file__).resolve().parent.parent)
    marker = tmp_path / "exited.txt"
    child_code = ("import sys, os, time; sys.path.insert(0, %r);"
                  "from draftcoach.window_supervisor import watch_parent;"
                  "watch_parent(int(sys.argv[1]), lambda: (open(%r, 'w').write('bye'), os._exit(0)));"
                  "time.sleep(60)") % (root, str(marker))
    parent_code = ("import subprocess, sys, os, time;"
                   "c = subprocess.Popen([sys.executable, '-c', %r, str(os.getpid())]);"
                   "print(c.pid, flush=True); time.sleep(60)") % child_code
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True)
    parent.stdout.readline()
    time.sleep(1.0)
    parent.kill()
    parent.wait(timeout=10)
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.2)
    assert marker.read_text() == "bye"


def test_shared_drafts_drive_the_lock_button_when_in_effect():
    from draftcoach import state as ds
    from draftcoach.hub import Hub
    from test_any_queue_assist import _session

    roster = Roster(auto_profiles=False)
    hub = Hub(roster)
    hub.last_session = _session(local=2, active=2)
    hub.state = ds.state_from_lcu_session(hub.last_session, roster, "ChampSelect", seat_fallback=False)
    hub.guidance = {"picks": [{"champion": "Ahri", "role": "Mid", "why": ["app"]}], "bans": []}
    hub.intent_guidance = {"picks": [{"champion": "Zed", "role": "Mid", "why": ["team"]}], "bans": [],
                           "control": {"team_present": True}}
    assert hub.local_view()["suggestion"]["champion"] == "Zed"
    roster.settings["draft_source"] = "app"
    assert hub.local_view()["suggestion"]["champion"] == "Ahri"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_a_window_started_after_its_server_died_exits_at_once():
    import subprocess

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=10)                                     # the server is gone before the window starts
    root = str(Path(__file__).resolve().parent.parent)
    code = ("import sys, os, time; sys.path.insert(0, %r);"
            "from draftcoach.window_supervisor import watch_parent;"
            "watch_parent(int(sys.argv[1]), lambda: os._exit(7)); time.sleep(30)") % root
    child = subprocess.Popen([sys.executable, "-c", code, str(dead.pid)])
    assert child.wait(timeout=15) == 7


def test_a_studio_draft_carries_the_send_to_team_payload_for_real_players_only(team, tier, lineup):
    """Will, 3 Nov: pick a Studio draft for the team. Seats left open in the lineup pin nobody."""
    open_top = dict(lineup, Top=None)
    draft = studio.generate(team, tier, lineup=open_top, count=1)["drafts"][0]
    control = draft["control"]
    assert control["signature"] == draft["signature"]
    chosen = control["control"]["chosen"]
    assert chosen["mode"] == "adaptive" and chosen["champions"] == draft["champions"]
    locks = control["control"]["role_locks"]
    assert "Top" not in locks                                     # the open seat is not pinned to a placeholder
    expected = {r: int(team.players[k].user_id) for r, k in open_top.items() if k and team.players[k].user_id}
    assert locks == expected and len(locks) == 4 and not any(studio.is_placeholder(uid) for uid in locks.values())
