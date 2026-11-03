"""Ordered pro draft chronology is not same-lane causal counter evidence."""
import csv,json
from pathlib import Path
from tools.build_ordered_pro_draft_2026 import extract
import league_pro_order_context as order

HEADER=("user_id:token item_id:token label:float patch:float team_result:float "
        "match_id:token hero_name:token timestamp:float action_type:token "
        "hero_action:token start_time:float draft_format:token first_team:float "
        "series_id:token game_number:float tournament:token tier:token "
        "season_phase:token").split()


def _rows(game, tier):
    picks={6:(0,"Ornn"),7:(1,"Lee Sin"),8:(1,"Jinx"),
           9:(0,"Skarner"),10:(0,"Ahri"),11:(1,"Syndra"),
           16:(1,"Nautilus"),17:(0,"Ziggs"),18:(0,"Braum"),
           19:(1,"Gnar")}
    banned={i:(i%2,"Ban"+str(i)) for i in list(range(6))+list(range(12,16))}
    for slot in range(20):
        team,champ=(picks if slot in picks else banned)[slot]
        picked=int(slot in picks)
        yield {
            "user_id:token":f"{game}_{team}","item_id:token":1,
            "label:float":picked,"patch:float":2616,
            "team_result:float":1-team,
            "match_id:token":game,"hero_name:token":champ,
            "timestamp:float":slot,
            "action_type:token":"pick" if picked else "ban",
            "hero_action:token":"drafted","start_time:float":1780000000,
            "draft_format:token":"td20_10.1","first_team:float":1,
            "series_id:token":"series","game_number:float":1,
            "tournament:token":"Test","tier:token":tier,
            "season_phase:token":"2026_2_MSI-to-Worlds"}


def test_only_later_picks_are_observed_responses(tmp_path):
    file=tmp_path/"one.inter"
    with file.open("w",encoding="utf8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=HEADER,delimiter="\t")
        writer.writeheader()
        writer.writerows(_rows("g1","primary"))
        writer.writerows(_rows("g2","secondary"))
    latest=extract(file,min_patch=2613)
    assert latest["games"]==1
    assert latest["responses"]["skarner|leesin"]["later_picks"]==1
    assert latest["responses"]["skarner|leesin"]["wins"]==1
    assert "leesin|skarner" not in latest["responses"]
    assert "ornn|leesin" not in latest["responses"]
    both=extract(file,min_patch=2613,primary_only=False)
    assert both["games"]==2


def test_reader_rejects_unlicensed_or_tiny_sample(tmp_path,monkeypatch):
    folder=tmp_path/"pro";folder.mkdir()
    path=folder/"pick-order-index.json"
    archive={
        "schema_version":1,"provider":"LoL-Draft / Leaguepedia",
        "licence":"CC BY-SA 4.0","primary_only":True,
        "patch_min":2613,"games":787,
        "responses":{"skarner|leesin":{"later_picks":9,"wins":5,
                                        "patch_counts":{"2616":9}}},
    }
    path.write_text(json.dumps(archive),encoding="utf8")
    monkeypatch.setenv("DRAFTCOACH_PRO_DRAFT_ORDER_DIR",str(folder))
    row=order.selected_after("Skarner","Lee Sin")
    assert row["games"]==9 and row["wins"]==5
    assert row["role_attested"] is False and row["draft_points"]==0
    assert order.selected_after("Lee Sin","Skarner")=={}
    assert order.selected_after("Skarner","Lee Sin",min_games=10)=={}
    archive["licence"]="unverified"
    path.write_text(json.dumps(archive)+"\n",encoding="utf8")
    assert order.selected_after("Skarner","Lee Sin")=={}


def test_ui_keeps_pro_order_separate_from_counter_scores():
    root=Path(__file__).resolve().parents[1]
    advice=(root/"counterpick_advice.py").read_text(encoding="utf8")
    js=(root/"draftcoach/static/selected-comp.js").read_text(encoding="utf8")
    assert "pro_selected_after(original, enemy)" in advice
    assert '"pro_order_reference"' in advice
    assert "selected AFTER" in js
    assert "positions unverified; historical only" in js
