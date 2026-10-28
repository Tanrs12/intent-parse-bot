"""Private, bounded OP.GG MCP meta reference for the INTent shared scorer.

The official lane-list endpoint does NOT expose patch, rank or queue filters.
Never describe this as exact-patch ranked statistics. It is an observational,
all-tier-unknown, freshness-gated tie-breaker; full draft quality and comfort win.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from league_source_stats import source_mtime_ns

PATH = Path(os.environ.get("DRAFTCOACH_OPGG_CACHE") or
            Path(__file__).resolve().parent / "var" / "opgg" / "lane_meta.json")
ROLES = ("Top","Jungle","Mid","ADC","Support")
MIN_GAMES = 300
MAX_CACHE_HOURS = 48

@lru_cache(maxsize=4)
def _load(path, stamp):
    try:
        data=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if data.get("source") != "OP.GG MCP" or data.get("provenance") != "unversioned-public-lane-meta":
        return {}
    if data.get("patch") is not None or data.get("rank") != "unspecified":
        return {}
    try:
        captured=datetime.fromisoformat(data["captured_at"].replace("Z","+00:00"))
        age=(datetime.now(timezone.utc)-captured.astimezone(timezone.utc)).total_seconds()/3600
        if age < -1 or age > MAX_CACHE_HOURS:
            return {}
    except (KeyError,TypeError,ValueError):
        return {}
    return data

def reference_signal(champions:dict[str,str], *, path:Path|None=None):
    target=Path(path) if path else PATH
    try:
        data=_load(str(target),source_mtime_ns(target))
    except OSError:
        data={}
    rows=data.get("roles") or {}
    adjustment=0.0
    evidence=[]
    for role in ROLES:
        group=rows.get(role,{})
        if not isinstance(group,dict):
            continue
        peers=[float(x["win_rate"]) for x in group.values()
               if isinstance(x,dict) and int(x.get("games",0)) >= MIN_GAMES
               and isinstance(x.get("win_rate"),(int,float))
               and .25 <= float(x["win_rate"]) <= .75]
        if len(peers) < 8:
            continue
        row=group.get(champions.get(role,""),{})
        if not isinstance(row,dict) or int(row.get("games",0)) < MIN_GAMES:
            continue
        wr=float(row.get("win_rate",0))
        if not .25 <= wr <= .75:
            continue
        n=int(row["games"])
        delta=max(-.17,min(.17,(wr-median(peers))*12.0*(n/(n+4000.0))))
        adjustment+=delta
        evidence.append({"role":role,"champion":champions[role],"games":n,
                         "win_rate":round(wr*100,2),"tier":row.get("tier")})
    return {"adjustment":round(max(-.35,min(.35,adjustment)),3),
            "covered_roles":len(evidence),"evidence":evidence,
            "source":"OP.GG MCP (unversioned lane reference)",
            "patch":None,"rank":"unspecified",
            "note":"Secondary 48-hour reference only; not verified patch/rank data."}
