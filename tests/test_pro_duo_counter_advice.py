"""Pro duo rows are explanatory only, never legal picks or counter scores."""
from pathlib import Path

import counterpick_advice
from counterpick_advice import counter_advice

ROOT = Path(__file__).resolve().parents[1]


def test_duo_evidence_shown_without_enemy_and_never_changes_lead(monkeypatch):
    calls = []
    def fake_duo(package, first, second, *, patch):
        calls.append((package, first, second, patch))
        return {
            "package": package, "first": first, "second": second,
            "games": 35, "wins": 20, "win_rate": 57.14,
            "patch": "2026 season aggregate", "exact_patch": False,
            "draft_points": 0,
        }
    monkeypatch.setattr(counterpick_advice, "pro_duo", fake_duo)
    lead = {"champions": {
        "Top": "Gnar", "Jungle": "Lee Sin", "Mid": "Sylas",
        "ADC": "Yunara", "Support": "Lulu",
    }}
    original = {**lead["champions"]}
    row = counter_advice(lead, None)
    assert row["status"] == "waiting_for_confirmed_enemy_lane"
    assert not row["lanes"]
    assert len(row["pro_duos"]) == 2
    assert row["pro_duos"]["Mid+Jungle"]["draft_points"] == 0
    assert row["pro_duos"]["ADC+Support"]["games"] == 35
    assert lead["champions"] == original
    assert calls[0][:3] == ("Mid+Jungle", "Sylas", "Lee Sin")
    assert calls[1][:3] == ("ADC+Support", "Yunara", "Lulu")
    row2 = counter_advice(lead, {"Top": "Shen"}, matchup_data={"matchups": {}})
    assert not row2["pro_duos"]
    assert len(calls) == 2  # Injected fixtures never query private caches.


def test_selected_comp_displays_attributed_pro_context_separately_from_locks():
    js = (ROOT / "draftcoach" / "static" / "selected-comp.js").read_text(encoding="utf-8")
    assert 'const proDuos = guidance?.counter_advice?.pro_duos || {};' in js
    assert '"PRO REFERENCE · "' in js
    assert '" · observational only"' in js
    assert "reference.exact_patch" in js
    assert 'const key = JSON.stringify([chosen' in js



def test_bridge_can_show_pro_duos_before_enemy_lanes_are_known():
    bridge = (ROOT / "tools" / "intent_live_draft_bridge.py").read_text(encoding="utf-8")
    assert 'if control.get("chosen") and lead.get("champions")' in bridge
    assert 'if control.get("chosen") and known_enemy_lanes and lead.get("champions")' not in bridge
