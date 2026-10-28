# Flexers DraftCoach — OP.GG MCP + Worlds 2026 (28 Oct 2026)

## What the shared engine now uses

- The 2026 Games of Legends snapshot includes **Worlds 2026 Play-In** and
  **Worlds 2026 Main Event**, as well as Demacia Cup. Completed ten-player
  game drafts feed the existing bot-lane, mid-jungle and broader pro-pair scores.
- `league_pro_archetypes.py` detects tank-top/hypercarry/enchanter, carry-top/
  engage-support, assassin-jungle/setup-mid, mid/jungle dive, front-to-back
  control, and coordinated engage/dive structures. Each pattern needs at least
  three completed Worlds team appearances for a bonus. Bonus ≤0.6 points and
  is *not* a causal win-rate claim.
- `league_opgg_reference.py` supplies a private, small (±0.35 point)
  freshness-limited *unversioned* OP.GG role-meta reference, only when a
  valid private cache exists. The official MCP lane list returns plays, wins,
  win rate and tier, but **does not provide patch, rank or queue filters**.
  Consequently these results must never be described as verified 26.21,
  Gold/Emerald or Flex-specific match win rates. Player comfort and coherent
  five-man composition remain dominant.

## Refreshing the private OP.GG data (on Windows)

In the local intent-parse-bot checkout, create an isolated venv and install
`pip install -r requirements-opgg-mcp.txt`, then run
`python tools/refresh_opgg_mcp.py --discover` and
`python tools/refresh_opgg_mcp.py --collect`.

This makes five official MCP requests (one per lane) with a short delay and
writes ONLY `var/opgg/lane_meta.json`; previous data are retained on errors.
The `var/` folder is ignored by Git and must **never** be committed.
The cache expires after 48 hours. A scheduled local refresh is optional; the
draft engine is neutral without cached data.

OP.GG support confirmed limited attributed non-commercial collection on
26 Oct 2026. Their open-source MCP uses
https://mcp-api.op.gg/mcp. Do not automate DPM.LOL; written approval has
not arrived. PocketPick patch 16.16 must remain historical evidence.

## Deployment caution

Publishing the Python engine updates the shared Discord drafting logic and
the Windows release *code*. OP.GG private local cache is intentionally **not
shipped to a public release or cloud runtime**. The live Discord service and
newly installed clients therefore use **Worlds data + existing feeds**, and
OP.GG adds a nonzero adjustment only where a user-authorised private cache has
been separately refreshed. Do not claim otherwise.

Tests: `python -m pytest tests`, including dedicated archetype and OP.GG
cache validation. Inspect GitHub's Draft Coach build workflow before downloading
the new release.
