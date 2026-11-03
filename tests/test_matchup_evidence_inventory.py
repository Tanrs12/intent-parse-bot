"""Distinct historical data-source coverage, never synthetic win rates."""
from league_matchup_universe import ROLES
from tools.matchup_evidence_inventory import inventory, norm


def test_coverage_deduplicates_overlaps_but_keeps_unequal_confidence():
    available = {r: set() for r in ROLES}
    available["Top"] = {"Gnar", "Shen", "Jax", "Wukong"}
    bundled = {"matchups": {"Top": {
        "Shen": {"responses": {"Gnar": {"games": 150}, "Jax": {"games": 99}}},
        "Jax": {"responses": {"Gnar": {"games": 120}}},
    }}}
    pocket = {"matches": 30000, "patches": {"16.16": 30000},
              "queue": 420, "region": "euw", "matchups": {
        "Gnar|TOP|Shen": {"n": 60},
        "Shen|TOP|Gnar": {"n": 120},
        "Jax|TOP|Shen": {"n": 40},
        "MonkeyKing|TOP|Jax": {"n": 15},
        "Jax|TOP|Jax": {"n": 1000},  # mirrors excluded
        "Kayn|JUNGLE|Elise": {"n": 60},  # no valid catalogue
    }}
    row = inventory(bundled, pocket, available=available)
    r = row["roles"]["Top"]
    assert r["eligible"] == 12
    assert r["bundled_100_plus"] == 2
    assert r["pocket_observed_1_plus"] == 4
    assert r["pocket_40_plus"] == 3
    assert r["pocket_100_plus"] == 1
    assert r["union_40_plus_or_bundled"] == 4
    assert r["union_100_plus"] == 3
    assert r["historical_additions_40_plus"] == 2
    assert norm("MonkeyKing") == norm("Wukong")
    assert row["totals"]["union_40_plus_or_bundled"] == 4
    assert row["pocket_provenance_valid"]


def test_invalid_pocket_provenance_never_joins_private_data():
    a = {r: set() for r in ROLES}
    a["Top"] = {"Gnar", "Shen"}
    source = {"matchups": {"Top": {"Shen": {"responses": {"Gnar": {"games": 112}}}}}}
    pocket = {"matches": 30000, "patches": {"16.19": 30000},
              "queue": 420, "region": "euw",
              "matchups": {"Shen|TOP|Gnar": {"n": 999}}}
    row = inventory(source, pocket, available=a)
    assert row["pocket_provenance_valid"] is False
    assert row["totals"]["union_40_plus_or_bundled"] == 1
    assert row["totals"]["pocket_observed_1_plus"] == 0
