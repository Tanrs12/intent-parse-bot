# Flexers multi-provider draft evidence (19 October 2026)

The draft engine uses **multiple evidence classes** but NEVER conflates pro-play pairing rates with Solo Queue champion matchups. All live inputs must record patch, scope, sample count and provenance. The player's deliberately submitted champion pool and whole-team win condition outrank noisy source statistics.

## Source register

| Provider | Data we want | Automated ingestion today | Status and reason |
| --- | --- | --- | --- |
| [LoLalytics](https://lolalytics.com/) | Large-sample, patch- and rank-filtered Solo Queue role win rates, counters, delta, synergies | **No new ongoing collection** | The prior limited 16.20 snapshot remains available. Its [private API](https://a3.lolalytics.com/) explicitly prohibits third-party reuse; request a permitted feed/license. |
| [League of Graphs](https://www.leagueofgraphs.com/) | Independent high-volume 2-day counter and pairing stats | **No** | Its [terms](https://www.leagueofgraphs.com/terms-of-use) expressly prohibit automated querying/scraping without written permission. Public site remains an excellent manual cross-check. |
| [LOL.PS](https://lol.ps/en/datastudio/introduction) | GM+ counter analysis, duo synergy, situational item and objective metrics | **No** | We have not identified a licensed, documented bulk data feed. Its [guide](https://lol.ps/en/datastudio/guide) directs detailed analysis/business inquiries to its operator. Public pages are useful for expert interpretation. |
| [ChainCC](https://chaincc.lol/free/data/) | Complete real pro drafts, bans, results and role-pair combinations | **Yes** | Official **CC BY 4.0** open CSV downloads; attribution is retained in the dataset, model reasons, and this document. Scheduled GitHub refresh. |
| Riot Match-v5 | Our linked team results and experience, not global mass-market counter tables | Existing team accounts via OP.GG; Riot API pending | Use official API once a key is approved. |
| Game of Legends and other pro sources | Current Worlds picks, bans and match results | Existing independent pro collector | Pro play supplies drafting priors, not a Solo Queue win rate. |

## Working collector: ChainCC

- `tools/refresh_open_pro_pairs.py` reads the actual published 2026 team and player CSV data, joins them on **game ID and side**, and analyses only complete five-player lineups.
- Retains data from the latest patch and the preceding two patches, independently verifying the side and champion roles.
- Calculates champion-role baselines and **Bayesian/shrunk pair win-rate lift**, restricting downstream influence to about **±0.72 points on a 10-point pairing score**. This corrects small samples; pro selection remains strongly confounded by team strength.
- `data/league_open_pro_pairs.json` is the small derived dataset (not raw copyrighted bulk data). Discord and the desktop app fetch the same shared JSON. A GitHub Actions job refreshes it daily.
- As first collected on 19 October 2026, patch **16.20**, it used **729 complete professional matches** (1,458 team sides) and produced **843 qualifying role-pair observations** with at least five appearances. This is *additional pro evidence*, not millions of Solo Queue games.
- Full attribution: **ChainCC, League of Legends esports match dataset, https://chaincc.lol/free/data (CC BY 4.0), accessed 2026-10-19.** Any onward distribution must preserve that attribution.

## What is still missing

A provider agreement or explicit permission is needed to import the large-sample, continuously refreshed **Solo Queue** champion win rate/counter/synergy tables that LoLalytics, League of Graphs and LOL.PS maintain. Merely referencing a web page does not mean the draft engine has that site's database.

We should ingest only provider-authorised exports/API feeds, normalize comparable **patch/tier/region/queue** groups, reject stale or tiny samples, avoid double-counting correlated or overlapping provider match pools, shrink matchup differentials, and weigh them below team composition/comfort. The engine's limited legacy LoLalytics snapshot is retained as a fallback until that feed is secured, with its original captured timestamp.

View the local live bridge's `GET http://127.0.0.1:8766/api/data-sources` for accurate `scheduled_open_dataset` vs `reference_pending_permission` status.
