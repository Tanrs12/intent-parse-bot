# Full UI / UX review and redesign of Flexers Draft Coach

You designed the Matchup Master visual system and reviewed this app three times. It has grown since:
it now lives inside the intent-parse-bot repository, ranks through the shared `/league game` brain
(`league_draft_backend.recommend_live_drafts`) via a local bridge on :8766, syncs one draft across a
five-player party (captain election on a relay), and gained a shared **draft control**: team identity,
hard role/champion locks, "Shake it up", and a soft "Prefer" per draft.

The team just ran it next to a live game and the verdict was "improve UI and UX". What they saw
(screenshot described, since you cannot see images):

- Left monitor: the coach page (1366 px wide browser) with the lobby table, draft board (our five vs
  enemy five with splash art rows), and on the right the guidance rail: "Draft complete", Draft control
  (identity select, Shake it up, five lock rows each with a checkbox "keep top/jg/mid/bot/sup", a
  "lock champion" text input and "LOCKED <champion>" meta), then "Draft captain" with the ranked drafts.
- The floating panel (430x480, dark) sat on top of the coach page, overlapping the board: header,
  a controls row (DRAFT select, IDENTITY select, Shake, PLAN select) that is now too crowded at 430 px
  (the identity label clips), "DRAFT COMPLETE / Draft complete / Plan: dive / all-in. Strength:
  reliable frontline.", pick order chips B1..B5, change line, lobby strip, Open Coach.
- Right monitor: the actual League game running (irrelevant to the overlay, but it shows the context:
  the tools are used between and during games, glanced at, not read).

Problems the team feels (you decide the fixes):
1. Too much, too small, too similar. Everything on the coach page has the same visual weight; the one
   decision that matters is not obvious; the lock rows read as a form, not as a team sheet.
2. The panel's controls row no longer fits; identity and shake were bolted on.
3. "Draft complete" is a dead end: it should become the post-draft summary (final five, identity, who
   protects whom, swaps to make, expected pick order next game) rather than a header.
4. The coach page and panel look like two products; the in-client HUD (native tkinter, colour-keyed,
   `draftcoach/hud_intent.py`) and the ten-draft gallery (`tools/intent_comp_overlay.py`) are a third.
5. No clear "mode" story for the user: when do I look at the panel, when at the coach page, when at
   the HUD? What should a non-captain see versus the captain?

Deliverables, all as drop-in code I will apply verbatim (markdown code fences, complete files where you
replace a file, exact old/new snippets where you edit):

A. **Information architecture** for the three faces (coach page, panel, in-client) and for the two
   roles (captain, player): what each shows, in what order, what is hidden until needed. One page.
B. **Coach page** (`draftcoach/static/coach.html`, `mm/draft-coach.css`, `mm/draft-ui.css`,
   `mm/tokens.css`): restructure the guidance rail around the decision, redesign Draft control as a
   team sheet (seat, player, role lock, champion lock, this seat's action) and the captain's drafts as
   scannable cards; a proper draft-complete summary. Keep every element id the JavaScript uses (list
   them in your answer) and keep these exact anchor lines the INTent UI patch tool matches:
   - the `let latest = null, champions = {}, names = [], ...` declaration line
   - `const st = snapshot.status || {}, state = snapshot.state, g = snapshot.guidance, draft = snapshot.draft || {};`
   - the `if (!state || !g) { key = ... }` standby line in render
   - `$("pick-section").hidden = showBan || !state || oursLocked === 5; $("ban-section").hidden = !showBan;`
   - `$("other-section").hidden = !state || oursLocked === 5;`
   - `function renderClientActions(snapshot) {` + its first line
C. **Panel** (`draftcoach/static/overlay.html`, `mm/draft-overlay.css`): fit draft type, plan,
   identity and shake without clipping at 430 px (collapse into one "Draft" popover or a second row
   that only appears before picks start), and the post-draft summary state.
D. **One visual language** across coach page, panel, HUD and gallery: token values (`tokens.css`) and
   the tkinter palette constants in `hud_intent.py` / `intent_comp_overlay.py` that should change to
   match; the HUD is solid colours only (colour-key transparency, no alpha).
E. A short list of **UX rules** for this product (glance-ability during a 30 s timer, when to use
   motion, what never to show on the HUD).

Be opinionated and concrete. Prefer fewer, larger, higher-contrast elements. No scores on the HUD.
