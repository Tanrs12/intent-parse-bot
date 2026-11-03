# INTent League Bot

Dedicated Discord bot/service for the INTent Ranked Flex tools.

It intentionally contains no Warcraft Logs/MoP functionality. League signup state is stored in the service's existing Postgres database; draft/profile data is bundled under `data/`.

## Flexers Draft Coach (desktop app)

**For Rat, Claude Code and future developers:** read [CLAUDE.md](CLAUDE.md) before changing Draft Coach. It documents the shared engine, team relay, Game Plan ownership, multi-pick visual integration, counterpick data limits, and the tests that protect Rat's features. The November 3 integration is additive, not a replacement of Draft Studio or the live overlay.

**Download:** https://github.com/Tanrs12/intent-parse-bot/releases/latest/download/FlexersDraftCoach.zip

**Team guide:** [docs/draftcoach/ROLLOUT.md](docs/draftcoach/ROLLOUT.md) (install, first game, what to do if something looks wrong). How it was tested: [docs/draftcoach/PARTY_TEST.md](docs/draftcoach/PARTY_TEST.md).

**Matchup database:** `python tools/matchup_coverage.py` reports coverage of all valid lane pairings; `league_matchup_universe.py` makes the complete champion-role universe eligible for scouting. Evidence with too little data remains clearly marked as scouting, not a statistical counter. See `CLAUDE.md` and `docs/riot-gold-rank-collector.md` for source requirements.

Unzip, open the folder, double-click `FlexersDraftCoach.exe` (or one of the `.bat`
launchers) while League is open. No Python needed. The app lives in this repository
(`draftcoach/`, `run.py`, `build_exe.py`, `loadfiles/`) and ranks through the same brain as
`/league game`: `league_draft_backend.recommend_live_drafts()` on top of
`league_flex_core.py` + `league_comp_engine.py`. Every push to `main` rebuilds the zip and
updates the `draft-coach-latest` release (see `.github/workflows/draft-coach.yml`); pushes to
`dev` build it as a workflow artifact.

- `/league coach` in Discord exports everyone's signup roles, priorities and champion pools as
  a loadfile; drop it into the app's `loadfiles/` folder and it ranks with exactly that data.
- The zip also contains the full source in `source/`.
- It coaches **every queue except ARAM and Arena** (Solo/Duo, Flex, normals, Quickplay, Clash,
  customs) with friends or strangers. A saved `settings.queues` list still limits it if you set one.
- Run from source: `pip install -r requirements.txt -r requirements-draftcoach.txt` then
  `python run.py --bridge --window` (`--simulate` replays a scripted draft without a client), or
  double-click `Draft Coach (source, every queue).bat`.

### What's new (November 2026)

- **One overlay** (the white panel); the in-client HUD is optional (`--hud`). Closing or crashing the
  overlay no longer stops the coach: reopen it from the coach page. It opens in light mode.
- **Overlay tabs:** *Draft* (live), *My champs* (who this PC is, your pool per role with best options,
  add/remove/reorder) and *Stats* (rank, form, champions, insights). Size S/M/L/XL.
- **Champion select:** one-click lock that only offers your own role and champions you own; enemy
  pick prediction from their bans; role and pick-order swap buttons (request / accept / decline /
  cancel) plus automatic swaps towards "My role tonight" (switch: *Auto: On/Off*).
- **Drafts: Auto / App / Discord.** Auto follows the team's shared Discord drafts when three or more
  of the team are in your lobby, otherwise the app ranks on its own.
- **Draft Studio** (`/studio`): build drafts for any lineup with pins, bans and a preferred style,
  reject one champion to re-roll just that lane, show different/creative drafts, and pool insight
  (weaknesses and champions to learn that play like your pool).
- **Team profiles sync:** champion pools edited in *My champs* and linked accounts (smurfs) reach
  every teammate's app the next time you are online together, via the team relay; every PC keeps its
  own copy. Link a PC once ("This PC is …") and every account used on it counts as that player.
- Tests: `python -m pytest tests` runs the bot's and the desktop app's suites together.
- Details, loadfiles, playbook and API: `FLEXERS_DRAFT_COACH.md` and
  `docs/draftcoach/research.md`.

