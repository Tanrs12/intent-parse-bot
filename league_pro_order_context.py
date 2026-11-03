"""Historical *ordered-pick* professional draft reference (never lane-counter proof).

LoL-Draft 2026: https://github.com/DeusExMachina1993/LoL-Draft
Based on Leaguepedia's pick/ban event order. Source licence CC BY-SA 4.0.
Local file is NOT bundled or published; queried only as human scouting.
Last match August 31, 2026, patch 26.17, not Worlds 26.20.
"""
from __future__ import annotations

import json,os,re,sys
from pathlib import Path
from functools import lru_cache
from league_source_stats import source_mtime_ns

URL="https://github.com/DeusExMachina1993/LoL-Draft"
ATTRIBUTION=("LoL-Draft (DeusExMachina1993) from Leaguepedia "
             "CC BY-SA 3.0, adaptation CC BY-SA 4.0")


def _canonical(name):
    x=re.sub("[^a-z0-9]","",str(name).casefold())
    return {"monkeyking":"wukong","nunu":"nunuandwillump"}.get(x,x)


def index_path()->Path:
    override=os.environ.get("DRAFTCOACH_PRO_DRAFT_ORDER_DIR","").strip()
    base=(Path(override) if override else
          (Path(sys.executable).resolve().parent if getattr(sys,"frozen",False)
           else Path(__file__).resolve().parent)/"var"/"leaguepedia_drafts")
    return base/"pick-order-index.json"


@lru_cache(maxsize=3)
def _load(path:str,stamp:tuple[int,int,int,int])->dict:
    try:
        data=json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError,TypeError,ValueError):
        return {}
    if (data.get("schema_version")!=1 or data.get("licence")!="CC BY-SA 4.0"
        or data.get("provider")!="LoL-Draft / Leaguepedia"
        or not data.get("primary_only")
        or int(data.get("patch_min") or 0)<2601
        or data.get("games",0)<100
        or not isinstance(data.get("responses"),dict)):
        return {}
    return data


def _index_stamp(path: Path) -> tuple[int,int,int,int]:
    """Detect rapid Windows rewrites and atomic replacements, not only mtime.

    CI/filesystems can preserve nanosecond timestamps across a fast rewrite;
    source licence validation MUST re-run if the file's size or identity changes.
    """
    s = path.stat()
    return (source_mtime_ns(path), s.st_ctime_ns, s.st_size, s.st_ino)


def selected_after(ours:str,enemy:str,*,min_games:int=5)->dict:
    """Only one fact: our champion was picked AFTER enemy's pick in pro games.

    No rank, lane or matchup causal inference; never draft points.
    """
    if not ours or not enemy or _canonical(ours)==_canonical(enemy):
        return {}
    path=index_path()
    if not path.is_file():
        return {}
    archive=_load(str(path),_index_stamp(path))
    if not archive:
        return {}
    sample=archive["responses"].get(_canonical(ours)+"|"+_canonical(enemy))
    if not sample or int(sample.get("later_picks",0))<min_games:
        return {}
    n=int(sample["later_picks"])
    wins=int(sample["wins"])
    if not 0<=wins<=n:
        return {}
    return {
        "ours":ours,"enemy":enemy,"games":n,"wins":wins,
        "win_rate":round(wins/n*100,1),
        "patches":sample.get("patch_counts",{}),
        "cohort_games":int(archive["games"]),
        "patch_window": "26.13–26.17",
        "source_url":URL,"attribution":ATTRIBUTION,
        "licence":"CC BY-SA 4.0","role_attested":False,
        "evidence_kind":"historical_pro_pick_after_reveal",
        "draft_points":0,
        "warning":("Observed later pro pick (different roles possible), "
                   "not a proven counter or current Worlds meta"),
    }


def archive_summary()->dict:
    path=index_path()
    archive=_load(str(path),_index_stamp(path)) if path.is_file() else {}
    return {
        "installed":bool(archive),
        "games":int(archive.get("games",0)),
        "later_pick_pairs":len(archive.get("responses",{})),
        "patches":archive.get("patches",{}),
        "last_patch":"26.17",
        "source_url":URL,"attribution":ATTRIBUTION,
        "warning":"No champion-role assignment and no Worlds 26.20; historical pick-order context only.",
    }
