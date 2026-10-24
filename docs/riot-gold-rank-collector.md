# Ranked matchup collection — Gold/Platinum/Emerald first

The user's five-player ranked-flex team is primarily Gold, with one stronger
carry player. **Do not force D2+ performance data to dominate everyone.**

## Actual current-patch statistical plan

Collect solo/duo queue=420 EUW ranked matches, anchored by sampled GOLD,
PLATINUM, EMERALD and optional DIAMOND ladder accounts; retain separate
tier and patch rows. A Gold anchor account is NOT proof all ten people in
its match are Gold; exact individual rank would require additional lookups.
Exclude games that are not on the specified patch, fail role validation, or
are not ranked Solo/Duo queue=420.

Run `tools/collect_riot_ranked_matchups.py` only with a valid **official
personal or production Riot API key** in the environment `RIOT_API_KEY`:

```powershell
python tools/collect_riot_ranked_matchups.py --tiers GOLD PLATINUM EMERALD --division II --seeds-per-tier 10 --matches-per-seed 10 --patch 16.21
```

**No Riot key was configured on the connected Windows PC at implementation
time (2026-10-24).** No current Riot sample was downloaded. The collector
does not bypass U.GG, DPM.LOL, LoLalytics or other websites.

The collector respects published personal-key limits by defaulting to a
1.6-second delay between calls and obeys `Retry-After` on HTTP 429,
retrying transient 5XX. It validates the real match queue and patch after
fetching: the Match-V5 list endpoint has previously had queue-filter bugs.
It deduplicates matches in a private SQLite DB, stores champion role lanes
and outcomes, never stores PUUIDs/account identifiers, and creates
`var/riot-rank-samples/ranked-matchups.json` as the safe scoring snapshot.
For a previously collected DB, `--export-only` is network-free.

Default rank weighting in `league_riot_ranked_sample.py`: Gold 40%,
Platinum 25%, Emerald 25%, Diamond 10%, renormalised over sampled tiers
meeting sample thresholds (minimum 30 matchup games and 75 base games).
All current-patch results are confidence-shrunk. Missing stats are neutral.
This is a rank-anchored sample, not a full representative Solo Queue census.
Do not assert a current matchup advantage before enough games are sampled.

## Third-party site access findings

- [OP.GG help centre](https://help.op.gg/hc/en-us/articles/31091405109401-Can-I-use-OP-GG-data)
  generally tolerates cautious cited crawling, but
  [general terms](https://op.gg/lol/policies/agreement) restrict scraping:
  conflicting policies mean request explicit permission before bulk storage.
- [DPM.LOL terms](https://dpm.lol/tos): automated scraping strictly
  prohibited without express written permission; no access-control bypass.
- [U.GG FAQ](https://u.gg/faq): Riot API-derived data, no authorised
  third-party bulk feed confirmed.
- LoLalytics: valuable rank/patch/role context, but request express data
  permission before bulk downloading an internal dataset.
- [Riot developer portal](https://developer.riotgames.com/docs/portal):
  any logged-in developer can use a 24-hour development key; personal
  keys are available by product registration for small private tools.
  A personal key commonly has 20 req/sec and 100 req/2 min caps.
  No Riot approval email was found in the connected mailbox.

## Existing sources retained

The permissioned PocketPick patch-16.16 archive remains a strongly
shrunk historical fallback. ChainCC CC BY 4.0 pro-game evidence retains
its pro selection bias and cannot be treated as Gold Solo Queue. Champion
main Reddit/YouTube notes are qualitative until reviewed and statistically
corroborated.
