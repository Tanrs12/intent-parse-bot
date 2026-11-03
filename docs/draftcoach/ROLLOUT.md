# Flexers Draft Coach: team rollout (3 November 2026)

Written for the five players. Five minutes per PC.

## 1. Install

1. Download: https://github.com/Tanrs12/intent-parse-bot/releases/latest/download/FlexersDraftCoach.zip
2. Extract the zip to a plain folder such as `C:\Games\FlexersDraftCoach`. Do not run it from inside the
   zip, and avoid OneDrive-synced folders (on many Windows 11 PCs that includes Desktop and Documents):
   the sync client holds files open and the self-updater cannot replace them.
3. Double-click `FlexersDraftCoach.exe`. Windows SmartScreen may ask once: "More info" then "Run anyway".
   The app removes the "downloaded from the internet" mark from its own files on first start.
4. It opens as a normal application (one "Flexers Draft Coach" button in the taskbar, no black console
   window) with two windows: the **coach window** (the coach page, resizable like any app) and the
   **floating panel** (small, always on top). Closing one window keeps the other running: the panel's
   **Open coach** button brings the coach window back, and the coach window's **Show panel** brings the
   panel back. Closing both windows quits the app.
   The app also updates itself from the same link when a newer build is published.

## 2. Before the first game

- Open League and sign in as usual. The panel's footer turns from "Client not detected" to "Live".
- Your seat is matched by your Riot ID. The team file already lists everyone's accounts; if you play on
  a new account, tell Will the Riot ID so it can be added, otherwise your seat shows "Own pool".
- Queue **Ranked Flex** (customs and practice tool also work, for testing). Normal and solo queue are
  ignored on purpose.
- Open the coach page's **Player review** card once and press **Refresh from my client**: it reads your own
  match history and rank through your League client and caches it.

## 3. During champion select

- Everyone sees the same drafts. One of you is the captain (decided automatically, Cian first); the rest
  follow. If the captain's app dies, another is chosen within seconds. You do not need to do anything.
- The panel shows one decision at a time. When it is **your** pick or ban, two buttons appear:
  **Hover** (shows the champion in League) and **Lock in** / **Ban**. They act through your own League
  client. When it is a teammate's turn, it says who is picking.
- **Choosing the draft happens in the app, before the game.** The coach window opens on **Game plan**:
  tick the five playing tonight (anyone with the app open is already ticked), press **Build drafts**,
  and the ten drafts appear in well under a second, built by the same engine the Discord bot uses, with
  each player's options and learning picks, the pick order and bans for either side. **Send to team**
  on a draft and every app follows it within a second: roles are pinned to the players in that draft,
  the five champions are the plan, and the draft's style is kept. **Different drafts** rerolls the ten;
  **Banned or taken** restricts them when bans are known beforehand (scrims). When the enemy bans or
  takes one of the five in champion select, only that seat is re-solved, in the same style, and the
  panel's team list says "was Sylas · banned" next to the replacement. **Release team draft** puts the
  apps back on automatic drafts. Nobody needs to open Discord. The choice travels between PCs through
  the team relay (the same service the bot runs in), so that service has to be up; if it restarts, the
  app that sent the draft sends it again by itself, and if that app is closed the panel says "No team
  draft chosen" and anyone can send it again.
- The chosen draft shows a **Team draft** editor (Game plan before the game, Ranked team drafts during
  champion select) where any seat's champion can be changed live; everyone follows within half a second.
- **Discord still works as before** for anyone who prefers it: the bot's **Lock Draft N** dropdown sends
  the same thing to the apps, and the bot keeps a **Live draft** message in the channel that follows
  champ select. The pools and role priorities you set in Discord reach the app through the same relay,
  so changing them there still changes the drafts here.
- **Sync check** (coach page, Draft settings & requirements, "Who's here") lists everyone who has the
  app open, whether they are in champion select, who the captain is, and the role each wants. Set
  **My role tonight** so the others see it; press **Sync check** to refresh and get a one-line summary.
- The **Draft settings** button (panel) or **Team sheet** (coach page) does the same by hand: team style
  (dive, poke, front-to-back, ...), pin a player to a role, lock a champion for a role, or ask for
  different drafts ("Find different drafts"). A change by any one of you reaches everyone within half
  a second.
- After the last lock the panel shows the final five, the carry plan and any champion trade to make.
  Trades still happen in League; the panel offers Accept when a planned trade arrives.

## 4. If something looks wrong

| You see | Do |
| --- | --- |
| "Client not detected" with League open | Restart the app after League is fully open. If it stays, send Will `var\draftcoach.log` from the app folder. |
| Buttons missing on your turn | Wait a second; if still missing, pick in League as normal. The drafts are still shared. |
| "Shared draft unavailable" | Internet or relay hiccup. Your app keeps ranking locally; it rejoins on its own. |
| Panel closed by mistake | The app is still running in the coach window: press **Show panel** there. If you closed both windows, run the exe again. |
| Different drafts to a teammate for more than a few seconds | Both of you: note the time and tell Will. |

Logs: `var\draftcoach.log` and `var\crash.log` next to the exe. Nothing is uploaded anywhere except
the draft itself to the team relay during champion select.

## 5. What was tested before this rollout

Five copies of the app on five fake League clients in one lobby, the team relay running locally, a
whole ranked-flex draft played through each user's own client, with the relay blocked like a firewall,
a client websocket dropped, a client restarted on a new port, the captain's app killed mid-draft and a
champion trade. A lock reached all five screens in about 100 ms; a shared control change in about
350 ms. Details: `docs/draftcoach/PARTY_TEST.md`.

What has not been tested: five real League clients on five real PCs through the real internet relay.
The first real game will tell; keep the logs.
