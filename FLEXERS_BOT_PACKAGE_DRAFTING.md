# Bot-lane package drafting — 2026-10-19

**Why this changed:** Prepared Draft 4 displayed a single Aphelios recommendation even when the same player listed Yunara and Kalista, and their teammate listed Lulu, Thresh and Renata. The old broad-pool and secondary-role experience gates excluded unplayed but user-confirmed ADCs *before* team-comp evaluation. The alternative picker then allowed only one-role substitutions, hiding the crucial two-role changes.

## Updated hierarchy

1. Submitted player pools remain the availability boundary. Player-confirmed ADC comfort (e.g. Cian's Yunara and Kalista) overrides absent recent-match records when deciding which champions are **legal to evaluate**. An explicit confirmation does **not** add a free win-rate, comfort or meta bonus.
2. Candidate pruning preserves a small selection of distinct established ADC+Support pairings when both champions are in the actual submitted pools. Candidate coverage is separate from prioritising the completed five-player draft.
3. Evaluate the **entire five-player team**, including the mid–jungle package, overall game plan, mixed damage and the enemy counter situation, before finalising a pairing. Pro meta and SoloQ evidence are supporting priors, not automatic locks.
4. The Discord draft image has a separate **LINKED BOT LANE ALTERNATIVES** row. Changing two champions together may change the team's game plan and requires recomputing pick order and bans. Live app JSON includes each linked package and champion IDs.
5. Diverse good-quality bot packages can appear across the six primary compositions. Prior personal success on two Aphelios games must not consume every candidate shortlist slot.

## Validated Draft 4 example

Base: Urgot / Amumu / Yasuo / Aphelios / Renata.

Conditional alternatives:
- **Aphelios + Thresh** — same scaling front-to-back plan.
- **Yunara + Lulu** — same scaling front-to-back plan.
- **Kalista + Renata Glasc** — a more aggressive alternative; full team-plan label changes.

The limited role/meta snapshot on patch 16.20 and historical professional pairing data are NOT a licensed, live LoLalytics feed. Source verification from public OP.GG patch 16.20 (consulted 2026-10-19):
- Yunara + Lulu, EUW Emerald+ Ranked: 5,582 appearances; 49.82% combined win rate (https://op.gg/nl/lol/champions/yunara/synergies/adc?patch=16.20&region=euw&tier=emerald_plus&type=ranked).
- Kalista + Renata, EUW Emerald+ Ranked: 1,254 appearances; 51.75% combined win rate (https://op.gg/nl/lol/champions/kalista/synergies/adc?patch=16.20&region=euw&tier=emerald_plus&type=ranked).
- Aphelios + Thresh, EUNE Emerald+ Ranked: 1,452 appearances; 51.93% combined win rate (https://op.gg/nl/lol/champions/aphelios/synergies/adc?patch=16.20&region=eune&tier=emerald_plus&type=ranked).

**Do not compare these raw win rates as causal duo synergy or across different rank/region pools.** League stats can reflect player selection effects, support popularity, matchup exposure, and patch differences. The site checks motivated preserving viable combinations; the internal heuristics remain provisional until authorised aggregate SoloQ data exists. A champion-specific enemy response can completely reverse the preferred ADC+Support pair.


## 2026-10-19 extension: all players and all lanes

- The same submitted-pool eligibility rule applies to every player and role, not only Cian's ADC choices. Recent champion wins or OP.GG recency cannot hard-remove declared primary-role picks. This is NOT a claim that every unverified 40-champion list is equally well practised: credible secondary-role screening remains in place, and user confirmations are the authoritative way to lift it.
- Dynamic selection now protects established **Mid+Jungle** packages, just as it protects **ADC+Support** pairs. Both are assessed against the *entire five-player composition*. Registered pairs only guarantee evaluation, not selection.
- Individual comfort and recent results carry lower weights during package shortlisting and in final player-fit scoring. A short hot streak should not outweigh viable lane synergy, counterpicking, or the team game plan.
- Distinct complete bot and mid+jungle packages receive portfolio coverage; one high-scoring ADC or jungler must not dominate all six prepared drafts by accident.
- Mid+jungle linked alternatives are shown together and labelled when changing both champions changes the team strategy. They are included in the desktop app payload as paired objects with champion IDs. Neither half should be mistaken for an independent substitution.
- Live champion locks, bans, selected roles, actual submitted champion pools, verified weak off-role proficiency, enemy counters, full team plan and damage profile remain higher-authority constraints than pro popularity.
- Real-time licensed SoloQ aggregate data is still provider-dependent. Existing pro and bounded baseline evidence are not proof that a specific combination wins on the present patch.
