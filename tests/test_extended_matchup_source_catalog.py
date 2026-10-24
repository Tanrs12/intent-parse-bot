"""Source catalog: citations, chapter indexing and zero numerical hallucination."""
from __future__ import annotations

from league_matchup_catalog import (
    lookup_matchup_references, source_catalog_summary, load_catalog,
)
from league_community_evidence import matchup_notes
from league_comp_engine import live_lane_matchup_adjustment


def test_broad_source_index_and_gwen_chapters_are_explicit():
    stats = source_catalog_summary()
    assert stats["statistical_provider_references"] >= 7
    assert stats["champion_guide_links"] >= 18
    assert stats["timestamped_matchup_chapters"] >= 45
    data = load_catalog()
    assert all(row["reuse"].startswith("reference") or row["reuse"].startswith("API")
               for row in data["statistical_sources"])


def test_gwen_into_shen_video_chapter_is_navigable_not_a_fake_tier():
    refs = lookup_matchup_references("Top", "Gwen", "Shen")
    assert any("youtube.com/watch?v=VXjsGYa3Z-g&t=4313s" in row["source_url"] for row in refs)
    assert all(row["draft_points"] == 0 for row in refs)
    assert all("not verified" in row["mechanic"] or "inspect" in row["mechanic"].lower() for row in refs)


def test_camille_gwen_and_sett_darius_have_high_elo_original_paraphrases():
    cw = matchup_notes("Top", "Camille", "Gwen")
    assert any("centre" in row["mechanic"] for row in cw)
    assert any("lolmatchups.gg" in row["source_url"] for row in cw)
    sd = matchup_notes("Top", "Sett", "Darius")
    assert any("level-two" in row["mechanic"] for row in sd)
    assert all(row.get("draft_points", 0) == 0 for row in sd)


def test_references_do_not_turn_into_scoring_edges():
    # Explicit custom-matchup fixtures stay self-contained, as in real unit
    # tests. Actual live history can offer references but not bonus points.
    result = live_lane_matchup_adjustment(
        {"Top": "Gwen"}, {"Top": "Shen"},
        matchup_data={"source": {"patches_detected": ["26.21"]}, "matchups": {}},
    )
    assert result == {"adjustment": 0.0, "edges": []}


def test_unverified_champion_has_no_invented_videos():
    refs = lookup_matchup_references("Jungle", "Gragas", "Lee Sin")
    assert not any(row["source_type"].startswith("Timestamped") for row in refs)
    # The kit interaction source is separate and still available.
    notes = matchup_notes("Jungle", "Gragas", "Lee Sin")
    assert any("interrupt" in row["mechanic"] for row in notes)
