"""Small, paced collection of complete public OP.GG champion counter pages.

The user's 26 Oct 2026 OP.GG email permits small-scale noncommercial crawling
with attribution, but warns about excessive traffic. Do NOT use this as a bulk
mirror, disable restrictions, access private endpoints, or redistribute rows.

Each page supplies a complete visible opponent list, unlike the official MCP
strong/weak top-three summary. Pages are versioned by the *displayed* patch,
tier, region and queue. No automatic draft scoring occurs here.

Examples:
  python tools/collect_opgg_full_web.py --role Top --champions Malphite Shen Gwen
  python tools/collect_opgg_full_web.py --role Mid --champions Sylas Akali Vex
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_matchup_universe import role_universe

SOURCE = "https://op.gg/lol/champions"
FOLDER = ROOT / "var" / "opgg_full"
ROLES = ("Top", "Jungle", "Mid", "ADC", "Support")
MIN_GAP_SECONDS = 3.5
MAX_PER_RUN = 20
CACHE_SECONDS = 24 * 3600
LABEL = "Emerald+"
USER_AGENT = "INTentDraftCoach-PrivateNonCommercial/1 (Tanrs12/intent-parse-bot)"

def normalise(value: str) -> str:
    return re.sub("[^a-z0-9]", "", str(value).casefold())

def slug(champion: str) -> str:
    key = normalise(champion)
    return {"wukong": "monkeyking", "nunuandwillump": "nunu"}.get(key, key)

def source_url(champion: str, role: str) -> str:
    if role not in ROLES or not re.fullmatch("[a-z0-9]{2,35}", slug(champion)):
        raise ValueError("unsupported role or champion")
    return f"{SOURCE}/{slug(champion)}/counters/{role.lower()}"

def parse_full_page(raw_html: str, champion: str, role: str, *, url: str,
                    captured_epoch: float | None = None) -> dict:
    """Read only visibly rendered table rows, never hidden private API payloads."""
    if len(raw_html) < 25000 or len(raw_html) > 3_000_000:
        raise ValueError("unexpected OP.GG response size")
    title = re.search(r'<title>([^<]+)</title>', raw_html, re.I)
    if not title:
        raise ValueError("no source title")
    title_text = html.unescape(title.group(1))
    page = re.search(r"(.+?) Counters Patch (\d{1,2}\.\d{1,2}):", title_text)
    if not page or normalise(page.group(1)) != normalise(champion):
        raise ValueError("OP.GG returned another champion or unversioned page")
    patch = page.group(2)
    # The public default URL shows Emerald+ / All Servers / Solo-Duo.
    # Reject a changed default instead of incorrectly labelling lower-tier rows.
    if not re.search(r'alt="Emerald\s*\+"', raw_html):
        raise ValueError("Emerald+ selected tier not attested")
    if "All Servers" not in raw_html or "Ranked Solo/Duo" not in raw_html:
        raise ValueError("default region or ranked queue not attested")
    if 'id="championSearchAndFilter"' not in raw_html:
        raise ValueError("full opponent list missing")

    found = re.findall(
        r'<ul class="border-t border-t-gray-200">([\s\S]*?)</ul>', raw_html)
    if len(found) != 1:
        raise ValueError("unknown OP.GG matchup-list layout")
    chunks = re.findall(r'<li class="cursor-pointer[^"]*"[^>]*>([\s\S]*?)</li>',
                        found[0])
    if not 4 <= len(chunks) <= 200:
        raise ValueError("counter table missing or unreasonably large")
    rows = []
    seen = set()
    for chunk in chunks:
        match_name = re.search(r'<img alt="([^"]+)"[^>]*?width="32"', chunk)
        match_wr = re.search(
            r'<strong class="[^"]*">([0-9]+(?:\.[0-9]+)?)%</strong>', chunk)
        match_games = re.search(
            r'<span class="text-xs text-gray-600">([0-9,]+)</span>', chunk)
        if not (match_name and match_wr and match_games):
            raise ValueError("one visible OP.GG counter row could not be parsed")
        enemy = html.unescape(match_name.group(1))
        win_rate = float(match_wr.group(1))
        games = int(match_games.group(1).replace(",", ""))
        key = normalise(enemy)
        if (key == normalise(champion) or key in seen or
            not 0 <= win_rate <= 100 or not 1 <= games <= 100_000_000):
            raise ValueError("invalid opponent, duplicate, win rate, or sample")
        seen.add(key)
        rows.append({"enemy": enemy, "games": games,
                     "head_to_head_win_rate": win_rate})
    now = time.time() if captured_epoch is None else captured_epoch
    return {
        "schema_version": 1,
        "source": "OP.GG public counters page", "url": url,
        "role": role, "ours": champion,
        "rank_filter": LABEL, "region_filter": "All Servers",
        "queue_filter": "Ranked Solo/Duo", "source_patch": patch,
        "retrieved_epoch": now,
        "retrieved_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "rows": rows,
        "usage": "Private noncommercial attributed research. No redistribution or auto-scoring.",
    }

def page_file(folder: Path, role: str, champion: str) -> Path:
    return folder / "pages" / role.lower() / (slug(champion) + ".json")

def read_page(path: Path) -> dict | None:
    try:
        row = json.loads(path.read_text(encoding="utf8"))
        if (row.get("schema_version") == 1 and row.get("source") ==
                "OP.GG public counters page" and len(row.get("rows") or []) >= 4):
            return row
    except (OSError, ValueError, TypeError):
        pass
    return None

def rebuild_index(folder: Path) -> dict:
    indexed = {role: {} for role in ROLES}
    baselines = {role: {} for role in ROLES}
    pages = rows = 0
    by_patch = {}
    for path in (folder / "pages").glob("*/*.json"):
        page = read_page(path)
        if not page or page["role"] not in ROLES:
            continue
        role, ours = page["role"], page["ours"]
        pages += 1
        by_patch[page["source_patch"]] = by_patch.get(page["source_patch"], 0) + 1
        # Each observed same-role game has one lane opponent. Weighting the
        # full matchup list by games closely reproduces the champion's
        # overall win rate without another provider request or mixing patches.
        counted_games = sum(int(e["games"]) for e in page["rows"])
        baseline_wr = (round(sum(float(e["head_to_head_win_rate"]) * int(e["games"])
                                 for e in page["rows"]) / counted_games, 3)
                       if counted_games else None)
        if baseline_wr is not None:
            baselines[role][normalise(ours)] = {
                "source_patch": page["source_patch"],
                "role_baseline_win_rate": baseline_wr,
                "role_baseline_games": counted_games,
                "retrieved_epoch": page["retrieved_epoch"],
            }
        for entry in page["rows"]:
            rows += 1
            key = normalise(entry["enemy"])
            indexed[role].setdefault(key, []).append({
                "ours": ours, "enemy": entry["enemy"],
                "games": entry["games"],
                "head_to_head_win_rate": entry["head_to_head_win_rate"],
                "role_baseline_win_rate": baseline_wr,
                "role_baseline_games": counted_games,
                "source_patch": page["source_patch"],
                "rank_filter": page["rank_filter"],
                "region_filter": page["region_filter"],
                "queue_filter": page["queue_filter"],
                "retrieved_at": page["retrieved_at"],
                "retrieved_epoch": page["retrieved_epoch"],
                "source_url": page["url"],
            })
    index = {
        "schema_version": 1, "source": "OP.GG public counters page",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pages": pages, "observations": rows, "by_patch": by_patch,
        "matchups": indexed,
        "role_baselines": baselines,
        "warning": "Private page snapshots; don't combine patches or infer an automatic counter bonus.",
    }
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "index.json"
    temp = folder / "index.json.incoming"
    temp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf8")
    temp.replace(target)
    return index

def collect(role: str, champions: list[str], *, folder: Path = FOLDER,
            max_new: int = 12, delay: float = MIN_GAP_SECONDS,
            expected_patch: str | None = None, force: bool = False,
            emit=print) -> dict:
    if role not in ROLES:
        raise ValueError("unsupported role")
    if not 0 <= max_new <= MAX_PER_RUN:
        raise ValueError("per-run request cap exceeded")
    if delay < MIN_GAP_SECONDS:
        raise ValueError("must respect minimum delay")
    allowed = {normalise(c): c for c in role_universe(role)}
    unique = []
    for c in champions:
        key = normalise(c)
        if key not in allowed:
            emit(f"SKIP {c}: not in supported {role} role catalogue")
            continue
        if key not in {normalise(x) for x in unique}:
            unique.append(allowed[key])
    requested = success = cached = stopped = 0
    for champion in unique:
        path = page_file(folder, role, champion)
        old = read_page(path)
        if (not force and old is not None and
            time.time() - float(old["retrieved_epoch"]) <= CACHE_SECONDS and
            (not expected_patch or old["source_patch"] == expected_patch)):
            cached += 1
            emit(f"CACHED {role} {champion}: {len(old['rows'])} matchups, patch {old['source_patch']}")
            continue
        if requested >= max_new:
            emit(f"SKIP {champion}: budget exhausted ({max_new} HTTP requests)")
            continue
        if requested:
            time.sleep(delay)
        requested += 1
        url = source_url(champion, role)
        req = urllib.request.Request(url,headers={
            "User-Agent": USER_AGENT,"Accept-Language":"en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                content = response.read(3_000_001)
            page = parse_full_page(content.decode("utf8", "replace"),
                                   champion, role, url=url)
            if expected_patch and page["source_patch"] != expected_patch:
                raise ValueError(f"source patch {page['source_patch']} != {expected_patch}")
            if old and old["source_patch"] != page["source_patch"]:
                archive = (folder / "archive" / old["source_patch"] /
                           role.lower() / (slug(champion) + ".json"))
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_text(json.dumps(old,ensure_ascii=False),encoding="utf8")
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".incoming")
            temp.write_text(json.dumps(page,ensure_ascii=False,indent=2),encoding="utf8")
            temp.replace(path)
            success += 1
            emit(f"NEW {role} {champion}: {len(page['rows'])} opponents, "
                 f"{page['source_patch']} {page['rank_filter']}")
        except urllib.error.HTTPError as exc:
            emit(f"HTTP {exc.code} for {champion}; no data saved")
            if exc.code in (401,403,429):
                stopped += 1
                emit("STOP: access restriction. Do not bypass or retry automatically.")
                break
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            emit(f"ERROR {champion}: {type(exc).__name__} {str(exc)[:160]}")
    index = rebuild_index(folder)
    return {"new_requests": requested, "new_pages": success,
            "cached_pages": cached, "stopped": stopped,
            "index_pages": index["pages"], "index_observations": index["observations"],
            "by_patch": index["by_patch"]}

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role",choices=ROLES,required=True)
    p.add_argument("--champions",nargs="+",required=True)
    p.add_argument("--max-new",type=int,default=12)
    p.add_argument("--delay",type=float,default=MIN_GAP_SECONDS)
    p.add_argument("--expected-patch")
    p.add_argument("--folder",type=Path,default=FOLDER)
    p.add_argument("--force",action="store_true")
    args=p.parse_args()
    print(json.dumps(collect(args.role,args.champions,folder=args.folder,
                             max_new=args.max_new,delay=args.delay,
                             expected_patch=args.expected_patch,
                             force=args.force),indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
