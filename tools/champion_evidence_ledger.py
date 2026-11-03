"""Generate counts-only research coverage for EVERY published role-catalogue champion.

No provider observations or player data are exported. This ledger separates
current-patch OP.GG, older PocketPick, authored qualitative guides and
role-unverified professional pick-order context. A blank entry is a GAP, not
a neutral/50% matchup.
"""
from __future__ import annotations
import argparse,collections,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from league_matchup_universe import ROLES,role_universe
from league_pocketpick_history import storage_dir
from league_opgg_full_reference import folder as current_folder


def norm(name):
    key=re.sub("[^a-z0-9]","",str(name).casefold())
    return {"monkeyking":"wukong","nunu":"nunuandwillump","nunuwillump":"nunuandwillump","renata":"renataglasc"}.get(key,key)


def safe(path):
    try:return json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError,ValueError):return {}


def build(*,opgg_dir=None,pocket_dir=None,pro_dir=None)->dict:
    catalog={role:list(role_universe(role)) for role in ROLES}
    all_names=sorted(set(sum(catalog.values(),[])))
    known={norm(champ):champ for champ in all_names}
    oppg=safe((opgg_dir or current_folder())/"index.json")
    opgg_ok=(oppg.get("schema_version")==1 and oppg.get("pages",0)>0 and
             oppg.get("by_patch") and set(oppg["by_patch"])=={"16.21"})
    historical=safe((pocket_dir or storage_dir())/"matchv5-metrics.json")
    historic_ok=(historical.get("matches")==30000 and
                 historical.get("patches")=={"16.16":30000} and
                 historical.get("queue")==420 and historical.get("region")=="euw")
    pro=safe((pro_dir or ROOT/"var"/"leaguepedia_drafts")/"pick-order-index.json")
    pro_ok=(pro.get("schema_version")==1 and pro.get("provider")=="LoL-Draft / Leaguepedia"
            and pro.get("licence")=="CC BY-SA 4.0" and pro.get("primary_only"))
    source_notes=[]
    for filename in ("league_otp_matchup_notes.json",
                     "league_onetricks_matchup_notes.json",
                     "league_curated_counter_scout.json"):
        datum=safe(ROOT/"data"/filename)
        source_notes.extend(datum.get("entries") or [])
    rows={}
    current_role_pairs=collections.defaultdict(set)
    for role in ROLES:
        for enemy,items in ((oppg.get("matchups") or {}).get(role) or {}).items() if opgg_ok else []:
            for row in items:
                ours=norm(row.get("ours") or "")
                enemy_key=norm(enemy)
                if (ours in known and enemy_key in known and ours!=enemy_key
                    and known[ours] in catalog[role] and known[enemy_key] in catalog[role]
                    and int(row.get("games") or 0)>=100
                    and row.get("source_patch")=="16.21"):
                    current_role_pairs[role].add((ours,enemy_key))
    source_roles={"TOP":"Top","JUNGLE":"Jungle","MID":"Mid",
                  "ADC":"ADC","SUPPORT":"Support"}
    historical_role_pairs=collections.defaultdict(set)
    historical_40_pairs=collections.defaultdict(set)
    for key,row in (historical.get("matchups") or {}).items() if historic_ok else []:
        values=key.split("|")
        if len(values)!=3:continue
        ours,tag,enemy=values
        role=source_roles.get(tag)
        a,b=norm(ours),norm(enemy)
        if (not role or a==b or a not in known or b not in known or
            known[a] not in catalog[role] or known[b] not in catalog[role]):
            continue
        n=int(row.get("n") or 0)
        if n>=1:historical_role_pairs[role].add((a,b))
        if n>=40:historical_40_pairs[role].add((a,b))
    notes=collections.defaultdict(set)
    for row in source_notes:
        role=row.get("role")
        ours=norm(row.get("our") or "")
        enemy=norm(row.get("enemy") or "")
        if role in ROLES and ours in known and enemy in known:
            notes[ours].add((role,enemy))
    pro_by_champion=collections.Counter()
    if pro_ok:
        for key,row in (pro.get("responses") or {}).items():
            champion,sep,enemy=key.partition("|")
            if sep and champion in known and int(row.get("later_picks") or 0)>=5:
                pro_by_champion[champion]+=1
    for champion in all_names:
        ident=norm(champion)
        roles=[r for r in ROLES if champion in catalog[r]]
        rows[champion]={
            "roles":roles,
            "current_26_21_emerald_plus_100game_directions":sum(
                sum(a==ident for a,b in current_role_pairs[r]) for r in roles),
            "historical_16_16_observed_directions":sum(
                sum(a==ident for a,b in historical_role_pairs[r]) for r in roles),
            "historical_16_16_40game_directions":sum(
                sum(a==ident for a,b in historical_40_pairs[r]) for r in roles),
            "qualitative_scout_notes":len(notes[ident]),
            "historical_pro_later_pick_pairs_at_least5":pro_by_champion[ident],
        }
    summaries={}
    for role in ROLES:
        d=len(catalog[role])*(len(catalog[role])-1)
        now=current_role_pairs[role]
        old=historical_role_pairs[role]
        summaries[role]={
            "role_champions":len(catalog[role]),"possible_directed_pairs":d,
            "current_26_21_100game_pairs":len(now),
            "historical_16_16_observed_pairs":len(old),
            "historical_16_16_40game_pairs":len(historical_40_pairs[role]),
            "any_observed_current_or_old_pairs":len(now|old),
        }
    missing=[c for c,d in rows.items()
             if not d["current_26_21_emerald_plus_100game_directions"] and
                not d["historical_16_16_observed_directions"]]
    return {
        "schema_version":1,"snapshot_date":"2026-11-03",
        "description":"Source-separated coverage counts, no raw provider data; historical is not current",
        "champions_in_role_catalogue":len(all_names),
        "source_status":{
            "opgg_current_16_21":bool(opgg_ok),
            "pocketpick_historical_16_16":bool(historic_ok),
            "ordered_pro_historical_2026":bool(pro_ok),
        },
        "source_attribution":[
            "OP.GG: https://op.gg (private small-scale cache)",
            "PocketPick: https://pocketpick.gg/data/ (EUW Silver–Platinum, 16.16)",
            "LoL-Draft: https://github.com/DeusExMachina1993/LoL-Draft (CC BY-SA 4.0)",
            "Onetricks.gg: https://www.onetricks.gg/guides (authored guide opinions)",
        ],
        "roles":summaries,
        "totals":{k:sum(x[k] for x in summaries.values())
                  for k in next(iter(summaries.values())) if k!="role_champions"},
        "unobserved_champions":missing,
        "champions":rows,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",type=Path,default=ROOT/"docs"/"draftcoach"/"CHAMPION_EVIDENCE_LEDGER_2026-11-03.json")
    args=p.parse_args()
    result=build()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf8")
    print(json.dumps({k:v for k,v in result.items()
                      if k in ("champions_in_role_catalogue","source_status",
                               "roles","totals","unobserved_champions")},indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
