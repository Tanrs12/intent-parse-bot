"""The desktop app plans a game with the same /league game pipeline as the Discord bot."""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import pytest

from league_draft_backend import plan_game

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
SIGNUPS = Path(__file__).resolve().parent.parent / "data" / "live_flex_signups.json"


def _signup_cache() -> list[dict]:
    try:
        rows = json.loads(SIGNUPS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    if len(rows) < 5:
        pytest.skip("live_flex_signups.json does not hold five signed-up players")
    return rows


@pytest.fixture(scope="module")
def planned() -> tuple[list[dict], dict]:
    """The first five-player subset of the signup cache that yields a draft board."""
    rows = _signup_cache()
    for subset in combinations(rows, 5):
        result = plan_game(list(subset), rotation_token="game-plan-test")
        if result["recommendations"]:
            return list(subset), result
    pytest.skip("no five-player subset of the signup cache can build a draft board")


def _every_shown_champion(rec: dict) -> set[str]:
    shown = set(rec["champions"].values())
    for options in rec.get("role_alternatives", {}).values():
        shown.update(options)
    for options in rec.get("learn_suggestions", {}).values():
        shown.update(options)
    for options in rec["control"]["control"]["chosen"]["options"].values():
        shown.update(options)
    return shown


def _every_ban(rec: dict) -> set[str]:
    bans: set[str] = set()
    for side in ("blue", "red"):
        bans.update(row["champion"] for row in rec["sides"][side]["bans"])
        for branch in rec["sides"][side]["conditional_bans"]:
            bans.update(branch["bans"])
    return bans


def test_plan_game_builds_up_to_ten_complete_drafts(planned):
    five, result = planned
    drafts = result["recommendations"]
    assert 0 < len(drafts) <= 10
    assert result["layouts"] >= 1
    assert isinstance(result["missing"], list)
    assert set(result["coverage"]) == {"missing_pools", "single_role"}
    assert result["side_notes"] is None
    for index, rec in enumerate(drafts, start=1):
        assert rec["index"] == index
        assert rec["label"] == f"Draft {index}"
        assert set(rec["champions"]) == set(ROLES)
        assert all(rec["champions"][role] for role in ROLES)
        assert len(set(rec["champions"].values())) == 5
        assert rec["identity"]
        for side in ("blue", "red"):
            board = rec["sides"][side]
            order = board["pick_order"]
            assert [row["slot"] for row in order] == [1, 2, 3, 4, 5]
            assert {row["role"] for row in order} == set(ROLES)
            assert {row["champion"] for row in order} == set(rec["champions"].values())
            assert all(row["pick_group_label"] for row in order)
            assert isinstance(board["bans"], list) and len(board["bans"]) <= 5
            assert all(row["champion"] and row["score"] > 0 for row in board["bans"])
            assert isinstance(board["conditional_bans"], list)


def test_plan_game_control_payload_follows_the_draft(planned):
    _, result = planned
    for rec in result["recommendations"]:
        control = rec["control"]
        chosen = control["control"]["chosen"]
        assert chosen["champions"] == rec["champions"]
        assert chosen["label"] == rec["label"]
        assert chosen["source"] == "app"
        assert chosen["mode"] == "adaptive"
        assert control["signature"] == "|".join(rec["champions"][role] for role in ROLES)
        assert set(control["control"]["role_locks"]) == set(ROLES)
        assert all(chosen["options"][role][0] == rec["champions"][role] for role in ROLES)


def test_plan_game_result_is_json_serialisable(planned):
    _, result = planned
    text = json.dumps(result)
    assert json.loads(text)["recommendations"][0]["champions"] == result["recommendations"][0]["champions"]


def test_plan_game_never_shows_unavailable_champions(planned):
    five, result = planned
    first = result["recommendations"][0]
    unavailable = {first["champions"]["Top"], first["champions"]["Jungle"]}
    unavailable.update(row["champion"] for row in first["sides"]["blue"]["bans"][:2])
    unavailable.update(row["champion"] for row in first["sides"]["red"]["bans"][:1])

    result = plan_game(five, unavailable=unavailable, rotation_token="game-plan-test")

    for rec in result["recommendations"]:
        assert not (_every_shown_champion(rec) & unavailable)
        assert not (_every_ban(rec) & unavailable)
    json.dumps(result)


def test_plan_game_requires_exactly_five_players(planned):
    five, _ = planned
    with pytest.raises(ValueError):
        plan_game(five[:4])
    with pytest.raises(ValueError):
        plan_game([*five, dict(five[0], user_id=1, display_name="Sixth")])


def test_plan_game_rotation_token_still_yields_valid_drafts(planned):
    five, _ = planned
    result = plan_game(five, rotation_token="another-rotation")
    drafts = result["recommendations"]
    assert 0 < len(drafts) <= 10
    for rec in drafts:
        assert len(set(rec["champions"].values())) == 5
        assert rec["control"]["control"]["chosen"]["champions"] == rec["champions"]
        for side in ("blue", "red"):
            assert len(rec["sides"][side]["pick_order"]) == 5
    assert plan_game(five, rotation_token="another-rotation") == result
