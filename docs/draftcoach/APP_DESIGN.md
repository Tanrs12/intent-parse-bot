# Flexers Draft Coach: the application, the overlay and the shared draft

Written for the team and for whoever works on the app next. Current as of the
`overlay-improvements` branch (1 November 2026).

## 1. What the product is

One desktop application (`FlexersDraftCoach.exe`) with three faces:

| Face | Where | What it is for |
| --- | --- | --- |
| Coach page | `http://127.0.0.1:8765/` | The full app: lobby, ten ranked drafts, captain and draft controls, loadfiles, playbook, history. |
| Floating panel | always-on-top 430x480 window | The one decision that matters now, plus draft type, plan, identity and shake. |
| In-client overlays | drawn over the League client | The decision card and the ten-draft gallery beside champion select, plus Hover / Lock for this seat. |

The drafting brain is the repository's own `/league game` code: `league_comp_engine.py`
(scoring), `league_flex_core.py` (role allocation, diversity, pick order, counter bans) and
`league_draft_backend.recommend_live_drafts()` (the live entry point). The app never ranks
on its own. `tools/intent_live_draft_bridge.py` runs inside the exe on port 8766: it reads the
live champion-select state from 8765, calls the backend after every pick or ban, adds the bounded
live lane adjustment against revealed enemies, and serves the result to every face.

## 2. One draft for the whole party

Every machine runs the same app. The bridge joins a draft room on the deployed bot's relay
(`bot.py`, `/draft-sync/{key}`), heartbeats its identity (Discord id from the seat it controls)
and the relay elects one **captain** per game (the lowest host priority among active
participants; failover when the captain goes stale). Only the captain computes and publishes
guidance; followers read it, so all five see the same drafts in the same order.

Everything a human decides travels through the same relay as one versioned **draft control**
object, so a change on any machine reaches all five within a second:

```json
{"signature": "Camille|Zac|Ahri|Ashe|Lulu",
 "identity": "dive",
 "role_locks": {"Top": 139837324438536193},
 "champion_locks": {"Jungle": "Zac"},
 "shake": 1}
```

- **signature**: the plan the party prefers (Cian's soft preference). It leads while it stays
  within 2.5 points of the objectively best draft; the engine overrides beyond that.
- **identity**: the team identity to keep. See section 3.
- **role_locks**: a player hard-pinned to a role (the backend's `role_players`).
- **champion_locks**: a champion every draft must contain for that role, i.e. a lock we have not
  revealed yet (the backend's `required_champions`).
- **shake**: a counter. Each increment retires the draft currently shown as best for this game and
  reshuffles the rotation seed, so the party is never stuck with the same five every night.

The bridge exposes `GET/POST /api/control` locally; the coach page's **Draft control** section
and the panel's **Identity** select and **Shake** button post to it. In a synced room the post is
forwarded to the relay, which bumps the version; every client applies the newest version.

## 3. Team identity

The engine already classifies every scored composition (`team_plan`, `archetypes`). The app
maps those labels onto seven identities the team can commit to (`league_draft_backend.IDENTITIES`):

| Identity | Engine plans / archetypes | Win condition |
| --- | --- | --- |
| Dive / all-in | `dive / all-in`, `dive` | Mobile engage that collapses on a target |
| Poke / siege | `poke / siege`, `poke + side-lane`, `poke` | Range and zone control, objectives taken safely |
| Front-to-back | `scaling front-to-back`, `front-to-back engage` | Frontline plus a scaling carry |
| Protect the carry | `protect-the-carry` | Peel and shields around one hypercarry |
| Pick / catch | `pick` | Control and burst on one target |
| Tempo skirmish | `tempo skirmish` | Early fights off a strong mid-jungle |
| Side-lane / 1-3-1 | `1-3-1 / split-pressure option` | Split threat with a four-man hold |

With an identity set, drafts of that family lead while the best of them is within
`IDENTITY_MAX_GAP` (6 points) of the strongest draft overall. A much better off-identity draft
still wins, which is the "maintain identity" rule made explicit rather than absolute. Every
recommendation carries its `identity`, and the guidance lists how many current drafts fit each.

## 4. Counterpicking, protection and live updates

The backend already does the live work the request asks for; the app surfaces it:

- **Enemy bans and picks**: every change re-calls the backend with the updated unavailable set,
  our locks and hovers, the enemy's revealed champions and their roles. Hovers of team-mates are
  respected (a lock always wins; a hover outside the pool never hides alternatives).
- **Counterpicking the right lanes**: `_counterpick_window` raises lane weight only in a genuine
  late solo-lane answer (red R5, blue B4/B5 after R4), and `live_lane_adjustment` applies the
  calibrated matchup evidence (sample-shrunk, capped, role-weighted) against revealed enemies.
  Unknown enemy roles never create fictional advantages.
- **Who we protect**: the engine's carry profiles and the `protect-the-carry` / front-to-back
  plans decide which player the composition is built around; pick order then reveals real flexes
  and safe blinds first and holds counter-prone solo lanes (`_pro_pick_order`).
- **Win-rate backend**: LoLalytics role win rates and matchups (complemented baseline, see the
  engine fixes), the licensed SoloQ aggregate, Riot Gold/Emerald collector, ChainCC pro pairs and
  PocketPick cache, refreshed on the bot's schedule and synced to the app (`refresh_shared_engine_data`).
- **Role swaps and pick order across the party**: each seat's hover/lock goes through its own
  client (`/api/client/*`), the role-swap expectation (`resolve_locked_swaps`) tells each player
  when to trade, and the pick order is part of every draft (`pick_order`).

## 5. Hover and lock from the recommendation

The INTent UI patch hides the old advisor's Accept button so nothing can lock a champion that
disagrees with the shared backend. The coach page now has an **Intent actions** row in Draft
control: it shows the bridge's pick for the local seat (`picks[].player_id == this seat`) and
offers Hover / Lock through this machine's client. The native HUD (`draftcoach/hud_intent.py`)
keeps its own Hover / Lock buttons for the same pick.

## 6. Player evidence (OP.GG snapshot, 31 October 2026)

`docs/draftcoach/flex_review_2026-10-31.json` holds the per-player review (verdicts, KDA, roles,
wins-versus-losses). The things that should steer locks and identities:

- Cian: Top 7-1 (Camille 4-0, Ambessa 2-1), Jungle 5-2, ADC 4-6. Keep Cian out of bot unless the
  draft needs it; Aphelios is the exception that performs.
- Jack: ADC 7-2, Support 6-2 (Thresh 3-0). Top 1-3: never Jack top.
- Adittive: Mid 7-1 (Galio 3-0), Jungle 60 % (Amumu 4-2).
- Pete: ADC 3-1, Jungle 6-3 (Wukong), Yasuo 4-1. Trial ADC/Jungle before Top.
- Reform: Top 4-2 on Urgot, Support 50 % over ten games (Lulu 2-3 flagged). Urgot top is a real option.
- SneakyFerret: Jungle 0-2, Support 2-1. Keep off jungle.
- Ste: Mid 3-5, nothing above 50 % except Lux. Developing: prefer safe, protected picks.
- Dyl: Top only, 1-2, 9.5 deaths a game on Sett.

Role win rates feed the roster loadfile as role priorities; the editorial verdicts are the
familiarity words (PRIORITISE = comfort, TEST MORE = refresh, REVIEW = developing).

## 7. What is still open

- The relay stores control in memory; a bot restart clears it mid-session (it is re-sent by the
  next captain post).
- Identity is a preference, not a constraint on the composition search itself; a composition
  search that generates identity-specific shells would widen the pool when the pools are thin.
- Locks and identity are not yet shown on the in-client gallery overlay; they are on the coach
  page and the panel.
