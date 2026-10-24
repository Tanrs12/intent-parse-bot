# INTent multi-source matchup research — index v1 (24 Oct 2026)

This catalog supplements the existing permissioned PocketPick archive (30,000 EUW
patch 16.16 Silver–Platinum games, 10,806 historical matchups).

## Research performed

- [LoLMatchups.GG](https://lolmatchups.gg/): modern Master+ matchup pages
  with lane differential, sample sizes, community guidance and VOD references.
  These pages are linked in the research catalog; their large numerical tables
  are **not** downloaded or republished without their own permission.
- [Camille Community Matchups Spreadsheet](https://www.reddit.com/r/CamilleMains/comments/1t8ugh8/camille_community_matchups_spreadsheet/):
  2026 work incorporating GM Camille specialist feedback.
- [Top Lane Matchup Bibles](https://www.reddit.com/r/leagueoflegends/comments/1i5pdtn/):
  older anthology linking many champion-main documents. Freshness uncertain.
- [Master+ spreadsheet directory](https://www.reddit.com/r/summonerschool/comments/1mx3lp7/):
  community index of individual matchup guides.
- [Kayle matchup explanations](https://www.reddit.com/r/Kaylemains/comments/1nnogdp/actual_matchup_spreadsheet_for_top_lane_with_tips/):
  2025 qualitative opponent notes.
- [Gwen Season 16 matchups](https://www.youtube.com/watch?v=VXjsGYa3Z-g):
  a video published Aug 20, 2026, with 47 named opponent chapters. All
  chapter markers are recorded, including Shen at 1:11:53. We have **not**
  manually classified all 47 video's tiers; the index is navigational.
- [Nerfplz Akali current statistics](https://www.nerfplz.com/lol-counters/akali/),
  [SeeMeta Top](https://seemeta.com/en/lol/top),
  [GamGem Gwen](https://gamgem.com/league-of-legends/champions/gwen),
  [Skill-Capped Akali Mid](https://www.skill-capped.com/lol/guides/builds/akali/mid),
  [LeagueCheck](https://leaguecheck.net/):
  independent candidates for cross-checking source discrepancies by patch,
  tier, region and sample size, **not** bulk numerical license grants.
- [Riot API portal](https://developer.riotgames.com/docs/portal):
  a developer key is issued on login (24-hour expiry). A personal key
  requires product registration; no Riot approval email was found in the
  user's Gmail as of Oct 24, 2026. Do not assert permission/access is granted
  or fetch match-v5 without a valid key.

## Data added in this change

- `data/league_matchup_reference_catalog.json` with seven verified statistical
  provider references, 18 individually verified champion guide links,
  and 47 Gwen YouTube timestamped matchup chapters.
- `data/league_high_elo_matchup_notes.json` with 13 independently paraphrased
  high-elo/community source observations about specific champion matchups.
- `league_matchup_catalog.py` resolves champion+role+opponent to proper
  source links and chapter start seconds in memory, without web requests.
- `league_community_evidence.py` joins existing notes, new reviewed notes
  and research links as **qualitative** evidence. All 47 chapter links and
  thirteen tips have **zero numerical draft bonus**, avoiding video-tier
  guesses or double counting with population matchup rates.

## Important: source confidence layers

1. Current explicitly licensed high-elo same-patch matchup observations,
   with game count, lane advantage and statistical uncertainty (highest).
2. Licensed historical population data, with rank/patch/sample decay (lower).
3. Master/Challenger OTP qualitative lane-mechanic claims and linked videos
   (context; no manufactured win-rate score).
4. Unreviewed video chapter or Reddit link (navigational reference only).

The app must avoid asserting "Gwen beats Shen" from the 16-game PocketPick
historical row, and must not turn a 2026 Gwen video titled "matchup tier list"
into a specific tier without verifying the relevant segment. Different OTPs
can disagree legitimately; preserve distinct source claims with versions.

## Remaining engineering requirements

- Request explicit bulk data feed permission from any additional stats
  provider whose tables we propose caching.
- Install/approve a Riot personal or production API key before collecting
  Match-V5. Safely store it in server-only secret storage, never Git.
- Build a rate-limit-compliant match-ID ingestion pipeline with patch/tier
  stratification, 429 Retry-After handling, deduplication and uncertainty.
- Curate individual expert VOD segments via authorised captions/transcripts
  and human review. Never claim footage was watched when only chapters are
  indexed.
- Show sources and the reason for any live draft counterpick suggestion in
  the UI. The present engine attaches notes to lane evidence records, but the
  final UI still needs a detailed source-expansion panel.
