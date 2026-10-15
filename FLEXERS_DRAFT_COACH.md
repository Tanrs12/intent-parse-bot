# Flexers Draft Coach integration

This repository is the source of truth for the Ranked Flex drafting logic used by `/league game`.

If Flexers Draft Coach should sort recommendations the same way as the Discord command, do **not** maintain a second independent ranking formula in the desktop app.  Use `league_draft_backend.recommend_live_drafts()` and keep the desktop app focused on League Client (LCU) state, UI, hovers/locks and presentation.

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

## Data snapshots

The engine consumes these files from `data/`:

- `league_comp_meta.json`
- `league_lane_matchups.json`
- `league_player_profiles.json`
- `league_recent_pro.json`
- `league_role_champions.json`

`league_pro_data.py` refreshes recent pro games. The Discord bot currently refreshes that snapshot every six hours; Cian's local Draft Coach helper refreshes it more frequently while the coach is running.

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

## Local live bridge

`tools/intent_live_draft_bridge.py` is the local adapter for the packaged Flexers Draft Coach build. It reads the app's live champion-select state, refreshes the canonical Discord signup rows from the deployed service, calls `league_draft_backend.recommend_live_drafts()`, and exposes the result at `http://127.0.0.1:8766/api/guidance`.

`tools/patch_flexers_intent_ui.py <FlexersDraftCoach folder>` patches the packaged Coach/HUD HTML so its visible pick and ban recommendations come from that bridge instead of the packaged advisor. The original HTML files are preserved as `.intent.bak` files.

The bridge deliberately disables Flex recommendations in standard ARAM and ARAM: Mayhem. It detects both the known queue IDs (450 and 2400) and the League Client gameflow mode/name (`ARAM`, `Mayhem`, `KIWI`, or Howling Abyss) so the HUD does not appear in those lobbies. It also requires a confirmed five-player premade: the League lobby must contain exactly five party members before guidance is enabled, and that confirmation is cached briefly so it survives the transition from lobby into champion select.
