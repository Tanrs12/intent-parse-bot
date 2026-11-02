"""Kayla is now Top main, not a Mid substitute, in Discord and Draft Coach."""
import json
from pathlib import Path

from league_flex_core import (
    ROLES, _role_priority_level, generate_team_options,
    normalise_team_role_update,
)

KAYLA = 548212317515677697
CIAN = 139837324438536193
RAT = 270517092703272960


def test_stale_discord_signup_normalises_to_top_main_without_mid():
    raw = {
        "user_id": KAYLA, "display_name": "Kayla",
        "roles": ["Jungle", "Top", "Mid"],
        "role_priority": {"Jungle": 1, "Top": 3, "Mid": 2},
        "champions": {"Top": ["Garen", "Nasus"], "Jungle": ["Amumu"], "Mid": ["Lux"]},
    }
    before = json.loads(json.dumps(raw))
    fixed = normalise_team_role_update(raw)
    assert fixed["roles"] == ["Top", "Jungle"]
    assert fixed["role_priority"] == {"Top": 1, "Jungle": 2}
    assert "Mid" not in fixed["champions"]
    assert _role_priority_level(fixed, "Mid") is None
    assert _role_priority_level(fixed, "Top") == 1
    assert raw == before  # Normalising never mutates stored source objects.


def test_kayla_top_always_first_when_available_in_actual_5():
    path = Path(__file__).resolve().parents[1] / "loadfiles" / "flexers-team.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    by_name = {p.get("rename"): p for p in data["players"]}
    record = by_name["Kayla"]
    assert "Mid" not in record["roles"]
    assert record["roles"][0] == "Top"
    assert len(record["pools"]["Top"]) >= 5

    p = [
        {"user_id": CIAN, "display_name": "Cian", "roles": ["Jungle", "Top"],
         "role_priority": {"Jungle": 1, "Top": 2},
         "champions": {"Jungle": ["Lee Sin"], "Top": ["Camille"]}},
        {"user_id": RAT, "display_name": "Rat", "roles": ["Mid", "Jungle"],
         "role_priority": {"Mid": 1, "Jungle": 3},
         "champions": {"Mid": ["Galio"], "Jungle": ["Vi"]}},
        {"user_id": KAYLA, "display_name": "Kayla", "roles": ["Jungle", "Top", "Mid"],
         "role_priority": {"Jungle": 1, "Top": 3, "Mid": 2},
         "champions": {"Top": ["Garen", "Nasus"], "Jungle": ["Amumu"], "Mid": ["Lux"]}},
        {"user_id": 100004, "display_name": "Jack", "roles": ["ADC"],
         "role_priority": {"ADC": 1}, "champions": {"ADC": ["Kai'Sa"]}},
        {"user_id": 100005, "display_name": "Jezz", "roles": ["Support"],
         "role_priority": {"Support": 1}, "champions": {"Support": ["Lulu"]}},
    ]
    options = generate_team_options(p, limit=20, require_pools=True)
    assert options
    assert all(row["Mid"]["user_id"] != KAYLA for row in options)
    assert options[0]["Top"]["user_id"] == KAYLA
    assert options[0]["Jungle"]["user_id"] == CIAN
    assert options[0]["Mid"]["user_id"] == RAT


def test_other_player_data_unchanged():
    p = {"user_id": 60000, "roles": ["Mid"], "champions": {"Mid": ["Vex"]}}
    assert normalise_team_role_update(p) is p


def test_live_bridge_displays_updated_roles_from_stale_remote_cache(monkeypatch, tmp_path):
    import importlib
    import sys
    tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
    monkeypatch.syspath_prepend(tools_dir)
    bridge = importlib.import_module("intent_live_draft_bridge")
    cache = tmp_path / "live_flex_signups.json"
    cache.write_text(json.dumps([{
        "user_id": KAYLA, "display_name": "JustKayla",
        "roles": ["Jungle", "Mid", "Top"],
        "role_priority": {"Jungle": 1, "Mid": 2, "Top": 3},
        "champions": {"Top": ["Darius"], "Jungle": ["Amumu"], "Mid": ["Lux"]},
    }]), encoding="utf-8")
    monkeypatch.setattr(bridge, "SIGNUP_CACHE", cache)
    monkeypatch.setattr(bridge, "_signups", [])
    bridge._load_cached_signups()
    signup = bridge._signups[0]
    assert signup["roles"] == ["Top", "Jungle"]
    assert signup["role_priority"] == {"Top": 1, "Jungle": 2}
    assert "Mid" not in signup["champions"]
