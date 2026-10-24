# Private PocketPick historical-matchup import

**Permission:** Steffen replied on 20 October 2026 permitting the request for use
of his aggregate exports in a private, non-commercial five-player draft assistant
with attribution to https://pocketpick.gg/data/. This is **not** permission to
republish the provider's proprietary scoring engine, use unrelated sources, or
market old stats as current Diamond+ results.

The upstream data comes from 30,000 Riot Match-V5 EUW Solo Queue games
(Silver–Platinum), patch 16.16, collected August 2026.

## Usage

```powershell
py -3 tools/sync_pocketpick_data.py
```

This verifies the two public, explicitly approved aggregate files and places
them in the *private* `var/pocketpick` folder outside Git. It never imports
raw match records, PUUIDs or summoner names. You can also use
`--from-dir <folder>` for an existing permitted offline copy.

On Windows DraftCoach, point `DRAFTCOACH_POCKETPICK_DIR` to a persistent
local folder, or put the downloaded files beside the installed executable
under `var/pocketpick`. On Render, a separate deployment/private startup sync
is needed; do not commit the full provider exports to a public repository.

## Current coverage

- 173 champions across role-stat rows.
- 10,806 historical champion-role/opponent matchups.
- Historical gold-difference-at-15 and win-rate rows retained with sample sizes.
- Pair residuals and kit measurements retained in the local archive for later
  modelling; **not currently used in the live draft scores**, because pair
  residuals do not carry confidence sample counts.
- Only matchup rows with at least 40 games and an adequately sampled champion
  baseline influence draft scores. This is 1,762 rows in the approved snapshot;
  smaller entries remain inspectable but have **zero ranking weight**.
- Historical contributions are heavily shrunk and capped; current, approved
  higher-tier statistics must override them when available.
- Historical numbers do not prove matchup strengths in patch 26.21 or D2+.

Example: Gwen/Shen has 16 historical games and Gragas/Lee Sin jungle has 5,
so those observations **cannot** establish a statistically supported counter.
The linked champion-main Gragas E versus Lee Sin Q interaction is recorded
separately as a community claim, not a numerical matchup advantage.

## Long-term pipeline

Keep explicit provenance keys (source URL, original tier, queue, region, patch,
date, game count, methodology) on every observation. Future Riot Match-V5
collector jobs should observe application/method rate limits, 429 Retry-After,
Riot product requirements and the user's approved use. Current high-elo
aggregates should never be synthesized by relabelling Silver–Platinum data.
Reddit/Discord/video observations require rights to access, source links, patch
context and human review, and must be qualitative unless numerical validation
independently exists.
