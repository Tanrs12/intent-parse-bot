from __future__ import annotations

import json
from pathlib import Path

import league_multi_source
from tools.refresh_open_pro_pairs import build_open_pro_pairs

def sample_rows():
    teams = []
    players = []
    for index in range(20):
        team = "Blue" if index % 2 == 0 else "Red"
        champs = ["Gnar", "Vi", "Orianna", "Jinx", "Lulu"]
        other = ["Ornn", "Wukong", "Ahri", "Ezreal", "Karma"]
        win = index < 15
        game = "g" + str(index)
        chosen = champs if index < 15 else other
        teams.append({
            "game_id": game, "side": team, "patch": "16.20",
            "result": "TRUE" if win else "FALSE",
            **{f"pick{i}": name for i,name in enumerate(chosen,1)},
        })
        for role, champ in zip(("top", "jng", "mid", "bot", "sup"),chosen):
            players.append({"game_id": game, "side": team,
                            "position": role, "champion": champ})
    return teams, players


def test_license_dataset_aggregates_real_five_player_pairs():
    teams, players = sample_rows()
    result = build_open_pro_pairs(teams,players)
    assert result["latest_patch"] == "16.20"
    assert result["team_side_count"] == 20
    assert result["source"]["license"] == "CC BY 4.0"
    pairs = [r for r in result["pairs"]
             if r["role_a"] == "ADC" and r["role_b"] == "Support"
             and r["champion_a"] == "Jinx" and r["champion_b"] == "Lulu"]
    assert len(pairs)==1 and pairs[0]["games"] == 15
    assert abs(pairs[0]["shrunken_lift"]) <= .08


def test_old_patch_cannot_dominate_current_pro_data():
    teams,players = sample_rows()
    teams.append({"game_id":"old", "side":"Blue", "patch":"16.15",
                  "result":"TRUE", **{f"pick{i}":c for i,c in
                  enumerate(["Ornn","Sejuani","Azir","Yunara","Milio"],1)}})
    players.extend({"game_id":"old","side":"Blue","position":role,"champion":champ}
                   for role,champ in zip(("top","jng","mid","bot","sup"),
                                        ("Ornn","Sejuani","Azir","Yunara","Milio")))
    result=build_open_pro_pairs(teams,players)
    assert result["team_side_count"] == 20


def test_open_pro_signal_is_small_and_correctly_attributed(monkeypatch,tmp_path):
    teams,players=sample_rows()
    data=build_open_pro_pairs(teams,players)
    location=tmp_path/"pairs.json"
    location.write_text(json.dumps(data),encoding="utf-8")
    monkeypatch.setattr(league_multi_source,"OPEN_PRO_PATH",location)
    league_multi_source._read_pairs.cache_clear()
    good=league_multi_source.open_pro_pair_signal("ADC","Jinx","Support","Lulu")
    assert good["games"] == 15
    assert abs(good["adjustment"]) < .73
    assert good["source"].startswith("ChainCC")
    bad=league_multi_source.open_pro_pair_signal("ADC","Lucian","Support","Maokai")
    assert bad["adjustment"] == 0.0 and bad["games"] == 0
    league_multi_source._read_pairs.cache_clear()
