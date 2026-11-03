"""A second champion change may come from *any* open lane."""
from adaptive_compensation_search import search_all_lane_companions

ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
ORIGINAL = dict(Top="Ornn", Jungle="Lee Sin", Mid="Orianna",
                ADC="Jinx", Support="Lulu")
TRAITS = {
    "Ornn": {"roles": ["TANK", "VANGUARD"], "ratings": {"toughness": 3},
             "adaptive_type": "PHYSICAL_DAMAGE"},
    "Vayne": {"roles": ["MARKSMAN"], "ratings": {"toughness": 1},
              "adaptive_type": "PHYSICAL_DAMAGE"},
    "Lee Sin": {"roles": ["DIVER"], "ratings": {"toughness": 2},
                "adaptive_type": "PHYSICAL_DAMAGE"},
    "Orianna": {"roles": ["MAGE"], "ratings": {"toughness": 1},
                "adaptive_type": "MAGIC_DAMAGE"},
    "Galio": {"roles": ["TANK", "WARDEN"], "ratings": {"toughness": 3},
              "adaptive_type": "MAGIC_DAMAGE"},
    "Jinx": {"roles": ["MARKSMAN"], "ratings": {"toughness": 1},
             "adaptive_type": "PHYSICAL_DAMAGE"},
    "Ziggs": {"roles": ["MAGE"], "ratings": {"toughness": 1},
              "adaptive_type": "MAGIC_DAMAGE"},
    "Zed": {"roles": ["ASSASSIN"], "ratings": {"toughness": 1},
            "adaptive_type": "PHYSICAL_DAMAGE"},
    "Lulu": {"roles": ["ENCHANTER"], "ratings": {"toughness": 1},
             "adaptive_type": "MAGIC_DAMAGE"},
    "Braum": {"roles": ["TANK", "WARDEN"], "ratings": {"toughness": 3},
              "adaptive_type": "PHYSICAL_DAMAGE"},
}
OWNERS = {role: index + 1 for index, role in enumerate(ROLES)}


def rec(champs, score=93, edge=None, bot=8, team=8.4, midjg=7):
    return {
        "champions": dict(champs),
        "player_ids": OWNERS,
        "score": score, "rank_score": score, "identity": "dive",
        "team_plan_score": team, "bot_lane_score": bot,
        "mid_jungle_score": midjg,
        "live_lane_edges": [edge] if edge else [],
    }


def evaluate(enemy, variant, *, anchor, expected_companion, blocked=None):
    base = rec(ORIGINAL, score=92, bot=8.4)
    received = []
    def backend(**kwargs):
        required = kwargs["required_champions"]
        received.append(dict(required))
        if expected_companion in required:
            return {"recommendations": []}
        if any(variant["champions"].get(role) != champ
               for role, champ in required.items()):
            return {"recommendations": []}
        return {"recommendations": [variant]}
    output = search_all_lane_companions(
        {"recommendations": [base]},
        chosen=ORIGINAL,
        known_enemy_lanes={anchor: enemy},
        actual_locks=blocked or {},
        hard_required={},
        unavailable=set(),
        backend_kwargs={"identity": "dive", "side": "blue"},
        backend=backend, champion_traits=TRAITS,
        global_counter_rules={},
    )
    return output, received


def test_vayne_top_galio_mid_replaces_frontline_even_when_jungle_unchanged():
    alt = rec({**ORIGINAL, "Top": "Vayne", "Mid": "Galio"},
              score=95, edge={"role": "Top", "enemy": "Malphite",
                              "games": 2500, "points": 2.5,
                              "usable_for_ranking": True})
    output, searched = evaluate("Malphite", alt, anchor="Top",
                                expected_companion="Mid")
    assert output["recommendations"][0]["champions"] == alt["champions"]
    assert output["compensation_search"]["selected_changes"] == ["Top", "Mid"]
    assert output["compensation_search"]["considered_roles"] == [
        "Jungle", "Mid", "ADC", "Support",
    ]
    assert any("Mid" not in required and "Jungle" in required
               and "Support" in required for required in searched)


def test_zed_mid_and_ziggs_adc_restore_magic_damage():
    alt = rec({**ORIGINAL, "Mid": "Zed", "ADC": "Ziggs"},
              score=95, bot=7.2,
              edge={"role": "Mid", "enemy": "Akali",
                    "games": 2500, "points": 2.5,
                    "usable_for_ranking": True})
    output, searched = evaluate("Akali", alt, anchor="Mid",
                                expected_companion="ADC")
    assert output["recommendations"][0]["champions"]["ADC"] == "Ziggs"
    assert output["compensation_search"]["selected_changes"] == ["Mid", "ADC"]
    assert any("ADC" not in required and "Top" in required
               and "Jungle" in required for required in searched)


def test_support_can_replace_top_frontline():
    alt = rec({**ORIGINAL, "Top": "Vayne", "Support": "Braum"},
              score=95, bot=7.5,
              edge={"role": "Top", "enemy": "Malphite",
                    "games": 2400, "points": 2.5,
                    "usable_for_ranking": True})
    output, _ = evaluate("Malphite", alt, anchor="Top",
                         expected_companion="Support")
    assert output["recommendations"][0]["champions"]["Support"] == "Braum"


def test_real_lock_stops_cross_lane_structural_changes():
    alt = rec({**ORIGINAL, "Top": "Vayne", "Mid": "Galio"},
              score=98, edge={"role": "Top", "enemy": "Malphite",
                              "games": 2500, "points": 2.5})
    output, searched = evaluate("Malphite", alt, anchor="Top",
                                expected_companion="Mid",
                                blocked={"Mid": "Orianna"})
    assert output["recommendations"][0]["champions"] == ORIGINAL
    assert all(required.get("Mid") != "" for required in searched)
    assert len(searched) == 3


def test_no_confirmed_lane_never_explores_any_companion():
    ran = []
    out = search_all_lane_companions(
        {"recommendations": [rec(ORIGINAL)]},
        chosen=ORIGINAL,
        known_enemy_lanes={}, actual_locks={}, hard_required={},
        unavailable=set(), backend_kwargs={},
        backend=lambda **kw: ran.append(kw),
        champion_traits=TRAITS, global_counter_rules={},
    )
    assert not ran
    assert out["recommendations"][0]["champions"] == ORIGINAL



def test_compensation_worker_never_blocks_live_ui_thread():
    import threading
    import time
    from adaptive_compensation_search import CompanionSearchService

    entered = threading.Event()
    release = threading.Event()
    reported = threading.Event()
    calls = []

    def delayed_runner(**kwargs):
        calls.append(kwargs["chosen"])
        entered.set()
        assert release.wait(timeout=5)
        return {"recommendations": [rec({**ORIGINAL, "Top": "Vayne", "Mid": "Galio"})]}

    service = CompanionSearchService(runner=delayed_runner)
    before = time.perf_counter()
    first, state = service.request("game-123-top", on_update=reported.set,
                                   chosen=ORIGINAL)
    assert time.perf_counter() - before < 0.3
    assert first is None and state == "evaluating"
    assert entered.wait(timeout=2)
    duplicate, state = service.request("game-123-top", chosen=ORIGINAL)
    assert duplicate is None and state == "evaluating"
    superseded, state = service.request("game-123-mid", chosen=ORIGINAL)
    assert superseded is None and state == "queued_next_state"
    release.set()
    assert reported.wait(timeout=4)
    finished, state = service.request("game-123-top", chosen=ORIGINAL)
    assert state == "ready"
    assert finished["recommendations"][0]["champions"]["Mid"] == "Galio"
    assert len(calls) == 1


def test_bridge_uses_same_async_legal_backend_for_all_companion_roles():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    bridge = (root / "tools" / "intent_live_draft_bridge.py").read_text(
        encoding="utf8"
    )
    assert "request_compensation_search(" in bridge
    assert "backend=recommend_live_drafts" in bridge
    assert "on_update=_invalidate_opgg_guidance" in bridge
    assert "not testing" in bridge
    assert "(known_enemy_lanes or forced_ally_pick)" in bridge
    assert "compensation_roles.add" not in bridge
    assert "role not in known_enemy_lanes" in bridge
    build = (root / "build_exe.py").read_text(encoding="utf8")
    assert "'adaptive_compensation_search.py'" in build



def test_already_locked_zed_mid_can_force_mage_bot_without_enemy_role():
    base = rec({**ORIGINAL, "Mid": "Zed"}, score=91.0, bot=8.4)
    alt = rec({**ORIGINAL, "Mid": "Zed", "ADC": "Ziggs"},
              score=94.0, bot=7.1)
    received = []
    def backend(**kwargs):
        fixed = kwargs["required_champions"]
        received.append(dict(fixed))
        if "ADC" in fixed:
            return {"recommendations": []}
        return {"recommendations": [alt]}
    result = search_all_lane_companions(
        {"recommendations": [base]},
        chosen=ORIGINAL,
        known_enemy_lanes={},
        actual_locks={"Mid": "Zed"},
        hard_required={}, unavailable=set(),
        backend_kwargs={"identity": "dive"}, backend=backend,
        champion_traits=TRAITS, global_counter_rules={},
    )
    winner = result["recommendations"][0]
    assert winner["champions"]["Mid"] == "Zed"
    assert winner["champions"]["ADC"] == "Ziggs"
    assert "already locked" in winner["adaptive_reasons"]["Mid"]
    assert "magic damage" in winner["adaptive_reasons"]["ADC"]
    assert result["compensation_search"]["selected_changes"] == ["Mid", "ADC"]
    assert all(request.get("Mid") is None for request in received)


def test_already_locked_zed_cannot_undo_existing_adc_lock():
    base = rec({**ORIGINAL, "Mid": "Zed"}, score=91)
    alt = rec({**ORIGINAL, "Mid": "Zed", "ADC": "Ziggs"}, score=99)
    called = []
    out = search_all_lane_companions(
        {"recommendations": [base]},
        chosen=ORIGINAL, known_enemy_lanes={},
        actual_locks={"Mid": "Zed", "ADC": "Jinx"},
        hard_required={}, unavailable=set(),
        backend_kwargs={"identity": "dive"},
        backend=lambda **kw: called.append(kw) or
                {"recommendations": [alt]},
        champion_traits=TRAITS, global_counter_rules={},
    )
    assert out["recommendations"][0]["champions"]["ADC"] == "Jinx"
    # The ADC is a *real champion lock*, passed separately to the shared
    # backend; it must never appear as an opened compensation lane.
    assert len(called) == 3


def test_locked_mid_with_no_lost_team_job_keeps_original_teammates():
    base = rec({**ORIGINAL, "Mid": "Galio"}, score=94)
    alternative = rec({**ORIGINAL, "Mid": "Galio", "ADC": "Ziggs"}, score=99)
    out = search_all_lane_companions(
        {"recommendations": [base]},
        chosen=ORIGINAL, known_enemy_lanes={},
        actual_locks={"Mid": "Galio"}, hard_required={},
        unavailable=set(), backend_kwargs={},
        backend=lambda **kw: {"recommendations": [alternative]},
        champion_traits=TRAITS, global_counter_rules={},
    )
    assert out["recommendations"][0]["champions"] == base["champions"]



def test_new_current_patch_opgg_index_version_triggers_fresh_companion_search():
    from pathlib import Path
    bridge = (Path(__file__).resolve().parents[1] / "tools" /
              "intent_live_draft_bridge.py").read_text(encoding="utf8")
    assert "opgg_full_folder()" in bridge
    assert "opgg_index_stamp" in bridge
    assert 'f"{cache_key}|opgg:{opgg_index_stamp}"' in bridge
