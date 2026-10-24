# INTent DraftCoach — evidence-led drafting roadmap (24 October 2026)

## Product contract

**Before queue:** /league game supplies 10 scored, genuinely different *opening plans* for the actual selected five players. A selected champion is eligible for its selected role, not automatically strong on the current patch. Plans keep real player-role priorities, carry allocation, complete 5v5 structure, ADC+Support and Mid+Jungle compatibility, early map access, and real blind/counter timing.

**In champion select:** use the lobby's stable game ID, our role assignments, our locks, enemy picks, bans, and enemy role confidence to recalculate those plans on every changed state. Rejected picks never remain in the active recommendation. A manually preferred opening five should get one scored candidate and only retain preference while close to the current best objective score; it is never a lock. Prefer secure pairings in an uninterrupted pick round, and preserve R5 counter where appropriate.

**Between games:** rotation changes **which eligible champions get full scoring opportunities**, not their win rates or their champion skill assumptions. Same match ID yields the same candidate seeds after every pick/ban; a new /league request ID or new Riot lobby ID explores a different subset. Core counter/duo/role-strength candidates remain. No arbitrary novelty points should raise an objectively weak comp over a strong comp.

## Source-index design: provenance before volume

Store rows with `source_id`, `source_url`, `licence_or_permission`, `observed_at`, `ingested_at`, `patch`, `queue`, `region`, `tier`, `role`, `sample_count`, `confidence`, and `methodology`. Never conflate normalized win rates across ranked tiers, patches or player sample windows. Preserve raw and normalized measures separately.

1. **High-elo game evidence:** approved/licensed champion role stats, matchup win-rate deltas, first-15 CS/gold, synergy-adjusted ADC/support and jungle/mid pairs. Minimum sample thresholds, patch freshness checks, shrinkage, and uncertainty bounds. The bundled LoLalytics D2+ matchup snapshot is tagged 16.20 while the live game is 26.21; stale snapshots are historical priors only, not current patch win rates. Do not scrape or redistribute sites that disallow it.
2. **Pro draft evidence:** patch, match/event ID, champion role, blue/red, pick slot, bans, matchup opponents, swaps and role reveals. Record actual pick sequence, not inferred lane counterpick from champion appearance alone. Worlds games run on 26.20; their draft logic is not 26.21 SoloQ strength.
3. **Expert/context observations:** user-provided Reddit discussion, permitted champion Discord guides, public creator YouTube/VOD timecodes, coaching notes. Link author, publication date, patch and direct quotation or short *paraphrase*. Qualitative claims like 'pick Lee Sin with mid priority' create labeled hypotheses, not invented probabilities.
4. **Team evidence:** user's submitted roles/champions, role priority, explicit high-competence declarations, known player reliability/carry role, actual team game outcomes and revision notes. Small samples are a weak tie-breaker. A role-selected champion is never silently removed solely because no recent game was observed.

## Planned ingestion and validation

- Periodic ingestion of licensed numerical feeds with structured validation, schema version, patch gate, timestamp and explicit provider attribution.
- Author-reviewed annotations from pro picks and public guides, tracked separately from numerical matchup records; allow conflicting claims to coexist by source and patch rather than overwrite.
- Precompute lane counter index, ADC+Support and Mid+Jungle pair edges, team archetype role assignments, early wave/objective coverage, and counter-sensitive draft order.
- Re-run recorded draft scenarios after every patch (enemy Malphite -> Sylas consideration, ADC before Thresh countersteal, Yunara+Lulu, Kai'Sa dive support, Aphelios alternatives, mixed damage, off-role proficiency).
- Live UI should show: best plan, objective score, confidence, why that pick, first safe rotation, pivot triggers (enemy bans/picks), and up to 10 legal plans with different complete bot/mid-jungle packages.
- Historical data remains distinguishable from the current patch. Fail open to qualitative team logic with uncertainty labels if licensed data is unavailable.

## Status

Implemented before this file: six-to-ten draft cards with paging, score/duo/comp logic, per-role alternatives, live ban/lock recompute, bounded lane-counter data, captain soft-preference, and independent release repair.

Implemented with this change: explicit competence for Cian's selected pool and Ste's submitted Mid pool, widened bounded searches, new game-ID candidate rotation, cross-role champion coverage, and carrying preferred live-plan options through candidate screening.

**Not yet implemented:** a massive automatically refreshed licensed D2+ matchup database, complete all-pro draft sequence history, automatically analyzed Reddit/Discord/YouTube evidence, or automatic Discord /league plan selection synced directly to the desktop application. Treat these as future stages, not current evidence.
