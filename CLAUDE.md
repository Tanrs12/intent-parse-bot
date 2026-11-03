# CLAUDE.md — INTent / Flexers Draft Coach handoff

**For Rat (williambutterworth1), Claude Code and future contributors.**
Read this before changing the app, UI or relay. Preserve Rat's existing architecture.

## Do not fork the drafting brain

- GitHub `Tanrs12/intent-parse-bot` is the repository of **code and bundled data**,
  NOT the live counterpick server. The deployed Discord bot and desktop app use
  that code and refreshed local caches; `bot.py` owns the shared team relay.
- Shared ranking authority: `league_flex_core.py` + `league_comp_engine.py`,
  accessed via `league_draft_backend.recommend_live_drafts()` and `plan_game()`.
  Do not build a second JS scoring engine.
- Rat's `draftcoach/gameplan.py` builds ten plans from the same engine used by
  Discord; `draftcoach/static/coach.html` sends `rec.control` through
  `tools/intent_live_draft_bridge.py` `/api/control` to the existing relay.
- `team_sync.py` validates team choices and multi-champion
  `options`/`learn_options`. The small panel (`overlay.html`) shows the NEXT
  actionable pick/ban; `selected-comp.js` shows the original chosen comp.
- Don't replace captain election, relay/recovery, League Hover/Lock actions,
  player pools, trades, My Champs, Stats, Draft Studio or window controls.

## 3 November 2026 visual + counter integration

- Main Game Plan: side-by-side **Blue** and **Red** pick/ban plans, independent
  coloured borders, pick order BEFORE bans, champion portraits for all owned
  alternatives plus separately labelled learning options.
- Full draft order is B1,R1,R2,B2,B3,R3,R4,B4,B5,R5. The Game Plan
  panels now show ONLY our five actual pick slots (B1..B5 or R1..R5),
  not empty enemy placeholders or invented enemy champions.
  Additive CSS: `draftcoach/static/mm/gameplan-enhancements.css`.
- Team-selected draft: `Send to team` checks returned control against the exact
  chosen five; do not claim all five PCs received it on HTTP 200 alone.
  The small panel never shows an unrelated automatic five as the active choice.
- `counterpick_advice.py` is READ-ONLY. The live bridge attaches
  `guidance.counter_advice`. Only evaluate *confirmed enemy roles*, not enemy
  hovers or speculative flex assignments. Compare in-pool alternatives against
  an original planned champion using the existing calibrated lane matchup and
  `score_comp()`. Require 100+ sampled games and a meaningful calibrated
  advantage while retaining team composition quality.
- `PLAYABLE` means actually in the assigned player's submitted role pool;
  `LEARN` means **not in pool**, never an automatic draft pick / Hover / Lock.
  A League-locked champion is immutable. The existing adaptive engine alone
  chooses legal in-pool live swaps.
- No verified counter data => explicitly explain the coverage gap, do not
  hallucinate a winning lane or an exact win-rate percentage.

## Evidence and data provenance

- `data/league_lane_matchups.json`: bundled LoLalytics D2+ patch 16.20 data
  with only EIGHT covered enemy champions per role. No Cho'Gath Top rows.
- `league_matchup_universe.py` expands COUNTER SCOUTING to every champion in
  `data/league_role_champions.json`, currently 12,128 valid ordered non-mirror
  same-role pairs. This does NOT create 12,128 statistical observations.
  Run `python tools/matchup_coverage.py` to measure the real evidence coverage.
  For locally permitted PocketPick aggregates, also run the tool with
  `--pocketpick-dir <private-folder>` to report patch-16.16 observed/40+/100+
  pair counts. Never commit or publish the proprietary raw JSON files; the app
  already reads them from its own `var/pocketpick/` if installed.
- `data/league_curated_counter_scout.json` contains four manually reviewed
  Shen Top examples with two cited current-patch sources each. Their
  `favourable`/`mixed` verdicts are QUALITATIVE SCOUTING ONLY, not win rates
  or scored counter picks. Web sources disagree on Gnar/Kayle, so never
  quietly convert these notes into a guaranteed lane win.
- `counterpick_advice.py` scans the entire role catalogue: owned options can be
  displayed as SCOUT, off-pool options as LEARN. Both show comp-fit tradeoffs.
- `tools/intent_live_draft_bridge.py::_scouting_lead_with_pools()` MUST attach
  the actual chosen players' submitted role pools by `player_ids` before
  counter advice: the display cards have only names and omit pools. Without
  this, Gnar for Cian Top was falsely marked LEARN and string player names
  could crash live advice. Use Discord signup ownership, not match-history
  inference. Never change the legal draft from this read-only enrichment.
  Do not move these options into the legal `picks` list unless the common
  engine has enough permitted numerical evidence and the player owns them.
- Qualitative specialist collection: `data/league_otp_matchup_notes.json`
  contains 101 short, attributed qualitative matchup observations indexed
  against 36 source entries (Top, Jungle, Mid and ADC). `data/league_otp_video_chapters.json` contains 47
  Gwen-specialist YouTube chapters with NO transcribed verdicts. The tier
  opinions can reorder SCOUT and LEARN suggestions in the presentation layer
  but never impersonate a current-patch win rate or override a player's pool.
  See `docs/draftcoach/MATCHUP_EVIDENCE_COLLECTION_2026-11-03.md`.
- `Drafts: Auto` controls which live ranking is displayed; it does not block
  the relay selection. A chosen comp is displayed pre-lobby. A Send to team
  action must receive the exact-five acknowledgement and update immediately;
  do not treat a plain HTTP 200 as evidence of five-client delivery.
- `league_opgg_mcp_reference.py` and `tools/collect_opgg_mcp_matchups.py`
  support *small-scale private* collection via OP.GG's official MCP, under
  the provider's written non-commercial attribution permission. Cache is
  outside GitHub, under the PC's AppData; 24-hour freshness, 3s delay,
  maximum 6 fresh requests per normal run. `cached_for_enemy()` is
  explicitly **read only** (never issues an HTTP request from the app).
  `counterpick_advice.py` returns a separate `opgg_reference` with owned
  or learning candidates; `selected-comp.js` shows observed win rates,
  sample sizes, and a patch/rank unknown warning.
  Do NOT assume rank/patch from OP.GG lane-guide responses or translate
  them into statistical draft scores without enough versioned metadata.
  The 3 Nov Cho'Gath collection has Malphite, Dr. Mundo, Aatrox, Vayne,
  Gnar and Gwen, while Shen has Camille, Gnar, Gwen and Kayle.
  Keep these **private**, never commit raw provider cache data. The separate
  `league_opgg_mcp_champion_counters.py` parses OP.GG's top-three
  strong/weak per-champion lists **without eval** and the
  `tools/collect_opgg_mcp_champion_counters.py` collects at most two
  summaries in its normal run, three max. This yields six matchup
  references per call. The results go into that same private cache,
  and must not be confused with calibrated current-patch data.
  Details and source limits are in the collection report.
- Additional 3 Nov private OP.GG MCP scouting: 12 small, rate-limited
  champion-summary requests added 72 reference pairs across Top, Jungle,
  Mid, ADC and Support. Total private cache: 94 pair references, all
  unversioned, and 14 summary metadata files. Never commit the cache or
  let these rolling rates change automatic picks. Run
  `python tools/opgg_cache_audit.py` to check local freshness/role counts
  without contacting any provider.
- The qualitative catalogue now also includes ten attributed Mid-specific
  human matchup opinions (Akali, Vex, Sylas, Qiyana, Lissandra). Some are
  deliberately labelled mixed due to disagreements about lane vs teamfight.
  A planned unfavourable matchup is a warning, not a lock/hover instruction.
- `league_riot_ranked_sample.py` and `tools/collect_riot_ranked_matchups.py`
  support permissioned current-patch Riot Match-V5 EUW samples, IF a valid
  authorised API key and data collection exist. Keep credentials server-side.
- `league_pocketpick_history.py` is a separate *older* permissioned historical
  source. `league_community_evidence.py` is qualitative, not numeric proof.
  Never bulk scrape another provider's proprietary tables without permission.
  See `docs/multi-source-matchup-research.md` for licensing/coverage details.
- User scenarios (Malphite -> Cho'Gath): Mundo for Kayla, Aatrox for Dyl, Vayne
  for Cian are *examples to investigate*, not verified current-patch counters
  or guaranteed ownership. The actual submitted pool must decide PLAYABLE.
  An off-pool candidate is only LEARN until explicitly submitted.

## Integration tests and design preservation

- Preserve Matchup Master `mm/tokens.css`, existing element IDs and anchors
  expected by `tools/patch_flexers_intent_ui.py`. Keep the floating panel's
  decision-first action, 430px constraints, manual/League states and actions.
- Run `python tools/verify_draftcoach_ui.py`, `node --check` for affected JS
  and `python -m pytest tests -q`. Five-player sync simulation:
  `python tools/party_test.py --scenario full` and `--side red`.
- Added tests in `tests/test_counterpick_advice_integration.py`. Use synthetic
  fixtures to verify source confidence, player ownership and no invented
  enemy roles. Never kill or overwrite a running user's desktop app.
- Before pushing, compare with latest `main` and inspect Rat's newer commits;
  only release after CI tests the packaged exe. Keep updater data in `var/`.

## Documents and collaboration

Read `FLEXERS_DRAFT_COACH.md`, `docs/draftcoach/APP_DESIGN.md`,
`docs/draftcoach/ROLLOUT.md`, and `docs/draftcoach/PARTY_TEST.md`.
Rat's Game Plan, Draft Studio and panel are the foundation, not components
to overwrite. Update this handoff whenever changing the above contracts.


## High-volume evidence handoff — 3 November 2026 continuation

- Full source and provenance audit: `docs/draftcoach/MATCHUP_EVIDENCE_COLLECTION_2026-11-03.md`.
  The **101 qualitative observations** (36 indexed sources) are short,
  independently phrased and non-scoring; several are conflicting or old.
- Private local PocketPick 16.16 archive is installed in this dev checkout's
  ignored `var/pocketpick/`. `tools/matchup_evidence_inventory.py` reports
  7,974 valid observed pairings and 2,594 deduplicated historical 40+
  pairings when combined with the bundled LoLalytics 16.20 100+ table.
  **Never commit raw PocketPick data** or call it current high-elo.
- The limited OP.GG official MCP cache reached 213 rolling matchup
  references from 34 champion summaries plus prior direct lane requests.
  All private, rank/patch unverified, AppData TTL 24 h. Source rights
  remain **small-scale noncommercial**, not unrestricted bulk fetching.
- **Openly licensed CC BY 4.0 ChainCC** pro data was imported locally for
  2014–2026 (13 seasons): 515,595 observed pro lane pairings and
  343,784 year/patch directional records. Data folder `var/chaincc/` is
  ignored; use `tools/build_chaincc_matchup_index.py --all-years --download`.
  Actual source: https://chaincc.lol/free/data/. Attribute ChainCC.
- The new `tools/build_chaincc_duo_index.py --all-years` indexes
  409,660 pro appearances of Mid+Jungle or ADC+Support pairs, producing
  124,832 year/patch rows. `league_chaincc_pro_synergy.py` supplies
  **read-only**, zero-draft-points reference lookups; `counterpick_advice`
  carries only two duo references to `selected-comp.js`, where they show
  as labelled PRO REFERENCE. The latest ChainCC pro patch is 16.20, not
  the live client 26.21; use a clearly marked season fallback only.
- Do not extrapolate match outcome from pick1..pick5 as draft chronology,
  or confuse a pro pair's win rate with a controlled synergy benefit.
  Preserve Rat's shared backend, the owner-submitted champion pools,
  legal switch gates, existing relay, Hover/Lock, 10-unique-draft rule
  and five-player sync. This research cannot automatically swap champions.
- Smoke/UI/build tests are required before release; see exact pytest
  commands and source notes in the collection report.

- The World Championship 2026 frozen snapshot was rebuilt from licensed
  ChainCC `league=WLDs`: `data/league_worlds_2026_chaincc_snapshot.json`
  contains 73 games through 31 Oct 2026, 103 champion pick/ban counts and
  159 professional duo pair records. It is an independent **read-only**
  historical snapshot; never infer draft pick order, patch 26.21 samples
  or force a champion from it.

- The live bridge now invokes read-only `counter_advice` for a chosen
  composition even before confirmed enemy lanes, so two pro duo references
  can display pre-lobby; `counter_advice` exits immediately with
  `waiting_for_confirmed_enemy_lane` and **does not evaluate counterpicks**
  until enemies are confirmed. This never runs the external collectors.


## Critical OP.GG full-counter correction — 3 November 2026

The OP.GG official MCP `lol_get_champion_analysis` selected fields
`data.strong_counters[]`, `data.weak_counters[]` and
`data.summary.positions[].counters[]` return only top-three summaries.
**This does NOT mean OP.GG has only six matchups.** Its publicly displayed
`/lol/champions/{slug}/counters/{role}` page shows the full opponent table,
win rate, games, visible patch and rank. We tested Malphite Top with 61 rows
on patch 16.21 Emerald+. `tools/collect_opgg_full_web.py` now fetches and
validates such pages cautiously, no more than 20 new HTTP requests per run
and >=3.5-second gaps. It fails closed on any page-layout change or
access denial. Wukong's page slug is `monkeyking`.

43 full pages yielded **2,238** distinct directional observations
(2,210 with >=100 games, 1,868 within the existing role-universe).
They are stored PRIVATELY at ignored `var/opgg_full/` in the dev checkout
and the installed `INTent Draft Coach/var/opgg_full/` folder, with matching
index hashes. **DO NOT commit raw OP.GG page data**, conflate it with the
separate 213 official-MCP cache references, or claim all ~12,128 possible
role pairings have been collected.

`league_opgg_full_reference.py` loads one local indexed file without
network access and accepts only exact current client/source patch,
Emerald+, All Servers, Ranked Solo/Duo, at least 100 games, age <=7 days.
`counterpick_advice.py` attaches `opgg_full_reference` statistics to
confirmed enemy lanes, and `draftcoach/static/selected-comp.js`
shows them as OP.GG STATS with patch/tier labels. This **does not**
change the legal pick scorer, captain sync, role locks or Hover/Lock.
Never auto-swap champions from raw matchup WR without a calibrated
expected-WR residual, confidence shrinkage, player pool and team-fit gates.

OP.GG explicitly permitted small-scale noncommercial attributed crawling
in their 26 Oct 2026 email but warned against excessive traffic, and
does not grant unrestricted bulk export. A single public query verified
`?tier=platinum_plus&region=global` displays Platinum+ rows. Future work:
explicit volume confirmation for full ~243-page collection, then a
patch/rank-aware, permissioned refresh process; separate Gold/Platinum
data and calibrate rankings after testing.

Run `python tools/opgg_full_audit.py` and review
`docs/draftcoach/MATCHUP_EVIDENCE_COLLECTION_2026-11-03.md` for exact
counts, source restrictions, tests, and resumption plan.


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


## 3 Nov 2026 — Source-separated champion research expansion
Added a bounded small-scale OP.GG public patch 16.21 page refresh: now 67 private champion-role pages, 3,453 directional rows; 3,393 >=100 games. Private data stays in var/opgg_full; future patch 26.22 requires *new* sources, not aliasing stale WR. `league_pocketpick_history.historical_for_enemy` provides all sampled older PocketPick 16.16 EUW Silver–Platinum role pairs as reference only. `data/league_onetricks_matchup_notes.json` contains 43 brief, source-attributed high-Elo authored qualitative tips, zero draft points. `league_pro_order_context` is the 2026 LoL-Draft / Leaguepedia CC BY-SA historical 26.13–26.17 pick-order index from 787 primary-tier matches, 5,359 later-pick relationships, *not role-attested*. `counterpick_advice` and selected-comp UI present all reference classes distinctly without changing player pools or auto scores. Counts-only per-champion ledger: `docs/draftcoach/CHAMPION_EVIDENCE_LEDGER_2026-11-03.json`; detailed sources, permissions, known Corki Mid data gap and next tasks: `docs/draftcoach/RESEARCH_COVERAGE_2026-11-03.md`. `build_exe.py` must include league_pro_order_context.py. New research source data in var/leaguepedia_drafts remains private/ignored. OP.GG permission small-scale only; Onetricks direct fetch yielded 429; do not bypass.
