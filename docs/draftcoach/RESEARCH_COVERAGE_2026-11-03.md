# Draft Coach research expansion — 3 November 2026

## Outcome
A source-separated, attributed reference pool has been built for all 172 champions in the currently supported five-role catalogue. This is **not** a complete current-patch statistical matchup matrix; coverage and sample thresholds vary considerably by champion and role. No data is invented to fill a gap. Only current exact-patch statistical evidence that passes the existing match-sample, baseline and player-pool gates can influence a coordinated counter-draft change.

### Counts after manual bounded refresh
| Data | Validated amount | Intended use |
|---|---:|---|
| OP.GG public 16.21 Emerald+ All-Servers Solo/Duo | **67 champion-role pages, 3,453 directional page observations**, 3,393 with >=100 games | Up-to-date lane statistics when exact patch, rank, role and baseline match; still observational, not proof of counter causation |
| OP.GG observations inside our *currently supported* role catalogue | 2,823 role directions; 2,799 with >=100 games | Current-patch qualified coverage denominator |
| PocketPick permitted EUW Solo/Duo 16.16 Silver–Platinum | 30,000 historical ranked games; 10,806 provider matchup rows; **8,100** valid role directions (1+ sample); **1,648** with >=40 games | Labelled historical human scouting; not automatically projected into Emerald+ 16.21 |
| Distinct role-directions with current >=100-game OP.GG OR *any* old PocketPick sample | **8,510 / 12,128** possible (~70.2%) | Observed reference presence, NOT high-confidence present-day coverage |
| Human-written high-Elo Onetricks guide tips added | 43 distinct qualitative positions from 3 guide authors | Source-attributed subjective lane tips, no numerical draft points or copied guide |
| Existing curated champion-main tips | 101 separate subjective notes | Supplemental scouting and learning ideas, not unearned player mastery |
| LoL-Draft ordered professional 2026 picks | 787 complete primary-tier matches (patches 26.13–26.17); **5,359** directed champion pair records where a pick happened *after* an enemy reveal | Draft chronology only. Not same-lane counter evidence; does NOT include 2026 Worlds on patch 26.20 |
| Earlier ChainCC pro pairs | Existing independently imported same-team professional duo records | Co-occurrence and comp ideas, not same-lane causal interaction |

The 172 currently supported champions are the published role catalogue's distinct names. PocketPick has 173 historic champion keys but some are name aliases or differ in historical role, so 173 does not imply 173 current supported champions. One exact role evidence gap remains **Corki Mid**: PocketPick Corki games are Bot-only; the OP.GG Corki Mid public page returned fewer than four accepted rows and the strict parser correctly rejected it. No source evidence was guessed or transplanted from Bot to Mid.

### New OP.GG pages
Added Top: Rumble, Darius, Sett. Jungle: Kindred, Viego, Taliyah, Nunu & Willump. Mid: Zed, Ryze, Taliyah. Bot: Draven, Varus, Ziggs. Support: Rakan, Nami, Pyke, Renata Glasc. The source fetcher rejects an unrecognized page structure, unexpected patch, unintended rank, or 401/403/429. Two requests for renamed champions originally failed safely; corrected Nunu & Willump and Renata Glasc slug/title aliases. Manual batch of 15 pages was deliberately capped, with only a handful of additional targeted debug requests. No unbounded crawling.

### Research quality rules
1. **Do not double count:** OP.GG page rows, PocketPick archived matchups and old LoLalytics samples can represent overlapping champion pairs; their sample sizes are not additive. Even OP.GG direct and reversed enemy-page observations are not independent.
2. **Do not mistake observations for causality:** Higher unadjusted WR can reflect baseline champion strength, team compositions, ELO skew, lane swap, banned opponent pools or selection effects. Use own-champion same-population baseline adjustment, minimum samples and shrinkage, and prefer actual player mastery and team composition over a small matchup advantage.
3. **Do not force off-pool picks:** Off-pool champions are labelled LEARN; if a user does not actually play Vayne Top, mage Bot or an unusual jungle role, that champion is not made eligible by any source tip.
4. **Do not turn one-trick guide tiers into statistics:** An author rating Vayne vs Malphite S+ means this author finds it very difficult, not a verified win-rate gap. The latest guide notes are concise original paraphrases with author and source link, never raw guide text or inferred population metrics.
5. **Do not turn chronological pro picks into lane counters:** LoL-Draft records what was already revealed when a champion was selected, but does not preserve the confirmed positions for these pairs. It is historical draft precedent, not verified response to a specific lane. Source: [LoL-Draft by DeusExMachina1993](https://github.com/DeusExMachina1993/LoL-Draft), adapting Leaguepedia CC BY-SA 3.0 under CC BY-SA 4.0. 2026 public corpus last match August 31.
6. **Do not refresh future patches by relabelling data:** 26.22 is scheduled for 4 November 2026. Source 16.21 must no longer count as same-patch evidence once Draft Coach switches to 26.22; older notes may remain clearly historical.

### Integration
- `league_pocketpick_history.historical_for_enemy` indexes the entire permitted old aggregate by enemy champion and exact lane, exposes sample size, actual older rank, source and age, and is read-only.
- `league_matchup_universe.scout_note` includes new one-trick author judgments beneath existing reviewed multi-source notes, above unverified anecdotes.
- `league_pro_order_context.selected_after` loads a local, licence-attested, source-labelled draft chronology index and never contributes automatic pick points.
- `counterpick_advice.counter_advice` includes separately named `pocket_reference`, `opgg_full_reference`, `pro_order_reference` and qualitative `scout` evidence for the actual owner's pool, without rewriting pool eligibility or scoring.
- `draftcoach/static/selected-comp.js` visibly distinguishes current OP.GG, historical PocketPick, ordered pro context, owned picks and LEARN-only off-pool ideas.
- `tools/champion_evidence_ledger.py` maintains `docs/draftcoach/CHAMPION_EVIDENCE_LEDGER_2026-11-03.json`, a per-champion counts-only audit containing no private provider rows. All game-level Leaguepedia rows stay in `var/leaguepedia_drafts/` and are not committed.
- Packaging: `build_exe.py` needs `league_pro_order_context.py` included as an engine data module, plus `data/league_onetricks_matchup_notes.json` through existing engine/data.

### Source permissions and next work
- OP.GG Oct 26 support reply: *small-scale noncommercial public-page access with attribution*, subject to rate limiting. Not a licence for a full 200+ page bulk crawl. Request explicit approval for a comprehensive high-volume export or low-frequency complete patch refresh.
- PocketPick permission covers the user's local personal noncommercial usage; the private aggregate is not committed to GitHub.
- Onetricks.gg: direct automated access returned HTTP **429** after an isolated page attempt; stopped. Do not circumvent or mass-copy guides. Link to author-owned pages; future additions should be hand-reviewed and compactly paraphrased with rank/patch provenance.
- LoL-Draft's open attribution and CC BY-SA 4.0 terms must be retained for derived professional draft-order context, and its historical end date must never be confused with Worlds 2026 draft order.
- Future improvements: captain-controlled manual enemy role confirmation, vetted live same-lane counter timing, renewed private OP.GG scrape permit for deeper coverage and tier-specific samples, post-26.22 patch refresh with strict provenance, actual champion-select outcome validation.
