"""Selected-pool trust and cross-game candidate rotation, without lowering comp quality."""
from __future__ import annotations

import league_comp_engine as eng
import league_draft_backend as backend

CIAN = 139837324438536193
STE = 286592835850076162


def test_selected_champions_are_playable_for_cian_across_roles():
    meta = eng.load_meta()
    roles = {
        "Jungle": ("Qiyana", "Lee Sin", "Nocturne", "Elise", "Lillia",
                   "Taliyah", "Xin Zhao", "Wukong", "Kindred"),
        "ADC": ("Lucian", "Kai'Sa", "Jinx", "Kog'Maw", "Kalista"),
        "Mid": ("Akali", "Sylas", "Orianna"),
    }
    for role, champions in roles.items():
        player = {"user_id": CIAN, "role_priority": {role: 2}}
        for champion in champions:
            assert eng._verified_secondary_role_pick(player, role, champion, meta)


def test_ste_mid_selected_pool_is_not_limited_to_recent_four():
    meta = eng.load_meta()
    player = {"user_id": STE, "role_priority": {"Mid": 2}}
    for champion in ("Akali", "Vex", "Qiyana", "Orianna", "Galio"):
        assert eng._verified_secondary_role_pick(player, "Mid", champion, meta)
    assert eng._role_search_pool_limit(player, "Mid", 10) >= 16


def test_wide_selected_pool_rotates_without_losing_core_candidates():
    meta = eng.load_meta()
    pool = [f"Candidate-{i:02d}" for i in range(28)]
    args = {"role": "Jungle", "champions": pool, "meta": meta, "limit": 10,
            "user_id": CIAN}
    first = eng._prune_pool(**args, rotation_token="game-abc")
    again = eng._prune_pool(**args, rotation_token="game-abc")
    next_game = eng._prune_pool(**args, rotation_token="game-xyz")
    assert first == again
    assert len(first) == len(set(first)) == 10
    assert len(next_game) == len(set(next_game)) == 10
    assert set(first[:6]) == set(next_game[:6])
    assert set(first[6:]) != set(next_game[6:])
    assert set(first) | set(next_game) <= set(pool)


def test_other_players_offrole_evidence_gate_remains(monkeypatch):
    # The high-competence declaration is specific; it must not automatically
    # make Jezz or an unfamiliar off-role player proficient on Vayne.
    meta = eng.load_meta()
    uid = 999444
    monkeypatch.setattr(eng, "load_recent_match_index", lambda: {
        uid: {"by_role_champion": {"Top": {}}}
    })
    monkeypatch.setattr(eng, "_mastery_points_for", lambda *_: 0)
    player = {"user_id": uid, "role_priority": {"Top": 3}}
    assert not eng._verified_secondary_role_pick(player, "Top", "Vayne", meta)


def test_live_backend_passes_preferred_opening_plan_into_engine(monkeypatch):
    preferred = {
        "Top": "Ornn", "Jungle": "Lee Sin", "Mid": "Orianna",
        "ADC": "Lucian", "Support": "Milio",
    }
    got = {}
    fake = {"Top": {"user_id": 1}, "Jungle": {"user_id": 2},
            "Mid": {"user_id": 3}, "ADC": {"user_id": 4},
            "Support": {"user_id": 5}}
    monkeypatch.setattr(backend, "generate_team_options", lambda *_args, **_kwargs: [fake])
    monkeypatch.setattr(backend, "_constrain_assignments", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(backend, "_filter_unavailable", lambda rows, blocked: rows)
    def recommend(_assignments, **kwargs):
        got.update(kwargs)
        return [], []
    monkeypatch.setattr(backend, "recommend_comps", recommend)
    output = backend.recommend_live_drafts(
        [{"user_id": i, "roles": ["Top"]} for i in range(5)],
        preferred_champions=preferred, rotation_token="match-987",
    )
    assert output["recommendations"] == []
    assert got["preferred_champions"] == preferred
    assert got["rotation_token"] == "match-987"
