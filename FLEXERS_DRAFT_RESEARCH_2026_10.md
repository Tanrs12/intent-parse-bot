# Ranked Flex Draft Research — 19 October 2026

## Purpose
This is a research-to-engine design note, **not a proven percentage-weight formula**. FLEXERS needs strong player-appropriate five-man plans that adjust after every enemy lock/ban. Professional draft findings are priors, not a blueprint for a lower-elo Ranked Flex five-stack.

## Findings with useful sources
1. **Comfort and team identity matter.** A competitive coach warns against importing pro priority lists into teams with different player strengths and limited champion pools, and stresses multiple executable win conditions. https://www.esportsheaven.com/features/the-art-of-the-draft-an-in-depth-look-into-drafting-from-a-coachs-perspective
2. **Champion comfort, early leads, matchups and meta interact.** iTero's retrospective professional model isolates player experience, early gold/agency, champion strength, counterpicks and synergies. Its evidence is pro-play-specific. https://www.itero.gg/articles/draft-pro
3. **Laning counters are not just 1v1s.** Soloqueue win rate relative to each champ's baseline can inform counter matchups, but cross-role interactions such as support into enemy ADC, and gank setup, matter. Results require sample shrinkage. https://www.itero.gg/de/articles/counters
4. **Bot synergy must account for jungle and enemy botlane.** Ex-pro coach Karakal ranks supports by allied ADC, jungle synergy and opponent botlane, not by ADC+support reputation alone. https://coachkarakal.com/drafting
5. **Use synergistic player/champion modeling.** Research into personalised MOBAs distinguishes player competence/preference and allied interactions. https://arxiv.org/abs/2204.12750
6. **Current meta is a prior, not the conclusion.** Oracle's Elixir game data updates daily (not needed more frequently), and the Worlds 2026 early tournament sample is small. https://lol.timsevenhuysen.com/ ; https://batru.gg/events/lol-worlds
7. **Big soloqueue synergy deltas can come from tiny samples.** Report empirical counts, confidence and patch, not just +8% lifts drawn from fewer than 100 matches. https://winrate.lol/en/synergies

## Draft ranking hierarchy
Hard gates: role eligibility, availability, user-submitted champion selection, legitimate game locks/bans, and a playable win condition. A hover is NOT a player's champion-competence declaration.
1. Highest priority: five-player game plan + comfort. Fit strong botlane 2v2 package and coordinated Mid/Jungle; complete a valid five-player plan (front-to-back, dive/pick, side-lane, siege) without sacrificing all frontline, AP/AD threat credibility or actual execution.
2. Counterplay: same-lane, ADC versus support, support versus ADC, Jungle interactions, and cross-role teamfight counters. Evaluate enemy LOCKED picks as hard evidence; flex champions with unknown enemy position carry less lane-matchup confidence. Late counterpicks may be strong but never rescue an implausible five-man.
3. Player carry/resource allocation: assign demanding jobs to players with demonstrated influence and proven comfort, while preserving non-carry alternatives and meaningful role swaps. Strong players need not always be assigned ADC.
4. Patch/pro meta and statistical win rates are evidence-based tie-breakers after the above; shrink toward neutral at low sample counts. Do not treat 1-5 game pro win rates as a deterministic ranking.
5. Pick order and bans: hold vulnerable roles until enemy answers reveal enough information. Blue side B1 priority is exchanged for red side R5 answer; Ranked Flex has a single *simultaneous* ban phase, unlike tournament draft's two ban phases and Fearless restrictions.

### Package specifics
- **Bot lane:** ADC+support practiced duo, push/trade/all-in or scale/peel identity; 2v2 into BOTH opponent bot champions; synergy with Jungle pathing and support roam windows; no unsupported Lucian+Maokai duo.
- **Mid/Jungle:** combine priority, crowd-control setup, skirmish 2v2, damage profiles, invade/objective windows; a generic historical pro pairing should not automatically defeat a practiced flexible pairing.
- **Top:** frontline/weakside, splitpush side win condition, carry/dive top, or counterpick. Assess blind-pick exposure, Jungle cover, late enemy pick and whether remaining four can survive without top.
- **Cross-role counters:** ult steal (Sylas v enemy Malphite/Ornn), anti-dash, anti-engage, anti-tank DPS, anti-shield or anti-dive must consider whole revealed enemy team. Every change triggers full five-champion rerank.

## Observed roster evidence and interpretation
OP.GG refreshed on 2026-10-19. On FakerChovyHilary-ALLY, the twelve latest fetched Ranked Flex games in the inspected block showed 10 wins and 2 losses. The user's screenshot shows the most recent ten, of which nine are wins and one is a loss. "10-1 since tryharding" is the user's separately defined start period, not a number to retroactively impose on all visible matches. The visible victories include 21/1/12 Sylas Jungle, 22/3/12 Camille Top, 19/6/8 Camille Top, 19/1/1 Camille Top, 12/4/8 Caitlyn ADC, 13/5/12 Vi Jungle, 16/3/3 Aphelios ADC, 25/3/9 Camille Top, 19/1/17 Sejuani Jungle; the previous displayed loss was Aphelios 6/9/7. The screenshots show MVP across nine displayed victories, but OP.GG's compact match row lacks an official MVP field.

Comparing pre-refresh and updated player scoring, Cian's global carry index remained 100 (already saturated), Top remained 100; Jungle 99.7 → 100; ADC 64.8 → 74.0. The updated statistics modestly improve role-specific confidence and recent game metrics, without justifying a forced fixed role or assuming a permanent 90% win rate. Re-evaluate team roles and package success, not solely individual KDA or MVP badges.

## Implementation status / necessary follow-ups
- Already in engine: bot / Mid-Jungle pair scores, team plan checks, all-player carry allocation, submitted champion pools, opponent counter adjustment, real-time recomputation.
- Current matchup snapshot built 2026-10-13 covers only eight common enemy champions per lane. Need comprehensive, patch-aware LoLalytics matchup refresh and source timestamps/confidence; many live counters currently lack usable data.
- Need direct matchup coverage for ADC↔enemy Support, Support↔enemy ADC, and paired botlane (ADC+Support) versus opposing duo; raw pick-pair win rate alone is not enough.
- Need flexible enemy role inference with uncertainty and a reranking log/replay set for real Ranked Flex drafts (including Sylas/Malphite/Ornn + Ste Zed hover).
- Should measure factual champions in player-selected pools against recent linked-account ownership; never silently add high mastery into populated Discord pools.
- Need scheduled source refresh with stable fallback. The one-time OP.GG snapshot update in this commit should not be confused with continuous live profile refresh.
- Outcome validation: hold out recent matches, compare ranked candidate sets, detect failed comp transitions, collect draft satisfaction / actually playable selections. Avoid interpreting a short winning streak as proof of a scoring formula.
