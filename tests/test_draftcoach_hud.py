from __future__ import annotations

import time

from draftcoach.hud import decide


def _snapshot(active, our_locked=0, their_locked=0, local=None, picks=None, bans=None, change=None, source="lcu"):
    our = [{"cell_id": i, "locked": f"Champ{i}" if i < our_locked else None, "role": r} for i, r in enumerate(["Top", "Jungle", "Mid", "ADC", "Support"])]
    their = [{"cell_id": 5 + i, "locked": f"Enemy{i}" if i < their_locked else None} for i in range(5)]
    return {
        "status": {"connection": "connected"},
        "state": {"source": source, "phase": "BAN_PICK", "active": active, "our_team": our, "enemy_team": their},
        "guidance": {"picks": picks or [{"champion": "Lee Sin", "champion_id": 64, "role": "Jungle", "priority": 1, "familiarity": "comfort"}],
                     "bans": bans or [{"champion": "Vi", "champion_id": 254, "tags": ["scouted"], "reasons": ["RivalJg's Jungle pick"]}]},
        "local": local, "last_change": change,
    }


def test_nothing_is_drawn_outside_a_live_draft():
    assert decide({}) == (None, None)
    assert decide(_snapshot({"kind": "none", "ours": False}, source="manual")) == (None, None)
    assert decide(_snapshot({"kind": "none", "ours": False}, our_locked=5, their_locked=5)) == (None, None)


def test_local_pick_and_ban_offer_actions_and_teammate_turns_do_not():
    me = {"cell_id": 1, "role": "Jungle", "can_act": True, "suggestion": {"kind": "pick", "champion": "Lee Sin", "role": "Jungle"}}
    d, _ = decide(_snapshot({"kind": "pick", "ours": True}, local=me))
    assert d["title"] == "Pick Lee Sin" and d["meta"] == "YOUR PICK · JUNGLE" and d["champion"] == "Lee Sin" and d["reason"] == "Confirmed comfort"
    d, _ = decide(_snapshot({"kind": "pick", "ours": True}, local={"cell_id": 3, "role": "ADC", "can_act": False}))
    assert d["title"] == "Suggest Lee Sin" and d["meta"].startswith("TEAM PICK") and d["champion"] is None
    me_ban = {"cell_id": 1, "role": "Jungle", "can_act": True, "suggestion": {"kind": "ban", "champion": "Vi"}}
    d, _ = decide(_snapshot({"kind": "ban", "ours": True}, local=me_ban))
    assert d["title"] == "Ban Vi" and d["kind"] == "ban" and d["champion"] == "Vi" and d["reason"] == "Scouted enemy champion"


def test_enemy_turns_prepare_the_local_role_plan_and_notice_expires():
    picks = [{"champion": "Ornn", "role": "Top"}, {"champion": "Lee Sin", "role": "Jungle", "blind_safe": True}]
    local = {"cell_id": 1, "role": "Jungle", "can_act": False}
    fresh = {"event": "Enemy banned Vi; Enemy banned Jinx", "effect": "Lee Sin leads now", "at": time.time() - 2}
    d, n = decide(_snapshot({"kind": "ban", "ours": False}, local=local, picks=picks, change=fresh))
    assert d["title"] == "Prepare Lee Sin" and d["meta"] == "ENEMY BAN · YOUR JUNGLE PLAN" and d["reason"] == "Blind-safe option"
    assert n == {"event": "Enemy banned Jinx", "effect": "Lee Sin leads now", "at": fresh["at"]}
    stale = dict(fresh, at=time.time() - 30)
    _, n = decide(_snapshot({"kind": "ban", "ours": False}, local=local, picks=picks, change=stale))
    assert n is None


def test_swaps_are_described_but_confirmed_in_league():
    local = {"cell_id": 2, "role": "Mid", "locked": "Ornn", "can_act": False,
             "swap": {"give": "Ornn", "receive": "Ahri", "with_player": "Chud", "trade_state": "AVAILABLE", "can_request": True}}
    d, _ = decide(_snapshot({"kind": "none", "ours": False}, our_locked=5, local=local))
    assert d["title"] == "Give Ornn → receive Ahri" and d["meta"] == "SWAP WITH CHUD IN LEAGUE" and d["champion"] is None
    local = {"cell_id": 2, "role": "Mid", "locked": "Ornn", "can_act": False,
             "incoming_swap": {"trade_id": 9, "from_player": "Chud", "champion": "Ahri", "expected": True, "can_accept": True}}
    d, _ = decide(_snapshot({"kind": "none", "ours": False}, our_locked=5, local=local))
    assert d["meta"] == "CHUD REQUESTED · CONFIRM IN LEAGUE" and d["warning"] is False


def test_ban_reason_keeps_champion_capitalisation():
    from draftcoach.hud import short_ban_reason
    assert short_ban_reason({"reasons": ["beats our Wukong Jungle by +6.2 over 236 games"], "tags": []}) == "Strong into our Wukong Jungle"
