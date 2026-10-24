"""References to verified high-elo guide pages and timestamped matchup videos.

This is a SOURCE CATALOG, not an unlicensed scrape of numerical tables.
No network on draft path. A chapter index is navigational evidence only:
watch the VOD before claiming a tier, counter or patch-specific outcome.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from league_source_stats import source_mtime_ns

PATH = Path(__file__).with_name("data") / "league_matchup_reference_catalog.json"


def _norm(value: str) -> str:
    return re.sub("[^a-z0-9]", "", str(value).casefold())


def _seconds(timestamp: str) -> int:
    values = [int(part) for part in timestamp.split(":")]
    if len(values) > 3 or not values:
        raise ValueError("Invalid guide chapter time")
    result = 0
    for value in values:
        result = result * 60 + value
    return result


@lru_cache(maxsize=4)
def _catalog(path: str, mtime: int) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if data.get("schema_version") == 1 else {}
    except (OSError, ValueError):
        return {}


def load_catalog() -> dict:
    try:
        return _catalog(str(PATH), source_mtime_ns(PATH))
    except OSError:
        return {}


def source_catalog_summary() -> dict:
    data = load_catalog()
    return {
        "statistical_provider_references": len(data.get("statistical_sources", [])),
        "champion_guide_links": len(data.get("champion_guides", [])),
        "video_guides": len(data.get("chapter_guides", [])),
        "timestamped_matchup_chapters": sum(
            len(g.get("chapters", [])) for g in data.get("chapter_guides", [])
        ),
        "status": "links and navigation only; numerical ingestion not licensed",
    }


def lookup_matchup_references(role: str, ours: str, enemy: str, max_results: int = 4) -> list[dict]:
    if not role or not ours or not enemy:
        return []
    data = load_catalog()
    results: list[dict] = []
    for guide in data.get("chapter_guides", []):
        if guide.get("role") != role or _norm(guide.get("champion", "")) != _norm(ours):
            continue
        for ch in guide.get("chapters", []):
            if _norm(ch.get("enemy", "")) != _norm(enemy):
                continue
            start = _seconds(ch["at"])
            results.append({
                "source_type": "Timestamped high-elo matchup video",
                "source_url": guide["url"] + "&t=" + str(start) + "s",
                "author": guide.get("author", ""),
                "published": guide.get("published", ""),
                "chapter": ch["at"],
                "mechanic": f"{guide.get('title', 'Matchup guide')}: {ours} versus {enemy} discussion starts at {ch['at']}. Video tier is not verified.",
                "evidence_kind": "chapter link; tier not independently transcribed",
                "confidence": "reference only; no directional matchup claim",
                "draft_points": 0,
            })
    for guide in data.get("champion_guides", []):
        if guide.get("role") != role or _norm(guide.get("champion", "")) != _norm(ours):
            continue
        results.append({
            "source_type": guide["provider"],
            "source_url": guide["url"],
            "mechanic": f"Reference page for {ours} {role} matchup against {enemy}; inspect guide for tips and patch-specific statistics.",
            "evidence_kind": "reference link; numerical statistics not imported",
            "confidence": "reference only",
            "draft_points": 0,
        })
    return results[:max(0, int(max_results))]
