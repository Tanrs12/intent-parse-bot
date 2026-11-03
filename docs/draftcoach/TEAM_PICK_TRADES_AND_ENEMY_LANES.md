# Team reveal-order picks and manually confirmed enemy lanes

Updated 3 November 2026.

## Which champion should I pick when League gives ME an early turn?
Draft Coach's leading team composition has five strategic **champion reveal** slots. **These are not tied to the player whose League client gets that pick turn.** If League gives Top the first pick but the team plans to reveal Jungle's Lee Sin first, Top's overlay recommends **Lee Sin — for the Jungler; trade after both locks**, provided Top's account is allowed to pick Lee Sin.

The Lock/Hover buttons act **only through the current player's own League client**. A teammate cannot lock picks on another PC. The draft may keep counter-sensitive picks until later while any eligible team member selects the current reveal champion.

For simultaneous team picks, the app allocates separate candidate champions to the active actors, honours current hovers, and avoids assigning the same pick twice. If one player cannot select the ideal champion because of ownership or pickability, it searches **other unpicked team champions**; it never promises an illegal or already locked pick. The alternative can change reveal order; the team should communicate before clicking Lock.

Automatic *pick-order swaps* stop when following a shared team plan. Auto role swaps still work when players are in the wrong roles; manual swap buttons are available for people who explicitly want them. Teammates trade champions in the League client after the relevant players have locked. The original player-role champion pool still governs who is capable of **playing** the champion, independent of who temporarily picks it.

The bridge now attributes our secured champions to their **planned destination role** for ongoing drafting rather than assuming the physical pick-turn player's role. Thus Top temporarily holding Lee Sin locks the team's **Jungle Lee Sin**, not its Top, and the next team reveal advances accordingly.

## Enemy lane correction
When enemy champions are **locked**, a new **Enemy lane assignments** section appears in both the compact overlay and the full Coach. The elected captain selects each opponent's lane from Top/Jungle/Mid/Bot/Support or leaves it on Auto. Other teammates see the same settings but cannot change them.

The controls post `enemy_role_overrides` through the existing team `/api/control` and relay rather than a private browser flag. The bridge resolves enemy roles in order: manual captain assignment first, then already known client roles, then only unambiguous standard-position inference. An unknown flex lane remains unconfirmed, receiving **no fabricated same-lane counter bonus**. The selected role feeds the next live draft re-ranking, team-level compensation search and attributed matchup scouting.

Overrides have a `game_id`. The next game does **not** reuse last game's enemy assignments. Hovers and banned champions are not eligible for manual mapping; only enemy locked picks apply. If the captain assigns two champions to one lane, the editor clears the earlier assignment. One enemy champion cannot fill two lanes. The UI never changes the actual enemy slot reported by League.

## Important caveats
- Trading requires that the receiving player and initial picker can complete the trade; eligibility, ownership and in-client trade rules still apply. The app cannot manufacture champion ownership.
- Confirming a flex lane is a human assertion, not verification of an enemy's eventual final position. The captain can change the dropdown when more picks appear.
- Current-patch scoring is still subject to the source's patch-expiry safeguards. Patch 26.21 observations expire on November 4 until new licensed 26.22 evidence has been verified.
- These edits are released through a new GitHub Windows build. They do **not** replace an already-running installed Draft Coach executable unless separately installed.

## Test coverage
`tests/test_team_reveal_and_enemy_lane_control.py` covers cross-player first picks, 2-actor pick allocation, hover reservations and champion ownership, secured destination-role attribution, captain overrides, stale-game rejection, and both UI integrations. `tests/test_any_queue_assist.py` verifies that the old automatic pick-order swap expectation is removed for a selected shared draft while manual swap commands remain available.
