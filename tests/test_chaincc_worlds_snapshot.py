"""Worlds snapshot counts real game sides, not imaginary draft chronology."""
import csv
import gzip

from tools.build_chaincc_worlds_snapshot import build_snapshot


def test_worlds_snapshot_counts_picks_bans_and_actual_same_team_duos(tmp_path):
    teams = [
        {"game_id":"11","date":"2026-10-15 18:00:00","league":"WLDs",
         "patch":"16.20","side":"blue","result":"TRUE",
         "pick1":"Sylas","pick2":"Lee Sin","pick3":"Yunara","pick4":"Lulu","pick5":"Gnar",
         "ban1":"Vi","ban2":"Nocturne","ban3":"Rumble","ban4":"Akali","ban5":"Aatrox"},
        {"game_id":"11","date":"2026-10-15 18:00:00","league":"WLDs",
         "patch":"16.20","side":"red","result":"FALSE",
         "pick1":"Galio","pick2":"Sejuani","pick3":"Jinx","pick4":"Braum","pick5":"Ornn",
         "ban1":"Vi","ban2":"Nocturne","ban3":"Rumble","ban4":"Akali","ban5":"Aatrox"},
        {"game_id":"12","date":"2026-10-15 18:00:00","league":"EM",
         "patch":"16.20","side":"blue","result":"TRUE",
         "pick1":"Sylas","pick2":"Lee Sin","pick3":"Yunara","pick4":"Lulu","pick5":"Gnar",
         "ban1":"Vi","ban2":"Nocturne","ban3":"Rumble","ban4":"Akali","ban5":"Aatrox"},
    ]
    fields = list(teams[0])
    with gzip.open(tmp_path / "chaincc-teams-2026.csv.gz","wt",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(teams)
    players = []
    for side, pairs in (
        ("blue",{"mid":"Sylas","jng":"Lee Sin","bot":"Yunara","sup":"Lulu","top":"Gnar"}),
        ("red",{"mid":"Galio","jng":"Sejuani","bot":"Jinx","sup":"Braum","top":"Ornn"}),
    ):
        for position, champ in pairs.items():
            players.append({"game_id":"11","side":side,"position":position,
                            "champion":champ,"patch":"16.20",
                            "result":"TRUE" if side=="blue" else "FALSE"})
    with gzip.open(tmp_path / "chaincc-players-2026.csv.gz","wt",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(players[0]))
        writer.writeheader()
        writer.writerows(players)
    result=build_snapshot(tmp_path)
    assert result["games"]==1 and result["team_sides"]==2
    assert result["patch_team_sides"]=={"16.20":2}
    assert result["last_source_game_date"]=="2026-10-15 18:00:00"
    champion={x["champion"]:x for x in result["champions"]}
    assert champion["Vi"]["banned"]==2
    assert champion["Lee Sin"]["picked"]==1
    assert champion["Lee Sin"]["games_picked_or_banned"]==1
    duos={(x["package"],x["first"],x["second"]):x for x in result["duos"]}
    assert duos[("Mid+Jungle","Sylas","Lee Sin")]["wins"]==1
    assert duos[("ADC+Support","Jinx","Braum")]["wins"]==0
    assert "draft_order" not in result and "counterpick" not in result
    assert result["licence"]=="CC BY 4.0"
