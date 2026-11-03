"""Counts-only cross-source evidence ledger; no provider rows leaked."""
from __future__ import annotations

import json
from pathlib import Path

from tools.champion_evidence_ledger import build, norm
from league_matchup_universe import ROLES, role_universe


def test_ledger_lists_every_supported_champion_and_all_five_role_denominators(tmp_path):
    result = build(opgg_dir=tmp_path, pocket_dir=tmp_path, pro_dir=tmp_path)
    names = set().union(*(set(role_universe(r)) for r in ROLES))
    assert len(names)==172
    assert set(result["champions"])==names
    assert result["champions_in_role_catalogue"]==172
    assert result["totals"]["possible_directed_pairs"]==12128
    assert not any(result["source_status"].values())
    assert result["totals"]["current_26_21_100game_pairs"]==0
    assert result["totals"]["historical_16_16_observed_pairs"]==0
    assert "Corki" in result["unobserved_champions"]


def test_canonical_aliases_do_not_hide_nunu_or_renata():
    from league_pocketpick_history import _norm
    from tools.collect_opgg_full_web import slug,source_url
    from league_opgg_full_reference import _page_slug
    assert norm("Nunu") == norm("Nunu & Willump")
    assert _norm("Nunu") == _norm("Nunu & Willump")
    assert norm("Renata") == norm("Renata Glasc")
    assert _norm("Renata") == _norm("Renata Glasc")
    assert slug("Nunu & Willump")=="nunu"
    assert _page_slug("Nunu & Willump")=="nunu"
    assert slug("Renata Glasc")=="renata"
    assert source_url("Renata Glasc","Support").endswith("/renata/counters/support")


def test_shared_ledger_is_metadata_only_not_privately_redistributed():
    doc = (Path(__file__).resolve().parents[1] /
           "docs/draftcoach/CHAMPION_EVIDENCE_LEDGER_2026-11-03.json")
    data=json.loads(doc.read_text(encoding="utf8"))
    assert data["champions_in_role_catalogue"]==172
    assert "unobserved_champions" in data
    assert set(data["source_status"])=={
        "opgg_current_16_21",
        "pocketpick_historical_16_16",
        "ordered_pro_historical_2026"
    }
    for champion,row in data["champions"].items():
        assert "win_rate" not in row
        assert "games" not in row
        assert "source_url" not in row
        assert "role" not in row or row.get("roles")
        assert len(row["roles"])>=1
    assert data["totals"]["any_observed_current_or_old_pairs"] <= 12128
    assert len(data["unobserved_champions"]) <= 172
