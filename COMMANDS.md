# Intent Parse Bot commands

## Everyone
- `/profile [member]` — current SoO score, SoO PB, next colour, guild rank and MoP history.
- `/parse [member]` — quick current SoO score/PB.
- `/leaderboard` — current SoO leaderboard.
- `/pbs` — MoP career-PB leaderboard.
- `/nextcolour [member]` — distance to the next parse colour.
- `/close [within]` — promotion watch; defaults to players within 3 points.
- `/history [member]` — MSV → HoF/ToES → ToT → SoO history.
- `/compare player1 player2` — head-to-head.
- `/improvements [period]` — last sync, 7d, 30d, or since tracking.
- `/raidrecap` — gains + promotion watch + current top 3.

For commands where `[member]` is optional, leaving it blank means yourself.

## Officers (Manage Server)
- `/syncparses` — immediately sync everyone from WCL and update roles.
- `/autosync enabled:true` — automatic checks every ~6 hours.
- `/backfill` — one-time pull of old MoP tier PBs.
- `/setchannel #channel` — where automatic promotions/PBs post.
- `/addraider` — add/link someone.
- `/setmetric` — switch DPS/HPS (clears that raider's bot history to avoid mixing metrics).
- `/removeraider` — remove someone and their stored bot history.
- `/parserroster` — linked roster and metrics.
- `/setup` — initial guild setup; safe to rerun.
