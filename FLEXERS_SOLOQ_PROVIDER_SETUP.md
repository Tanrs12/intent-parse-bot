# Licensed Solo Queue statistics: integration and permission status (2026-10-19)

## What is ready

- Both `cogs/league_flex.py` and the desktop bridge refresh the same shared JSON data.
- `league_aggregate_sources.py` loads `data/league_licensed_soloq.json`; observations affect the shared engine **only** when the exact patch matches the active LoLalytics snapshot's patch and the sample exceeds the relevant minimum.
- `tools/import_licensed_soloq.py` converts an authorised provider's documented aggregate JSON export to the canonical schema. Requires exact patch, rank, region, Ranked Solo/Duo queue and per-source attribution. Rejects low-sample/noisy rows. If writing into the public repository it requires `public_redistribution_allowed: true`.
- `tools/refresh_approved_provider_feed.py` and `.github/workflows/flexers-licensed-soloq.yml` are **dormant** until GitHub secrets for an approved HTTPS JSON export are configured. They do not scrape providers or call private APIs.
- The sample `data/league_licensed_soloq.json` currently has **no active sources** and therefore adds **zero** weighting to the draft. This is honest; do not count provider names in a registry as ingested data.

## Contact status

- **PocketPick:** sent permission inquiry to the verified `hello@pocketpick.gg` address on 2026-10-19. Its 30,000 EUW SoloQ matches and 10,806 matchup rows are from patch 16.16, not current 16.20. We seek explicit internal-use rights and optionally a refreshed, licensed export. No approval yet.
- **League of Graphs:** contact form at https://www.leagueofgraphs.com/contact and Discord at https://discord.gg/sETupGR. Terms prohibit automated scraping without written permission. Provider bulk access not granted.
- **LoLalytics:** private API at https://a3.lolalytics.com/ explicitly prohibits outside use; need provider permission and contact channel. Do not use unofficial scraper libraries against it.
- **LOL.PS:** Data Studio at https://lol.ps/en/datastudio/introduction, asks for feedback/contact through its dev.ps Discord channel. No documented approved bulk feed found.
- **PocketPick data** is published for transparency; public viewing alone does not establish republication rights. Wait for written permission.

## What we request from providers

Daily (or per patch) **aggregate** Ranked Solo Queue exports:
- champion-role observations: games, wins, baseline-adjusted win rate
- role-resolved opponent matchups: samples, response advantage in percentage points
- duo/team synergies: roles, champion identities, expected-versus-observed lift, samples
- unambiguous patch, region, rank bracket, queue, capture timestamp, licensing
- permission for automated consumption and **separate permission** for publicly distributing derived exports, or else a restricted private service

No raw player IDs, profiles or private match records are requested.

## When permission arrives

1. Verify the actual license. If the publisher permits a **public aggregate export**, adapt that feed to the canonical JSON shape shown at the top of `tools/import_licensed_soloq.py`.
2. Store the approved HTTPS export URL as the GitHub Actions secret `SOLOQ_APPROVED_FEED_URL`; any required server-side token as `SOLOQ_APPROVED_FEED_TOKEN`.
3. Trigger the `Licensed aggregate SoloQ feed` GitHub workflow. Confirm it completed, validates the actual current patch, and published a nonempty approved snapshot.
4. Both Discord and the desktop app will pick the same snapshot up on hourly refresh. **Never send API credentials to the distributed app or a public GitHub file.**
5. If permission is for **private/internal use only**, do not use the public-GitHub workflow; first deploy an authenticated private source or change the distribution architecture.

## Scoring priority

Team comp + actual Discord-submitted comfort > matchup counter evidence > player carry fit / practice > pro and meta priors. Imported provider evidence is quality-gated by sample count and exact patch, limited in score, and **not naively summed across overlapping provider datasets**. A provider with the largest valid sample is chosen for each identical observation until independent-data validation is possible.
