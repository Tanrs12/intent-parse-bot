# INTent League Bot

Dedicated Discord bot/service for the INTent Ranked Flex tools.

It intentionally contains no Warcraft Logs/MoP functionality. League signup state is stored in the service's existing Postgres database; draft/profile data is bundled under `data/`.

## Flexers Draft Coach

The shared live-draft integration entry point is league_draft_backend.py. See FLEXERS_DRAFT_COACH.md for how the desktop Draft Coach should reuse the same ranking, role-allocation, pick-order and counter-ban logic as /league game.

