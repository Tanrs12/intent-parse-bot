# Matchup evidence collection — 3 November 2026

## Objective

Build a large, reproducible, **player-aware** matchup research system that
augments Rat's existing `league_comp_engine` and shared bridge. Do not fork
ranking into an independent UI service, fabricate statistics or import bulk
third-party proprietary tables without permission.

A pre-selected comp can adapt when an enemy lane is **confirmed**. An actual
statistically qualified counter that is in the player's submitted role pool
can influence live ranking. Qualitative one-trick advice appears separately as
**SCOUT** or **LEARN**, can flag adverse planned lanes, and is not automatically
picked or locked in League.

## Three evidence tiers

| Tier | Data | How it is used |
| --- | --- | --- |
| Measured, calibrated | Riot Match-V5 authorised samples or appropriately licensed, patch/rank-labelled tables | Soft numerical lane adjustment in the shared engine, with sample gates |
| Historical, small samples | Existing permissioned PocketPick 16.16 EUW 30k matches | Historical low-confidence context; never misrepresent as current patch |
| Human matchup opinion | Champion mains' posts, one-trick guides, curated site pages, YouTube descriptions | Opinion-level scouting, source URL and qualitative confidence; do not translate tiers into invented win rates |

## Current inventory

- Approximately **12,128** non-mirror ordered champion-vs-champion pairings
  across all known positions in the existing champion-role catalogue.
- Existing LoLalytics 16.20 bundle contains **1,319** 100+-game pairs (~10.9%
  coverage). Its source initially selected only 8 enemy champions per role.
- The user's private PocketPick EUW 16.16 sample contains **10,806** raw pair
  rows from **30,000** historical matches. Only a subset has sufficiently many
  games; do not commit that private dataset to GitHub.
- **101 curated, short, individually attributed directional notes** stored in
  `data/league_otp_matchup_notes.json` from champion mains' discussions and
  public counter summaries, now spanning Top, Jungle, Mid and ADC.
  This is not a licensed win-rate export.
- **36 research index entries**, some navigation-only (including LoLMatchups.GG OTP pages, Gnar/Kayle
  communities, Onetricks.gg, relevant YouTube and player-main guides).
- **47 accurate Gwen tierlist video chapter pointers** in
  `data/league_otp_video_chapters.json`; explicitly **unreviewed**
  as video verdicts, not converted to scoring input.

## Specific confirmed issues and fixes

1. **Gnar** is in the Discord signup cache for player
   `139837324438536193`'s Top pool (along with Gwen, Camille, Kayle,
   Vayne). It was incorrectly categorised as `LEARN` by the live bridge because
   its presentation comp contained only display-name strings and no player's
   submitted role pools. `_scouting_lead_with_pools()` now attaches the
   correct owner and role pool using exact player IDs before calling
   `counter_advice`. Never infer ownership from player history alone.
2. Main Game Plan now shows only our **five actual slots** on each side, not
   five extra placeholder boxes for unknown enemy picks.
3. Live `Send to team` was tested locally: a correctly flattened POST to
   `/api/control` accepted a five-champion selection; five subsequent reads
   retained it in `auto-idle` mode, and the original selection was restored.
   `Drafts: Auto` therefore does NOT stop the relay's chosen comp from
   synchronising. It controls recommendation source in verified team lobbies.
   The Coach UI now rejects an acknowledgement that does not contain the exact
   five selected champions, and immediately refreshes its own preview.
4. Camille into Shen demonstrates the desired distinction: Gwen is a playable
   Top alternative when in the submitted pool; Gnar too. Public source
   disagreements and team-fit tradeoffs are surfaced. Small/old samples do
   not certify a statistical counter and should not force an auto-swap.

## Guide acquisition workflow

1. Discover source and author: champion, lane, author self-reported rank,
   publication date/patch, stable original URL, whether sources disagree.
2. Review genuine material before asserting a matchup verdict; an unreviewed
   video chapter may be indexed as **navigation only**.
3. Capture **brief original paraphrases and citations** plus confidence tier,
   not full copyrighted spreadsheets, guide text or raw video transcripts.
4. Treat one contributor as one opinion even if 20 comments agree. Comments
   may correct the tierlist and should not be hidden.
5. Require current-patch, sufficient samples (and proper access rights)
   before assigning numerical matchup points. Missing data is unknown, not
   50/50. A strong one-trick opinion is useful scouting, not a win-rate
   measurement.
6. Add data under `data/`; `league_matchup_universe.scout_note()` loads
   it after the stronger curated notes. Only the shared backend decides legal
   live picks. Log coverage gaps, rank/patch and source freshness.

## Longer-term collection

The official Riot Match-V5 collector
`tools/collect_riot_ranked_matchups.py` already supports rate limits,
queue and patch validation, duplicate filtering and rank-anchor attribution.
It needs a legitimately authorised `RIOT_API_KEY` or approved/licensed
aggregate export; no such key is set on the user's PC. Make the aggregator
sample-size aware rather than mass-copying LoLalytics/OP.GG/U.GG pages.
The public LoLalytics counter page *can* be viewed and referenced, but viewing
it does not by itself grant blanket bulk-copy and redistribution rights.
Refer to `docs/riot-gold-rank-collector.md` and
`docs/multi-source-matchup-research.md`.

## Permissioned OP.GG numerical reference (added 3 November)

The private OP.GG permission response explicitly permits small-scale
non-commercial site use with attribution and reasonable request volume,
and recommends its official [OP.GG MCP](https://op.gg/open-source/opgg-mcp).
`league_opgg_mcp_reference.py` calls the official
`lol_get_lane_matchup_guide` method, recording actual wins/games and
the current champion-role baseline when supplied. It requires no website
scraping. The lane response does NOT guarantee a patch, queue or rank
filter, so numbers are labelled **rolling, rank/patch unverified** and are
reference-only, never automatically converted into calibrated draft points.

`tools/collect_opgg_mcp_matchups.py` performs a maximum of 6 fresh requests
by default (8 hard ceiling) and waits at least 3 seconds between calls.
It caches observations for 24 hours in the local user's AppData under
`FlexersDraftCoach/opgg-mcp` (OUTSIDE the repository). The cache and any
potentially licensed raw data must not be committed to GitHub.

Example: `python tools/collect_opgg_mcp_matchups.py --role Top --enemy Shen --ours Camille Gnar Gwen Kayle`

Cached Shen Top observations (not patch-labelled): Camille 819/1714,
Gnar 727/1440, Gwen 631/1267, Kayle 506/995.

The 3 November additional Cho'Gath Top collection (same unknown patch/rank):
- Malphite 1162/2544 (45.68%, versus 51.55% Malphite Top baseline)
- Dr. Mundo 688/1277 (53.88%, versus 50.04% baseline)
- Aatrox 859/1608 (53.42%, versus 50.03% baseline)
- Vayne 229/415 (55.18%, versus 48.62% baseline)
- Gnar 503/984 (51.12%, versus 49.11% baseline)
- Gwen 569/1078 (52.78%, versus 50.63% baseline)

These ten matchups exist only in the user's **private 24-hour local cache**;
do not commit provider samples as a bulk redistributed database. Viewing
past-the-expiry records is not the same as having current data.

**Actual app integration:** `league_opgg_mcp_reference.cached_for_enemy()`
reads the private cache **without making any network calls**. The existing
`counterpick_advice.py` attaches `opgg_reference` to verified enemy-lane
scouting, tagging in-pool versus learning choices. The selected-comp cards
show sample sizes and the warning "patch/rank unknown". This data NEVER
enters the `league_comp_engine` scorer or auto-pick decision until the
provider supplies compatible patch/rank/queue metadata and source usage
permits automated ranking.

**High-efficiency OP.GG official counter summaries:** the tool
`lol_get_champion_analysis` exposes a champion's three strongest and three
weakest sampled matchups with wins and game counts. We implemented a *strict
non-executable parser* for the official MCP's class-like compact output in
`league_opgg_mcp_champion_counters.py`, and
`tools/collect_opgg_mcp_champion_counters.py` limits use to two champion
summaries per normal run (three absolute maximum, 3s apart). Results populate
the same private, read-only pair cache as the per-opponent collector.
No bulk mirroring, and no unverified automatic matchup scoring.

Two 3 Nov ranked-mode requests yielded **12 additional reference pairs**:
Malphite Top vs Vayne (643/1026), Quinn (361/624), Jayce (4644/8197),
Aurora (177/402), Ornn (1050/2319), Singed (470/1053).
Camille Top vs Ambessa (1368/2456), Malzahar (91/163), Yasuo (755/1375),
Zac (108/241), Illaoi (559/1184), Kennen (321/681).
These are provider-returned directional samples, not patch/rank-certified
matchups. The second page includes niche/low-usage Top enemies; do not
promote a 163-game Malzahar lane to a reliable global counter.

**Identifier bug:** OP.GG expects `CHOGATH` rather than `CHO_GATH`, and
`DR_MUNDO` rather than `DRMUNDO`. The collector now normalises both.
All six attempted Cho'Gath requests initially failed before this repair,
with no bad rows saved.

These samples must be interpreted against each champion's baseline and
source uncertainty; a raw 50+% head-to-head rate alone is not proof of
a winning draft lane.

## Testing

`python -m pytest -q tests/test_otp_sources_and_bridge_sync.py`
`python -m pytest -q tests/test_opgg_mcp_reference.py`
`python -m pytest -q tests/test_counterpick_advice_integration.py`
`python tools/verify_draftcoach_ui.py`
`python tools/party_test.py --scenario smoke`
`python tools/party_test.py --scenario smoke --side red`

Preserve Rat's champion legality checks, live action ID/game ID safeguards,
Discord role assignments, the elected captain's control and existing
`CLAUDE.md` source attribution policy.

## Continuation: multi-role OP.GG and Mid specialist notes (3 November 2026)

Official OP.GG MCP small-scale private champion-counter summaries were collected
for Sylas, Akali, Vex and Lissandra Mid; Jax and Rumble Top; Lee Sin and Diana
Jungle; Kaisa and Jinx ADC; and Lulu and Braum Support. The 12 new summaries
returned 72 directional pairs. Combined with 22 previous pairs, the local
24-hour cache contains 94 observations: 34 Top, 12 Jungle, 24 Mid, 12 ADC,
12 Support, plus 14 summary metadata files. These numbers are local and
temporary, not bundled statistical coverage or patch-qualified counters.

Representative provider observations: Akali vs Lissandra Mid 42.4% (n=5339),
Lissandra vs Akali Mid 57.6% (n=5339); Sylas vs Vex Mid 43.3% (n=2182);
Lee Sin vs Gragas Jungle 44.3% (n=1447); Kaisa vs Ezreal ADC 55.1% (n=36731);
Lulu vs Leona Support 46.0% (n=9520). All are ranked-mode provider summaries
with rank, patch and exact population unverified. Do not use as draft scores.

Ten additional, short attributed Mid-language qualitative entries were
reviewed from r/akalimains, r/LissandraMains, r/sylasmains, r/QiyanaMains,
and r/vexmains. Some opinions from 2022-2026 conflict (e.g. Akali can trade
into Lissandra early while being limited later by crowd control), so the
catalogue marks them mixed rather than forcing a positive/negative verdict.
The full catalogue is now 61 directional notes and 23 source index entries.
These are historical opinions, not patch-26.21 measurements.

To audit local references without data export, network requests or game
actions, run: python tools/opgg_cache_audit.py. It reports fresh/expired
private sample counts and unversioned status by role. New unit test:
tests/test_opgg_cache_audit.py. The scouting source and pool rules remain
unchanged; known planned Mid risks can now appear in the read-only panel.

Patch 26.21 is current on 3 November 2026; 26.22 is scheduled for
4 November. Revalidate source freshness after the patch before suggesting
current-patch numerical counter advantages.


## Large-scale licensed evidence expansion — 3 November (continued)

**The user explicitly requested as much credible coverage as possible.** The
solution now has three distinct kinds of sources; do not add their record
counts together as independent current-patch counters.

### Local private sources

- The two specifically approved PocketPick 16.16 EUW 30,000-match aggregates
  were restored to this development PC's ignored `var/pocketpick/`. Their
  original 10,806 raw ordered matchup rows should never be published.
- `tools/matchup_evidence_inventory.py` calculates unique role-qualified
  coverage after alias normalisation. LoLalytics 16.20 has **1,319**
  same-role 100+-game pairings. PocketPick contributes **7,974** observed
  role-valid pairings, **1,644** with 40+ games. The deduplicated union
  of LoLalytics 100+ and PocketPick 40+ is **2,594**, and at a 100+
  threshold from either source is **1,548**. These are **historical**
  observations from different populations, NOT 26.21 matchups.
- Official OP.GG MCP cache extended through small, rate-limited, permissioned
  requests to **213 fresh pair references** (58 Top / 35 Jungle / 48 Mid /
  36 ADC / 36 Support) from **34 per-champion summary requests**, including
  earlier per-lane guide observations. All still lack attested rank/patch;
  24-hour private AppData cache, not committed and not ranked automatically.
  Do not turn this into an unlimited bulk-mirroring job. See
  `tools/opgg_cache_audit.py`.

### Open professional dataset

ChainCC at https://chaincc.lol/free/data/ explicitly releases seasonal pro
CSV archives under **CC BY 4.0 with attribution**, with no API key or rate
limit, accessed 3 November 2026. `tools/build_chaincc_matchup_index.py`
now supports `--all-years --download` for **2014–2026**. The local
`var/chaincc/` index (not GitHub) contains:

- 13 seasons, **515,595 observed game-role lane pairings** and
  **343,784 year/patch directional matchup records**, which overlap by
  time window and are not unique current-patch enemy matchups.
- New `tools/build_chaincc_duo_index.py --all-years` reads the same player
  files, joins exact game/side/patch, and indexes actual **Mid+Jungle** and
  **ADC+Support** rosters: **409,660 observed team-duo appearances** and
  **124,832 year/patch duo records**. No invented pick order or synergy.
- In 2026 separately, the year-level index contains **6,302** directional
  matchup rows and **2,309** distinct team duo rows. The latest patch
  present in ChainCC 2026 is **16.20** (the live client is 26.21 on 3 Nov).
  There are **1,040** exact-patch directional rows at 16.20, but only
  **160** have 5+ games and **none** have 20+; of 372 exact-patch duo
  rows, 51 have 5+ and only 5 have 20+. Never treat the small numbers as
  proof of champion counters or turn pro team results into SoloQ win rates.
- `league_chaincc_pro_synergy.pro_duo()` is a read-only, zero-score
  retrieval helper for season/patch duo observations. `counterpick_advice`
  attaches at most two references to guidance for the selected five,
  and `draftcoach/static/selected-comp.js` labels them **PRO REFERENCE**
  with game count and explicit patch/season information. Locked champions,
  captain election, pools, ranker, relay and Hover/Lock stay unchanged.
- The role-based matchup index and duo index are collected to ignored local
  `var/` files. The code and attribution can be pushed to GitHub, not raw
  provider exports or user-specific data. The new
  `tools/pro_evidence_audit.py` reports coverage without network calls.

### Specialist human guidance

Now **101 short directional notes** and **36 indexed source entries**. Added
forty entries spanning Kindred/Kha'Zix/Gragas/Taliyah Jungle, Yasuo/Qiyana/
Taliyah Mid, and Kog'Maw/Ezreal/Yunara ADC. Dates range from 2024–2026;
very old tips and mutually contradictory player anecdotes are labelled,
often **mixed**, and can never certify numerical draft adjustments.
Specialist link directories for Fiora, Swain and Kog'Maw item research
were indexed as navigation, not misrepresented as viewed matchup verdicts.

### Reproducible commands

- `python tools/matchup_evidence_inventory.py`
- `python tools/opgg_cache_audit.py`
- `python tools/pro_evidence_audit.py`
- `python tools/build_chaincc_matchup_index.py --all-years --download`
- `python tools/build_chaincc_duo_index.py --all-years`
- `python -m pytest -q tests/test_matchup_evidence_inventory.py
  tests/test_chaincc_duo_index.py tests/test_pro_duo_counter_advice.py`

The next high-quality numerical source would be authorised versioned Riot
Match-V5 with an approved credential, or a licensed patch/rank-filtered
provider feed. Nothing we collected gives unrestricted OP.GG bulk rights.

### Frozen Worlds 2026 snapshot from ChainCC (through 31 October)

`tools/build_chaincc_worlds_snapshot.py` filters the licensed 2026 source
`league=WLDs` and joins team + player side/game rows, without pretending
`pick1` through `pick5` are chronological. The generated, attributable
`data/league_worlds_2026_chaincc_snapshot.json` records **73 games**,
**146 team-side drafts**, **103 champions** seen in picks/bans, and
**159 distinct Mid+Jungle or ADC+Support pairings**. All 73 games were
played on patch **16.20**; the latest match in that filtered source is
**31 October 2026, 23:05:06**. This does not include games after that.
The largest pick counts are Lee Sin 24, Ezreal 22, Jarvan IV 21,
Rumble 21 and Alistar 20. The most common bans are Nocturne 53,
Vi 42, Cassiopeia 38, Renata Glasc 36 and Olaf 32. This is
**descriptive professional draft priority**, not player-pool or SoloQ
scoring authority. Rebuild after newer licensed source updates.

- The live bridge now invokes read-only `counter_advice` for a chosen
  composition even before confirmed enemy lanes, so two pro duo references
  can display pre-lobby; `counter_advice` exits immediately with
  `waiting_for_confirmed_enemy_lane` and **does not evaluate counterpicks**
  until enemies are confirmed. This never runs the external collectors.


## Correction: OP.GG complete public matchup tables are accessible (3 November 2026)

**Important:** The earlier official MCP counter collectors only retrieved
the strongest three plus weakest three by champion, and optionally three
`data.summary.positions[].counters[]` examples per role. That *was a
limitation of the MCP fields we chose*, not proof that OP.GG only publishes
six matchups. The public pages at
`https://op.gg/lol/champions/{champion}/counters/{role}`
display complete same-role opponent lists, game counts and win rates.
Example Malphite Top rendered **61 opponents on one HTML page**. We
inspected live OP.GG MCP's `tools/list`; its champion-analysis schema
includes an optional tier parameter, but **the counters and summary fields
are truncated summaries, not a full export**.

OP.GG's 26 October 2026 written support reply allowed *small-scale,
non-commercial attributed crawling*, with no separate restrictions for
that scope, but warns that request volume or usage patterns may trigger
limits and asks that traffic not become excessive. The same reply
explicitly states that OP.GG supplies no separate data feed beyond its
service and suggests the official open-source MCP. That is **not blanket
permission for a 243-page bulk mirror**. Do not bypass access restrictions,
use private endpoints or run unrestricted background scraping.

### What is really collected

A new, deliberately paced `tools/collect_opgg_full_web.py` uses **only**
public rendered champion pages, at least **3.5 seconds between new
requests**, no more than **20 fresh page requests per invocation**,
24-hour per-page cache and immediate stop on HTTP 401/403/429. The
parser validates visible champion, patch, selected tier and the complete
row structure and **fails closed** if any part differs. Wukong uses
OP.GG's `monkeyking` URL identifier.

43 public page requests succeeded (one earlier Wukong URL failed safely
and was corrected). All saved rows indicate **patch 16.21, Emerald+,
All Servers, Ranked Solo/Duo**. The private index
`var/opgg_full/index.json` has **2,238 unique directional champion/opponent
observations** across **43 champions' full pages**:

- Top: 549; Jungle: 522; Mid: 495; ADC: 304; Support: 368.
- **2,210** observed pairs have 100+ games reported by OP.GG.
- **1,868** observations fall within our older 12,128-pair role-universe
  denominator; the rest concern OP.GG-listed additional lane opponents.
  Do not call 2,238 / 12,128 the same-catalogue coverage percentage.
- These rows **overlap** earlier 213 rolling official-MCP observations
  and historical LoLalytics/PocketPick pairs. Never sum them into an
  inflated independent matchup count.
- A one-page probe confirmed `?tier=platinum_plus&region=global`
  returns a different page explicitly marked **Platinum+**, so other
  rank-scoped public statistics appear possible. We have **not** run a
  broad Platinum+/Gold+ collection or integrated that tier selector.

The private full counters folder was copied to the installed app at
`INTent Draft Coach/var/opgg_full/`; the source and installed
`index.json` SHA-256 hashes matched. No original executable was
restarted or overwritten. The Web collector and its tests are committed
to GitHub but **not the private full data**.

### How the app uses it safely

`league_opgg_full_reference.full_for_enemy()` is **read-only** and
never calls OP.GG in champion select. It checks exactly the requested
live client patch (e.g. 26.21 => source 16.21), Emerald+ tier,
Ranked Solo/Duo, All Servers, 100+ games, and collection age
(7 days maximum for current-patch display). A changed patch gives
no matches; it NEVER treats an older patch as fresh.

`counterpick_advice` appends the planned champion's OP.GG
head-to-head stats and up to four alternative current-patch observations
in `opgg_full_reference`, ranking **in-pool playable** candidates
ahead of **off-pool LEARN** ideas for display. The small Draft Coach
selected-comp panel labels them OP.GG STATS, patch/tier and sample size.
The unversioned MCP three-and-three results are secondary fallback.

**No OP.GG full-page figure is inserted into the automatic
`league_comp_engine` scoring, eligible auto counter swaps, or
Hover/Lock.** Raw matchup win rate alone does not isolate counter
advantage from champion base strength, enemy base strength, comp, and
player mastery. Before auto-scoring, add a calibrated, same-patch
expected-win-rate residual with sample shrinkage and role ownership
checks, then test across both sides and five instances.

### Reproducible audits and further work

- `python tools/opgg_full_audit.py` shows coverage, source-patch
  counts and 100+ samples without network calls.
- `python tools/collect_opgg_full_web.py --role Top --champions
  Malphite Shen Gwen --expected-patch 16.21` collects at most three
  new pages if not already cached.
- `python -m pytest -q tests/test_opgg_full_web.py
  tests/test_opgg_full_advice_ui.py` checks parsing, alias handling,
  patch segregation and no forced picks.
- `python tools/opgg_cache_audit.py` remains the *separate* official
  MCP top-three cache audit.

For a genuine **complete 243-champion-role page acquisition**, seek
explicit guidance/permission from OP.GG for one-time and ongoing
bulk-scale volume and caching, or an authorised complete
patch/rank dataset. Continue small, respectful, opt-in collection only
within the scope they already approved. Never claim the full matrix
is already available locally.


## Live enemy counter pulls and coordinated counter-draft (3 November 2026)

**User decision policy:** Once an enemy is *locked* and its lane is established,
look at our assigned player for that role, their actual submitted role pool,
the planned champion's job, relevant current-patch counter performance,
and the whole team. Consider a second champion swap to replace lost frontline
or other structure (e.g. Ornn Top + Lee Sin Jungle -> Sylas Top + Skarner
Jungle versus Malphite Top). Treat uncommon enormous counters, such as Sylas
stealing Malphite R, differently from small raw win-rate swings. Carry-top
Vayne is credible only for a skilled player who submitted Vayne Top; never
force Jezz or any other player onto an unfamiliar carry just because OP.GG
reports a favourable statistic.

Implementation is inside the **existing shared live draft backend**, not an
independent second ranker:
- `league_draft_backend.recommend_live_drafts` still generates pool-legal,
  player-assigned candidates and scores whole-team composition, ranked
  comfort, bot lane and mid-jungle synergy.
- `adaptive_draft.select_adaptive_comp` may now select a coordinated TWO
  champion replacement from those existing candidates if at least one changed
  role is a confirmed enemy counter; the second change preserves a team job.
  No actual role-owner changes. No actual locked pick changes. A frontline
  role lost by the counter must be explicitly compensated, with team, bot and
  jungle-mid scores protected. Normal single-seat adaptation remains legal.
- `league_comp_engine.DRAFT_SPECIFIC_COUNTERS` is reused for the already
  documented Sylas-versus-Malphite interaction. No new duplicated rule table.
- A strong, **baseline-adjusted** OP.GG current-patch Emerald+ signal is
  eligible as an additional cautious swap input only with both original
  and candidate own-champion baselines, each >=10,000 role games and
  >=500 head-to-head games, from the same patch/rank/queue. Per-role
  baseline = sum(head-to-head WR * games) / sum(games) over the full opponent
  table. A 700-game confidence shrinkage and 0.26 point-per-percentage-point
  upper-bounded conversion prevent naive high-WR autopicking. Ordinary raw
  WR alone does not qualify. Observational, not a proof of causality.
- `league_opgg_full_reference.full_for_enemy` now uses **one enemy page**
  to infer each opponent's complementary head-to-head WR; a directly
  collected own-champion matchup takes priority when available. Reverse
  estimates and their page provenance are labelled.
- The live bridge frees a *possible* Top/Jungle compensation pair after a
  confirmed Top or Jungle reveal (or Mid/Jungle for a Mid reveal), rather
  than hard-freezing all roles not directly countered. The shared scorer
  decides whether either or both switches are actually better.
- When the chosen composition changes, the live chosen-status panel now
  receives `adaptive_reasons` for both the counter and its compensating
  role, rather than just saying "swapped".
- `league_opgg_live_fetch.LiveCounterCollector` schedules **background**
  HTTPS GETs for confirmed enemy roles only, prioritising one complete
  enemy table and at most two submitted role-pool alternatives plus the
  planned champion. Hard ceilings: 15 new page requests per UTC day per
  captain PC, 5 per lobby, 4 seconds apart, 24h cache, stop immediately
  on 401/403/429. Persistent counters survive app restarts; optional off
  via `DRAFTCOACH_LIVE_OPGG=0`. Only the elected captain fetches in a
  shared lobby; follower apps use shared guidance. Never fetch on hover,
  in tests, or synchronously on the score/UI thread. No unlimited scraping.
- Private OP.GG full pages and indexes remain ignored under
  `var/opgg_full/` in the dev checkout and installed folder, not GitHub.
  The collector index now records per-champion weighted win-rate baseline
  without any additional HTTP requests.
- Flex-role ambiguity stays conservative: `_locked_enemy_roles` accepts
  trusted role annotations and unique-role inference but does not guess
  ambiguous enemy lane roles. **A captain-controlled manual role override
  is still a future UI task** and must be lobby-scoped and synchronised
  through the relay before being used for scoring.

Tests:
`tests/test_adaptive_pair_swaps.py`,
`tests/test_opgg_live_fetch.py`,
`tests/test_opgg_full_web.py`,
`tests/test_opgg_full_advice_ui.py` and existing Discord adaptive,
live bridge and five-player simulator tests.

Source permission reminder: OP.GG's reply allows careful small-scale,
attributed, non-commercial page visits and warns against excessive
traffic. This is **not** permission for an unrestricted full-site crawl.



## All-lane coordinated team-role compensation (3 Nov 2026 — latest decision)

**Updated after user clarification:** A counter pick does not force its second
team-structure adjustment into Jungle. The second champion change can come
from **Top, Jungle, Mid, ADC/Bot or Support**, whichever assigned player's
role pool and composition scorer can support. Examples:
- Ornn Top -> Vayne Top into an appropriate counter, while Orianna Mid
  -> Galio Mid adds back a legitimate frontline/engage champion
- A similar Vayne Top counter can instead ask Lulu Support -> Braum Support
- Orianna Mid -> Zed or Yasuo Mid can be paired with Jinx Bot -> Ziggs Bot
  to keep magic damage; the bot player must actually own Ziggs in their ADC
  role pool and the resulting bot duo must remain viable
- **If Zed or Yasuo is already locked in League**, that lock is immutable.
  A separate repair path finds a one-seat magic-damage replacement elsewhere
  without needing a confirmed enemy lane; no change if no lost structural
  job or if the proposed companion is itself locked

Code:
- `adaptive_draft.py`: score and player ownership gates, actual locks,
  frontend count, role-based AP/AD mix, strong matchup evidence, and
  explanations of the job being restored; no more than two champion
  changes. Minor bot-duo score loss can be tolerated only if the new
  overall five scores sufficiently well and the bot lane remains viable.
- `adaptive_compensation_search.py`: **at most four independent
  secondary-lane branches** from the same shared, legally-scored
  `recommend_live_drafts` brain, each freezing the other planned roles.
  A single background worker prevents UI blocking, caches results against
  the exact champ-select state, and invalidates the bridge response cache
  when ready. A newer state cannot consume stale candidates.
- `tools/intent_live_draft_bridge.py`: normal chosen draft resolved first,
  then only the elected captain runs optional all-lane companion search
  after a confirmed enemy role or a newly locked actual champion conflicts
  with the selected five. Do not run this search for test simulations or
  followers. Never undo a live champion lock. Manual enemy-role overrides
  remain a separate future feature.
- `draftcoach/static/selected-comp.js`: shows search status, the two
  changed lanes and the chosen-status reasons once ready.
- `build_exe.py`: packages the new `adaptive_compensation_search.py`.

**Safety:** These are CHAMPION changes, not player role reassignment. Every
alternative comes from the existing shared backend and its submitted role
pool, comfort and five-man scoring. Do not force a high-stat Vayne Top on a
support main who cannot play it, or turn every lane into high-variance
counter-pick musical chairs. Preserving overall comp > small matchup swing.


## Early enemy ADC lock: Bot mage counter with flexible physical-DPS replacement

A **confirmed enemy ADC lock** now functions as a normal counter-draft anchor,
even before the remaining enemy team is known. The player assigned to our ADC
seat can choose a strong statistical mage-Bot counter **only if it is already
in their explicitly submitted ADC role pool**. The solver then searches any
other unlocked lane (Top, Jungle, Mid, Support) for a player-legal physical
damage replacement, using the same five-player composition score:

- Example layouts checked with synthetic proven-counter fixtures:
  Hwei Bot + Graves Jungle; Vel'Koz Bot + Kindred Jungle;
  Swain Bot + Tristana Mid; Swain Bot + Vayne Top.
  These are **conditional legal examples, not assertions that every mage is
  a hard counter to every marksman**.
- The champion itself may be off-meta in the reference catalogue. The real
  `recommend_live_drafts` backend passed tests with Hwei, Vel'Koz and Swain
  Bot as the assigned player's submitted role pick. Neither Hwei nor Vel'Koz
  becomes an unrequested option for players who did not submit them.
- `tools/collect_opgg_full_web.py --declared-offmeta` permits public Bot-page
  data lookup for an explicitly confirmed enemy champion or an actual owner's
  submitted Bot pick, after validating it exists in the champion catalogue.
  Live captain fetches use this flag only for confirmed enemy roles, planned
  champion and up to two submitted pool alternatives. The CLI default still
  excludes off-meta role candidates.
- Real 3 Nov OP.GG 16.21 Emerald+ complete Bot pages were independently
  retrieved for Hwei, Vel'Koz and Swain, **38 opponent rows per page**.
  Now **50** private full counter pages and **2,575** page observations across
  five roles; all patch 16.21. Never check these rows into GitHub.
- `adaptive_draft._damage_roles` now counts meaningful physical/magic
  sources (`ratings.damage >= 2`) rather than treating the incidental damage
  of a full tank/support as equivalent to an ADC. A mage replacing Jinx Bot
  can be paired with a legitimate AD Mid, Top or Jungle; the full team
  must retain viable frontline, bot pairing and mid-jungle identity.
- We do **not** declare every raw favourable Bot win rate a hard counter.
  Example: Hwei vs Caitlyn observed 54.64% in 1,305 games, with Hwei's
  own overall Bot baseline 53.339%. Jinx vs Caitlyn is 52.39% in
  20,677 games, against Jinx's own 51.185% Bot baseline. Both outperform
  their normal expectation by ~1.2–1.3 percentage points: little
  differential counter advantage. This is why same-patch baseline adjustment,
  sample shrinkage, player skill, and compensated team shape are required.
- A **real locked ADC pick** prevents bot replacement regardless of enemy
  matchup score; the optimiser does not attempt to undo it. Flex-role
  enemy picks whose ADC position cannot be inferred still require explicit
  confirmation in the future manual enemy-role UI rather than guessing.

Tests: `tests/test_mage_bot_compensation.py` has synthetic four-lane,
lock, sample-quality and real-backend explicit off-meta pool checks;
`tests/test_opgg_full_web.py` prevents off-meta page fetch unless caller
explicitly marks the confirmed/declared context.
