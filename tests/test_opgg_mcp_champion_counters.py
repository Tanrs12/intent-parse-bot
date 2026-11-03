"""OP.GG champion-analysis compact counters are safely parsed (never eval'd)."""
from pathlib import Path

import pytest

from league_opgg_mcp_champion_counters import parse_counter_summary, save_summary
from league_opgg_mcp_reference import cached_for_enemy, _filename

SAMPLE = '''class LolGetChampionAnalysis: data
class Data: strong_counters,weak_counters
class StrongCounter: champion_id,champion_name,play,win,my_win_rate,counter_win_rate,win_rate

LolGetChampionAnalysis(Data([StrongCounter(67,"Vayne",1026,643,0.63,0.37,0.63),StrongCounter(133,"Quinn",624,361,0.58,0.42,0.58)],[StrongCounter(893,"Aurora",402,177,0.44,0.56,0.56),StrongCounter(516,"Ornn",2319,1050,0.45,0.55,0.55)]))'''


def test_safe_compact_response_extracts_both_directions_without_invention():
    rows = parse_counter_summary(SAMPLE, "Malphite", "Top")
    assert len(rows) == 4
    by_enemy = {x["enemy"]: x for x in rows}
    assert by_enemy["Vayne"]["head_to_head_win_rate"] == round(643 / 1026 * 100, 2)
    assert by_enemy["Ornn"]["provider_group"] == "weak"
    assert by_enemy["Vayne"]["provider_group"] == "strong"
    assert all(x["source_patch"] is None and x["rank_filter"] is None for x in rows)
    assert all(x["role_baseline_win_rate"] is None for x in rows)


def test_nonstandard_or_malicious_pseudo_text_is_rejected():
    with pytest.raises(ValueError):
        parse_counter_summary("__import__('os').system('echo bad')", "Malphite", "Top")
    with pytest.raises(ValueError):
        parse_counter_summary(SAMPLE.replace("643,0.63", "983,0.63"), "Malphite", "Top")
    with pytest.raises(ValueError):
        parse_counter_summary(SAMPLE.replace('StrongCounter(67,"Vayne"', 'BadCounter(67,"Vayne"'),
                              "Malphite", "Top")


def test_private_records_visible_to_reader_but_do_not_replace_prior_pair(tmp_path):
    from league_opgg_mcp_reference import fetch_cached, extract_record
    rows = parse_counter_summary(SAMPLE, "Malphite", "Top")
    first, already = save_summary(rows, folder=tmp_path)
    assert (first, already) == (4, 0)
    observed = cached_for_enemy("Top", "Vayne", folder=tmp_path)
    assert observed["malphite"]["wins"] == 643
    first, already = save_summary(rows, folder=tmp_path)
    assert (first, already) == (0, 4)
    assert (tmp_path / _filename("Top", "Malphite", "Ornn")).exists()
