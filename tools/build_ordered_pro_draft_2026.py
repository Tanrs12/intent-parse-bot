"""Build a small *private* ordered pro-draft response index from CC BY-SA data.

Original dataset: Leaguepedia via LoL-Draft, CC BY-SA 4.0 (plus original
Leaguepedia CC BY-SA 3.0). Source: github.com/DeusExMachina1993/LoL-Draft

Only 2026 completed 20-action games. Data stops Aug 31, 2026 (26.17);
it does NOT contain Worlds 26.20 or confirm lane-specific counterpicks.
The evidence means a champion was selected AFTER a revealed opposing pick.
No source game data may silently become ranked SoloQ win rates or auto-pick
bonuses. Derived local index is intentionally ignored in Git.
"""
from __future__ import annotations

import argparse,csv,json,re,collections
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_SOURCE=ROOT/"var"/"leaguepedia_drafts"/"2026-all_patches.inter"
DEFAULT_INDEX=ROOT/"var"/"leaguepedia_drafts"/"pick-order-index.json"
SOURCE_URL="https://github.com/DeusExMachina1993/LoL-Draft"
LICENCE="CC BY-SA 4.0"


def canon(name):
    key=re.sub("[^a-z0-9]","",str(name).casefold())
    return {"monkeyking":"wukong","nunu":"nunuandwillump",
            "jarvaniv":"jarvaniv"}.get(key,key)


def extract(source: Path, *, min_patch:int=2601, primary_only:bool=True) -> dict:
    if not source.is_file():
        raise FileNotFoundError(source)
    grouped=collections.defaultdict(list)
    with source.open(encoding="utf8",newline="") as f:
        reader=csv.DictReader(f,delimiter="\t")
        expected={"match_id:token","hero_name:token","timestamp:float",
                  "team_result:float","label:float","patch:float",
                  "user_id:token","tier:token","draft_format:token"}
        if not expected.issubset(set(reader.fieldnames or [])):
            raise ValueError("Unexpected LoL-Draft schema; refuse import")
        for row in reader:
            try:
                patch=int(float(row["patch:float"]))
                if patch<min_patch or patch>2617:
                    continue
                tier=row["tier:token"]
                if primary_only and tier!="primary":
                    continue
                match_id=row["match_id:token"]
                team=row["user_id:token"]
                if not team.startswith(match_id+"_"):
                    continue
                team=int(team.split("_")[-1])
                slot=int(float(row["timestamp:float"]))
                label=int(float(row["label:float"]))
                won=int(float(row["team_result:float"]))
            except (KeyError,ValueError,TypeError):
                continue
            if team not in (0,1) or slot<0 or slot>=20 or label not in (0,1) or won not in (0,1):
                continue
            champ=canon(row["hero_name:token"])
            if not champ:
                continue
            grouped[match_id].append((slot,team,label,won,champ,patch,tier))
    responses=collections.defaultdict(lambda:[0,0,collections.Counter()])
    appearances=collections.defaultdict(lambda:[0,0])
    accepted=0
    patches=collections.Counter()
    invalid=0
    for game,actions in grouped.items():
        # Both sides must share a complete and unambiguous draft.
        if (len(actions)!=20 or {r[0] for r in actions}!=set(range(20))
            or len([r for r in actions if r[2]==1])!=10
            or {r[1] for r in actions}!={0,1}):
            invalid+=1;continue
        picks=sorted([r for r in actions if r[2]==1],key=lambda r:r[0])
        if any(sum(1 for row in picks if row[1]==team)!=5 for team in (0,1)):
            invalid+=1;continue
        if len({r[4] for r in picks})!=10:
            invalid+=1;continue
        if not all(len({r[3] for r in actions if r[1]==team})==1 for team in (0,1)):
            invalid+=1;continue
        if sum(picks[0][3] == r[3] for r in picks)!=5:
            invalid+=1;continue
        accepted+=1
        patches[actions[0][5]]+=1
        for slot,team,_pick,won,champ,patch,tier in picks:
            appearances[champ][0]+=1
            appearances[champ][1]+=won
            for prior in picks:
                if prior[1]==team or prior[0]>=slot:
                    continue
                enemy=prior[4]
                record=responses[(champ,enemy)]
                record[0]+=1
                record[1]+=won
                record[2][str(patch)]+=1
    compact={
        f"{champ}|{enemy}":{
            "later_picks":n,"wins":wins,
            "patch_counts":dict(sorted(patches.items())),
        } for (champ,enemy),(n,wins,patches) in responses.items()
    }
    return {
        "schema_version":1,"provider":"LoL-Draft / Leaguepedia",
        "licence":LICENCE,
        "attribution":"LoL-Draft (DeusExMachina1993), adaptation of Leaguepedia CC BY-SA 3.0, licensed CC BY-SA 4.0",
        "source_url":SOURCE_URL,
        "window":"2026 through August 31, last released patch 26.17",
        "patch_min":min_patch,
        "primary_only":primary_only,
        "games":accepted,"excluded_bad_drafts":invalid,
        "patches":dict(sorted(patches.items())),
        "distinct_directed_later_pick_pairs":len(compact),
        "appearances":{champ:{"picks":n,"wins":w}
                       for champ,(n,w) in appearances.items()},
        "responses":compact,
        "notice":"Later draft pick is observed chronology, NOT same-lane matchup, causal counter, or current-patch strength. Never auto-score.",
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,default=DEFAULT_SOURCE)
    p.add_argument("--out",type=Path,default=DEFAULT_INDEX)
    p.add_argument("--min-patch",type=int,default=2612)
    p.add_argument("--all-tiers",action="store_true")
    args=p.parse_args()
    data=extract(args.source,min_patch=args.min_patch,
                 primary_only=not args.all_tiers)
    if data["games"]<100:
        raise SystemExit("Too few valid primary-draft games; refusing misleading index")
    args.out.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,separators=(',',':'))+"\n",encoding="utf8")
    tmp.replace(args.out)
    print(json.dumps({k:v for k,v in data.items() if k in
        ("games","patches","excluded_bad_drafts",
         "distinct_directed_later_pick_pairs","licence","window")},indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
