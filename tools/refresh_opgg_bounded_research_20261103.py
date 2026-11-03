"""Small one-time, permission-paced current-patch public matchup refresh.

Never attempt all 173 champions in one run. Public OP.GG sources with <=15
fresh HTTP pages; >=4sec spacing; cached pages not revisited. Abort on
access restriction and refuse unexpected patch.
"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.collect_opgg_full_web import collect, FOLDER, page_file, read_page
priority={
"Top":["Shen","Gwen","Camille","Irelia","Rumble","Jax","Aatrox","Darius","Sett","Gangplank","Renekton","Olaf","Jayce","Ornn","Garen","Ambessa"],
"Jungle":["Diana","Gragas","Qiyana","Lee Sin","Kindred","Nocturne","Lillia","Viego","Taliyah","Xin Zhao","Jarvan IV","Bel'Veth","Amumu"],
"Mid":["Sylas","Akali","Zed","Yasuo","Lissandra","Vex","Qiyana","Ryze","Galio","Taliyah","Azir","Orianna","Hwei"],
"ADC":["Jinx","Kai'Sa","Caitlyn","Draven","Ashe","Ezreal","Varus","Ziggs","Kog'Maw","Yunara","Swain"],
"Support":["Braum","Leona","Rakan","Thresh","Nautilus","Lulu","Janna","Nami","Alistar","Pyke","Maokai"],
}
selected={}
for role,names in priority.items():
    missing=[c for c in names if not read_page(page_file(FOLDER,role,c))]
    selected[role]=missing[:3]
    print(role,"uncached",len(missing),"selected",selected[role],flush=True)
new=0
for role,champs in selected.items():
    if not champs:continue
    item=collect(role,champs,folder=FOLDER,max_new=min(3,15-new),
                 delay=4,expected_patch="16.21",emit=print)
    new+=item["new_requests"]
    if item.get("stopped"):raise SystemExit("Source access restricted; STOP")
    if new>=15:break
print("DONE_NEW_REQUESTS",new,flush=True)
