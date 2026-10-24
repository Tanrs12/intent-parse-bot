"""Ste's ten draft cards should not repeat two mids when credible alternatives exist."""
from league_flex_core import _select_diverse_recommendations

STE = 286592835850076162


def _row(i, mid, score=97):
    roles=("Top","Jungle","Mid","ADC","Support")
    champ={
        "Top": ("Camille","Gwen","Ornn")[i%3],
        "Jungle": ("Lee Sin","Vi","Zac")[i%3],
        "Mid": mid,
        "ADC": ("Jinx","Yunara","Kai'Sa","Kog'Maw")[i%4],
        "Support": ("Lulu","Thresh","Nautilus","Rell")[i%4],
    }
    return {
        "score": score, "team_plan_score":8.1, "bot_lane_score":8.1,
        "mid_jungle_score":7.9,"credible_mixed_damage":True,
        "champions":champ,"team_plan":("front-to-back","skirmish","dive / all-in")[i%3],
        "players":{r:{"user_id":STE if r=="Mid" else 9200+j,
            "roles":[r],"role_priority":{r:1}} for j,r in enumerate(roles)},
        "carry_plan":{"role":"Mid"},"archetypes":["skirmish",str(i%3)]
    }


def test_ste_mid_frequency_balanced_among_viable_drafts():
    pool=[_row(i,"Orianna" if i%2 else "Galio",100-i*.4) for i in range(16)]
    pool += [_row(20+i,mid,96.0-i*.25) for i,mid in
        enumerate(("Akali","Vex","Ahri","Viktor","Taliyah","Sylas"))]
    pool.sort(key=lambda rec:rec["score"],reverse=True)
    recs=_select_diverse_recommendations(pool,limit=10,rotation_token="game-one")
    mids=[rec["champions"]["Mid"] for rec in recs]
    assert len(recs)==10
    assert sum(m in ("Orianna","Galio") for m in mids)<=4, mids
    assert len(set(mids))>=5,mids
    assert all(rec["bot_lane_score"]>=7 and rec["mid_jungle_score"]>=7 for rec in recs)


def test_no_force_akali_when_composition_not_viable():
    pool=[_row(i,"Orianna" if i%2 else "Galio",100-i*.4) for i in range(16)]
    pool += [_row(20+i,mid,68.0-i) for i,mid in
        enumerate(("Akali","Vex","Ahri","Viktor"))]
    pool.sort(key=lambda rec:rec["score"],reverse=True)
    chosen=_select_diverse_recommendations(pool,limit=10,rotation_token="game-two")
    assert "Akali" not in [rec["champions"]["Mid"] for rec in chosen]


def test_different_draft_tokens_still_preserve_mid_diversity():
    pool=[_row(i,"Orianna" if i%2 else "Galio",100-i*.45) for i in range(16)]
    pool += [_row(30+i,mid,96.3-i*.16) for i,mid in
        enumerate(("Akali","Vex","Ahri","Viktor","Taliyah","Sylas"))]
    pool.sort(key=lambda rec:rec["score"],reverse=True)
    for token in ("session-a","session-b","session-c"):
        chosen=_select_diverse_recommendations(pool,limit=10,rotation_token=token)
        mids=[row["champions"]["Mid"] for row in chosen]
        assert sum(m in ("Orianna","Galio") for m in mids)<=4,(token,mids)
