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
- **51 curated, short, individually attributed directional notes** stored in
  `data/league_otp_matchup_notes.json`, from Reddit specialist discussion,
  public counter summaries and longer champion-main work. This is not a
  licensed win-rate export.
- **15 research index sources** (including LoLMatchups.GG OTP pages, Gnar/Kayle
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

First cached Shen Top observations (not patch-labelled): Camille 819/1714,
Gnar 727/1440, Gwen 631/1267, Kayle 506/995. These must be interpreted
against champ-level base win rates and source uncertainty; 50+% head-to-head
alone is not proof of a winning draft lane.

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
