
from collections import Counter

from league_comp_engine import recommend_comps
from league_flex_core import _select_diverse_recommendations

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")


def test_ten_draft_gallery_distributes_distinct_strategies():
    players = {
        role: {"user_id": idx + 1, "display_name": role, "roles": [role]}
        for idx, role in enumerate(ROLES)
    }
    shells = [
        ("pick / control", ["Ornn", "Vi", "Ahri", "Ashe", "Braum"]),
        ("dive / all-in", ["Camille", "Nocturne", "Sylas", "Kai'Sa", "Nautilus"]),
        ("scaling front-to-back", ["Shen", "Sejuani", "Orianna", "Jinx", "Lulu"]),
        ("tempo skirmish", ["Renekton", "Lee Sin", "Lissandra", "Lucian", "Milio"]),
        ("poke / siege", ["Gnar", "Taliyah", "Xerath", "Ezreal", "Karma"]),
    ]
    # Each plan has more than one legitimate high-scoring five-man variant.
    options = []
    for plan_idx, (plan, champs) in enumerate(shells):
        for i in range(3):
            variants = list(champs)
            variants[0] = f"{variants[0]}-{i}"
            variants[1] = f"{variants[1]}-{i}"
            options.append({
                "score": 100.0 - .3 * plan_idx - .15 * i,
                "team_plan_score": 8.1,
                "team_plan": plan,
                "bot_lane_score": 8.2,
                "mid_jungle_score": 8.0,
                "carry_plan": {"role": "ADC" if "scaling" in plan else "Mid"},
                "archetypes": [plan],
                "credible_mixed_damage": True,
                "champions": dict(zip(ROLES, variants)),
                "players": players,
            })
    options.sort(key=lambda rec: rec["score"], reverse=True)
    chosen = _select_diverse_recommendations(
        options, limit=10, core_limit=4, role_mix_slots=6,
    )
    assert len(chosen) == 10
    assert len({tuple(rec["champions"][role] for role in ROLES) for rec in chosen}) == 10
    counts = Counter(rec["team_plan"] for rec in chosen)
    assert len(counts) == 5
    assert max(counts.values()) <= 3
    assert len(set(chosen[i]["team_plan"] for i in (0, 8, 9))) >= 2


def test_prepared_gallery_avoids_ashe_seraphine_if_other_submitted_duos_exist():
    def player(idx, role, champions):
        return {"user_id": idx, "display_name": role, "roles": [role],
                "champions": {role: champions}}
    assignment = {
        "Top": player(1, "Top", ["Ornn"]),
        "Jungle": player(2, "Jungle", ["Jarvan IV"]),
        "Mid": player(3, "Mid", ["Orianna"]),
        "ADC": player(4, "ADC", ["Ashe", "Jinx"]),
        "Support": player(5, "Support", ["Seraphine", "Lulu"]),
    }
    options, missing = recommend_comps(
        [assignment], limit=20, pool_limit=10, prepared_flex_gallery=True,
    )
    assert not missing and options
    assert not any(
        row["champions"]["ADC"] == "Ashe" and row["champions"]["Support"] == "Seraphine"
        for row in options
    )
