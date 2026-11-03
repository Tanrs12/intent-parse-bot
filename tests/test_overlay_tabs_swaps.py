"""Overlay round two (Will, 3 Nov 2026): swap buttons + manual switch, own-role lock only, owned champions,
one overlay that outlives its window, My champs / Stats tabs, smurf-proof identity, app-first drafting."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draftcoach import assist, client_actions, identity
from draftcoach import champions as champ_map
from draftcoach import loadfiles as lf
from draftcoach.hub import Hub
from draftcoach.roster import Roster
from draftcoach.window_supervisor import WindowSupervisor

from test_any_queue_assist import FakeRest, _session, _live_hub, _run, TEAM_FILE  # noqa: F401

STATIC = Path(__file__).resolve().parent.parent / "draftcoach" / "static"


@pytest.fixture
def roster() -> Roster:
    r = Roster(auto_profiles=True)
    assert lf.import_path(r, TEAM_FILE).ok
    return r


# --------------------------------------------------------------------------- swap buttons

def test_offer_rows_list_every_offer_with_the_verbs_its_state_allows():
    sess = _session(local=2)
    offers = {"position-swaps": [{"id": 1, "cellId": 0, "state": "AVAILABLE"}, {"id": 2, "cellId": 1, "state": "RECEIVED"},
                                 {"id": 3, "cellId": 3, "state": "SENT"}, {"id": 4, "cellId": 4, "state": "INVALID"},
                                 None, {"id": "x", "cellId": 0, "state": "AVAILABLE"}],
              "pick-order-swaps": [{"id": 9, "cellId": 4, "state": "AVAILABLE"}]}
    rows = assist.swap_offer_rows(sess, offers, {0: "Ann", 1: "Bob"})
    by_id = {r["swap_id"]: r for r in rows}
    assert set(by_id) == {1, 2, 3, 9}
    assert by_id[1]["verbs"] == ["request"] and by_id[1]["name"] == "Ann" and by_id[1]["role"] == "Top"
    assert by_id[2]["verbs"] == ["accept", "decline"] and by_id[3]["verbs"] == ["cancel"]
    assert by_id[9]["family"] == "pick-order-swaps" and by_id[9]["pick_slot"] == 4 and by_id[9]["name"] == "Seat 5"


def test_session_swap_lists_win_over_fetched_ones():
    sess = _session(local=2, position_swaps=[{"id": 7, "cellId": 0, "state": "RECEIVED"}])
    rows = assist.swap_offer_rows(sess, {"position-swaps": [{"id": 1, "cellId": 0, "state": "AVAILABLE"}]})
    assert [r["swap_id"] for r in rows] == [7]


def test_snapshot_carries_offers_and_the_auto_switch(roster, monkeypatch, tmp_path):
    rest = FakeRest(offers={"position-swaps": [{"id": 5, "cellId": 0, "state": "AVAILABLE"}], "pick-order-swaps": []})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path, role=None)

    async def go():
        await hub.ingest_lcu_session(_session(local=2), "ChampSelect", {"id": 420})
        await hub.settle()
        await hub._offers_task
        return hub.snapshot()
    a = _run(go())["assist"]
    assert a["auto_swap"] is True and [r["swap_id"] for r in a["offers"]] == [5]
    roster.settings["auto_swap"] = False
    assert hub.snapshot()["assist"]["auto_swap"] is False


def test_manual_cancel_is_a_valid_seat_swap(roster, monkeypatch, tmp_path):
    rest = FakeRest()
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=0), "ChampSelect", {"id": 420})
        await hub.settle()
        await hub.client_act("seat_swap", family="position-swaps", swap_id=3, verb="cancel")
    _run(go())
    assert ("POST", "/lol-champ-select/v1/session/position-swaps/3/cancel", None) in rest.calls


def test_overlay_has_swap_buttons_size_and_auto_switch():
    html = (STATIC / "overlay.html").read_text(encoding="utf-8")
    for needle in ('id="swaps"', 'id="auto-swap"', "seat_swap", 'id="size"', "api.set_scale", "auto_swap: !a.auto_swap"):
        assert needle in html, needle


# --------------------------------------------------------------------------- lock: own role, owned champions

class PickableRest(FakeRest):
    def __init__(self, pickable, **kw):
        super().__init__(**kw)
        self.pickable = pickable

    def get(self, path, **kw):
        if path.endswith("/pickable-champion-ids"):
            self.calls.append(("GET", path))
            return self.pickable
        return super().get(path, **kw)


def _shared_jungle_only():
    return {"picks": [{"champion": "Lee Sin", "champion_id": champ_map.id_for("Lee Sin"), "role": "Jungle",
                       "why": ["team plan"]}], "comps": [], "bans": []}


def test_my_seat_is_never_offered_another_roles_champion(roster, monkeypatch, tmp_path):
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path)
    hub._tierlist = {"roles": {"Mid": {"Ahri": {"win": 51, "pick": 9, "games": 9000}}}}

    async def go():
        await hub.ingest_lcu_session(_session(local=2, active=2), "ChampSelect", {"id": 0, "name": "Practice Tool"})
        await hub.settle()
        hub.intent_guidance = _shared_jungle_only()
        return hub.local_view()
    view = _run(go())
    assert view["suggestion"]["role"] == "Mid" and view["suggestion"]["champion"] == "Ahri"


def test_unowned_champions_are_neither_suggested_nor_locked(roster, monkeypatch, tmp_path):
    rest = PickableRest([champ_map.id_for("Garen")])
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)
    hub._tierlist = {"roles": {"Top": {"Darius": {"win": 53, "pick": 8, "games": 9000}, "Garen": {"win": 52, "pick": 6, "games": 8000}}}}

    async def go():
        await hub.ingest_lcu_session(_session(local=0, active=0), "ChampSelect", {"id": 420})
        await hub.settle()
        await hub._offers_task
        view = hub.local_view()
        with pytest.raises(RuntimeError, match="can't pick Darius"):
            await hub.client_act("lock", "Darius", action_id=100, kind="pick", game_id=4242)
        return view, hub.snapshot()["assist"]
    view, a = _run(go())
    assert view["suggestion"]["champion"] == "Garen"
    assert [p["champion"] for p in a["picks"]] == ["Garen"]


def test_lock_that_league_did_not_take_is_reported():
    class Ignoring:
        def __init__(self):
            self.session = _session(local=0, active=0)

        def request(self, *_a, **_k):
            return None

        def get(self, path, **_):
            return self.session                              # still not completed after the PATCHes

    actions = client_actions.ClientActions(Ignoring())
    with pytest.raises(RuntimeError, match="League did not lock Garen"):
        actions.lock(_session(local=0, active=0), "Garen")


def test_hud_proxy_never_falls_back_to_another_seats_pick(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import intent_hud_proxy as proxy

    snap = {"state": {"active": {"kind": "pick", "ours": True}}, "local": {"can_act": True, "role": "Mid", "suggestion": None}}
    monkeypatch.setattr(proxy, "_json", lambda url: dict(snap) if "/api/state" in url else
                        {"active": True, "guidance": _shared_jungle_only()})
    out = proxy.merged_state()
    assert out["local"]["suggestion"] is None


# --------------------------------------------------------------------------- one overlay, decoupled from the server

def test_crashed_window_is_reopened_but_a_closed_one_stays_closed(tmp_path):
    crash = WindowSupervisor([sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path / "w.log")
    crash.open()
    deadline = time.time() + 15
    while len(crash.crashes) < 2 and time.time() < deadline:
        time.sleep(0.2)
    crash.stop()
    assert len(crash.crashes) >= 2                           # restarted after the first crash
    closed = WindowSupervisor([sys.executable, "-c", "pass"], tmp_path / "c.log")
    closed.open()
    closed.proc.wait(timeout=10)
    time.sleep(0.5)
    assert closed.crashes == [] and not closed.running
    assert "overlay window started" in (tmp_path / "c.log").read_text(encoding="utf-8")


def test_launchers_open_only_the_white_overlay():
    root = Path(__file__).resolve().parent.parent
    bat = (root / "Draft Coach (source, every queue).bat").read_text(encoding="utf-8")
    assert "--hud" not in bat and "--cloud-sync-mode follow" in bat and "INTENT_LIVE" not in bat
    assert "--hud" not in (root / "build_exe.py").read_text(encoding="utf-8")
    run = (root / "run.py").read_text(encoding="utf-8")
    assert "args.window, args.hud = True, True" not in run and "WindowSupervisor" in run


def test_window_size_preset_is_remembered(tmp_path, monkeypatch):
    from draftcoach import window

    monkeypatch.setattr(window, "_settings_path", lambda: tmp_path / "overlay-window.json")
    api = window.Api("http://127.0.0.1:1")
    assert api.scale == 1.0 and api.set_scale(1.2) == 1.15
    assert window.load_scale() == 1.15


def test_theme_is_saved_and_shared_across_both_windows():
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    body = common.split("function themeControl", 1)[1].split("function fmtTimer", 1)[0]
    assert "localStorage.getItem(sharedKey)" in body
    assert "localStorage.setItem(sharedKey" in body
    assert "fd-theme-preference-v1" in body
    assert 'window.addEventListener("storage"' in body
    for rel in ("draftcoach/hud.py", "draftcoach/hud_intent.py"):
        text = (STATIC.parent.parent / rel).read_text(encoding="utf-8")
        assert 'theme or "porcelain"' in text and 'theme or "client"' not in text


# --------------------------------------------------------------------------- identity across smurfs

def test_owner_link_is_bound_to_this_machine(tmp_path, monkeypatch):
    identity.save_owner("will", tmp_path)
    assert identity.load_owner(tmp_path) == "will"
    monkeypatch.setattr(identity, "device_id", lambda: "another-pc")
    assert identity.load_owner(tmp_path) is None             # a copied var folder carries no owner


def test_any_account_on_the_owners_pc_becomes_theirs(roster, monkeypatch, tmp_path):
    monkeypatch.setattr(identity, "VAR_DIR", tmp_path)
    player = next(p for p in roster.players.values() if p.riot_ids)
    identity.save_owner(player.key, tmp_path)
    monkeypatch.setattr("draftcoach.identity.load_owner", lambda var_dir=None: player.key)
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path)
    sess = _session(local=2)
    sess["myTeam"][2].update({"gameName": "Cylybria", "tagLine": "EUW", "puuid": "smurf-puuid"})

    async def go():
        await hub.ingest_lcu_session(sess, "ChampSelect", {"id": 420})
        await hub.settle()
    _run(go())
    assert "smurf-puuid" in player.puuids and "Cylybria#EUW" in player.riot_ids
    assert next(p for p in hub.state.our_team if p.cell_id == 2).player_key == player.key


# --------------------------------------------------------------------------- My champs API

class MeRest(FakeRest):
    account = {"gameName": "Cylybria", "tagLine": "EUW", "puuid": "smurf-puuid"}

    def get(self, path, **kw):
        if path == "/lol-summoner/v1/current-summoner":
            return dict(self.account)
        if path.endswith("/champion-mastery"):
            return [{"championId": champ_map.id_for("Akshan"), "championPoints": 40000, "lastPlayTime": 0}]
        return super().get(path, **kw)


@pytest.fixture
def me_client(tmp_path, monkeypatch):
    from draftcoach import server

    monkeypatch.setattr(identity, "VAR_DIR", tmp_path)
    monkeypatch.setattr(identity, "load_owner", lambda var_dir=None: getattr(identity, "_test_owner", None))
    monkeypatch.setattr(identity, "save_owner", lambda key, var_dir=None: setattr(identity, "_test_owner", key))
    identity._test_owner = None
    app = server.create_app({}, enable_lcu=False, roster_path=tmp_path / "roster.json", watch_dir=None,
                            load_files=[TEAM_FILE])
    hub = app.state.hub
    hub.rest = MeRest()
    hub.lcu_enabled = True
    with TestClient(app) as client:
        yield client, hub


def test_my_champs_edits_create_and_update_the_pool(me_client):
    client, hub = me_client
    me = client.get("/api/me").json()
    assert me["riot_id"] == "Cylybria#EUW" and me["player_key"] is None and set(me["options"]) == set(assist.ROLES)
    assert any(o["champion"] == "Akshan" for o in me["options"]["Mid"])
    r = client.post("/api/me/pool", json={"role": "Mid", "champions": ["akshan", "Ahri"]})
    assert r.status_code == 200 and r.json()["pool"] == ["Akshan", "Ahri"]
    me = client.get("/api/me").json()
    assert me["pools"]["Mid"] == ["Akshan", "Ahri"] and "Mid" in me["roles"]
    assert client.post("/api/me/pool", json={"role": "Mid", "champions": ["Notachamp"]}).status_code == 400
    assert client.post("/api/me/pool", json={"role": "Lane", "champions": []}).status_code == 400
    assert client.post("/api/me/pool", json={"role": "Mid", "champions": ["Ahri"]}).json()["pool"] == ["Ahri"]


def test_linking_this_pc_makes_the_smurf_the_owner(me_client):
    client, hub = me_client
    key = client.get("/api/me").json()["players"][0]["key"]
    assert client.post("/api/me/owner", json={"player_key": key}).json()["device_owner"] == key
    me = client.get("/api/me").json()
    assert me["player_key"] == key and me["linked"]
    assert "Cylybria#EUW" in hub.roster.players[key].riot_ids
    assert client.post("/api/me/owner", json={"player_key": "nobody"}).status_code == 404


# --------------------------------------------------------------------------- app-first drafting

def test_app_ranks_its_own_drafts_unless_discord_is_switched_on(roster, monkeypatch, tmp_path):
    from draftcoach.roster import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["draft_source"] == "auto"
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path)
    roster.settings["draft_source"] = "app"

    async def go():
        await hub.ingest_lcu_session(_session(local=0, active=0), "ChampSelect", {"id": 420})
        await hub.settle(timeout=60)
        return hub.guidance
    local = _run(go())
    assert local["headline"] != "INTent live backend" and local["picks"]
    roster.settings["draft_source"] = "discord"
    assert _run(go())["headline"] == "INTent live backend"
    review = (STATIC / "draft-review.js").read_text(encoding="utf-8")
    assert 'if ((snapshot.draft_source || {}).effective !== "discord") return snapshot;' in review


def test_overlay_has_its_tabs_and_stats_lives_in_the_app():
    html = (STATIC / "overlay.html").read_text(encoding="utf-8")
    for needle in ('id="tab-draft"', 'id="tab-champs"', 'id="pane-champs"', 'id="open-stats"',
                   "/api/me/pool", "/api/me/owner", 'id="discord-drafts"'):
        assert needle in html, needle
    assert 'id="tab-stats"' not in html and 'id="pane-stats"' not in html      # Will, 3 Nov: stats get a page in the app
    stats = (STATIC / "stats.html").read_text(encoding="utf-8")
    assert "/api/stats/me" in stats


def test_run_py_starts_and_serves(tmp_path):
    """Smoke test of the real entry point: a misplaced import once made main() crash on its first line."""
    import socket
    import subprocess
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen([sys.executable, str(root / "run.py"), "--simulate", "--no-bridge", "--no-auto-sync", "--no-update",
                             "--no-comp-overlay", "--port", str(port), "--roster", str(tmp_path / "roster.json"),
                             "--config", str(tmp_path / "config.json"), "--watch", ""],
                            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as r:
                    assert r.status == 200
                    return
            except OSError:
                assert proc.poll() is None, proc.stdout.read().decode("utf-8", "replace")[-2000:]
                time.sleep(0.5)
        pytest.fail("run.py did not start serving within 60 s")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# --------------------------------------------------------------------------- Codex round-two regressions (366)

from draftcoach import my_champs  # noqa: E402
from draftcoach.roster import Player  # noqa: E402


def test_unmatched_namesake_gets_their_own_player_and_never_touches_the_teammate():
    r = Roster(auto_profiles=False)
    alex = Player(key="alex", name="Alex", riot_ids=["Alex#ONE"])
    my_champs.set_pool(alex, "Top", ["Garen"])
    r.players["alex"] = alex
    assert my_champs.account_player(r, "Alex#TWO", "p2", None) is None
    mine = my_champs.new_player(r, "Alex", "Alex#TWO")
    my_champs.link_unique(r, mine, "Alex#TWO", "p2")
    my_champs.set_pool(mine, "Top", ["Darius"])
    assert mine.key != "alex" and alex.champions("Top") == ["Garen"] and alex.riot_ids == ["Alex#ONE"]
    assert my_champs.account_player(r, "Alex#TWO", "p2", None) is mine


def test_an_account_owned_by_another_player_is_not_taken_over():
    r = Roster(auto_profiles=False)
    a, b = Player(key="a", name="A", puuids=["acc"]), Player(key="b", name="B")
    r.players.update(a=a, b=b)
    assert my_champs.link_unique(r, b, "A#EUW", "acc") is False and b.puuids == []
    assert my_champs.link_unique(r, a, "A#EUW", "acc") is True and "A#EUW" in a.riot_ids


def test_reordering_a_pool_persists_the_order():
    p = Player(key="p", name="P")
    my_champs.set_pool(p, "Mid", ["Ahri", "Akshan", "Yone"])
    my_champs.set_pool(p, "Mid", ["Yone", "Ahri", "Akshan"])
    assert p.champions("Mid") == ["Yone", "Ahri", "Akshan"]
    assert [e.priority for e in p.pools["Mid"]] == [1, 2, 3]
    my_champs.set_pool(p, "Mid", [])
    assert "Mid" not in p.pools


def test_switching_owner_links_the_new_account_to_the_new_owner_only(me_client):
    client, hub = me_client
    players = client.get("/api/me").json()["players"]
    a, b = players[0]["key"], players[1]["key"]
    client.post("/api/me/owner", json={"player_key": a})
    hub.rest.account = {"gameName": "Fresh", "tagLine": "NEW", "puuid": "fresh-puuid"}
    client.post("/api/me/owner", json={"player_key": b})
    assert "fresh-puuid" in hub.roster.players[b].puuids and "fresh-puuid" not in hub.roster.players[a].puuids
    assert client.post("/api/me/owner", json={"new_name": ""}).status_code == 400


def test_pool_save_for_an_unknown_namesake_creates_a_separate_player(me_client):
    client, hub = me_client
    name = next(p for p in hub.roster.players.values() if p.riot_ids).name
    hub.rest.account = {"gameName": name, "tagLine": "ZZ99", "puuid": "namesake"}
    before = {k: {r: list(p.champions(r)) for r in p.pools} for k, p in hub.roster.players.items()}
    r = client.post("/api/me/pool", json={"role": "Top", "champions": ["Garen"]}).json()
    assert r["player_key"] not in before
    after = {k: {r2: list(p.champions(r2)) for r2 in p.pools} for k, p in hub.roster.players.items() if k in before}
    assert after == before


NODE_LOCAL_PICK = r"""
const fs = require('fs'); global.window = {}; window.Draft = { $: () => null, el: () => ({}) };
eval(fs.readFileSync(process.argv[1], 'utf8'));
const R = window.Draft.Review;
const snap = (sugg) => ({ state: { source: 'lcu', active: { kind: 'pick', ours: true }, our_team: [{ is_local: true, role: 'Mid', user_id: 7 }] },
  status: {}, draft_source: { effective: "discord" }, local: { can_act: true, role: 'Mid', suggestion: sugg } });
const bridge = { active: true, guidance: { picks: [{ champion: 'Lee Sin', role: 'Jungle', player_id: 7 }, { champion: 'Zed', role: 'Mid' }] } };
const a = R.liveSnapshot(snap({ kind: 'pick', champion: 'Ahri', role: 'Mid' }), bridge).local.suggestion;
const b = R.liveSnapshot(snap(null), bridge).local.suggestion;
console.log(JSON.stringify([a && a.champion, b]));
"""


def test_overlay_lock_button_uses_only_the_servers_checked_suggestion():
    import subprocess

    out = subprocess.run(["node", "-e", NODE_LOCAL_PICK, str(STATIC / "draft-review.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["Ahri", None]


class ScriptedRest(FakeRest):
    def __init__(self, fail=(), pickable=None, **kw):
        super().__init__(**kw)
        self.fail, self.pickable, self.gets = set(fail), pickable, 0

    def get(self, path, **kw):
        if any(path.endswith(f) for f in self.fail):
            raise RuntimeError("client error")
        if path.endswith("/pickable-champion-ids"):
            self.gets += 1
            return self.pickable
        return super().get(path, **kw)


def test_one_failing_swap_list_never_hides_the_pickable_list(roster, monkeypatch, tmp_path):
    rest = ScriptedRest(fail=["/position-swaps"], pickable=[1, 2],
                        offers={"pick-order-swaps": [{"id": 9, "cellId": 0, "state": "AVAILABLE"}]})
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=2), "ChampSelect", {"id": 420})
        await hub.settle()
        await hub._offers_task
    _run(go())
    assert hub._pickable == {1, 2} and hub._offers.get("pick-order-swaps")


def test_same_pickable_ids_next_game_still_enforce_ownership(roster, monkeypatch, tmp_path):
    rest = ScriptedRest(pickable=[champ_map.id_for("Garen")])
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def game(gid):
        await hub.ingest_lcu_session(_session(local=0, game_id=gid), "ChampSelect", {"id": 420})
        await hub.settle()
        while hub._offers_task and not hub._offers_task.done():
            await hub._offers_task
        hub._offers_at = 0
    _run(game(1))
    _run(game(2))
    assert hub.can_pick("Garen") and not hub.can_pick("Darius")


def test_forced_refresh_during_a_running_fetch_is_not_dropped(roster, monkeypatch, tmp_path):
    rest = ScriptedRest(pickable=[])
    hub = _live_hub(roster, rest, monkeypatch, tmp_path)

    async def go():
        await hub.ingest_lcu_session(_session(local=0), "ChampSelect", {"id": 420})
        await hub.settle()
        hub.refresh_offers(force=True)                       # first fetch still running: queued, not dropped
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not hub._offers_task or hub._offers_task.done():
                break
        await asyncio.sleep(0.2)
    _run(go())
    assert rest.gets >= 2


def test_no_window_starts_once_shutdown_began(tmp_path):
    sup = WindowSupervisor([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "w.log")
    sup.stop()
    assert sup.open() is False and sup.proc is None


PARENT = (
    "import sys, time; sys.path.insert(0, {root!r});"
    "from draftcoach.window_supervisor import WindowSupervisor;"
    "from pathlib import Path;"
    "s = WindowSupervisor([sys.executable, '-c', 'import time; time.sleep(60)'], Path({log!r}));"
    "s.open(); print(s.proc.pid, flush=True); time.sleep(60)"
)


def _alive(pid):
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259                                   # STILL_ACTIVE


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_killing_the_server_process_takes_the_window_with_it(tmp_path):
    import subprocess

    code = PARENT.format(root=str(Path(__file__).resolve().parent.parent), log=str(tmp_path / "w.log"))
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline().strip())
    assert _alive(child)
    parent.kill()                                            # Task Manager style: no finally, no cleanup code
    parent.wait(timeout=10)
    deadline = time.time() + 10
    while _alive(child) and time.time() < deadline:
        time.sleep(0.2)
    assert not _alive(child)


def test_lock_readback_unavailable_is_reported_unverified():
    class Blind:
        def request(self, *_a, **_k):
            return None

        def get(self, *_a, **_k):
            raise RuntimeError("no answer")
    out = client_actions.ClientActions(Blind()).lock(_session(local=0, active=0), "Garen")
    assert out["completed"] and out["verified"] is False


def test_packaged_build_relaunches_itself_for_the_window(monkeypatch):
    from draftcoach import window_supervisor as ws

    monkeypatch.setattr(ws.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ws.sys, "executable", r"C:\Games\Flexers Coach\FlexersDraftCoach.exe")
    cmd = ws.window_command("127.0.0.1", 8765, Path("run.py"))
    assert cmd[0].endswith("FlexersDraftCoach.exe") and cmd[1] == "--window-only" and "run.py" not in cmd
    assert "--no-update" in cmd and "--no-auto-sync" in cmd



def test_my_champs_shares_a_newly_seen_smurf(me_client):
    client, hub = me_client
    key = client.get("/api/me").json()["players"][0]["key"]
    client.post("/api/me/owner", json={"player_key": key})
    hub.rest.account = {"gameName": "Smurfy", "tagLine": "NEW", "puuid": "smurf-2"}
    client.get("/api/me")
    assert hub.profiles.claims.get("smurf-2", {}).get("player") == key


def test_app_source_never_puts_a_discord_pick_on_the_lock_button(roster, monkeypatch, tmp_path):
    hub = _live_hub(roster, FakeRest(), monkeypatch, tmp_path)
    roster.settings["draft_source"] = "app"
    hub._tierlist = {"roles": {"Mid": {"Ahri": {"win": 51, "pick": 9, "games": 9000}}}}

    async def go():
        await hub.ingest_lcu_session(_session(local=2, active=2), "ChampSelect", {"id": 420})
        await hub.settle(timeout=60)
        hub.intent_guidance = {"picks": [{"champion": "Zed", "role": "Mid", "why": ["discord"]}], "control": {"team_present": True}}
        return hub.local_view()
    view = _run(go())
    assert view["suggestion"] and view["suggestion"]["champion"] != "Zed"


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_suspended_window_is_bound_and_then_actually_runs(tmp_path):
    marker = tmp_path / "ran.txt"
    sup = WindowSupervisor([sys.executable, "-c", f"open(r'{marker}', 'w').write('ok')"], tmp_path / "w.log")
    assert sup.open()
    sup.proc.wait(timeout=20)
    sup.stop()
    assert marker.read_text() == "ok"
