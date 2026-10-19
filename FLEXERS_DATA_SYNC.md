# Flexers data freshness and source of truth (October 2026)

## Discord bot
- `cogs/league_flex.py` runs `refresh_player_data_forever` after startup, then every 12h. That refresh reads **every linked OP.GG account** stored for **every player**, not only the current team captain.
- `refresh_pro_meta_forever` updates recent professional-game evidence every 6h.
- The Discord /league draft and Windows live overlay use `league_comp_engine.py` and `league_flex_core.py`; a new engine version must still be deployed in the hosted Discord service for both to have identical logic.

## Shared player data
- `.github/workflows/flexers-team-data.yml` checks all linked Ranked accounts **twice per day** (00:17 and 12:17 UTC), publishing `data/league_player_profiles.json` to GitHub when new matches are found. A failed/blocked OP.GG fetch cannot be described as an update; check GitHub Actions run history.
- `tools/intent_live_draft_bridge.py` loads a previously cached valid JSON snapshot at startup and checks the GitHub main-branch versions of `league_player_profiles.json` and `league_lane_matchups.json` every hour in the background. This **does not require a Windows app rebuild**; the engine's cached indices are invalidated when data changes.
- Query the bridge's local `http://127.0.0.1:8766/api/data-sources` to inspect whether each remote JSON file downloaded and its source timestamp. These are source timestamps, not proof that every linked account was reachable.
- Bot and app do not *copy data to each other directly*. They use the same shared upstream snapshots and their respective runtime refreshes; different machines can briefly have different data, especially if a source is offline or after an update.

## SoloQ/counterpick data — NOT YET LIVE
- The checked-in `data/league_lane_matchups.json` (LoLalytics, D2+, patch 16.20 at time of setup) remains the latest manually prepared snapshot, not a live feed. The app fetches new versions of this file whenever it changes upstream, but downloading an unchanged file is not a **new match-data scrape**.
- LoLalytics explicitly states its `a3.lolalytics.com` API is private and third-party use is prohibited. Do not use undocumented endpoint scraping as a background collector.
- To automate trustworthy patch-aware champion win rates and counters, use an **authorised data source** (for example Riot Games' documented Match-v5 API with an appropriately approved key, or a licensed public data feed). Riot's 24-hour development keys are not suitable for unattended 12-hour refreshes. Refresh role baselines and matchup samples, compute confidence-shrunk deltas, and write a validated `league_lane_matchups.json` with patch/time/sample metadata.
- Until then, UI should label the matchup source's actual patch and timestamp and never present stale or absent matchup rows as current-patch proof.

## Performance, reliability and privacy
- Player comfort pools continue to be submitted Discord selections (explicit first), mastery and ranked history are supporting evidence, and unrevealed enemy lanes never become certain counters.
- OP.GG refresh excludes Arena, ARAM and other non-Summoner's-Rift modes; Ranked receives more weight than Normal for player skill. Current GitHub scheduler fetches Ranked matches only, preserving older Normal records in the snapshot.
- A zero-success collector run fails rather than overwriting the repository with empty data. Scheduled publication of *new* matches is what triggers an upstream commit.
- Profiles can be public in the repository. If a participant needs a private dataset, move the source off the public main branch and use an authenticated, access-controlled data service before adding private account details.
