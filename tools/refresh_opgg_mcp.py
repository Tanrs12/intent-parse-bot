"""Refresh private OP.GG lane-tier reference through the official MCP server.

OP.GG support permitted limited attributed noncommercial usage (2026-10-26).
Five role-specific MCP calls at most. No private endpoints, raw provider
dataset publication, player identifiers or champion-select HTTP calls.
Official lane-list responses have no patch/rank/queue filters: label unknown.
"""
from __future__ import annotations
import argparse, asyncio, json, re
from datetime import datetime, timezone
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT=Path(__file__).resolve().parents[1]
ENDPOINT="https://mcp-api.op.gg/mcp"
ROLES={"top":"Top","jungle":"Jungle","mid":"Mid","adc":"ADC","support":"Support"}
PERMISSION="OP.GG support 2026-10-26: small noncommercial attributed MCP use"
DEFAULT=ROOT/"var"/"opgg"/"lane_meta.json"

def parse_lane_reply(raw:str, lane:str)->dict:
    """Validate actual official MCP text representation, never eval provider text."""
    class_name=lane.capitalize()
    pattern=(r'\b'+class_name+
             r'\("([^"\\]+)",([0-9]+),([0-9]+),([0-9]+(?:\.[0-9]+)?),([0-9]+)\)')
    entries={}
    for name,n,w,wr,tier in re.findall(pattern,raw):
        games,wins,rate,grade=int(n),int(w),float(wr),int(tier)
        if not (games>=300 and games<=100_000_000 and 0<=wins<=games
                and .25<=rate<=.75 and 1<=grade<=5):
            continue
        if abs((wins/games)-rate)>.03:
            continue
        if games>entries.get(name,{}).get("games",0):
            entries[name]={"games":games,"wins":wins,"win_rate":rate,"tier":grade}
    return entries

async def discover_or_collect(discover:bool)->dict:
    async with streamable_http_client(ENDPOINT) as (reader,writer,_):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            listed=await session.list_tools()
            tool=next((t for t in listed.tools if t.name=="lol_list_lane_meta_champions"),None)
            if tool is None:
                raise RuntimeError("Official OP.GG MCP did not advertise lane meta tool")
            schema=tool.inputSchema
            props=schema.get("properties",{})
            if "desired_output_fields" not in props:
                raise RuntimeError("Schema changed: no requested field selection")
            if discover:
                return {"tool":tool.name,"required":schema.get("required",[]),
                        "positions":props.get("position",{}).get("enum"),
                        "source":ENDPOINT}
            fields_description=props["desired_output_fields"].get("description","")
            for lane in ROLES:
                if f"data.positions.{lane}[]" not in fields_description:
                    raise RuntimeError(f"OP.GG schema no longer documents {lane} lane rows")
            role_data={}
            for lane, role in ROLES.items():
                field=f"data.positions.{lane}[].{{champion,play,win,win_rate,tier}}"
                result=await session.call_tool(tool.name,{
                    "position":lane,"desired_output_fields":[field]})
                if result.isError:
                    raise RuntimeError(f"OP.GG returned error for {lane}; keeping previous cache")
                combined="\n".join(str(getattr(x,"text","")) for x in result.content
                                   if getattr(x,"type",None)=="text")
                parsed=parse_lane_reply(combined,lane)
                if len(parsed)<8:
                    raise RuntimeError(f"Unrecognised or underfilled {lane} response: {len(parsed)} rows")
                role_data[role]=parsed
                print(f"{role}: {len(parsed)} verified entries")
                await asyncio.sleep(.8)
            return {"schema_version":1,"source":"OP.GG MCP",
                    "source_url":"https://op.gg/",
                    "endpoint":ENDPOINT,"permission":PERMISSION,
                    "captured_at":datetime.now(timezone.utc).isoformat(),
                    "provenance":"unversioned-public-lane-meta",
                    "patch":None,"rank":"unspecified","queue":"unspecified",
                    "redistribution_allowed":False,"roles":role_data}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--discover",action="store_true")
    group.add_argument("--collect",action="store_true")
    args=parser.parse_args()
    result=asyncio.run(discover_or_collect(args.discover))
    if args.discover:
        print(json.dumps(result,indent=2))
        return
    DEFAULT.parent.mkdir(parents=True,exist_ok=True)
    temp=DEFAULT.with_suffix(".new")
    temp.write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
    temp.replace(DEFAULT)
    print(f"Private cache updated at {DEFAULT}. No patch/rank-specific claims.")
if __name__=="__main__":
    main()
