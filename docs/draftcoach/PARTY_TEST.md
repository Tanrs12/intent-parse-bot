# Party test: five users, five League clients, one draft

Written for whoever runs or extends the live test. Current as of 2 November 2026.

## What it is

`tools/party_test.py` stands up everything a five-player ranked flex party would use, on one PC,
and drives a whole draft through it the way five teammates would:

| Piece | Tool | Port(s) |
| --- | --- | --- |
| Draft-sync relay (the deployed bot's code, no Discord) | `tools/local_relay.py` | 8790 |
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
- shared controls (team style, role lock, champion lock, shake) posted on one app reach all five, timed
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
