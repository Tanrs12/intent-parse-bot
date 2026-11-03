"""Curated original paraphrases of a few already reviewed high-Elo public guides.

NO automatic crawling, NO tier=win rate translation, NO copied guide text.
The source author controls the original guide and may change their opinion.
Output is community scouting with links, never player pool eligibility.
"""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"league_onetricks_matchup_notes.json"
guides=[
  {
  "champion":"Qiyana","role":"Mid","author":"Kaitania",
  "rank":"Challenger; published #1 Qiyana",
  "source_url":"https://www.onetricks.gg/guide/dhsyzn3/kaitanias-ultimate-challenger-qiyana-guide",
  "updated":"2026-10-24",
  "matchups":[
    ("Diana","S+","Her early shield and defensive rune choices blunt burst; prioritise surviving and finding map plays."),
    ("Zoe","S+","Long-range sleep and poke make lane exceptionally unforgiving; cleanse may be needed."),
    ("Kennen","S+","Avoid committing into his available spells or ultimate; look for windows after waveclear."),
    ("Tryndamere","S+","Sustain and sustained melee damage make extended fighting unattractive."),
    ("Jayce","S+","Early ranged pressure and hammer-form trading create a demanding lane."),
    ("Akali","S","Her level-six all-in remains threatening even if Qiyana has an early lead."),
    ("Aurora","S","Bait her poke and control before choosing an all-in."),
    ("Hwei","S","Wait for his defensive crowd control to be unavailable before engaging."),
    ("Vex","S","Respect her fear setup; take trades only after she has expended the key spell."),
    ("Ryze","S","Phase Rush and short range make spacing and defensive-rune trades critical."),
    ("Viktor","S","Avoid repeating his empowered Q trade pattern; preserve health."),
    ("LeBlanc","S","Trading into her burst is difficult; surviving lane can be more valuable than chasing kills."),
    ("Sylas","A","Short grass-element trades can deny part of Sylas's healing window."),
    ("Yasuo","A","Respect shield and defensive setup; avoid wasting your main burst into Wind Wall interactions."),
    ("Gragas","A","His dash and sustain punish all-ins; seeking roams or jungle assistance is safer."),
    ("Anivia","B","Hold mobility to escape her wall; avoid overcommitting just to break her passive."),
    ("Fizz","C","The author's defensive setup reduces his burst threat; execute trades around mobility cooldowns."),
    ("Zed","C","The first ultimate committed can be punished; manage defensive mobility during his all-in."),
    ("Yone","C","Use brief stealth trades and avoid giving him a long chase with E."),
    ("Ahri","D","Avoid early basic attack plus W poke; grass-element trades can prevent easy retaliation."),
    ("Akshan","D","Grass-element trading and interrupt tools can disrupt his extended-ranged patterns."),
    ("Ekko","D","Short trades that deny his passive trigger are favoured."),
    ("Talon","D","Avoid both parts of his return blade, then use stealth to prevent his bleed."),
  ]},
  {
  "champion":"Vayne","role":"Top","author":"ATUMOWY",
  "rank":"Master peak; published Vayne specialist",
  "source_url":"https://www.onetricks.gg/guide/jq03lt5/2621-1200lp-euw-vayne-top-guide-builds-matchups",
  "updated":"2026-10-29",
  "matchups":[
    ("Malphite","S+","Armor stacking and repeatable poke make this a very hard Vayne lane; early team assistance may be necessary."),
    ("Akali","S","Vayne can pressure level one, but the lane becomes dangerous when Akali obtains more tools."),
    ("Cassiopeia","S","Her sustained magic damage and grounding create strong anti-kiting pressure."),
    ("Jayce","S","Range switching and burst require extremely accurate spacing."),
    ("Teemo","S","Blind and mushroom control heavily constrain Vayne; cleanse timing is important."),
    ("Pantheon","S","Point-and-click stun threatens all-ins; play around his commitment cooldown."),
    ("Ryze","S","Root and burst require excellent spacing; all-ins depend on dodging his follow-up."),
    ("Varus","S","Long-range poke and control make standard auto-trading difficult."),
    ("Gangplank","A","Barrel spacing and timing are central; do not walk into his combo."),
    ("Yasuo","A","Minion-dash angles make spacing dangerous; Vayne must respect his engage reach."),
    ("Jax","A","Counter Strike creates a dangerous commitment window for a short-range marksman."),
    ("Singed","B","Even matchup according to the guide; protect minion waves from proxies."),
    ("Fiora","B","Wave and weak-point positioning matter more than a simple range advantage."),
    ("Sion","B","Durability and waveclear prevent easy snowballing; prioritise reliable farm."),
    ("Gragas","B","He depends on dash timing; punish missed engage rather than starting long trades."),
    ("Ornn","B","The author rates the lane even rather than an automatic Vayne counter."),
    ("Rumble","B","The power balance changes sharply across early levels and level six."),
    ("Nasus","C","Favourable in active lanes where Vayne can pressure and get support; weak-side can still be rough."),
    ("Irelia","C","Vayne can space her if she avoids dash reset angles and uses defensive runes."),
  ]},
  {
  "champion":"Tryndamere","role":"Top","author":"Kostas",
  "rank":"Master; authored Tryndamere guide",
  "source_url":"https://www.onetricks.gg/guide/jadx5hm/in-depth-tryndamere-top-guide-matchup-sheet-2621",
  "updated":"2026-10-21",
  "matchups":[
    ("Malphite","S+","Armor scaling can shut down the split-push duelist if Tryndamere fails to gain an early advantage."),
  ]},
]
# These authors use similar but not identical tier headings. Only explicit
# C/D/F favourable and S+/S/A unfavourable are mapped here. B is mixed.
verdict_for={"S+":"unfavourable","S":"unfavourable","A":"unfavourable",
             "B":"mixed","C":"favourable","D":"favourable","F":"favourable"}
entries=[]
for g in guides:
    for enemy,tier,summary in g["matchups"]:
        entries.append({
            "role":g["role"],"our":g["champion"],"enemy":enemy,
            "verdict":verdict_for[tier], "summary":summary,
            "guide_tier":tier,
            "reviewed_on":"2026-11-03",
            "patch":"26.21 guide revision; author opinion, not match statistics",
            "evidence_kind":"single high-Elo one-trick matchup guide",
            "source_quality":g["rank"]+"; individual author; subjective",
            "weight_label":"one high-Elo author; not a counterpick score",
            "draft_points":0,
            "sources":[{"provider":g["author"]+" · Onetricks.gg",
                        "url":g["source_url"],"updated":g["updated"]}],
        })
if len(entries)!=43:
    raise ValueError(f"Expected manually reviewed 43 records, got {len(entries)}")
payload={
 "schema_version":1,"reviewed_on":"2026-11-03",
 "notes_policy":"Original concise summaries of already-reviewed public high-Elo author matchup judgments; attributed, source links retained. Not licensed statistics, not automatic champion selection, and not guide text republication.",
 "source_guides":[{k:v for k,v in g.items() if k!="matchups"} for g in guides],
 "entries":entries,
}
OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
print("WRITTEN",OUT, "guides",len(guides),"qualitative matchup entries",len(entries))
