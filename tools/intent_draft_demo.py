"""Run a visual-only custom/bot Draft Coach demo against the real INTent logic.

It never clicks the League client. Test events are accepted only by the local bridge and are
ignored outside custom / versus-AI testing.
"""
from __future__ import annotations

import json
import time
import urllib.request

GUIDANCE = "http://127.0.0.1:8766/api/guidance"
TEST = "http://127.0.0.1:8766/api/test"


def get() -> dict:
    with urllib.request.urlopen(GUIDANCE, timeout=20) as response:
        return json.load(response)


def post(payload: dict) -> None:
    req = urllib.request.Request(TEST, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


def step(label: str, payload: dict | None = None, wait: float = 5.0) -> None:
    print(label, flush=True)
    if payload is not None:
        post(payload)
    time.sleep(wait)


def main() -> None:
    post({"clear": True})
    time.sleep(2)
    snap = get()
    if not snap.get("testing") or not snap.get("active"):
        raise SystemExit("Open a custom / bot champ select first.")
    comps = ((snap.get("guidance") or {}).get("comps") or [])
    if not comps:
        raise SystemExit("No INTent comps are available yet.")
    first = comps[0]
    order = first.get("pick_order") or []
    victim = (order[0].get("champion") if order else None) or next(iter((first.get("champions") or {}).values()))

    step(f"1/5 Baseline: six live plans. Top score {first.get('live_score')}", wait=4)
    step(f"2/5 TEST BAN {victim}: its plan should go grey/red with an X", {"ban": victim}, wait=7)
    step(f"3/5 Unban {victim}; TEST ENEMY TOP Rumble: same scoring logic adds matchup evidence and may reorder close plans",
         {"unban": victim, "enemy_pick": {"role": "Top", "champion": "Rumble"}}, wait=7)
    step("4/5 TEST ENEMY MID Ryze: another real matchup input; watch score deltas/rank order",
         {"enemy_pick": {"role": "Mid", "champion": "Ryze"}}, wait=7)

    live = get()
    pick = (((live.get("guidance") or {}).get("picks") or [{}])[0])
    if pick.get("role") and pick.get("champion"):
        step(f"5/5 TEST OUR LOCK {pick['role']} {pick['champion']}: row should mark gold and remaining draft recalculates",
             {"our_pick": {"role": pick["role"], "champion": pick["champion"]}}, wait=7)
    post({"clear": True})
    print("Demo cleared. No League-client picks/bans were changed.", flush=True)


if __name__ == "__main__":
    main()
