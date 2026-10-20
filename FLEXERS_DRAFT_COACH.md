# Flexers Draft Coach integration

This repository is the source of truth for the Ranked Flex drafting logic used by `/league game`.

If Flexers Draft Coach should sort recommendations the same way as the Discord command, do **not** maintain a second independent ranking formula in the desktop app.  Use `league_draft_backend.recommend_live_drafts()` and keep the desktop app focused on League Client (LCU) state, UI, hovers/locks and presentation.

## Where the desktop app lives now

The desktop app is in this repository: `draftcoach/` (package), `run.py` (launcher),
`build_exe.py` (PyInstaller package + zip), `loadfiles/` (team loadfiles, playbook),
`tests/test_draftcoach_*.py` and `docs/draftcoach/`. `league_flex_core.py` holds the
discord-free drafting functions that both `cogs/league_flex.py` and `league_draft_backend.py`
import, so the desktop build never needs discord.py, asyncpg or Pillow.

`draftcoach/backend.py` turns the seated five into `/league game` signups (roles, role
priorities and champion pools from the roster and loadfiles) and calls
`recommend_live_drafts()` after every pick or ban. The overlay then only adds what the live
draft knows and the bot cannot: calibrated matchups against enemy locks, how answerable a blind
pick still is, scouting-based bans, the team's chosen playbook plan, and the client actions.
`settings.ranking = "engine"` switches back to the raw composition search for comparison.

The live coach supports **3-, 4- and 5-player Ranked Flex premades**. In 3/4-man queues, known teammates keep their saved account/profile data while unmatched teammates are marked as PUGs, fixed to the role Riot assigns them, and treated neutrally until their real hover/lock is visible. One/two-player normal PvP stays disabled. Custom games and versus-AI queues are also allowed as solo test modes, while ARAM / ARAM: Mayhem stay disabled. The in-client left overlay freezes the six opening `/league game` plans, shows the player/role assignment from each plan, and marks plans when a champion is banned or taken by the enemy. Revealed enemy lane picks add the Draft Coach calibrated matchup layer on top of the shared composition/player score; the adjustment is deliberately bounded so a nominal counter cannot rescue a much worse five-man composition.

Download: https://github.com/Tanrs12/intent-parse-bot/releases/latest/download/FlexersDraftCoach.zip

## Stable integration entry point

```python
from league_draft_backend import recommend_live_drafts

result = recommend_live_drafts(
    signups=current_five,
    unavailable=bans_and_enemy_picks,
    locked_champions={"Top": "Camille"},
    role_players={"Top": 139837324438536193},
    side="blue",
)

for draft in result["recommendations"]:
    print(draft["score"], draft["champions"], draft["pick_order"])
```

Call it again whenever a pick or ban changes. `locked_champions` are our already-locked champions by role; those champions are automatically kept available to the scorer. `role_players` is optional and can pin a known live seat to a role once the client makes that unambiguous.

The wrapper deliberately reuses the same pieces as `/league game`:

- role assignment and role priorities from `generate_team_options()`
- the current composition/player-fit model from `recommend_comps()`
- lineup-aware ADC handling and draft diversity from `_select_diverse_recommendations()`
- role alternatives and learn suggestions
- roster-real flex-aware pick ordering from `_pro_pick_order()`
- counter-ban ranking from `counter_ban_recommendations()`

This means changes made to the bot's drafting brain can flow into Draft Coach without re-implementing them there.

## Early-map and role reliability risk (20 October 2026)

The shared composition ranker now applies a small, bounded **draft-stage early-map
risk screen** after champion synergy, the team-plan score, and player fit. It uses
qualitative champion wave-access priors (not invented matchup win rates), plus
role-specific reliability *where there is history*. It flags multiple low-priority
lanes, jungle picks needing nearby lane access, isolated solo-lane/jungle win
conditions, and whether a **revealed** enemy roaming support can leave bot without
a meaningful wave/plate response. Unknown support picks do not imply real roams.

For transparency, recommended drafts include `early_map_adjustment`,
`early_map_lane_priority`, `early_map_bot_priority`,
`early_map_enemy_roam_threat`, and `early_map_reasons`. Major map warnings also
appear in `gaps` for existing bot/HUD consumers. The map adjustment is capped
to -5.8/+0.65 points and the package-search tie-breaker is modest: pro pairing
synergy, actual counterpicks, submitted comfort pools, and real team composition
remain dominant. Yunara/Lulu and other scaling duos stay eligible. We are not
claiming to infer actual warding, roams, lane outcomes or wave states from a static
draft. Post-match evaluation still requires match events/replays.

## Data snapshots

The engine consumes these files from `data/`:

- `league_comp_meta.json`
- `league_lane_matchups.json`
- `league_player_profiles.json`
- `league_recent_pro.json`
- `league_role_champions.json`

`league_pro_data.py` refreshes recent pro games. The Discord bot and lightweight live Draft Coach both use a six-hour pro-data cadence; player/signup data refreshes every ten minutes. Cached snapshots are used immediately at launch so network refreshes never block champion select.

A reusable sync helper is included at `tools/sync_flexers_draft_coach.py`. Example:

```powershell
python tools/sync_flexers_draft_coach.py `
  --coach-dir "C:\path\to\FlexersDraftCoach" `
  --server http://127.0.0.1:8765 `
  --interval 600
```

The helper copies changed engine snapshots into Draft Coach's `loadfiles/` folder and posts them to `/api/import` when the local Draft Coach server is available.

## Important behaviour to preserve

The current bot intentionally treats role priority as a preference rather than a hard lock. Priority 1 should usually win, but a materially better team composition may justify a Priority 2/3 flex. If there is no true P1 ADC in the five, fallback ADC duty can rotate rather than repeatedly spending the strongest solo-lane/jungle carry bot. If a genuine P1 ADC exists, there is no forced equal rotation.

The strongest carry-profile player pays an opportunity cost for flexing ADC at P2/P3 because bot lane shares early agency. A true P1 ADC does not receive that penalty. Low-economy Top tanks also carry an opportunity cost when they consume the strongest carry profile.

Pick-order flex value is **roster-real**, not theoretical. A champion is not treated as a useful flex bluff merely because it appears in multiple roles globally; the actual five-player roster needs credible alternate player/role destinations for that champion.

For blue side, B4/B5 are effectively one protected pair because Red has no pick between them. The model therefore cares about which two picks are preserved for B4/B5, not cosmetic ordering inside that pair.

## Development workflow

The main branch is `main`. Small changes should be tested before pushing. The existing League tests can be run with:

```powershell
python -m pytest tests
```

On Cian's machine the known-good test interpreter is:

```powershell
"C:\Users\cianm\Desktop\Classic+ Bot\.venv\Scripts\python.exe" -m pytest tests
```

When changing the drafting brain, update the shared backend here first. Draft Coach should consume the updated backend rather than forking the scoring logic again.

### Source live launcher (Windows)

For the lightweight source build, create a dedicated environment once:

```powershell
py -3.14 -m venv .draftcoach-venv
.\.draftcoach-venv\Scripts\python.exe -m pip install -r requirements-draftcoach.txt
```

Then run `tools/start_intent_draftcoach.ps1`; `tools/stop_intent_draftcoach.ps1` shuts down only this repository's Draft Coach processes. The launcher starts one lightweight LCU/state server plus the shared bridge, the six-comp outside-left overlay, and the next-action HUD. Each helper also has a singleton guard so a double launch cannot leave duplicate workers behind.

Rat's original `draftcoach/hud.py` remains untouched. `draftcoach/hud_intent.py` is a preserved copy used by the INTent launcher, with Rat's presentation/buttons but INTent backend data. In `INTENT_LIVE` mode the source server skips Rat's second full advisor search and only supplies LCU state/client actions; the shared backend on port 8766 is the one scoring drafts. This avoids doing the same expensive composition search twice.

The overlays are champion-select-only. They stay attached to the League window if focus moves to another monitor, hide immediately when champion select ends / game starts, and do not activate in ARAM or ARAM: Mayhem. Normal PvP accepts confirmed 3-5 player premades; one/two-player queues remain off. Custom and versus-AI drafts are test exceptions.

## Local live bridge

The packaged build from `main` **is** this flow: `build_exe.py` applies `tools/patch_flexers_intent_ui.py`
to the packaged coach page and HUD, and `run.py` starts `tools/intent_live_draft_bridge.py` inside the
exe on port 8766 (`--no-bridge` turns it off; `--bridge` turns it on when running from source). The
visible picks and bans therefore come from `recommend_live_drafts()` through the bridge. On machines
without the Northflank CLI the bridge's signup cache is seeded from the Draft Coach roster (team
loadfile or a `/league coach` export); a cache written by the bridge itself is never overwritten.
A solo custom or versus-AI lobby is padded to five seats from the roster lineup so the backend can rank it. The bridge's signup cache is still the way the Discord pools reach the app: Draft Coach watches
`data/live_flex_signups.json` (next to the exe: `_internal/engine/data/`, or the repo's `data/`) and
imports it whenever it changes, and `/league coach` in Discord exports the same rows as a loadfile for
anyone without Northflank access.


`tools/intent_live_draft_bridge.py` is the local adapter for the packaged Flexers Draft Coach build. It reads the app's live champion-select state, refreshes the canonical Discord signup rows from the deployed service, calls `league_draft_backend.recommend_live_drafts()`, and exposes the result at `http://127.0.0.1:8766/api/guidance`.

`tools/patch_flexers_intent_ui.py <FlexersDraftCoach folder>` patches the packaged Coach/HUD HTML so its visible pick and ban recommendations come from that bridge instead of the packaged advisor. The original HTML files are preserved as `.intent.bak` files.

The bridge deliberately disables Flex recommendations in standard ARAM and ARAM: Mayhem. It detects both the known queue IDs (450 and 2400) and the League Client gameflow mode/name (`ARAM`, `Mayhem`, `KIWI`, or Howling Abyss) so the HUD does not appear in those lobbies. Normal PvP requires a confirmed 3-5 player premade; that party-size confirmation is cached briefly so it survives the transition from lobby into champion select. If the helper starts after the lobby endpoint has disappeared, three matched live profiles can carry the same confirmation forward.

## Live UI split

During champion select the six composition cards are docked **outside the left edge** of the League client. Each card shows the five champion portraits, the player/role assignment, and a 1-5 badge for that composition's planned reveal order. The cards are ordered by the same shared backend score; Rat's bounded live matchup adjustment can move close plans up/down when an enemy role is revealed. The number shown is a **draft score, not a literal calibrated win percentage**.

The familiar bottom-right HUD has a different job: it shows only the **next action now** (pick or ban) and keeps the Hover / Lock-in buttons. In packaged builds it is fed through `tools/intent_hud_proxy.py`, so it uses the same INTent recommendation as the left-side cards instead of a second advisor result.

Live labels use the Riot account actually present in champion select. The canonical player profile still supplies that person's roles/pools in the background. If somebody uses a Riot account that is not mapped yet, the app does **not** guess their identity: it displays that account as an unmapped PUG until the new Riot ID is added to that person's `riot_ids` in `loadfiles/flexers-team.json`. In a 3/4-man queue, genuine random teammates are handled the same way; the overlay shows `PUG` and `?` until their real hover/lock is known rather than pretending the neutral placeholder is a recommendation for them.

If a displayed plan becomes impossible because one of its champions is banned or taken by the enemy, the left card is kept temporarily as a red/grey `OUT` card with an X on the affected portrait while the surviving/new plans move ahead of it.

### Custom / bot visual testing

Custom and versus-AI champ selects can inject **local-only test events** through `http://127.0.0.1:8766/api/test`. They are ignored outside test queues and never click the League client. Helpers:

```powershell
python tools/intent_draft_test.py ban Camille
python tools/intent_draft_test.py enemy Top Rumble
python tools/intent_draft_test.py ours Jungle Amumu
python tools/intent_draft_test.py clear
python tools/intent_draft_demo.py
```

`intent_draft_demo.py` demonstrates a plan being invalidated, live matchup-driven re-ranking, and one of our planned locks while leaving the real client untouched.
