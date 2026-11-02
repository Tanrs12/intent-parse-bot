# Full UX improvement and UI rebuild of the Flexers Draft Coach panel and coach page

You designed the Matchup Master visual system and reviewed this app four times (your 005 review was applied:
`draftcoach/static/draft-review.js`, team sheet, collapsed sections, Draft settings dialog on the panel). You
are now in WRITE mode inside a git worktree on branch `codex-ui-rebuild`. Rebuild the UI in place; Claude Code
is building a competing version in parallel and the team will pick the better one, so finish every file.

## What the team saw (screenshot described)

The floating panel (430x480, now defaulting to the dark "graphite" theme) during a scripted draft:
header "FLEXERS DRAFT / OUR TEAM · BLUE SIDE", "Dark" toggle, "Draft settings" button, "Auto", timer 17s.
Then "Pick Nami / SUPPORT · SASHA", "Practice pick", a "TEAM COMP · LEADING DRAFT" list of five rows
(role, player, champion, LOCKED / PICKS NEXT / slot), and a change line "Aristo locked Kai'Sa → Kai'Sa is gone,
Nami leads now". Champion portraits showed only initials ("N", "O", "KS"): the artwork did not load.

The verdict: "improve the UI. Where is the button to pick? Full UX improvement and UI rebuild, same style,
light mode default, include the artwork of our champions."

## What must be true afterwards

1. **The action is unmissable.** When it is this seat's turn the panel shows one primary button
   ("Lock in Nami") and a secondary "Hover" beside the headline, full width, in the accent colour, with the
   timer next to them. When it is a teammate's turn, say so in plain words ("Sasha picks next · Nami") and show
   no dead buttons. When the client cannot act (no League client, simulation), show the buttons disabled with a
   one-line reason. The existing JavaScript does this via `renderActions(snap)` using `snap.local`
   (`can_act`, `suggestion`, `kind`, `name`, `role`); keep that contract. Note `snap.local` is now also
   present in the scripted simulation (Claude Code is adding a simulated client so the buttons work in demo).
2. **Light ("porcelain") is the default theme** on the panel and the coach page; graphite stays as the toggle.
   Both must pass WCAG AA contrast for text.
3. **Champion artwork everywhere a champion is named.** Endpoints (all same-origin, cached by the server):
   `/api/champion-icon/{id}` square icon, `/api/art/tile/{id}` square tile, `/api/art/portrait/{id}` tall
   loading-screen portrait, `/api/art/splash/{id}` and `/api/art/centered/{id}` splash; `/api/art/porcelain/{id}`
   and `/api/art/gesture/{id}` from the Matchup Master collection when it is installed (404 otherwise, check
   `/api/art/index` which lists the slugs that exist). The lead pick should carry its centered splash as a
   backdrop or a large portrait; the team-comp rows and alternatives carry icons; never show initials when an
   id is known (champion ids are in every pick/row as `champion_id`, and `D.learnFromGuidance` fills `idFor`).
   Images must fade in and never cause layout shift (fixed boxes, `object-fit: cover`).
4. **Full team comp** stays: the five of the leading draft in role order, with the locked seats, the seat that
   picks next, our own seat (`state.our_team[].is_local`), and player names, now with art.
5. **Same visual language** (Matchup Master: tokens in `mm/tokens.css`, mono eyebrows, 1px lines, no
   gradients except art scrims, motion only on arrival). Fewer, larger, higher-contrast elements. The panel is
   glanced at during a 30 s timer.
6. **Packages.** The app is offline-capable (PyInstaller, no build step, static files served by FastAPI, a
   WebView2 window). If a UI/UX package genuinely improves the result (an icon set, a font, a motion helper,
   a tiny component library), install it with npm into `draftcoach/static/vendor/<package>/` (copy only the
   files used, note the version in `draftcoach/static/vendor/VENDORED.md`) and reference it with relative URLs.
   No CDN at runtime. If nothing earns its place, say so in VENDORED.md.
7. **Keep every element id the JavaScript uses** and these exact anchor lines (the INTent UI patch tool
   `tools/patch_flexers_intent_ui.py` matches them in coach.html / hud.html):
   - the `let latest = null, champions = {}, names = [], ...` declaration line
   - `const st = snapshot.status || {}, state = snapshot.state, g = snapshot.guidance, draft = snapshot.draft || {};`
   - the `if (!state || !g) { key = ... }` standby line in render
   - `$("pick-section").hidden = showBan || !state || oursLocked === 5; $("ban-section").hidden = !showBan;`
   - `$("other-section").hidden = !state || oursLocked === 5;`
   - `function renderClientActions(snapshot) {` + its first line
   Run `python tools/patch_flexers_intent_ui.py <dir>` against a copy laid out as
   `<dir>/_internal/draftcoach/static/` to prove it still applies, and `node --check` on every inline script.
8. **Coach page**: the same rebuild for `coach.html` + `mm/draft-coach.css` + `mm/draft-ui.css`: decision first
   with art, team sheet, compare drafts as cards with icons, light default.
9. **HUD palette**: update `LIGHT_THEME`/`THEMES` in `draftcoach/hud_intent.py` and
   `tools/intent_comp_overlay.py` if your tokens change (solid colours only, colour-key transparency).

## Files

`draftcoach/static/overlay.html`, `mm/draft-overlay.css`, `mm/tokens.css`, `mm/draft-ui.css`, `common.js`,
`draft-review.js`, `coach.html`, `mm/draft-coach.css`, `hud.html`, `draftcoach/hud_intent.py`,
`tools/intent_comp_overlay.py`, `tools/patch_flexers_intent_ui.py`, `docs/draftcoach/APP_DESIGN.md`,
`docs/draftcoach/codex_ux_review_2026-11-01.md` (your previous answer). The app runs with
`python run.py --simulate --window --bridge --no-auto-sync --no-update --load loadfiles/flexers-team.json`
(panel at http://127.0.0.1:8765/overlay, coach page at /), but another instance is already using ports 8765
and 8766, so do not start one; verify with `node --check`, the patch tool and `python -m pytest tests -q -x`.

Finish with a short summary: what changed, packages vendored (or none), and anything you could not do.
