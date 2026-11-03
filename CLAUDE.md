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
  contains 51 short, attributed human/reviewed matchup opinions indexed
  against 15 sources. `data/league_otp_video_chapters.json` contains 47
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
