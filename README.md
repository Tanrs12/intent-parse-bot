# INTent League Bot

Dedicated Discord bot/service for the INTent Ranked Flex tools.

It intentionally contains no Warcraft Logs/MoP functionality. League signup state is stored in the service's existing Postgres database; draft/profile data is bundled under `data/`.

## Flexers Draft Coach (desktop app)

**Download:** https://github.com/Tanrs12/intent-parse-bot/releases/latest/download/FlexersDraftCoach.zip

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
- It only coaches **Ranked Flex** champion selects (queue 440). Customs and normals show
  "standing by"; pass `--any-queue` or set `settings.queues` to `[]` to coach those too.
- Run from source: `pip install -r requirements.txt -r requirements-draftcoach.txt` then
  `python run.py --window --hud` (`--simulate` replays a scripted draft without a client).
- Tests: `python -m pytest tests` runs the bot's and the desktop app's suites together.
- Details, loadfiles, playbook and API: `FLEXERS_DRAFT_COACH.md` and
  `docs/draftcoach/research.md`.

