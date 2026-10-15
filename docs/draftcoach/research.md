# Research notes: what feeds the drafting logic

## 1. The Codex team review (ChatGPT share, 10 October 2026)

Source: the shared Codex session "Yasuo Learning Journey" (flex-team-evidence-review site).
Decoded transcript kept out of the repo; the parts that matter are folded into
`loadfiles/flexers-team.json`.

**Roster.** Seven people, 23 confirmed accounts, all mapped to the engine's player
profiles by Discord id:

| Person | Engine profile | Roles (research) | Accounts |
| --- | --- | --- | --- |
| Cian / Chud | Chud Useless Robber of Progress | Jungle main, flexible to high level | FakerChovyHilary, Atomic, Loot Lake Loony, xX2Skill4uXx, Schyaitale, BotIanebanter, vlwaurept, KINGOFFORTNITE |
| Jack / Ice Cream | The Ice Cream Fella | ADC main, some top, support cover trial | JackJak, Tilt with Tilt, DylFan1 |
| Aristo / Rat (Pete) | The AristoCAT | Yasuo mid to Emerald, Gnar/Jax/Rumble top, jungle learning (Wukong 6-4) | RISE OF PETE, BAN EVADER BARRY, PINGINGPETER, CHATBANCHARLIE, GRASS Q GARY |
| Sasha / Ste's Third (Ferret) | Ste's Third | Warwick jungle OTP, support trial, some top, Malzahar mid | SneakyFerret, CaptainUsov |
| Ste / Weekend Stev | Weekend Stev | Mid (Anivia, Xerath, Vex), older ADC | LargeLarry, ScubbaSte, HaroldHaroldson |
| Kayla (Adittive) | JustKayla | Farming jungle (Karthus 66W/103), Amumu trial, mid transition, support trial | Adittive |
| Jezz / Cheeky Chuckler (Reform) | The Cheeky Chuckler | Lulu/Leona support, developing | ReformUKGaming |

**Findings that shape the advice.**

- Cian supplied about 42% of team damage in the squad's recorded games; the team
  died almost twice as often per minute in losses as in wins.
- Comfort versus refresh versus developing matters more than rank: four of the
  proposed starters had no recent solo games in their recommended role. Old
  results (Anivia, Karthus) are refresh assignments, not current mastery.
- Support is the weakest-covered role; Jezz needs coached repetition, Jack has a
  Thresh backup pathway, Kayla and Sasha are trials.
- Opening trial lineup: Cian top, Sasha jungle, Aristo mid, Jack ADC, Jezz or
  Kayla support; then test the Cian/Sasha top-jungle swap.
- Six target compositions with win conditions, fight sequences, constraints and
  substitutions (Catch → commit, Lift → finish, Hold → punish, Protect → outlast,
  Range → catch, Pressure → collapse).

**How it is used.** Accounts and pools link the lobby to the right profile and
history; familiarity words are stored as their own field (comfort / refresh /
developing) and drive the score directly; the compositions are the playbook the
advisor scores against; the opening trial is the manual-board lineup.

## 2. Backend changes made from this research

- Draft-order exposure: counter risk now scales with how many enemy answers can
  still follow the pick being ranked (blue B1 full, red R5 none), and the true
  last pick weighs lane counters up. Jungle and support lane edges count 75%.
- Flex picks get a small bonus while answers remain (they hide the role plan).
- Bans that nobody on the scouted enemy team plays are scaled down and tagged
  "unlikely" instead of competing with their known picks.
- Playbook: viability, progress, next picks (with substitutions), seat
  mismatches and a completion bonus for picks (`plan_weight`).
- Profile players populate the roster automatically; their account history is
  the working pool until a loadfile supplies a submitted one.

## 3. Codex backend review

`handoff/003_from_codex_backend-deep-review.md` (requested with the engine,
advisor, state, the compositions and the research inlined). The README's
"Changelog: Codex backend review" section lists what was adopted. In short:

| Finding | Verified? | Change |
| --- | --- | --- |
| Solo-queue baseline is inverted (100 − own WR stored) | Yes: all 1570 rows satisfy baseline = enemy WR − delta | Complemented at load; contribution capped at 4 points, averaged over five roles |
| Matchup deltas are over-trusted and mis-worded | Yes (D2+ outcome data, no lane measure) | `calibrated_matchup`: sample shrinkage, ±2.5 cap, role weights, 0.7 quality, new wording |
| Counter risk uses the three worst answers | Yes | Expected loss over likely answers, 80/20 mean/tail; unknown coverage ≠ blind-safe |
| Unknown players penalised by the ownership term | Yes (−1.7 per seat) | Neutral when no profile |
| Display score clamps hide ordering | Yes | `rank_score` unclamped |
| Priority tiers double as mastery | Yes | Separate familiarity field with competitive / practice tables; priority is preference only |
| Exhausted pools silently fall through to history or meta | Yes | "exhausted" source, alert, emergency ranking |
| Playbook substitutions combine freely and ignore players | Yes | One-substitution variants, pool membership per assigned player, reseat cost, bounded bonus |
| Blank enemy roles are guessed | Yes | Enemy roles stay unknown without champion evidence |
| Hypothetical lock does not advance the action list | Yes | Fixed |
| Ban score is an additive pile of bonuses | Yes | Expected marginal damage: probability × (comfort, harm, narrow-pool) − own-pick cost + policy |

Not adopted: replacing the engine's composition search, and any change to the
engine's public function signatures (the Discord bot shares the file; the
engine edits are marked `FLEXERS` and shipped as `docs/engine-changes.patch`).
