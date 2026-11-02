"""Party test: five app instances, five fake League clients, one shared lobby, one local relay.

Starts everything, drives a full ranked-flex draft the way five teammates would (each seat hovers
and locks through its own client, via its own app), checks that every instance sees the same
draft and the same shared controls, throws the usual quirks at it (relay blocked like a firewall,
client websocket drops, client restart, captain's app dying, a late joiner) and prints a report.

    python tools/party_test.py                 # full scenario
    python tools/party_test.py --scenario smoke
    python tools/party_test.py --keep          # leave everything running afterwards for a look

Ports: relay 8790, lobby control 8700, fake clients 8710-8714, app N on 8800+10N (bridge +1).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
ROOM_KEY = "flexers-party-test-room-0001"
OUR = ["FakerChovyHilary#ALLY", "Adittive#EUW", "RISE OF PETE#PING", "JackJak#EUW", "ReformUKGaming#7710"]
ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.started = time.time()

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail, "at": round(time.time() - self.started, 1)})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""), flush=True)
        return bool(ok)

    def summary(self) -> dict:
        failed = [c for c in self.checks if not c["ok"]]
        return {"passed": len(self.checks) - len(failed), "failed": len(failed), "checks": self.checks,
                "seconds": round(time.time() - self.started, 1)}


LAST_ERROR: dict[str, str] = {}


def http(method: str, url: str, body: dict | None = None, timeout: float = 6.0, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} from {url.split('127.0.0.1:')[-1]}: {detail}") from None
    return json.loads(raw) if raw else None


def get(url: str, timeout: float = 6.0):
    try:
        return http("GET", url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        LAST_ERROR[url] = f"{exc.__class__.__name__}: {str(exc)[:120]}"
        return None


def wait_for(predicate, timeout: float, every: float = 0.5, what: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
        except Exception as exc:  # noqa: BLE001
            last = exc
        if last and not isinstance(last, Exception):
            return last
        time.sleep(every)
    return None


class Instance:
    def __init__(self, index: int, port: int, lockfile: Path, var: Path, log_dir: Path, extra: list[str] | None = None):
        self.index, self.port, self.lockfile, self.var = index, port, lockfile, var
        self.log = log_dir / f"app{index}.log"
        self.extra = extra or []
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def bridge(self) -> str:
        return f"http://127.0.0.1:{self.port + 1}"

    def start(self) -> None:
        cmd = [PY, str(REPO / "run.py"), "--port", str(self.port), "--lockfile", str(self.lockfile), "--bridge",
               "--cloud-sync-mode", "auto", "--cloud-sync-url", "http://127.0.0.1:8790", "--cloud-sync-key", ROOM_KEY,
               "--load", str(REPO / "loadfiles" / "flexers-team.json"), "--roster", str(self.var / f"roster{self.index}.json"),
               "--reset-roster", "--watch", "", "--no-update", "--log-level", "info", *self.extra]
        env = {**os.environ, "INTENT_LIVE": "1"}    # the packaged exe runs this way: the bridge ranks, the app acts
        self.proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=open(self.log, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
                                     env=env, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def state(self) -> dict | None:
        return get(self.url + "/api/state", timeout=4)

    def guidance(self) -> dict | None:
        """The bridge payload with its nested guidance (picks, comps) lifted to the top level."""
        data = get(self.bridge + "/api/guidance", timeout=4)
        if isinstance(data, dict) and isinstance(data.get("guidance"), dict):
            data = {**data["guidance"], **{k: v for k, v in data.items() if k != "guidance"}}
        return data

    def control(self) -> dict | None:
        return get(self.bridge + "/api/control", timeout=4)


class Party:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.var = REPO / "var" / "party"
        self.lobby_dir = self.var / "lobby"
        self.report = Report()
        self.procs: list[subprocess.Popen] = []
        self.instances: list[Instance] = []
        self.lobby = "http://127.0.0.1:8700"
        self.relay = "http://127.0.0.1:8790"
        self.latencies: list[int] = []
        self.control_latencies: list[int] = []

    # ---- infrastructure -----------------------------------------------------------------------
    def start_infra(self) -> None:
        if self.var.exists() and not self.args.keep_var:
            shutil.rmtree(self.var, ignore_errors=True)
        self.var.mkdir(parents=True, exist_ok=True)
        self.procs.append(subprocess.Popen([PY, str(REPO / "tools" / "local_relay.py"), "--port", "8790"], cwd=str(REPO),
                                           stdout=open(self.var / "relay.log", "w"), stderr=subprocess.STDOUT))
        self.procs.append(subprocess.Popen([PY, str(REPO / "tools" / "fake_lcu.py"), "--dir", str(self.lobby_dir), "--control-port", "8700",
                                            "--client-port", "8710", "--our", ",".join(OUR), "--side", self.args.side,
                                            "--turn", str(self.args.turn), "--planning", "6", "--finalization", "150"],   # long enough for the failover step before the trade
                                           cwd=str(REPO), stdout=open(self.var / "lobby.log", "w"), stderr=subprocess.STDOUT))
        ok = wait_for(lambda: get(self.relay + "/health"), 15) and wait_for(lambda: get(self.lobby + "/control/state"), 15)
        self.report.check("relay and fake lobby started", bool(ok))

    def start_apps(self, count: int) -> None:
        for i in range(count):
            inst = Instance(i, 8800 + 10 * i, self.lobby_dir / f"client{i}" / "lockfile", self.var, self.var)
            inst.start()
            self.instances.append(inst)
        for inst in self.instances:
            ok = wait_for(lambda: get(inst.url + "/api/config"), 40)
            self.report.check(f"app {inst.index} serving on {inst.port}", bool(ok))
        for inst in self.instances:
            st = wait_for(lambda: (lambda s: s if s and s["status"].get("connection") == "connected" else None)(inst.state()), 30)
            self.report.check(f"app {inst.index} connected to its League client", bool(st),
                              (st or {}).get("status", {}).get("detail", "") if st else "not connected")

    def stop_all(self) -> None:
        for inst in self.instances:
            inst.stop()
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()

    # ---- helpers ------------------------------------------------------------------------------
    def lobby_state(self) -> dict:
        return get(self.lobby + "/control/state") or {}

    def instance_for_cell(self, cell: int) -> Instance | None:
        for inst in self.instances:
            s = inst.state()
            if s and (s.get("local") or {}).get("cell_id") == cell:
                return inst
        return None

    def all_states(self) -> list[dict]:
        return [inst.state() or {} for inst in self.instances]

    # ---- scenario steps -----------------------------------------------------------------------
    def step_champ_select(self) -> None:
        http("POST", self.lobby + "/control/start", {"planning": 6, "turn": self.args.turn})
        for inst in self.instances:
            s = wait_for(lambda: (lambda s: s if s and (s.get("state") or {}).get("source") == "lcu" else None)(inst.state()), 20)
            local = (s or {}).get("local") or {}
            expected = OUR[inst.index].split("#")[0]
            seat = next((p for p in (s or {}).get("state", {}).get("our_team", []) if p.get("is_local")), {})
            self.report.check(f"app {inst.index} is in champion select as its own seat",
                              bool(s) and seat.get("riot_id", "").startswith(expected),
                              f"local seat {seat.get('riot_id')} -> {seat.get('display_name')} ({seat.get('role')})")
        s0 = self.instances[0].state() or {}
        matched = [(p.get("riot_id"), p.get("display_name"), p.get("user_id")) for p in s0.get("state", {}).get("our_team", [])]
        self.report.check("all five seats matched to roster players with Discord ids", all(m[2] for m in matched), str(matched))
        q = (s0.get("status") or {}).get("queue")
        self.report.check("queue recognised as Ranked Flex", bool(q) and int(q.get("id", -1)) == 440, str(q))

    def step_sync(self) -> None:
        def converged():
            gs = [inst.guidance() or {} for inst in self.instances]
            if not all(g.get("active") for g in gs):
                return None
            leaders = {(g.get("sync") or {}).get("leader") for g in gs}
            hosts = [i for i, g in enumerate(gs) if (g.get("sync") or {}).get("mode") == "auto-host"]
            sigs = {json.dumps([c.get("champions") for c in (g.get("comps") or [])[:3]], sort_keys=True) for g in gs}
            if len(leaders) == 1 and len(hosts) == 1 and len(sigs) == 1 and (gs[0].get("comps") or gs[0].get("picks")):
                return {"leader": leaders.pop(), "host": hosts[0], "comps": len(gs[0].get("comps") or [])}
            return None
        result = wait_for(converged, 45, every=1.0)
        self.report.check("one captain elected and all five show the same drafts", bool(result), str(result or self._sync_debug()))
        # Measure what a follower actually waits for: a fresh guidance read through its own bridge.
        t0 = time.time(); self.instances[1].guidance(); self.report.check("follower guidance read time", True, f"{round((time.time() - t0) * 1000)} ms")
        rooms = get(self.relay + "/control/rooms") or {}
        room = (rooms.get("rooms") or {}).get(ROOM_KEY) or {}
        self.report.check("relay room has five participants", len(room.get("participants") or {}) == 5,
                          f"{len(room.get('participants') or {})} participants, leader {room.get('leader_name')}")

    def _sync_debug(self) -> str:
        out = []
        for inst in self.instances:
            g = inst.guidance() or {}
            out.append(f"app{inst.index}: active={g.get('active')} sync={g.get('sync')} picks={len(g.get('picks') or [])} comps={len(g.get('comps') or [])} status={g.get('status') or g.get('reason') or g.get('message')}")
        return " | ".join(out)

    def step_control_sync(self, label: str, update: dict, expect) -> None:
        source = self.instances[2 % len(self.instances)]
        try:
            http("POST", source.bridge + "/api/control", update)
        except Exception as exc:  # noqa: BLE001
            self.report.check(f"{label}: post accepted", False, str(exc))
            return
        def all_match():
            cs = [((inst.control() or {}).get("control") or {}) for inst in self.instances]
            return cs if all(expect(c) for c in cs) else None
        t0 = time.time()
        cs = wait_for(all_match, 12, every=0.1)
        lag = round((time.time() - t0) * 1000)
        self.control_latencies.append(lag)
        self.report.check(f"{label}: all five apps show it", bool(cs), f"{lag} ms" if cs else str([(inst.control() or {}).get("control") for inst in self.instances]))

    def step_draft(self, quirks: bool) -> None:
        acted: set[int] = set()
        quirk_done = {"ws": False, "relay": False, "rotate": False}
        deadline = time.time() + 60 * 6
        while time.time() < deadline:
            lobby = self.lobby_state()
            phase = (lobby.get("timer") or {}).get("phase")
            if lobby.get("phase") != "ChampSelect" or phase in ("FINALIZATION", "GAME_STARTING"):
                break
            current = [a for g in lobby.get("groups", []) for a in g if a["current"] and a["ours"] and not a["completed"]]
            for action in current:
                if action["id"] in acted:
                    continue
                inst = self.instance_for_cell(action["cell"])
                if inst is None:
                    continue
                s = wait_for(lambda: (lambda s: s if s and (s.get("local") or {}).get("can_act") and (s.get("local") or {}).get("suggestion") else None)(inst.state()), 8)
                local = (s or {}).get("local") or {}
                sug = local.get("suggestion") or {}
                ok = bool(s) and sug.get("kind") == action["type"] and bool(sug.get("champion"))
                g = (s or {}).get("guidance") or {}
                st = (s or {}).get("status") or {}
                diag = (f"suggestion {sug.get('champion')} ({sug.get('role')})" if ok else
                        (f"no answer from app {inst.index} ({LAST_ERROR.get(inst.url + '/api/state')}); " if not s else "") +
                        f"suggestion {sug.get('champion')}; local={ {k: local.get(k) for k in ('kind', 'can_act', 'role', 'locked')} } "
                        f"guidance: headline={g.get('headline')!r} roles_now={g.get('roles_now')} picks={len(g.get('picks') or [])} "
                        f"bans={len(g.get('bans') or [])} compute_ms={st.get('compute_ms')} events={st.get('events')}")
                self.report.check(f"seat {action['cell']} ({OUR[inst.index].split('#')[0]}): app offers the {action['type']} button", ok, diag)
                if not ok:
                    acted.add(action["id"])
                    continue
                champion = sug.get("champion")
                try:
                    http("POST", inst.url + "/api/client/hover", {"champion": champion})
                    time.sleep(0.6)
                    http("POST", inst.url + "/api/client/lock", {"champion": champion})
                except Exception as exc:  # noqa: BLE001
                    self.report.check(f"seat {action['cell']}: hover + lock through its own client", False, str(exc))
                    acted.add(action["id"])
                    continue
                acted.add(action["id"])
                done = wait_for(lambda: self._lobby_action(action["id"]), 6)
                self.report.check(f"seat {action['cell']}: hover + lock through its own client",
                                  bool(done) and done.get("completed") and done.get("champion") == sug.get("champion"),
                                  f"client shows {done.get('champion') if done else None}")
                t0 = time.time()
                seen = wait_for(lambda: self._everyone_sees(action, done.get("champion") if done else None), 6, every=0.05)
                lag = round((time.time() - t0) * 1000)
                self.latencies.append(lag)
                self.report.check(f"seat {action['cell']}: all five apps see the {action['type']}", bool(seen), f"{lag} ms after the lock")
                if quirks:
                    self._maybe_quirk(quirk_done, len(acted))
            time.sleep(0.5)
        lobby = self.lobby_state()
        ours = [a for g in lobby.get("groups", []) for a in g if a["ours"]]
        auto = [e for e in lobby.get("events", []) if "auto-" in e.get("text", "")]
        self.report.check("every one of our bans and picks was locked by its app, none by the timer",
                          all(a["completed"] for a in ours) and not auto, f"{len(auto)} timer auto-locks")

    def _lobby_action(self, action_id: int) -> dict | None:
        for g in self.lobby_state().get("groups", []):
            for a in g:
                if a["id"] == action_id and a["completed"]:
                    return a
        return None

    def _everyone_sees(self, action: dict, champion: str | None) -> bool | None:
        if not champion:
            return None
        for inst in self.instances:
            s = inst.state() or {}
            st = s.get("state") or {}
            if action["type"] == "ban":
                if champion not in (st.get("our_bans") or []):
                    return None
            else:
                seat = next((p for p in st.get("our_team", []) if p.get("cell_id") == action["cell"]), {})
                if seat.get("locked") != champion:
                    return None
        return True

    def _maybe_quirk(self, done: dict, progress: int) -> None:
        if progress == 3 and not done["ws"]:
            done["ws"] = True
            http("POST", self.lobby + "/control/ws-drop/1")
            s = wait_for(lambda: (lambda s: s if s and s["status"].get("connection") == "connected" and (s.get("state") or {}).get("source") == "lcu" else None)(self.instances[1].state()), 20)
            self.report.check("quirk: client 1 websocket dropped, app 1 reconnected and is still in champion select", bool(s))
        elif progress == 5 and not done["relay"]:
            done["relay"] = True
            http("POST", self.relay + "/control/block")
            time.sleep(8)
            gs = [inst.guidance() or {} for inst in self.instances]
            self.report.check("quirk: relay blocked (firewall) for 8 s, every app still has guidance",
                              all((g.get("picks") or g.get("comps")) for g in gs),
                              str([((g.get("sync") or {}).get("mode"), (g.get("sync") or {}).get("stale_seconds")) if g else LAST_ERROR.get(inst.bridge + "/api/guidance") for g, inst in zip(gs, self.instances)]))
            http("POST", self.relay + "/control/unblock")
            result = wait_for(lambda: self._one_host(), 30, every=1.0)
            self.report.check("quirk: relay back, one captain again and drafts converge", bool(result), str(result))
        elif progress == 7 and not done["rotate"]:
            done["rotate"] = True
            http("POST", self.lobby + "/control/rotate/3")
            s = wait_for(lambda: (lambda s: s if s and s["status"].get("connection") == "connected" and (s.get("state") or {}).get("source") == "lcu" else None)(self.instances[3].state()), 30)
            self.report.check("quirk: client 3 restarted on a new port/password, app 3 followed the new lockfile", bool(s),
                              (s or {}).get("status", {}).get("detail", ""))

    def _one_host(self):
        gs = [inst.guidance() or {} for inst in self.instances]
        hosts = [i for i, g in enumerate(gs) if (g.get("sync") or {}).get("mode") == "auto-host"]
        leaders = {(g.get("sync") or {}).get("leader") for g in gs}
        sigs = {json.dumps([c.get("champions") for c in (g.get("comps") or [])[:3]], sort_keys=True) for g in gs}
        return {"host": hosts[0], "leader": leaders.pop()} if len(hosts) == 1 and len(leaders) == 1 and len(sigs) == 1 else None

    def step_captain_failover(self) -> None:
        gs = [inst.guidance() or {} for inst in self.instances]
        host = next((i for i, g in enumerate(gs) if (g.get("sync") or {}).get("mode") == "auto-host"), None)
        if host is None:
            self.report.check("captain failover: a captain exists to kill", False)
            return
        self.instances[host].stop()
        result = wait_for(lambda: self._one_host_among([i for i in range(len(self.instances)) if i != host]), 30, every=1.0)
        self.report.check(f"captain failover: app {host} killed, another captain elected and followers converge", bool(result), str(result))
        self.instances[host].start()
        back = wait_for(lambda: (lambda g: g if g and g.get("active") and (g.get("sync") or {}).get("leader") else None)(self.instances[host].guidance()), 60, every=1.0)
        self.report.check(f"captain failover: app {host} restarted mid-draft and rejoined the room", bool(back), str((back or {}).get("sync")))

    def _one_host_among(self, indexes: list[int]):
        gs = [self.instances[i].guidance() or {} for i in indexes]
        hosts = [i for i, g in zip(indexes, gs) if (g.get("sync") or {}).get("mode") == "auto-host"]
        leaders = {(g.get("sync") or {}).get("leader") for g in gs}
        return {"host": hosts[0], "leader": leaders.pop()} if len(hosts) == 1 and len(leaders) == 1 and all(g.get("active") for g in gs) else None

    def step_trade(self) -> None:
        lobby = wait_for(lambda: (lambda l: l if (l.get("timer") or {}).get("phase") == "FINALIZATION" else None)(self.lobby_state()), 60)
        if not lobby:
            self.report.check("finalisation reached", False)
            return
        a, b = 0, 2
        http("POST", self.lobby + f"/control/trade/{a}/{b}")
        inst = self.instance_for_cell(b)
        s = wait_for(lambda: (lambda s: s if s and (s.get("local") or {}).get("incoming_swap") else None)(inst.state()), 10) if inst else None
        self.report.check("trade: the receiving app shows the incoming swap", bool(s), str((s or {}).get("local", {}).get("incoming_swap")))
        if s:
            try:
                http("POST", inst.url + "/api/client/swap_accept", {"trade_id": s["local"]["incoming_swap"]["trade_id"]})
                ok = wait_for(lambda: (lambda l: l if any(t.get("state") == "DONE" for t in l.get("trades", [])) else None)(self.lobby_state()), 6)
                self.report.check("trade: accepted through the client, champions swapped", bool(ok))
            except Exception as exc:  # noqa: BLE001
                self.report.check("trade: accepted through the client, champions swapped", False, str(exc))

    def step_stats(self) -> None:
        """Every app pulls its own player's history through its own client and builds the review."""
        for inst in self.instances:
            if inst.proc is None or inst.proc.poll() is not None:
                continue
            data = get(inst.url + "/api/stats/me?refresh=1", timeout=60)
            review = (data or {}).get("review") or {}
            expected = OUR[inst.index].split("#")[0]
            ok = bool(review) and str(review.get("account", {}).get("name", "")).startswith(expected) and (review.get("overview") or {}).get("games", 0) >= 20
            self.report.check(f"app {inst.index}: player review built from its own account ({expected})", ok,
                              f"{(review.get('overview') or {}).get('games')} games, {len(review.get('insights') or [])} insights" if ok else str((data or {}).get("error") or data)[:200])

    def step_end(self) -> None:
        http("POST", self.lobby + "/control/end")
        for inst in self.instances:
            s = wait_for(lambda: (lambda s: s if s and not (s.get("state") or {}).get("our_team") or (s.get("state") or {}).get("source") != "lcu" else None)(inst.state()), 15)
            self.report.check(f"app {inst.index} left champion select when the game started", bool(s))

    # ---- run ------------------------------------------------------------------------------------
    def run(self) -> dict:
        smoke = self.args.scenario == "smoke"
        print("== starting relay, fake lobby and apps", flush=True)
        self.start_infra()
        self.start_apps(5)
        print("== champion select", flush=True)
        self.step_champ_select()
        print("== sync", flush=True)
        self.step_sync()
        if not smoke:
            print("== shared controls", flush=True)
            self.step_control_sync("team style set to dive on app 2", {"identity": "dive"}, lambda c: c.get("identity") == "dive")
            self.step_control_sync("role lock Top -> Cian on app 2", {"role_locks": {"Top": 139837324438536193}},
                                   lambda c: str((c.get("role_locks") or {}).get("Top")) == "139837324438536193")
            self.step_control_sync("champion lock Support -> Thresh on app 2", {"champion_locks": {"Support": "Thresh"}},
                                   lambda c: (c.get("champion_locks") or {}).get("Support") == "Thresh")
            before = ((self.instances[0].control() or {}).get("control") or {}).get("shake", 0)
            self.step_control_sync("shake it up on app 2", {"shake_it_up": True}, lambda c, b=before: int(c.get("shake") or 0) > int(b))
        print("== the draft", flush=True)
        self.step_draft(quirks=not smoke)
        if not smoke:
            print("== captain failover", flush=True)
            self.step_captain_failover()
        print("== player review", flush=True)
        self.step_stats()
        print("== trade + game start", flush=True)
        self.step_trade()
        self.step_end()
        if self.latencies:
            self.report.check("propagation: a lock reaches all five screens", True,
                              f"median {sorted(self.latencies)[len(self.latencies) // 2]} ms, worst {max(self.latencies)} ms over {len(self.latencies)} locks")
        if self.control_latencies:
            self.report.check("propagation: a shared control change reaches all five", True,
                              f"median {sorted(self.control_latencies)[len(self.control_latencies) // 2]} ms, worst {max(self.control_latencies)} ms")
        summary = self.report.summary()
        (self.var / "report.json").write_text(json.dumps({**summary, "lobby_events": self.lobby_state().get("events")}, indent=2), encoding="utf-8")
        print(f"\n== {summary['passed']} passed, {summary['failed']} failed in {summary['seconds']} s  (report: {self.var / 'report.json'})")
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=["smoke", "full"], default="full")
    parser.add_argument("--side", choices=["blue", "red"], default="blue")
    parser.add_argument("--turn", type=float, default=25.0, help="seconds per ban/pick turn in the fake client")
    parser.add_argument("--keep", action="store_true", help="leave relay, lobby and apps running afterwards")
    parser.add_argument("--keep-var", action="store_true", help="do not wipe var/party first")
    args = parser.parse_args()
    party = Party(args)
    try:
        summary = party.run()
    finally:
        if not args.keep:
            party.stop_all()
        else:
            print("left running: relay 8790, lobby 8700, apps " + ", ".join(i.url for i in party.instances))
    sys.exit(1 if summary["failed"] else 0)


if __name__ == "__main__":
    main()
