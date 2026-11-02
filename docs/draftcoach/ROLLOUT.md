# Flexers Draft Coach: team rollout (3 November 2026)

Written for the five players. Five minutes per PC.

## 1. Install

1. Download: https://github.com/Tanrs12/intent-parse-bot/releases/latest/download/FlexersDraftCoach.zip
2. Extract the zip anywhere (Desktop is fine). Do not run it from inside the zip.
3. Double-click `FlexersDraftCoach.exe`. Windows SmartScreen may ask once: "More info" then "Run anyway".
   The app removes the "downloaded from the internet" mark from its own files on first start.
4. Two things open: the floating panel (small window, always on top) and, in your browser, the coach page.
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
- The **Draft settings** button (panel) or **Team sheet** (coach page) sets the team style
  (dive, poke, front-to-back, ...), pins a player to a role, locks a champion for a role, or asks for
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
| Panel closed by mistake | Closing the panel closes the app. Run the exe again. |
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
