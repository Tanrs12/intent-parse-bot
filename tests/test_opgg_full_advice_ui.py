"""Full OP.GG web observations appear as statistics, never legal draft swaps."""
from pathlib import Path

import counterpick_advice
from counterpick_advice import counter_advice
from league_comp_engine import score_comp

ROOT = Path(__file__).resolve().parents[1]


def test_full_page_evidence_is_pool_aware_and_does_not_change_ranked_picks(monkeypatch):
    champions = {"Top": "Malphite", "Jungle": "Lee Sin", "Mid": "Sylas",
                 "ADC": "Yunara", "Support": "Lulu"}
    lead = {**score_comp(champions), "champions": champions,
            "submitted_pools": {"Top": ["Malphite", "Camille", "Gwen"]},
            "players": {"Top": {"display_name": "Cian"}}}

    def mock_stats(role, enemy, *, patch):
        if role != "Top":
            return {"rows": []}
        assert enemy == "Cho'Gath" and patch.startswith("26.")
        return {
            "rows": [
                {"ours": name, "games": games, "head_to_head_win_rate": wr,
                 "source_patch": "16.21", "rank_filter": "Emerald+",
                 "retrieved_at": "2026-11-03", "source_url": "https://op.gg"}
                for name, wr, games in [
                    ("Malphite", 45.68, 2544), ("Camille", 52.62, 1108),
                    ("Gwen", 52.78, 1078), ("Vayne", 55.0, 450)
                ]
            ],
            "source_patch": "16.21", "rank_filter": "Emerald+",
        }
    monkeypatch.setattr(counterpick_advice, "full_for_enemy", mock_stats)
    with_stats = counter_advice(lead, {"Top": "Cho'Gath"})["lanes"][0]
    parsed = with_stats["opgg_full_reference"]
    assert parsed["planned"]["head_to_head_win_rate"] == 45.68
    assert parsed["patch"] == "16.21"
    assert [r["champion"] for r in parsed["alternatives"][:2]] == ["Gwen", "Camille"]
    assert [r["type"] for r in parsed["alternatives"][:3]] == ["playable", "playable", "learn"]

    monkeypatch.setattr(counterpick_advice, "full_for_enemy",
                        lambda *args, **kwargs: {"rows": []})
    no_stats = counter_advice(lead, {"Top": "Cho'Gath"})["lanes"][0]
    assert with_stats["playable"] == no_stats["playable"]
    assert with_stats["learn"] == no_stats["learn"]
    assert lead["champions"] == champions


def test_selected_comp_labels_current_patch_and_does_not_score_from_ui():
    js = (ROOT / "draftcoach" / "static" / "selected-comp.js").read_text(encoding="utf8")
    assert "opgg_full_reference" in js
    assert '"OP.GG " + full.patch + " " + full.rank' in js
    assert "OP.GG STATS" in js
    assert "player comfort and team composition take priority" in js
    assert "patch/rank unknown" in js
