from __future__ import annotations

import json

import pytest

from draftcoach import champions as champ_map
from draftcoach import loadfiles as lf
from draftcoach import state as ds
from draftcoach.draft_order import ManualDraft
from draftcoach.roster import Roster

POOLS = {
    "Top": ["Ornn", "Gnar", "Shen", "Riven", "Malphite", "Urgot", "Jax", "Camille"],
    "Jungle": ["Vi", "Jarvan IV", "Amumu", "Sejuani", "Nocturne", "Viego", "Lee Sin", "Xin Zhao"],
    "Mid": ["Ahri", "Orianna", "Viktor", "Syndra", "Yasuo", "Malzahar", "Galio", "Taliyah"],
    "ADC": ["Jinx", "Ashe", "Caitlyn", "Ezreal", "Jhin", "Smolder", "Kai'Sa", "Xayah"],
    "Support": ["Lulu", "Nautilus", "Leona", "Nami", "Braum", "Karma", "Rakan", "Rell"],
}


@pytest.fixture
def roster() -> Roster:
    r = Roster(auto_profiles=False)
    lf.apply_loadfile(r, {"default_pools": POOLS}, "pools")
    return r


def _member(cell, team, position, champion=None, intent=None, name=None):
    return {
        "cellId": cell, "team": team, "assignedPosition": position,
        "championId": champ_map.id_for(champion) or 0, "championPickIntent": champ_map.id_for(intent) or 0,
        "puuid": f"p{cell}", "gameName": name or f"Player{cell}", "tagLine": "EUW",
    }


def session(our_locked, enemy_locked, our_bans, enemy_bans, active_cells, hover=None, names=None):
    names = names or {}
    my = [_member(i, 1, pos, our_locked.get(i), hover.get(i) if hover else None, names.get(i))
          for i, pos in enumerate(["top", "jungle", "middle", "bottom", "utility"])]
    their = [_member(5 + i, 2, "", enemy_locked.get(5 + i), None, names.get(5 + i)) for i in range(5)]
    actions = [[{"id": i, "actorCellId": i, "type": "ban", "completed": True, "isInProgress": False,
                 "championId": 1, "isAllyAction": i < 5} for i in range(10)]]
    next_id = 100
    for cell, champ in our_locked.items():
        actions.append([{"id": next_id, "actorCellId": cell, "type": "pick", "completed": True, "isInProgress": False,
                         "championId": champ_map.id_for(champ), "isAllyAction": True}])
        next_id += 1
    for cell, champ in enemy_locked.items():
        actions.append([{"id": next_id, "actorCellId": cell, "type": "pick", "completed": True, "isInProgress": False,
                         "championId": champ_map.id_for(champ), "isAllyAction": False}])
        next_id += 1
    if active_cells:
        actions.append([{"id": next_id + i, "actorCellId": c, "type": "pick", "completed": False, "isInProgress": True,
                         "championId": 0, "isAllyAction": c < 5} for i, c in enumerate(active_cells)])
    remaining = [c for c in range(10) if c not in our_locked and c not in enemy_locked and c not in active_cells]
    for c in remaining:
        next_id += 10
        actions.append([{"id": next_id, "actorCellId": c, "type": "pick", "completed": False, "isInProgress": False,
                         "championId": 0, "isAllyAction": c < 5}])
    return {
        "localPlayerCellId": 2, "myTeam": my, "theirTeam": their,
        "bans": {"myTeamBans": [champ_map.id_for(c) for c in our_bans], "theirTeamBans": [champ_map.id_for(c) for c in enemy_bans], "numBans": 10},
        "actions": actions, "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 24000}, "counter": 7, "gameId": 1, "trades": [],
    }


def test_lcu_session_parses_side_bans_locks_hovers_and_active_cells(roster):
    s = session({0: "Ornn"}, {5: "Ambessa", 6: "Lee Sin"}, ["Rumble", "Yone", "Aurora"], ["Vi", "Jinx", "Orianna"], [1, 2], hover={2: "Ahri"})
    state = ds.state_from_lcu_session(s, roster)
    assert state.side == "blue" and state.source == "lcu"
    assert state.our_bans == ["Rumble", "Yone", "Aurora"] and state.enemy_bans == ["Vi", "Jinx", "Orianna"]
    assert state.our_locked() == {"Top": "Ornn"}
    assert state.active.kind == "pick" and state.active.ours and state.active.cell_ids == [1, 2]
    assert state.pick_number == 4
    mid = next(p for p in state.our_team if p.cell_id == 2)
    assert mid.hover == "Ahri" and mid.locked is None and mid.is_local and mid.pool_source == "default"
    assert {p.locked: p.role for p in state.enemy_team if p.locked} == {"Ambessa": "Top", "Lee Sin": "Jungle"}
    assert {"Vi", "Ornn", "Ambessa"} <= state.unavailable()
    json.dumps(state.to_dict())


def test_red_side_detected_from_team_number(roster):
    s = session({}, {}, [], [], [])
    for member in s["myTeam"]:
        member["team"] = 2
    for member in s["theirTeam"]:
        member["team"] = 1
    assert ds.state_from_lcu_session(s, roster).side == "red"


def test_roster_players_and_scouting_attach_to_client_seats():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {
        "default_pools": POOLS,
        "players": [{"name": "Chud", "riot_ids": ["Player1#EUW"], "roles": ["Jungle"],
                     "pools": {"Jungle": [{"champion": "Lee Sin", "priority": 1}, "Vi"]}, "avoid": ["Amumu"]}],
        "opponents": [{"name": "RivalTop", "riot_ids": ["Player5#EUW"], "roles": ["Top"], "pools": {"Top": ["Rumble"]}}],
    }, "cfg")
    state = ds.state_from_lcu_session(session({}, {}, [], [], [0]), roster)
    jungle = next(p for p in state.our_team if p.cell_id == 1)
    assert jungle.display_name == "Chud" and jungle.player_key == "chud"
    assert jungle.pool["Jungle"] == ["Lee Sin", "Vi"] and jungle.priorities["Jungle"] == {"Lee Sin": 1} and jungle.avoid == ["Amumu"]
    top = next(p for p in state.our_team if p.cell_id == 0)
    assert top.pool_source == "default"
    enemy_top = next(p for p in state.enemy_team if p.cell_id == 5)
    assert enemy_top.scouted and enemy_top.scouted["name"] == "RivalTop" and enemy_top.role == "Top"


def test_profile_accounts_link_user_ids_automatically():
    roster = Roster(auto_profiles=True)
    state = ds.state_from_lcu_session(session({}, {}, [], [], [0], names={1: "FakerChovyHilary"}), roster)
    jungle = next(p for p in state.our_team if p.cell_id == 1)
    assert jungle.user_id == 139837324438536193


def test_lock_made_for_a_teammate_is_resolved_to_its_real_role(roster):
    state = ds.state_from_lcu_session(session({0: "Ahri", 2: "Ornn"}, {5: "Ambessa"}, [], [], [1]), roster)
    effective, swaps = ds.resolve_locked_swaps(state.our_team)
    assert effective[0] == "Ornn" and effective[2] == "Ahri"
    assert {(s["champion"], s["to_role"]) for s in swaps} == {("Ornn", "Top"), ("Ahri", "Mid")}
    flex = ds.state_from_lcu_session(session({0: "Gragas", 1: "Vi"}, {}, [], [], [2]), roster)
    assert ds.resolve_locked_swaps(flex.our_team)[1] == []


def test_manual_board_state_assigns_seats_by_pool_and_infers_enemy_roles():
    roster = Roster(auto_profiles=False)
    lf.apply_loadfile(roster, {
        "default_pools": POOLS,
        "players": [{"name": "Chud", "roles": ["Jungle"], "pools": {"Jungle": ["Lee Sin"]}},
                    {"name": "Atomic", "roles": ["Top"], "pools": {"Top": ["Ornn", "Jax"]}}],
        "lineup": ["Chud", "Atomic"],
    }, "cfg")
    draft = ManualDraft(side="blue")
    for c in ["Rumble", "Vi", "Yone", "Jinx", "Aurora", "Kalista"]:
        draft.act(c)
    state = ds.state_from_manual_draft(draft, roster)
    assert state.phase == "BAN_PICK" and state.active.kind == "pick" and state.active.ours
    assert state.our_bans == ["Rumble", "Yone", "Aurora"] and state.enemy_bans == ["Vi", "Jinx", "Kalista"]
    assert {p.display_name: p.role for p in state.our_team}["Chud"] == "Jungle"
    assert {p.display_name: p.role for p in state.our_team}["Atomic"] == "Top"
    assert len({p.role for p in state.our_team}) == 5

    draft.act("Ornn")                      # no role given: Atomic's seat lists Ornn for Top
    draft.act("Lee Sin")                   # enemy R1: role inferred
    draft.act("Syndra")                    # enemy R2
    state = ds.state_from_manual_draft(draft, roster)
    assert state.our_locked() == {"Top": "Ornn"}
    assert next(p for p in state.our_team if p.locked == "Ornn").display_name == "Atomic"
    assert state.enemy_locked() == {"Jungle": "Lee Sin", "Mid": "Syndra"}
    assert state.active.ours and state.active.kind == "pick"
    assert state.pick_number == 4
    assert [a["kind"] for a in state.actions_remaining][:3] == ["pick", "pick", "pick"]

    draft.set_role(8, "Top")               # coach corrects the enemy Syndra to top
    state = ds.state_from_manual_draft(draft, roster)
    assert state.enemy_locked()["Top"] == "Syndra"


def test_manual_board_red_side_uses_cells_five_to_nine(roster):
    draft = ManualDraft(side="red")
    state = ds.state_from_manual_draft(draft, roster)
    assert state.side == "red" and [p.cell_id for p in state.our_team] == [5, 6, 7, 8, 9]
    assert state.active.kind == "ban" and not state.active.ours


def test_payload_shape_for_bots(roster):
    payload = {
        "side": "red", "phase": "BAN_PICK", "pick_number": 5,
        "our_bans": ["Rumble"], "enemy_bans": ["Jinx"],
        "our_picks": {"Top": "Ornn", "Jungle": "Vi"},
        "enemy_picks": ["Ambessa", "Lee Sin", "Syndra"],
        "players": [{"role": r, "name": r} for r in ds.ROLES],
        "active": {"kind": "pick", "roles": ["Mid", "ADC"]},
    }
    state = ds.state_from_payload(payload, roster)
    assert state.side == "red" and state.our_locked() == {"Top": "Ornn", "Jungle": "Vi"}
    assert state.active.cell_ids == [2, 3]
    assert {p.locked for p in state.enemy_team if p.locked} == {"Ambessa", "Lee Sin", "Syndra"}


def test_profile_players_populate_the_roster_and_draft_from_history():
    roster = Roster(auto_profiles=True)
    names = {p.name for p in roster.players.values()}
    assert "Chud Useless Robber of Progress" in names and len(roster.players) >= 7
    chud = roster.find_player(riot_id="FakerChovyHilary#ALLY")
    assert chud is not None and chud.user_id == 139837324438536193 and chud.roles[0] == "Jungle"
    state = ds.state_from_lcu_session(session({}, {}, [], [], [1], names={1: "FakerChovyHilary"}), roster)
    jungle = next(p for p in state.our_team if p.cell_id == 1)
    assert jungle.player_key == chud.key and jungle.pool_source == "history" and jungle.pool["Jungle"]
    # A later loadfile pool replaces the history pool for that player.
    lf.apply_loadfile(roster, {"players": [{"name": "Chud Useless Robber of Progress", "pools": {"Jungle": ["Vi"]}}]}, "p")
    state = ds.state_from_lcu_session(session({}, {}, [], [], [1], names={1: "FakerChovyHilary"}), roster)
    jungle = next(p for p in state.our_team if p.cell_id == 1)
    assert jungle.pool_source == "player" and jungle.pool["Jungle"] == ["Vi"]


def test_client_champion_summary_duplicates_keep_the_real_id():
    from draftcoach import champions
    before = champions.id_for("Wukong")
    snapshot = dict(champions.all_ids())                      # restore the full map afterwards
    try:
        n = champions.refresh_from_client([{"id": 62, "name": "Wukong"}, {"id": 60062, "name": "Wukong"}, {"id": 60, "name": "Elise"}, {"id": -1, "name": "None"}])
        assert n == 2 and champions.id_for("Wukong") == 62 and champions.name_for(60062) is None or champions.name_for(60062) == "Champion 60062"
        assert champions.id_for("Elise") == 60
    finally:
        champions.refresh_from_client([{"id": i, "name": n} for n, i in snapshot.items()])


def test_side_falls_back_to_first_pick_when_team_numbers_missing(roster):
    s = session({}, {}, [], [], [])
    for member in s["myTeam"] + s["theirTeam"]:
        member.pop("team", None)
    # The helper's first future pick belongs to our cell 0, therefore we are blue.
    assert ds.state_from_lcu_session(s, roster).side == "blue"
    # Move the first pick action to enemy cell 5: first-pick side is now theirs, so we are red.
    first_pick = next(action for group in s["actions"] for action in group if action.get("type") == "pick")
    first_pick["actorCellId"] = 5
    first_pick["isAllyAction"] = False
    assert ds.state_from_lcu_session(s, roster).side == "red"
