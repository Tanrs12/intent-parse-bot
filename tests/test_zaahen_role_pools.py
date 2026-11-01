"""Regression coverage for Zaahen's selectable, player-submitted roles."""
from __future__ import annotations

import json
from pathlib import Path

from draftcoach.champions import id_for, name_for
from league_flex_core import _CURATED_ROLE_CHAMPIONS, _build_role_champions


def test_zaahen_is_in_top_and_jungle_signup_menus():
    roles = _build_role_champions()
    for role in ("Top", "Jungle"):
        assert "Zaahen" in _CURATED_ROLE_CHAMPIONS[role]
        assert "Zaahen" in roles[role]
    assert "Zaahen" not in _CURATED_ROLE_CHAMPIONS["ADC"]


def test_zaahen_bundled_roles_and_champion_id():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "data" / "league_role_champions.json").read_text(encoding="utf-8"))
    for role in ("Top", "Jungle"):
        assert "Zaahen" in data["roles"][role]
    assert id_for("Zaahen") == 904
    assert name_for(904) == "Zaahen"
