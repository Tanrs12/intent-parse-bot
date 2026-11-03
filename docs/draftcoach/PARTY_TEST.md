# Party test: five users, five League clients, one draft

Written for whoever runs or extends the live test. Current as of 2 November 2026.

## What it is

`tools/party_test.py` stands up everything a five-player ranked flex party would use, on one PC,
and drives a whole draft through it the way five teammates would:

| Piece | Tool | Port(s) |
| --- | --- | --- |
| Draft-sync relay (the deployed bot's code, no Discord) | `tools/local_relay.py` | 8795 |
| Fake League lobby: five fake clients sharing one champion select | `tools/fake_lcu.py` | control 8700, clients 8710–8714 |
| Five Draft Coach apps, each on its own fake client, in production mode (`INTENT_LIVE=1`) | `run.py` | app 8800/8810/…, bridge = app port + 1 |

The fake client speaks the subset of the LCU the app uses: lockfile, basic auth, the champ-select
session over REST and the wamp websocket, action PATCH (hover / lock), trades, gameflow phase,
queue, champion summary, and the signed-in player's ranked standing and match history (synthetic,
deterministic per player). The app connects to it exactly as it connects to League.

## What it checks

- every app connects to its own client and sits in its own seat (Riot ID → roster player → Discord id)
- the queue is recognised as Ranked Flex
- one captain is elected through the relay and all five show the same drafts
- the game plan (`game plan:`), before champion select and with no Discord: one app lists the five
  test players with pools, builds the ten drafts through `/api/gameplan` (both sides' pick order and
  bans, a relay control body per draft, timed), sends draft 1 through its bridge, all five apps follow
  it (adaptive plan, five roles pinned), and release returns everyone to automatic
- shared controls (team style, role lock, champion lock, shake) posted on one app reach all five, timed
- sync check (`sync check:`): a wanted role set on one app shows for that player on all five, one
  captain everywhere, all five in champion select (each app has its own role file, `INTENT_ROLE_FILE`)
- the captain's own choice (`app choice:`): a draft sent from one app's coach page is followed by all
  five (pinned roles, locked five), a live seat edit reaches all five, release returns everyone to automatic
- a draft chosen in Discord (`discord choice:`): the relay post the bot makes reaches all five and leads
  on every app; after the draft (`chosen draft:`) the seat whose champion the scripted enemy banned was
  re-solved and nothing else moved. The harness prefers a draft containing a champion the enemy will
  ban and reports it when no such draft exists, so the ban check is never skipped silently
- every one of our bans and picks: the seat's app offers the button, hovers and locks through its own
  client, the lobby shows it, and all five apps see it, timed
- quirks: a client websocket drop (reconnect), the relay blocked like a firewall for 8 s (nobody loses
  guidance; one captain again afterwards), a client restart on a new port and password (new lockfile)
- captain failover: the captain's app is killed mid-draft, another captain is elected, the old one is
  restarted and rejoins
- each app builds its player review from its own account
- a champion trade requested by one seat and accepted by another through the client
- every app leaves champion select when the game starts

Timings on the 2 November run: a lock reached all five screens in a median 69–120 ms (worst 152 ms);
a shared control change reached all five in a median 330–370 ms.

## Running it

```
python tools/party_test.py                 # full scenario, about 7 minutes
python tools/party_test.py --scenario smoke # connection, sync, the draft, trade: about 90 s
python tools/party_test.py --keep          # leave everything running to look at the apps
```

Reports land in `var/party/report.json`; app logs in `var/party/app<N>.log`, the lobby's in
`var/party/lobby.log`. Everything the test starts is killed at the end unless `--keep` is given.

To try the fake lobby by hand: start `tools/local_relay.py` and `tools/fake_lcu.py --our "<five Riot IDs>"`,
then run one app with `--lockfile var/party/lobby/client0/lockfile`, and `POST /control/start` on 8700.

## Kinks it found (and the fixes)

- Ports 8765/8766 were hardcoded in the pages, server and bridge, so only one app could run per PC.
  The bridge now runs on app port + 1 everywhere.
- Followers went blank when the relay was unreachable. The bridge now serves the last payload it saw
  (up to 20 s old) and then ranks locally, and the captain keeps its role through a transient failure.
- Relay timeouts queued behind one lock, stalling the panel. One fetch is ever in flight; a failure
  backs the relay off for two seconds; every relay call fails fast (2.5 s).
- With five apps each running the heavy local ranking, one app stopped answering for eight seconds.
  Production mode (the bridge ranks, the app only acts) is what the test runs now, and the hub takes
  its seat suggestion from the bridge in that mode.
- A seat was offered a champion a teammate had just locked. The suggestion is now role-correct and
  skips champions already taken.
- CommunityDragon returns 403 to Python's default user agent, so champion art never cached offline.

## Rollout hardening (2 November, codex-ui-rebuild)

The full scenario now also exercises a duplicate launch on the occupied app port, two concurrent
page polling loops per app, red-side trades (`--side red`), four known players plus an unlisted
Riot ID, a client genuinely closed and reopened, a dodge followed by a new lobby after ten seconds
while the relay returns HTTP 500, a relay clock one hour ahead, ARAM/normal standby, and a 30-second
idle ban. The idle ban checks explicit client PATCH audit events: the fake client's timer is the
actor. Its generic "locked" event alone cannot identify who acted.

Use `--scenario full` and `--scenario full --side red` consecutively. Logs append across the captain
restart and flush immediately; `run.py` also sends Python logging to stderr so each app log includes
LCU errors. Port constants remain 8700 / 8710 / 8795 / 8800+10N. The test asserts ten actual actions,
keeps the complete lobby event history, and verifies the new port after client rotation.

The original smoke/full scenarios passed (65/0 in 54.1 s, 76/0 in 72.1 s), but the full run logged
uncaught connection-abort exceptions during relay timeouts. Added regressions exposed:

- The relay backoff path returned arbitrarily old guidance. It now uses a monotonic, bounded
  20-second cache, and both the bridge and hub poller reject another game's `game_id`.
- Normal websocket closes during client restart were reported as app errors. The watcher now
  treats `ConnectionClosed` as a reconnect, alongside socket errors and timeouts.
- Clearing a session during debounce could restore the old draft. A generation counter invalidates
  pending/in-flight work; an explicit manual board still survives an idle LCU notification.
- A stale session could enable client actions, and an empty role pool could fall back to another
  role's first pick. Stale actions now fail with `League client state is stale; wait for it to reconnect`;
  missing role-correct guidance leaves the suggestion empty.
- A second launch started its bridge before losing the app port bind, briefly registering a sixth
  participant. Startup reserves the actual app socket first, using `SO_EXCLUSIVEADDRUSE` on Windows,
  then passes that socket to Uvicorn. It starts no bridge if the port is occupied.
- Player review could select another account's newest cache. The endpoint scopes cached and in-memory
  reviews to the currently signed-in PUUID, including account switches.
- Stats now map legacy numeric account/summoner identities, `queueMap`, and MID/BOT lane aliases;
  ambiguous bot-lane roles stay unknown. LCU seconds are preserved for old patches. Impossible KP
  values are missing, rather than clamped to 100%. Short SR early surrenders (under five minutes) are
  excluded, with the limitation that LCU cannot certify refunded LP stated in the review notes.

Identity/rank compatibility was checked against the extracted
[LCU schema](https://raw.githubusercontent.com/MingweiSamuel/lcu-schema/gh-pages/lcu/openapi.json).
Tests use synthetic histories, including missing full-game details; no real signed-in account was
used for this verification. The fake lobby is a controlled draft harness, not a complete LCU emulator.

On this Windows restricted token, Python 3.14's `mkdir(mode=0o700)` creates a directory the process
then cannot read. Plain pytest fails while setting up `tmp_path`. The following process-local
workaround uses inherited directory permissions for test-created directories; it changes no installed
package or repository test behavior. Give each invocation a fresh `--basetemp`.

```powershell
python -u -c "import os,sys; mkdir=os.mkdir; os.mkdir=lambda path,mode=0o777,**kw: mkdir(path,0o777,**kw); import pytest; sys.exit(pytest.main(['tests','-q','--basetemp=var/verify-suite-final','-o','cache_dir=var/verify-cache','--tb=short']))"
```

The environment separately denies the named pipe used to start Playwright's driver:
`asyncio.windows_utils.pipe -> _winapi.CreateFile: PermissionError [WinError 5]`. All eleven existing
UI tests fail at fixture setup before Chromium or any assertion runs. Do not count them as passed
or skip them to make the suite green. Run them on an unrestricted Windows test runner before rollout.
`python tools/verify_draftcoach_ui.py` succeeds here and checks the unchanged IDs, patch anchors,
JavaScript syntax, and palette contrast without starting a browser.

Blocked cases in `tests/test_draftcoach_ui.py` (all the same driver pipe setup error):

```text
test_portraits_load_when_ids_are_only_in_team_comp
test_simulated_local_contract_exposes_working_lock_button
test_teammate_turn_has_plain_owner_and_no_actions
test_unavailable_simulation_explains_disabled_action
test_coach_local_action_and_art_match_without_player_id
test_panel_keeps_five_and_action_in_view
test_icon_failure_uses_tile_and_never_initials
test_stale_swap_is_never_actionable
test_missing_suggestion_cannot_lock_another_roles_top_pick
test_legacy_automatic_dark_preference_does_not_override_new_default
test_coach_mobile_has_no_horizontal_overflow
```

Final verification evidence is kept in ignored runtime files:

- `var/pytest-release.txt`: **389 passed, 11 setup errors, 3 deprecation warnings in 148.80 s**.
  The setup errors are precisely the Playwright cases listed above. The stats-only suite passes
  **16 tests**; the bridge/rollout regressions pass **51 tests** with the temp-directory workaround.
- `var/party-release-blue.txt` and `var/party-release-blue/report.json`: **89 passed, 0 failed in
  120.4 s**, 4,104 concurrent page polling requests with zero failures, lock propagation median
  92 ms / worst 136 ms, no app ERROR records or tracebacks.
- The immediately following `var/party-release-red.txt` and `var/party-release-red/report.json`:
  **89 passed, 0 failed in 112.5 s**, 3,804 polling requests with zero failures, lock propagation
  median 107 ms / worst 121 ms, no app ERROR records or tracebacks. These two runs use the same
  final application and scenario code. `var/party/report.json` retains the last run as usual.
- `python tools/verify_draftcoach_ui.py`: source and patched copy each pass seven script checks;
  IDs unique, patch idempotent, contrast checks pass. No static HTML/CSS/JS was edited.
