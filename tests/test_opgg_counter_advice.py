"""OP.GG cached matchup observations remain read-only scouting references."""
import counterpick_advice as c


def test_unversioned_cached_opgg_never_creates_auto_counter(monkeypatch):
    five = {"Top": "Malphite", "Jungle": "Lee Sin", "Mid": "Ahri",
            "ADC": "Jinx", "Support": "Lulu"}
    lead = {"champions": five, "players": {"Top": {"display_name": "Dyl"}},
            "submitted_pools": {"Top": ["Malphite", "Aatrox"]}}
    def fit(champions, meta=None):
        return {"score": 90, "team_plan": "front-to-back", "team_plan_score": 5,
                "bot_lane_score": 5, "mid_jungle_score": 5}
    monkeypatch.setattr(c, "score_comp", fit)
    monkeypatch.setattr(c, "load_meta", lambda: {})
    monkeypatch.setattr(c, "load_lane_matchups", lambda: {"matchups": {}})
    monkeypatch.setattr(c, "load_solo_queue_winrates", lambda: {})
    monkeypatch.setattr(c, "live_lane_matchup_adjustment",
                        lambda champions, enemies, matchup_data=None:
                        {"edges": [{"points": 0, "games": 0, "qualitative_only": True}]})
    def row(champion, games, wr, baseline):
        return {"ours": champion, "games": games, "head_to_head_win_rate": wr,
                "role_baseline_win_rate": baseline, "residual_pp": wr - baseline,
                "source_url": "https://op.gg/open-source/opgg-mcp"}
    monkeypatch.setattr(c, "cached_for_enemy", lambda role, enemy: {
        "malphite": row("Malphite", 2544, 45.7, 51.55),
        "aatrox": row("Aatrox", 1608, 53.4, 50.03),
        "vayne": row("Vayne", 415, 55.2, 48.62),
    })
    lane = c.counter_advice(lead, {"Top": "Cho'Gath"})["lanes"][0]
    assert lane["opgg_reference"]["original"]["champion"] == "Malphite"
    choices = {r["champion"]: r for r in lane["opgg_reference"]["alternatives"]}
    assert choices["Aatrox"]["type"] == "playable"
    assert choices["Vayne"]["type"] == "learn"
    assert all(r["source_patch"] is None for r in choices.values())
    assert lane["playable"] == []
    assert lane["learn"] == []
