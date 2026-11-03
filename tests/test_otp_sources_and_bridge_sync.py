"""Research provenance, full-role scouting and live-bridge player ownership."""
from __future__ import annotations

import json
from pathlib import Path

from counterpick_advice import counter_advice
from league_comp_engine import score_comp
from league_matchup_universe import scout_note
from tools.intent_live_draft_bridge import _scouting_lead_with_pools

ROOT = Path(__file__).resolve().parents[1]
UID = 139837324438536193


def sample_lead():
    ch = {"Top": "Camille", "Jungle": "Lee Sin", "Mid": "Ahri",
          "ADC": "Jinx", "Support": "Lulu"}
    row = score_comp(ch)
    return {**row, "champions": ch,
            "players": {"Top": "Cian"},
            "player_ids": {"Top": str(UID)}}


def test_bridge_uses_actual_discord_owner_pools_not_ui_option_labels():
    lead = sample_lead()
    signup = [{"user_id": UID, "display_name": "Cian",
               "champions": {"Top": ["Camille", "Gnar", "Gwen", "Kayle"]}}]
    enriched = _scouting_lead_with_pools(lead, signup)
    assert enriched["submitted_pools"]["Top"] == ["Camille", "Gnar", "Gwen", "Kayle"]
    assert enriched["players"]["Top"]["display_name"] == "Cian"
    assert enriched["players"]["Top"]["user_id"] == UID
    assert "submitted_pools" not in lead  # Never rewrite the live scoring comp.
    advice = counter_advice(enriched, {"Top": "Shen"})["lanes"][0]
    assert advice["player"] == "Cian"
    scouts = {x["champion"]: x for x in advice["scout"]}
    assert scouts["Gnar"]["type"] == "playable"
    assert scouts["Gwen"]["type"] == "playable"
    assert scouts["Gnar"]["qualitative_only"]
    assert not advice["playable"]  # Opinion does not manufacture a stat-supported counter.


def test_otp_catalog_has_sources_and_restricts_uncertified_statistics():
    info = json.loads((ROOT / "data/league_otp_matchup_notes.json").read_text(encoding="utf8"))
    assert info["schema_version"] == 1
    assert len(info["sources"]) >= 36
    assert len(info["entries"]) >= 101
    assert len({(x["role"], x["our"], x["enemy"]) for x in info["entries"]}) == len(info["entries"])
    assert all(len(row["sources"]) >= 1 for row in info["entries"])
    assert all(s["url"].startswith("https://") for row in info["entries"] for s in row["sources"])
    assert all(row["verdict"] in ("favourable", "mixed", "unfavourable") for row in info["entries"])
    assert all("win_rate" not in row and "draft_points" not in row for row in info["entries"])


def test_kayle_gnar_otp_notes_live_beside_original_curated_shen_notes():
    kayle = scout_note("Top", "Kayle", "Jax")
    assert kayle["verdict"] == "unfavourable"
    assert "rank unverified" in kayle["source_quality"]
    gnar = scout_note("Top", "Gnar", "Irelia")
    assert gnar["verdict"] == "unfavourable"
    assert scout_note("Top", "Gwen", "Shen")["patch"] == "26.21"


def test_video_chapters_are_navigation_only_and_not_fake_evidence():
    video = json.loads((ROOT / "data/league_otp_video_chapters.json").read_text(encoding="utf8"))
    assert video["schema_version"] == 1
    assert len(video["chapters"]) == 47
    assert all(row["verdict"] is None and row["review_status"] == "index_only"
               for row in video["chapters"])
    shen = next(row for row in video["chapters"] if row["enemy"] == "Shen")
    assert shen["timestamp"] == "1:11:53"
    assert "t=4313s" in shen["url"]


def test_qualitative_matchup_warning_does_not_override_legal_picks():
    lead = sample_lead()
    lead = _scouting_lead_with_pools(lead, [
        {"user_id": UID, "display_name": "Cian",
         "champions": {"Top": ["Camille", "Gnar", "Gwen"]}}])
    row = counter_advice(lead, {"Top": "Shen"})["lanes"][0]
    assert row["planned_risk"]["summary"]
    assert not row["playable"]  # no legal numerical picks inferred
    assert row["scout"][0]["type"] == "playable"
    assert row["scout"][0]["source_weight"] >= 1


def test_big_window_only_our_five_slots_and_shared_selection_ack():
    html = (ROOT / "draftcoach/static/coach.html").read_text(encoding="utf8")
    assert "const ourSlots = fullSequence.filter" in html
    assert "ownSteps.slice(0, 5)" in html
    assert '"Enemy pick"' not in html
    assert "accepted in the shared room" in html


def test_mid_otp_notes_separate_lane_risks_from_autopick_scores():
    notes = json.loads((ROOT / "data/league_otp_matchup_notes.json").read_text(encoding="utf8"))
    mid = [x for x in notes["entries"] if x["role"] == "Mid"]
    assert len(mid) >= 10
    assert scout_note("Mid", "Akali", "Vex")["verdict"] == "unfavourable"
    assert scout_note("Mid", "Akali", "Lissandra")["verdict"] == "mixed"
    assert scout_note("Mid", "Vex", "Qiyana")["verdict"] == "favourable"
    assert scout_note("Mid", "Qiyana", "Vex")["verdict"] == "unfavourable"
    assert all("win_rate" not in row and "draft_points" not in row for row in mid)
    assert all(row["sources"] and row["source_quality"] for row in mid)
    lead = sample_lead()
    lead["champions"]["Mid"] = "Akali"
    advice = counter_advice(lead, {"Mid": "Vex"})
    lane = next(x for x in advice["lanes"] if x["role"] == "Mid")
    assert lane["planned"] == "Akali"
    assert "fear" in lane["planned_risk"]["summary"].lower()
    assert "https://" in lane["planned_risk"]["sources"][0]["url"]



def test_additional_scouting_covers_multiple_roles_without_fake_statistics():
    catalog = json.loads((ROOT / "data/league_otp_matchup_notes.json").read_text(encoding="utf8"))
    assert len(catalog["entries"]) >= 101
    assert len(catalog["sources"]) >= 36
    assert scout_note("Jungle", "Kindred", "Lee Sin")["verdict"] == "unfavourable"
    assert scout_note("Jungle", "Kindred", "Kayn")["verdict"] == "mixed"
    assert scout_note("Jungle", "Taliyah", "Master Yi")["verdict"] == "mixed"
    assert scout_note("ADC", "Kog'Maw", "Ezreal")["verdict"] == "mixed"
    assert scout_note("ADC", "Kog'Maw", "Caitlyn")["verdict"] == "unfavourable"
    assert scout_note("Mid", "Yasuo", "Vex")["verdict"] == "unfavourable"
    assert scout_note("Mid", "Qiyana", "Twisted Fate")["verdict"] == "mixed"
    assert all("win_rate" not in row and "draft_points" not in row for row in catalog["entries"])
    for row in catalog["entries"]:
        assert row["sources"] and all(x["url"].startswith("https://") for x in row["sources"])
    from collections import Counter
    counts = Counter(row["role"] for row in catalog["entries"])
    assert all(counts[role] > 0 for role in ("Top","Jungle","Mid","ADC"))
