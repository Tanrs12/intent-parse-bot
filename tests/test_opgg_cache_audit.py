"""The private cache audit reports counts without performing network requests."""
import json

from tools.opgg_cache_audit import audit_cache


def test_audit_distinguishes_fresh_expired_and_invalid(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / "_summaries").mkdir()
    sample = {
        "role": "Mid", "ours": "Akali", "enemy": "Vex",
        "games": 110, "retrieved_epoch": 10000.0,
        "source_patch": None, "rank_filter": None,
    }
    (folder / "mid-akali-vex.json").write_text(json.dumps(sample), encoding="utf-8")
    sample2 = {
        **sample, "role": "Top", "ours": "Gnar", "enemy": "Jax",
        "retrieved_epoch": -80000.0, "source_patch": "26.21",
        "rank_filter": "Diamond+",
    }
    (folder / "top-gnar-jax.json").write_text(json.dumps(sample2), encoding="utf-8")
    (folder / "broken.json").write_text("{", encoding="utf-8")
    (folder / "_summaries" / "summary.json").write_text("{}", encoding="utf-8")
    report = audit_cache(folder, now=11000.0)
    assert report["pair_files"] == 2
    assert report["fresh"] == 1
    assert report["expired"] == 1
    assert report["invalid"] == 1
    assert report["unversioned"] == 1
    assert report["summary_files"] == 1
    assert report["by_role"]["Mid"] == {"pairs": 1, "fresh": 1}
    assert report["by_role"]["Top"] == {"pairs": 1, "fresh": 0}
