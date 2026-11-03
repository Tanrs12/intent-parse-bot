"""Full public OP.GG counter list parsing and same-patch scouting boundaries."""
import json
import re
from pathlib import Path

import pytest

from tools.collect_opgg_full_web import parse_full_page, rebuild_index, page_file, source_url
from league_opgg_full_reference import full_for_enemy


def sample_html(*, champ="Malphite", patch="16.21", tier="Emerald +"):
    names = [("Jayce", "57.00", "6295"),
             ("Cho&#x27;Gath", "45.89", "2177"),
             ("Gnar", "53.12", "3333"), ("Darius", "48.10", "2022")]
    parts = []
    for name, wr, n in names:
        parts.append(
            '<li class="cursor-pointer bg-gray-0 hover:bg-gray-100 border-b border-gray-200 flex">'
            f'<img alt="{name}" width="32" height="32"/>'
            f'<strong class="text-xs">{wr}%</strong>'
            f'<span class="text-xs text-gray-600">{n}</span></li>'
        )
    return (
        f'<html><head><title>{champ} Counters Patch {patch}: Best Counter Picks | OP.GG</title></head>'
        f'<body><img alt="{tier}"/> All Servers Ranked Solo/Duo '
        '<label id="championSearchAndFilter">Search a champion</label>'
        '<ul class="border-t border-t-gray-200">' + "".join(parts) + '</ul>'
        + (" " * 26000) + "</body></html>"
    )


def test_full_list_reads_all_opponents_not_just_three():
    page = parse_full_page(sample_html(), "Malphite", "Top",
                           url="https://op.gg/lol/champions/malphite/counters/top",
                           captured_epoch=200000)
    assert page["rank_filter"] == "Emerald+"
    assert page["source_patch"] == "16.21"
    assert len(page["rows"]) == 4
    assert page["rows"][0] == {
        "enemy": "Jayce", "games": 6295, "head_to_head_win_rate": 57.0,
    }
    assert page["rows"][1]["enemy"] == "Cho'Gath"
    assert all("win" not in row for row in page["rows"])


def test_reject_wrong_rank_champion_or_broken_table():
    kwargs = dict(champion="Malphite", role="Top", url="https://op.gg/lol/champions/malphite/counters/top")
    with pytest.raises(ValueError, match="tier"):
        parse_full_page(sample_html(tier="Gold +"), **kwargs)
    with pytest.raises(ValueError, match="champion"):
        parse_full_page(sample_html(champ="Shen"), **kwargs)
    with pytest.raises(ValueError, match="list layout"):
        parse_full_page(sample_html().replace("border-t border-t-gray-200", "UNKNOWN"), **kwargs)
    with pytest.raises(ValueError, match="duplicate"):
        t = sample_html().replace('alt="Gnar"', 'alt="Jayce"')
        parse_full_page(t, **kwargs)


def test_local_index_filters_patch_rank_samples_and_not_autoscoring(tmp_path):
    p = page_file(tmp_path, "Top", "Malphite")
    p.parent.mkdir(parents=True)
    entry = parse_full_page(sample_html(), "Malphite", "Top",
                            url="https://op.gg/lol/champions/malphite/counters/top",
                            captured_epoch=200000)
    p.write_text(json.dumps(entry), encoding="utf8")
    overview = rebuild_index(tmp_path)
    assert overview["pages"] == 1 and overview["observations"] == 4
    r = full_for_enemy("Top", "Jayce", patch="26.21",
                       now=200100, index_path=tmp_path / "index.json")
    assert r["status"] == "matching_patch" and len(r["rows"]) == 1
    assert r["rows"][0]["ours"] == "Malphite"
    assert r["rows"][0]["type"] == "statistical_reference_only"
    assert "draft_points" not in r["rows"][0]
    other_patch = full_for_enemy("Top", "Jayce", patch="26.22",
                                 now=200100, index_path=tmp_path / "index.json")
    assert other_patch["rows"] == []
    stale = full_for_enemy("Top", "Jayce", patch="26.21",
                           now=200000 + 8*86400, index_path=tmp_path / "index.json")
    assert stale["rows"] == []


def test_missing_cache_cannot_be_invented(tmp_path):
    r = full_for_enemy("Mid", "Vex", patch="26.21",index_path=tmp_path / "no.json")
    assert r["status"] == "no_local_index" and r["rows"] == []



def test_wukong_uses_provider_monkeyking_slug():
    assert source_url("Wukong", "Jungle") == (
        "https://op.gg/lol/champions/monkeyking/counters/jungle"
    )



def test_enemy_page_inverts_all_matchups_without_extra_network_calls(tmp_path):
    page = parse_full_page(sample_html(), "Malphite", "Top",
                           url="https://op.gg/lol/champions/malphite/counters/top",
                           captured_epoch=200000)
    path = page_file(tmp_path, "Top", "Malphite")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(page), encoding="utf8")
    rebuild_index(tmp_path)
    result = full_for_enemy("Top", "Malphite", patch="26.21",
                            now=200100, index_path=tmp_path / "index.json")
    rows = {x["ours"]: x for x in result["rows"]}
    assert len(rows) == 4
    assert rows["Jayce"]["head_to_head_win_rate"] == 43.0
    assert rows["Jayce"]["source_direction"] == "reverse_from_enemy_page"
    assert rows["Cho'Gath"]["head_to_head_win_rate"] == 54.11
    assert all(x["source_patch"] == "16.21" for x in rows.values())
    no_stale = full_for_enemy("Top", "Malphite", patch="26.22",
                              now=200100, index_path=tmp_path / "index.json")
    assert no_stale["rows"] == []


def test_direct_champion_page_takes_precedence_over_reverse_enemy_estimate(tmp_path):
    base = parse_full_page(sample_html(champ="Malphite"), "Malphite", "Top",
                           url="https://op.gg/lol/champions/malphite/counters/top",
                           captured_epoch=200000)
    jayce_html = sample_html(champ="Jayce")
    jayce_html = jayce_html.replace('alt="Jayce"', 'alt="Vayne"')
    jayce_html = jayce_html.replace('alt="Darius"', 'alt="Malphite"')
    jayce = parse_full_page(jayce_html, "Jayce", "Top",
                            url="https://op.gg/lol/champions/jayce/counters/top",
                            captured_epoch=200000)
    for champ, page in (("Malphite", base), ("Jayce", jayce)):
        path = page_file(tmp_path, "Top", champ)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(page), encoding="utf8")
    rebuild_index(tmp_path)
    row = {x["ours"]: x for x in full_for_enemy(
        "Top", "Malphite", patch="26.21", now=200100,
        index_path=tmp_path / "index.json")["rows"]}
    assert row["Jayce"]["source_direction"] == "own_champion_page"
    assert row["Jayce"]["head_to_head_win_rate"] == 48.10



def test_offmeta_bot_web_fetch_requires_explicit_confirmed_or_declared_context(
    tmp_path, monkeypatch
):
    from tools.collect_opgg_full_web import collect
    requests = []
    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, max_bytes):
            return sample_html(champ="Hwei").encode("utf8")
    def open_page(req, timeout=30):
        requests.append(req.full_url)
        return FakeResponse()
    monkeypatch.setattr("urllib.request.urlopen", open_page)
    blocked = collect(
        "ADC", ["Hwei"], folder=tmp_path, max_new=1,
        allow_declared_offmeta=False, expected_patch="16.21",
        emit=lambda *_: None,
    )
    assert blocked["new_pages"] == 0
    assert not requests

    approved = collect(
        "ADC", ["Hwei"], folder=tmp_path, max_new=1,
        allow_declared_offmeta=True, expected_patch="16.21",
        emit=lambda *_: None,
    )
    assert approved["new_pages"] == 1
    assert approved["index_observations"] == 4
    assert requests == ["https://op.gg/lol/champions/hwei/counters/adc"]
    assert page_file(tmp_path, "ADC", "Hwei").is_file()
