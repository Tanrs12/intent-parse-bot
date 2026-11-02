# Implementation note · 2 November 2026

The `codex-ui-rebuild` implementation supersedes the proposed presentation below.
The panel now uses porcelain by default, an illustrated decision, a 44 px
**Lock in {champion}** primary action, adjacent **Hover**, and five comp rows with
champion icons. Teammates get **{player} picks next · {champion}**; unavailable
clients get disabled controls and a reason. Settings moved into the header dialog.

The coach page now starts with that decision and an illustrated leading five,
then draft comparison cards; the board and shared requirements occupy the second
column. The shared image loader fixes detached lazy images, and bundled icon
delivery fixes the server's previous external redirect during offline simulation.
High-resolution remote art is cached on disk. Existing palette values and native
HUD color keys remain unchanged. No runtime package was added.

Implementation and verification details are in `APP_DESIGN.md` and
`tools/verify_draftcoach_ui.py`. Historical code blocks below are review context,
not a patch to apply over the rebuilt files.

---

# Codex reply — ux-full-review

_model: config default · effort: xhigh · 1019s · rc=0 · status=ok · 2026-11-01 21:45_

Make **the next action** the dominant object. Move setup out of the first viewport, distinguish requirements from actual League locks, and turn completion into a game-plan view.

Two limits matter: the current bridge supplies a carry target, but **no protector assignments or next-game forecast**. It also emits `swaps: []`. The code below shows available local trade instructions and explicit fallback copy; it does not invent those missing recommendations.

No files changed. I checked the required anchors, parsed the proposed shared JavaScript, exercised seven decision/summary cases, and calculated palette contrast. Browser layout, WebView2 and Tk rendering remain unverified.

A. Use this information architecture.

| Face | Captain default | Player default | Progressive disclosure |
|---|---|---|---|
| **Coach** | Current decision → board → leading drafts → team sheet | Your action → board → team sheet | Compare drafts, shared settings, reveal order, analysis, player links/imports |
| **Floating panel** | Current action or team preparation; one reason; timer | Your action; otherwise the next team action | One **Draft settings** dialog containing format, identity, playbook and shake |
| **In-client HUD** | Same personal action as every player | Your pick/ban, your confirmed hover, or your trade | Nothing expands. Detailed planning belongs in Coach |
| **Gallery** | Optional comparison view beside League | Off by default | Explicit paging; no automatic page changes |

The elected captain is currently a **computation/sync role**, not an exclusive editing permission. Preserve that distinction: players initially see folded shared controls, but opening them permits the existing shared edits. Label them **“Edits affect everyone.”** Do not imply that followers lack permission.

Use these state transitions:

| State | Primary wording and content |
|---|---|
| Before draft | **Prepare your five**; lineup, format and shared requirements |
| Your turn | **Pick Galio** / **Ban Draven**; one reason; explicit Hover/Lock or Select/Ban |
| Teammate’s turn | **Team pick · Galio**; recipient and role; no local action button |
| Enemy turn | **Prepare Galio**; recommendation may change |
| Our five locked | **Our five locked · enemy still picking**; actual champions and any trade |
| Both teams locked | **Play front-to-back**; final five, carry focus, trades, protection/next-game details |
| Stale/disconnected | **Waiting for a fresh draft**; remove actionable recommendations |

Use Coach on the second monitor. Use the panel when Coach is closed or inconvenient. Use the HUD while looking at League. The gallery is an explicit captain accessory. Window mutual exclusion needs launcher integration; these files do not expose a reliable API for implementing it.

B. Apply the following coach changes.

The `diff` fences below express exact old/new replacements, not a standalone `git apply` patch.

In `draftcoach/static/coach.html`, make these markup replacements:

```diff
-<body class="coach-page">
+<body class="coach-page graphite">
```

```diff
-  <section class="mm-card coach-lobby" id="lobby-card" aria-labelledby="lobby-title">
-    <header class="fd-card-head"><h2 id="lobby-title">Lobby</h2><span class="mm-meta" id="lobby-sub">Waiting for players</span></header>
+  <details class="mm-card coach-lobby" id="lobby-card" aria-labelledby="lobby-title">
+    <summary class="fd-card-head"><span id="lobby-title">Player links &amp; pools</span><span class="mm-meta" id="lobby-sub">Waiting for players</span></summary>
```

```diff
-    <p class="fd-note">Link an account to use that player&rsquo;s champion pools. Default pools remain available for unlinked players.</p>
-  </section>
+    <p class="fd-note">Link an account to use that player&rsquo;s champion pools. Default pools remain available for unlinked players.</p>
+  </details>
```

There are currently **two `order-sub` IDs**. Keep the guidance ID and rename the board description:

```diff
-      <details class="fd-details"><summary>Draft order</summary><p class="fd-note" id="order-sub"></p><div class="seq" id="seq"></div></details>
+      <details class="fd-details"><summary>Draft order</summary><p class="fd-note" id="board-order-description"></p><div class="seq" id="seq"></div></details>
```

```diff
-    $("order-sub").textContent = (snapshot.draft_types && snapshot.draft_types[snapshot.draft_type] || {}).description || "";
+    $("board-order-description").textContent = (snapshot.draft_types && snapshot.draft_types[snapshot.draft_type] || {}).description || "";
```

```diff
-          <header class="coach-section-head"><h3>Draft control</h3><span class="mm-meta" id="control-sync"></span></header>
+          <header class="coach-section-head"><h3>Team sheet</h3><span class="mm-meta" id="control-sync"></span></header>
```

```diff
-          <div class="control-locks" id="control-locks"></div>
+          <p class="fd-note">Requirements shape every suggested draft. “Lock in” confirms a champion in League.</p>
+          <div class="control-locks" id="control-locks"></div>
```

```diff
-          <header class="coach-section-head"><h3>Draft captain</h3><span class="mm-meta" id="captain-side"></span></header>
+          <header class="coach-section-head"><h3>Ranked team drafts</h3><span class="mm-meta" id="captain-side"></span></header>
```

```diff
-        <details class="fd-details"><summary>Draft analysis</summary>
+        <details class="fd-details" id="analysis-section"><summary>Draft analysis</summary>
```

Load the shared presentation file provided below:

```diff
-<script src="/static/common.js"></script>
+<script src="/static/common.js"></script>
+<script src="/static/draft-review.js"></script>
```

Mount the new layout once. It moves existing nodes, preserving their IDs and handlers:

```diff
-  D.hydrateIcons(document);
+  D.hydrateIcons(document);
+  D.Review.mountCoach();
+  let coachStreamOpen = false;
```

Preserve the requested declaration and guidance anchors. Change only this preceding render entry:

```diff
-  function render(snapshot) {
-    latest = snapshot;
+  function render(snapshot) {
+    latest = snapshot;
+    snapshot = D.Review.liveSnapshot(snapshot, intentGuidance);
+    if (!coachStreamOpen) snapshot = {
+      ...snapshot,
+      status: { ...snapshot.status, stale: true }
+    };
```

Replace **both occurrences** of this statement—one in polling, one in `postControl`’s `finally`:

```diff
-if (latest) renderDraftControl(latest);
+if (latest) render(latest);
```

Make failed control reads invalidate the cached controls:

```diff
-      if (r.ok) { const d = await r.json(); intentControl = d.control; identityList = d.identities || identityList; }
+      if (!r.ok) throw new Error("control " + r.status);
+      const d = await r.json();
+      intentControl = d.control;
+      identityList = d.identities || identityList;
```

Make `postControl` return whether the save succeeded:

```diff
-    if (controlBusy) return;
+    if (controlBusy) return false;
```

```diff
-      toast(update.shake_it_up ? "Shaken: new drafts coming" : "Draft control updated");
-    } catch (e) { toast("Could not update draft control: " + e.message, true); }
+      toast(update.shake_it_up ? "Finding different drafts…" : "Shared draft updated.");
+      return true;
+    } catch (e) {
+      toast("Could not update the shared draft: " + e.message, true);
+      return false;
+    }
```

Replace this entire existing team-lock block:

```javascript
    // Hard locks: one row per seated player.
    const box = $("control-locks"); box.replaceChildren();
    const roleLocks = intentControl.role_locks || {}, champLocks = intentControl.champion_locks || {};
    for (const seat of state.our_team || []) {
      if (!seat.role) continue;
      const row = D.el("div", "control-lock");
      const name = D.el("b", "", seat.display_name || seat.role);
      const roleLabel = D.el("span", "mm-meta", (D.ROLE_SHORT[seat.role] || seat.role));
      const lockRole = D.el("label", "control-check");
      const cb = D.el("input"); cb.type = "checkbox"; cb.checked = Number(roleLocks[seat.role] || 0) === Number(seat.user_id || -1) && !!seat.user_id;
      cb.disabled = !seat.user_id;
      cb.onchange = () => { const next = { ...roleLocks }; if (cb.checked) next[seat.role] = seat.user_id; else delete next[seat.role]; postControl({ role_locks: next }); };
      lockRole.append(cb, document.createTextNode(" keep " + (D.ROLE_SHORT[seat.role] || seat.role).toLowerCase()));
      const champ = D.el("input", "control-champ"); champ.placeholder = "lock champion"; champ.setAttribute("list", "champion-names");
      champ.value = champLocks[seat.role] || "";
      champ.onchange = () => { const next = { ...champLocks }; if (champ.value.trim()) next[seat.role] = champ.value.trim(); else delete next[seat.role]; postControl({ champion_locks: next }); };
      row.append(roleLabel, name, lockRole, champ);
      if (seat.locked) row.append(D.el("span", "mm-meta", "locked " + seat.locked));
      box.append(row);
    }
```

With:

```javascript
    D.Review.teamSheet(
      $("control-locks"), snapshot, g, intentControl,
      postControl, () => intentControl, names, controlBusy
    );
```

This retains each input across polling, uses explicit Save, validates champion spelling, and merges edits against the latest available control object.

Remove decorative splash loading:

```diff
-    setHero((head.querySelector(".portrait") || {}).title || "");
+    setHero("");
```

Finish each render with the shared summary and local action presenter. The original `renderClientActions` declaration and first line remain untouched:

```diff
-    renderClientActions(snapshot);
-    D.hydrateIcons($("headline"));
+    renderClientActions(snapshot);
+    D.Review.coachEnd(
+      snapshot, g,
+      typeof intentBridge !== "undefined" ? intentBridge : intentGuidance,
+      clientAct, acting
+    );
+    D.hydrateIcons($("headline"));
```

Replace the final connection subscription:

```diff
-  D.connect(render, (s) => { if (s === "closed") { $("status").textContent = "Server unreachable"; $("status").dataset.mode = "error"; deadline = 0; } });
+  D.connect((snapshot) => {
+    coachStreamOpen = true;
+    render(snapshot);
+  }, (s) => {
+    if (s !== "closed") return;
+    coachStreamOpen = false;
+    deadline = 0;
+    if (latest) render(latest);
+    $("status").textContent = "Server unreachable";
+    $("status").dataset.mode = "error";
+  });
```

For captain cards, keep rank and team identity visible; put numerical estimates inside “Why this plan.” Preserve open details and pause card replacement while somebody is interacting with them.

```diff
-  let captainRanks = new Map();
+  let captainRanks = new Map();
+  let captainRenderKey = "";
```

Inside `renderCaptain`:

```diff
-    if (!state || !comps.length) { section.hidden = true; captainRanks = new Map(); return; }
+    if (!state || !comps.length) {
+      section.hidden = true;
+      captainRanks = new Map();
+      captainRenderKey = "";
+      return;
+    }
+    const renderKey = JSON.stringify([g, state.our_team]);
+    if (renderKey === captainRenderKey) return;
+    if (box.matches(":hover, :focus-within")) {
+      section.dataset.waiting = "true";
+      return;
+    }
+    section.dataset.waiting = "false";
+    captainRenderKey = renderKey;
+    const expandedCards = new Set(
+      [...box.querySelectorAll(".captain-comp")]
+        .filter(row => row.querySelector(".captain-detail")?.open)
+        .map(row => row.dataset.signature)
+    );
+    const moreWasOpen = !!box.querySelector(".captain-more")?.open;
```

```diff
-    box.replaceChildren();
-    const nextRanks = new Map();
-    comps.slice(0, 6).forEach((comp, index) => {
+    box.replaceChildren();
+    const more = D.el("details", "fd-details captain-more");
+    more.open = moreWasOpen;
+    more.append(D.el("summary", "", "Show " + Math.max(0, Math.min(10, comps.length) - 3) + " more drafts"));
+    const nextRanks = new Map();
+    comps.slice(0, 10).forEach((comp, index) => {
```

```diff
-      const row = D.el("div", "captain-comp" + (index === 0 ? " lead" : "") + (isPreferred ? " preferred" : ""));
+      const row = D.el("div", "captain-comp" + (index === 0 ? " lead" : "") + (isPreferred ? " preferred" : ""));
+      row.dataset.signature = signature;
```

```diff
-      head.append(D.el("b", "", "#" + (index + 1) + (index === 0 ? " BEST" : "")), D.el("span", "captain-move " + (movement > 0 ? "up" : movement < 0 ? "down" : ""), movementText));
+      head.append(
+        D.el("b", "", comp.team_plan || "Team draft"),
+        D.el("span", "captain-rank", index === 0 ? "#1 · Leading" : "#" + (index + 1))
+      );
```

```diff
-      head.append(prefer, D.el("span", "captain-score", Number(comp.live_score ?? comp.score ?? 0).toFixed(1)));
+      prefer.setAttribute("aria-pressed", String(isPreferred));
+      head.append(prefer);
```

```diff
-        chip.append(D.portrait(champion, "tiny", (comp.champion_ids || {})[role]), D.el("b", "", champion), D.el("small", "", D.ROLE_SHORT[role] || role));
+        const owner = comp.players?.[role];
+        const playerName = typeof owner === "string" ? owner : owner?.display_name || "";
+        chip.append(
+          D.el("small", "", D.ROLE_SHORT[role] || role),
+          D.portrait(champion, "", (comp.champion_ids || {})[role]),
+          D.el("b", "", champion),
+          D.el("small", "captain-player", playerName)
+        );
+        chip.title = [role, champion, playerName].filter(Boolean).join(" · ");
```

```diff
-      const detailsBits = [];
+      const detailsBits = [
+        "Model estimate: " + Number(comp.live_score ?? comp.score ?? 0).toFixed(1) +
+        ". For comparing drafts; not a win probability."
+      ];
```

```diff
-        detail.append(D.el("summary", "", "Why this plan"));
+        detail.open = expandedCards.has(signature);
+        detail.append(D.el("summary", "", "Why this plan"));
```

```diff
-      box.append(row);
-    });
-    captainRanks = nextRanks;
+      (index < 3 ? box : more).append(row);
+    });
+    if (more.childElementCount > 1) box.append(more);
+    captainRanks = nextRanks;
```

Because cards can remain stationary during interaction, validate a Prefer click against current guidance:

```diff
-  async function setCaptainPreference(signature) {
-    try {
+  async function setCaptainPreference(signature) {
+    const current = typeof intentBridge !== "undefined"
+      ? intentBridge.guidance : intentGuidance?.guidance || latest?.guidance;
+    if (signature && !(current?.comps || []).some(comp =>
+      D.ROLES.map(role => comp.champions?.[role] || "?").join("|") === signature
+    )) {
+      toast("That draft is no longer available. Choose a current draft.", true);
+      return;
+    }
+    try {
```

Change the obsolete “top six” message:

```diff
-      prefNote.textContent = "Preferred plan is no longer available in the current top six · INTent is drafting normally.";
+      prefNote.textContent = "Your preferred draft is unavailable. Following the leading draft.";
```

Create `draftcoach/static/draft-review.js` with this complete content:

```javascript
/* Shared presentation only: the bridge remains the ranking authority. */
(function (D) {
  "use strict";
  const $ = D.$, el = D.el;
  const role = r => ({
    ADC: "Bot", Jungle: "Jungle", Support: "Support"
  }[r] || r || "Unassigned");

  const sheets = new WeakMap();
  let sheetContext = null;

  function localPick(snapshot, g) {
    const s = snapshot.state, local = snapshot.local, a = s?.active;
    if (!s || !g || snapshot.status?.stale || !local?.client ||
        !local.can_act || !a?.ours) return null;

    if (a.kind === "ban") {
      const suggested = (g.bans || []).find(
        p => p.champion === local.suggestion?.champion
      );
      const p = suggested || g.bans?.[0];
      return p ? { ...p, kind: "ban" } : null;
    }

    if (a.kind !== "pick") return null;
    const me = (s.our_team || []).find(p => p.is_local);
    if (!me || me.locked || !me.user_id) return null;
    const p = (g.picks || []).find(p =>
      p.player_id != null && String(p.player_id) === String(me.user_id)
    );
    return p ? { ...p, kind: "pick" } : null;
  }

  function liveSnapshot(snapshot, bridge) {
    if (!snapshot.state || snapshot.state.source === "manual") return snapshot;
    const g = bridge?.active ? bridge.guidance : null;
    return {
      ...snapshot,
      guidance: g,
      local: snapshot.local ? {
        ...snapshot.local, suggestion: localPick(snapshot, g)
      } : snapshot.local
    };
  }

  function summaryModel(snapshot, g) {
    const s = snapshot.state;
    const seats = s?.our_team || [];
    if (seats.length !== 5 || seats.some(p => !p.locked)) return null;

    const actual = seats.map(p => p.locked).sort().join("|");
    const comp = (g?.comps || []).find(c =>
      D.ROLES.map(r => c.champions?.[r])
        .filter(Boolean).sort().join("|") === actual
    );
    const carry = comp?.carry_plan;
    const sw = snapshot.local?.incoming_swap;
    const own = snapshot.local?.swap;
    const swaps = (g?.swaps || []).map(x =>
      (x.champion || "Champion") + " to " +
      (x.to_player || role(x.to_role))
    );

    if (sw?.can_accept) {
      swaps.unshift(
        "Receive " + (sw.champion || "their champion") +
        " from " + sw.from_player + "."
      );
    } else if (own) {
      swaps.unshift(
        "Give " + own.give + "; receive " +
        (own.receive || "their champion") +
        " from " + own.with_player + "."
      );
    }

    return {
      title: comp?.team_plan ? "Play " + comp.team_plan : "Your final five",
      phase: (s.enemy_team || []).filter(p => p.locked).length === 5
        ? "Game plan" : "Our five locked · enemy still picking",
      seats: seats.map(p => ({
        role: role(p.role),
        player: p.display_name || "Player",
        champion: p.locked,
        local: !!p.is_local
      })),
      carry: carry?.champion
        ? "Carry focus: " + (carry.display_name || role(carry.role)) +
          " · " + carry.champion + "."
        : "Carry focus unavailable.",
      protection:
        "Peel assignments are not supplied. Agree who stays with the carry.",
      swaps: swaps.length
        ? swaps.join(" ") : "No swap instruction received.",
      next: snapshot.draft_type === "fearless"
        ? "Next game: record this game in Coach, then build an order from the remaining champions."
        : "Next game: recalculate after the side, lineup and bans are known.",
      order: (comp?.pick_order || [])
        .map(p => role(p.role) + " · " + p.champion).join(" → ")
    };
  }

  function summary(box, model, compact) {
    box.hidden = !model;
    if (!model) {
      box.dataset.key = "";
      box.replaceChildren();
      return;
    }

    const key = JSON.stringify([model, compact]);
    if (box.dataset.key === key) return;

    const open = box.querySelector("details")?.open || false;
    box.dataset.key = key;
    box.replaceChildren();

    const five = el("ul", "summary-five");
    for (const s of model.seats) {
      const li = el("li");
      li.append(
        el("span", "mm-meta", s.role),
        el("strong", "", s.champion),
        el("span", "summary-player", s.player + (s.local ? " · You" : ""))
      );
      five.append(li);
    }

    box.append(five, el("p", "summary-carry", model.carry));

    const detail = el("details", "fd-details summary-detail");
    detail.open = open;
    detail.append(
      el("summary", "", "Protection, swaps & next game"),
      el("p", "fd-note", model.protection),
      el("p", "fd-note", model.swaps),
      el("p", "fd-note", model.next)
    );
    if (model.order) {
      detail.append(el(
        "p", "fd-note", "This draft’s planned order · " + model.order
      ));
    }
    box.append(detail);
  }

  function fold(id, label, host) {
    const details = el("details", "fd-details");
    details.id = id + "-fold";
    details.append(el("summary", "", label), $(id));
    host.append(details);
  }

  function mountCoach() {
    const aside = document.querySelector(".coach-guidance");
    const decision = aside.querySelector(".mm-card");
    decision.classList.add("coach-decision");

    const support = el("div", "coach-support");
    aside.append(support);

    const context = el("div", "coach-context");
    $("board").before(context);

    const sheet = el("details", "mm-card team-sheet");
    sheet.id = "team-sheet-fold";
    sheet.append(
      el("summary", "", "Team sheet & shared settings"),
      $("control-section")
    );
    context.append($("board"), sheet);

    decision.append($("intent-actions"));

    const box = el("section", "draft-summary");
    box.id = "draft-summary";
    box.hidden = true;
    box.setAttribute("aria-label", "Final team and game plan");
    decision.append(box);

    fold("pick-section", "Other picks", support);
    fold("ban-section", "Ban options", support);
    fold("order-section", "Reveal order", support);
    fold("captain-section", "Compare team drafts", support);
    fold("plans-section", "Playbook", support);
    support.append($("other-section"), $("analysis-section"));

    document.querySelector(".coach-main").append($("lobby-card"));
  }

  function teamSheet(
    box, snapshot, g, control, submit, getControl, names, busy
  ) {
    sheetContext = {
      snapshot, g, control, submit, getControl, names, busy
    };
    let cache = sheets.get(box);
    if (!cache) {
      cache = new Map();
      sheets.set(box, cache);
    }

    const wanted = new Set();

    for (const [index, seat] of
      (snapshot.state?.our_team || []).entries()) {
      const key = String(seat.cell_id ?? index);
      wanted.add(key);
      let row = cache.get(key);

      if (!row) {
        const form = el("form", "team-sheet-row");
        const who = el("div", "sheet-who");
        const position = el("span", "mm-meta");
        const player = el("strong");
        who.append(position, player);

        const lock = el("button", "fd-button sheet-role");
        lock.type = "button";

        const label = el("label", "sheet-champion");
        label.append(el("span", "mm-meta", "Required champion"));

        const input = el("input");
        input.type = "text";
        input.name = "required-champion";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.setAttribute("list", "champion-names");
        input.placeholder = "Any champion";
        label.append(input);

        const save = el("button", "fd-button");
        save.type = "submit";
        save.textContent = "Save";

        const status = el("span", "sheet-status");
        status.setAttribute("role", "status");

        form.append(who, lock, label, save, status);
        row = {
          form, position, player, lock, input, save, status,
          seat, dirty: false, pending: false
        };

        input.oninput = () => {
          row.dirty = true;
          input.setCustomValidity("");
        };

        lock.onclick = async () => {
          const c = sheetContext, s = row.seat;
          if (row.pending || c.busy ||
              !s.role || !s.user_id || s.locked) return;

          const next = { ...(c.getControl()?.role_locks || {}) };
          const pinned =
            String(next[s.role] || "") === String(s.user_id);
          if (pinned) delete next[s.role];
          else next[s.role] = s.user_id;

          row.pending = true;
          lock.disabled = true;
          try {
            await c.submit({ role_locks: next });
          } finally {
            row.pending = false;
          }
        };

        form.onsubmit = async event => {
          event.preventDefault();
          const c = sheetContext, s = row.seat;
          if (row.pending || c.busy || s.locked || !s.role) return;

          const value = input.value.trim();
          const canonical = c.names.find(
            n => n.toLowerCase() === value.toLowerCase()
          );

          if (value && !canonical) {
            input.setCustomValidity(
              "Choose a champion from the list, or clear this field."
            );
            input.reportValidity();
            return;
          }

          const next = {
            ...(c.getControl()?.champion_locks || {})
          };
          if (canonical) next[s.role] = canonical;
          else delete next[s.role];

          row.pending = true;
          save.disabled = true;
          save.textContent = "Saving…";

          try {
            if (await c.submit({ champion_locks: next })) {
              row.dirty = false;
            }
          } finally {
            row.pending = false;
            save.textContent = "Save";
          }
        };

        cache.set(key, row);
        box.append(form);
      }

      if (row.seat.role !== seat.role) {
        row.dirty = false;
        row.input.value = "";
        row.input.setCustomValidity("");
      }

      row.seat = seat;
      row.position.textContent =
        "Seat " + (index + 1) + " · " + role(seat.role);
      row.player.textContent =
        (seat.display_name || "Waiting for player") +
        (seat.is_local ? " · You" : "");

      const pinned = !!seat.user_id &&
        String(control.role_locks?.[seat.role] || "") ===
        String(seat.user_id);

      row.lock.textContent = pinned ? "Role fixed" : "Role flexible";
      row.lock.setAttribute("aria-pressed", String(pinned));
      row.lock.setAttribute(
        "aria-label",
        (pinned ? "Release " : "Fix ") +
        (seat.display_name || "player") + " in " + role(seat.role)
      );
      row.lock.disabled = busy || row.pending ||
        !seat.user_id || !seat.role || !!seat.locked;
      row.input.disabled =
        busy || row.pending || !seat.role || !!seat.locked;
      row.save.disabled = row.input.disabled;

      if (!row.dirty && document.activeElement !== row.input) {
        row.input.value =
          control.champion_locks?.[seat.role] || "";
      }

      const active = snapshot.state?.active;
      row.form.dataset.local = String(!!seat.is_local);
      row.status.textContent = seat.locked
        ? "In League · " + seat.locked
        : active?.ours &&
          (active.cell_ids || []).includes(seat.cell_id)
          ? (active.kind === "ban" ? "Banning now" : "Picking now")
          : "Waiting for turn";
    }

    for (const [key, row] of cache) {
      if (!wanted.has(key)) {
        row.form.remove();
        cache.delete(key);
      }
    }

    let dl = $("champion-names");
    if (!dl) {
      dl = el("datalist");
      dl.id = "champion-names";
      document.body.append(dl);
    }
    if (dl.options.length !== names.length) {
      dl.replaceChildren(...names.map(n => new Option(n)));
    }
  }

  function coachEnd(snapshot, g, bridge, act, busy) {
    const model = summaryModel(snapshot, g);
    const fresh = !snapshot.status?.stale &&
      snapshot.status?.connection !== "error";

    summary($("draft-summary"), fresh ? model : null, false);
    document.body.dataset.complete = String(!!model);

    const sync = bridge?.sync || {};
    const follower = /follower/.test(sync.mode || "");

    $("control-sync").textContent = follower
      ? "Following " + (sync.leader || "the captain") +
        " · edits affect everyone"
      : /host/.test(sync.mode || "")
        ? "You are captain · edits affect everyone"
        : "Local draft";

    const compare = $("captain-section-fold");
    if (!compare.dataset.initialised && bridge?.active) {
      compare.open = !follower;
      $("team-sheet-fold").open = !follower;
      compare.dataset.initialised = "true";
    }
    $("team-sheet-fold").hidden =
      !fresh || !!model || $("control-section").hidden;
    document.body.dataset.audience = follower ? "player" : "captain";

    for (const id of [
      "pick-section", "ban-section", "order-section",
      "captain-section", "plans-section"
    ]) {
      $(id + "-fold").hidden =
        !fresh || !!model || $(id).hidden;
    }

    if (model) {
      $("control-section").hidden = true;
      $("statekey").textContent = model.phase;
      $("headline").textContent = model.title;
      $("reason").textContent =
        "Check champion trades before loading in.";
      $("change").hidden = true;
    }

    const actions = $("intent-actions");
    actions.hidden = true;
    if (snapshot.state?.source !== "manual") {
      $("client-actions").hidden = true;
    }

    if (!fresh) {
      $("statekey").textContent = "Connection lost";
      $("headline").textContent = "Waiting for a fresh draft";
      $("reason").textContent =
        "Live recommendations are unavailable.";
      return;
    }

    const local = snapshot.local;
    if (!local?.client) return;

    const hover = $("intent-hover");
    const lock = $("intent-lock");
    const label = $("intent-actions-label");

    hover.hidden = true;
    hover.disabled = lock.disabled = busy;
    hover.onclick = lock.onclick = null;

    let action = null, body = {};

    if (local.incoming_swap?.can_accept) {
      const sw = local.incoming_swap;
      label.textContent =
        "Give " + (local.locked || "your champion") +
        " · receive " + (sw.champion || "their champion") +
        " from " + sw.from_player +
        (sw.expected ? "" : " · unplanned");
      lock.textContent = "Accept swap";
      action = "swap_accept";
      body = { trade_id: sw.trade_id };
    } else if (local.swap?.can_request) {
      const sw = local.swap;
      label.textContent =
        "Give " + sw.give + " · receive " +
        (sw.receive || "their champion") +
        " from " + sw.with_player;
      lock.textContent = "Request swap";
      action = "swap_request";
    } else {
      const pick = localPick(snapshot, g);
      if (!pick) return;

      const ban = pick.kind === "ban";
      label.textContent =
        ban ? "Your ban" : "Your pick · " + role(pick.role);
      lock.textContent =
        (ban ? "Ban " : "Lock in ") + pick.champion;
      action = "lock";
      body = { champion: pick.champion };

      hover.hidden = false;
      hover.textContent = ban ? "Select" : "Hover";
      hover.onclick = () =>
        act("hover", { champion: pick.champion });

      $("statekey").textContent = label.textContent;
      $("headline").replaceChildren(
        D.portrait(pick.champion, "", pick.champion_id),
        document.createTextNode(
          (ban ? "Ban " : "Pick ") + pick.champion
        )
      );
      $("reason").textContent = ban
        ? D.overlayBanReason(pick)
        : D.overlayPickReason(pick);
    }

    lock.onclick = () => act(action, body);
    actions.hidden = false;
  }

  D.Review = {
    localPick, liveSnapshot, summaryModel, summary,
    mountCoach, teamSheet, coachEnd
  };
})(window.Draft);
```

One existing transport limitation remains: Discord snowflakes should be serialized as **strings**. Replacing `Number(...)` comparisons with `String(...)` avoids further coercion, but cannot recover precision already lost when JSON numeric IDs exceed JavaScript’s safe integer range.

Append this to `draftcoach/static/mm/draft-coach.css`:

```css
/* Decision-first coach layout. */
.coach-main {
  max-width: 1480px;
  padding: 24px;
}
.coach-workspace {
  grid-template-columns: minmax(0, 1.12fr) minmax(480px, 1fr);
  gap: 24px;
  margin-top: 0;
}
.coach-context, .coach-guidance, .coach-support {
  min-width: 0;
}
.coach-context {
  display: grid;
  gap: 20px;
}
.coach-guidance {
  position: static;
}
.coach-guidance .mm-card {
  overflow: visible;
}
.coach-decision {
  border-top: 3px solid var(--mm-blue);
  padding: 24px;
}
.coach-hero {
  display: none;
}
.coach-guidance-head,
.coach-headline,
.coach-reason {
  text-shadow: none;
}
.coach-guidance-head {
  align-items: center;
}
.coach-guidance-head [role="timer"] {
  font-size: 24px;
  font-weight: 700;
  color: var(--mm-ink);
}
.coach-headline {
  flex-wrap: wrap;
  margin: 14px 0 12px;
  font-size: 32px;
  line-height: 1.15;
  font-weight: 650;
  letter-spacing: -.65px;
  overflow-wrap: anywhere;
}
.coach-headline .portrait {
  width: 52px;
  height: 52px;
}
.coach-headline small {
  flex-basis: 100%;
  font-size: 12px;
}
.coach-reason {
  font-size: 16px;
  line-height: 1.5;
}
.coach-support {
  padding: 0 8px;
}
.coach-section-head {
  flex-wrap: wrap;
}
.coach-section-head h3 {
  font-size: 17px;
  font-weight: 600;
}
.coach-lobby {
  margin-top: 24px;
}
.coach-lobby > summary,
.team-sheet > summary {
  cursor: pointer;
  font-size: 16px;
  font-weight: 600;
}
.coach-lobby > summary {
  margin-bottom: 0;
}
.coach-lobby[open] > summary {
  margin-bottom: 20px;
}

.coach-teams {
  gap: 20px;
}
.slot {
  grid-template-columns: 40px minmax(0, 1fr) auto;
  min-height: 72px;
}
.slot .portrait {
  width: 40px;
  height: 40px;
}
.slot .name {
  font-size: 16px;
  font-weight: 600;
}
.slot .sub {
  font-size: 13px;
}
.slot .role, .slot .state {
  font-size: 11px;
}
.slot.locked-art {
  background: none;
}
.coach-board-controls {
  gap: 10px;
}

.control-row {
  flex-wrap: wrap;
  gap: 12px;
}
.control-field {
  min-width: min(240px, 100%);
}
.control-field select {
  min-height: 40px;
}
#control-sync {
  flex-basis: 100%;
  text-transform: none;
  letter-spacing: 0;
}
.control-locks {
  gap: 0;
}
.team-sheet-row {
  display: grid;
  grid-template-columns:
    minmax(110px, 1fr) auto minmax(145px, 1.2fr) auto;
  align-items: end;
  gap: 8px 12px;
  padding: 16px 0;
  border-top: 1px solid var(--mm-line);
}
.team-sheet-row[data-local="true"] {
  border-top-color: var(--mm-blue);
}
.sheet-who, .sheet-champion {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.sheet-who strong {
  font-size: 16px;
  overflow-wrap: anywhere;
}
.sheet-role {
  white-space: nowrap;
  padding-inline: 10px;
}
.sheet-champion input {
  width: 100%;
  min-height: 40px;
}
.sheet-status {
  grid-column: 1 / -1;
  font-size: 13px;
  color: var(--mm-secondary);
}
.control-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  border-top: 0;
  margin-top: 16px;
  padding-top: 0;
}
.control-actions .mm-meta {
  flex: 1 0 100%;
  font-size: 13px;
  text-transform: none;
  letter-spacing: 0;
}
.control-actions .fd-button {
  min-height: 44px;
  font-size: 16px;
}
.control-actions .primary {
  flex: 1;
}
#intent-hover {
  min-width: 100px;
}

.card {
  grid-template-columns: 40px minmax(0, 1fr);
}
.card .portrait {
  width: 40px;
  height: 40px;
}
.card .champ {
  font-size: 17px;
  font-weight: 600;
}
.card .role, .card .why {
  font-size: 13px;
}
.card .score, .card .tags {
  display: none;
}
.change, .alert, .plan-note {
  font-size: 14px;
}
#captain-locks {
  display: none;
}
#captain-section[data-waiting="true"]::before {
  content: "Live changes waiting · move away to refresh";
  display: block;
  margin-top: 12px;
  color: var(--mm-warn);
  font-size: 12px;
}
.captain-phase {
  padding: 12px;
}
.captain-comp {
  margin-top: 12px;
  padding: 16px;
  border: 1px solid var(--mm-line);
  border-radius: 4px;
  background: var(--mm-surface);
}
.captain-comp.lead {
  border-color: var(--mm-blue);
}
.captain-comp.preferred {
  box-shadow: inset 3px 0 var(--mm-blue);
  padding-left: 16px;
}
.captain-comp-head {
  align-items: center;
  flex-wrap: wrap;
}
.captain-comp-head > b {
  flex: 1 1 170px;
  font: 600 18px/1.3 var(--mm-sans);
  letter-spacing: 0;
}
.captain-rank {
  font: 12px/1.4 var(--mm-mono);
  color: var(--mm-secondary);
}
.captain-prefer {
  min-height: 36px;
  margin-left: 0;
  padding: 7px 12px;
  font: 600 13px/1.2 var(--mm-sans);
  text-transform: none;
}
.captain-role-row {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-top: 16px;
}
.captain-role-row .chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
  gap: 5px;
  padding: 0;
  border: 0;
  border-radius: 0;
  font-size: 14px;
}
.captain-role-row .portrait {
  width: 36px;
  height: 36px;
}
.captain-role-row .chip b {
  overflow-wrap: anywhere;
}
.captain-role-row .chip small {
  font-size: 11px;
}
.captain-role-row .captain-player {
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-transform: none;
  font-family: var(--mm-sans);
}
.captain-preference-note,
.captain-detail,
.captain-detail-line {
  font-size: 13px;
  line-height: 1.5;
}
.captain-detail summary {
  font: 13px/1.5 var(--mm-sans);
  text-transform: none;
  letter-spacing: 0;
}
.captain-detail-line {
  margin-top: 8px;
}

@media (max-width: 1100px) {
  .coach-workspace {
    grid-template-columns: minmax(0, 1fr);
  }
  .coach-guidance {
    grid-row: 1;
  }
}
@media (max-width: 640px) {
  .coach-main {
    padding: 14px;
  }
  .coach-decision {
    padding: 20px;
  }
  .coach-headline {
    font-size: 28px;
  }
  .team-sheet-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .sheet-champion {
    grid-column: 1;
  }
  .captain-role-row {
    gap: 6px;
  }
  .captain-role-row .chip {
    font-size: 12px;
  }
}
```

C. Put panel settings in one native dialog.

In `draftcoach/static/overlay.html`, replace the entire existing `<div class="ov-controls">…</div>` block with:

```html
    <div class="ov-controls">
      <button class="fd-button" id="settings-open" type="button"
              aria-haspopup="dialog" aria-controls="draft-settings">
        Draft settings
      </button>
      <span class="ov-settings-state" id="settings-label">Auto identity</span>
      <div class="ov-clock" id="clock-wrap" hidden>
        <span class="mm-icon" data-icon="clock" aria-hidden="true"></span>
        <span role="timer" aria-live="off"
              aria-label="Time remaining" id="timer">&mdash;</span>
      </div>
    </div>

    <dialog class="ov-settings" id="draft-settings"
            aria-labelledby="settings-title">
      <form method="dialog" class="ov-settings-head">
        <h2 id="settings-title">Draft settings</h2>
        <button class="fd-button" value="close" autofocus>Done</button>
      </form>

      <p class="fd-note">Identity and Shake change the shared draft for everyone.</p>

      <div class="ov-settings-fields">
        <label class="ov-format">
          <span class="mm-meta">Draft format</span>
          <select id="type" aria-describedby="format-help"></select>
        </label>
        <span id="format-help" class="sr-only">
          Choose the format before picks or bans begin.
        </span>

        <label class="ov-format">
          <span class="mm-meta">Team identity</span>
          <select id="identity" aria-label="Preferred team identity">
            <option value="">Auto</option>
          </select>
        </label>

        <label class="ov-format ov-settings-wide">
          <span class="mm-meta">Playbook plan</span>
          <select id="plan" aria-label="Playbook plan">
            <option value="">Any plan</option>
          </select>
        </label>

        <button class="fd-button ov-settings-wide" id="shake" type="button">
          Find different drafts
        </button>
      </div>

      <p class="fd-note">
        A preferred identity can be overridden by a substantially stronger draft.
      </p>
      <p id="settings-feedback" role="status" aria-live="polite"></p>
    </dialog>
```

The entire replaced block is bounded by the unique existing `<div class="ov-controls">` and the following `<main class="ov-body">`; retain that `<main>`.

Insert the post-draft host:

```diff
-      <div id="order" hidden>
+      <section id="post-draft-summary" class="draft-summary"
+               aria-label="Final team and game plan" hidden></section>
+      <div id="order" hidden>
```

Load the shared file:

```diff
-<script src="/static/common.js"></script>
+<script src="/static/common.js"></script>
+<script src="/static/draft-review.js"></script>
```

Use dark by default in both browser and desktop:

```diff
-  let graphite = hosted;
+  let graphite = true;
```

Bind the dialog:

```diff
-  D.hydrateIcons(document);
+  D.hydrateIcons(document);
+  $("settings-open").onclick = () => $("draft-settings").showModal();
```

Replace the existing shared-control block—from the exact comment below through the closing `$("shake").onclick` handler:

```javascript
  // ---- shared draft control (identity, shake) through the INTent bridge ---------
```

With:

```javascript
  // ---- shared draft control and authoritative panel guidance -------------------
  const BRIDGE = "http://127.0.0.1:8766";
  let identityPending = false, identityFilled = false;
  let panelBridge = null, panelControl = null, controlPolling = false;

  async function pollControl() {
    if (controlPolling || identityPending) return;
    controlPolling = true;

    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 4000);

    try {
      const [controlResponse, guidanceResponse] = await Promise.all([
        fetch(BRIDGE + "/api/control", {
          cache: "no-store", signal: abort.signal
        }),
        fetch(BRIDGE + "/api/guidance", {
          cache: "no-store", signal: abort.signal
        })
      ]);

      if (!controlResponse.ok) {
        throw new Error("control " + controlResponse.status);
      }

      const data = await controlResponse.json();
      panelControl = data.control;
      panelBridge = guidanceResponse.ok
        ? await guidanceResponse.json()
        : { active: false };

      const sel = $("identity");
      if (!identityFilled && data.identities?.length) {
        for (const item of data.identities) {
          sel.append(new Option(item.label, item.key));
        }
        identityFilled = true;
      }

      if (document.activeElement !== sel) {
        sel.value = panelControl?.identity || "";
      }
      sel.parentElement.hidden = false;
      $("shake").hidden = false;

      $("settings-label").textContent =
        sel.selectedOptions[0]?.textContent || "Auto identity";
      if (!panelBridge.active) {
        $("settings-label").textContent = "Shared guidance unavailable";
      }
    } catch (e) {
      panelBridge = null;
      panelControl = null;
      $("identity").parentElement.hidden = true;
      $("shake").hidden = true;
      $("settings-label").textContent = "Shared draft unavailable";
    } finally {
      clearTimeout(timeout);
      controlPolling = false;
      if (lastSnapshot) render(lastSnapshot);
    }
  }

  async function updatePanelControl(update) {
    if (identityPending) return;
    identityPending = true;
    $("identity").disabled = $("shake").disabled = true;
    $("settings-feedback").textContent = "Updating shared draft…";

    try {
      const response = await fetch(BRIDGE + "/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update)
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      panelControl = data.control || panelControl;
      $("settings-feedback").textContent = update.shake_it_up
        ? "Finding different drafts…"
        : "Shared identity updated.";
    } catch (e) {
      $("identity").value = panelControl?.identity || "";
      $("settings-feedback").textContent =
        "Could not update the shared draft. Try again.";
    } finally {
      identityPending = false;
      $("identity").disabled = $("shake").disabled = false;
      await pollControl();
    }
  }

  $("identity").onchange = () =>
    updatePanelControl({ identity: $("identity").value || null });

  $("shake").onclick = () =>
    updatePanelControl({ shake_it_up: true });

  setInterval(pollControl, 2000);
  pollControl();
```

This also fixes an existing functional issue: the original identity and shake handlers treat HTTP 400/500 responses as success.

Preserve raw snapshots, then adapt live guidance:

```diff
-  function render(snap) {
-    lastSnapshot = snap;
+  function render(snap) {
+    lastSnapshot = snap;
+    snap = D.Review.liveSnapshot(snap, panelBridge);
```

Lock the format after **any** picks or bans, including live state:

```diff
-    const populated = s && s.source === "manual" && (snap.draft && snap.draft.actions || []).length > 0;
+    const populated = !!(
+      snap.draft?.actions?.length ||
+      s?.our_bans?.length || s?.enemy_bans?.length ||
+      s?.our_team?.some(p => p.locked) ||
+      s?.enemy_team?.some(p => p.locked)
+    );
```

Change these error destinations so failures are visible inside the dialog:

```diff
-    catch (e) { $("detail").textContent = "Couldn’t change draft format. Try again."; if (lastSnapshot) $("type").value = lastSnapshot.draft_type; }
+    catch (e) { $("settings-feedback").textContent = "Couldn’t change draft format. Try again."; if (lastSnapshot) $("type").value = lastSnapshot.draft_type; }
```

```diff
-    catch (e) { $("detail").textContent = "Couldn’t change the plan. Try again."; }
+    catch (e) { $("settings-feedback").textContent = "Couldn’t change the playbook plan. Try again."; }
```

The panel only needs the next reveal, not a miniature five-pick timeline:

```diff
-    const rows = (g && g.pick_order) || [];
+    const rows = ((g && g.pick_order) || []).filter(r => r.status === "next").slice(0, 2);
```

```diff
-        <p class="ov-list-label mm-meta" id="order-label">Pick order</p>
+        <p class="ov-list-label mm-meta" id="order-label">Next reveal</p>
```

Inside `renderAlternatives`:

```diff
-    for (const row of rows) {
+    for (const row of rows.slice(0, 1)) {
```

After the existing final render calls:

```diff
-    renderAlternatives(alts);
-    renderChange(snap.last_change, changeAllowed);
-    renderActions(snap);
+    renderAlternatives(alts);
+    renderChange(snap.last_change, changeAllowed);
+    renderActions(snap);
+
+    panel.dataset.started = String(populated);
+    const summary = !st.stale && st.connection !== "error"
+      ? D.Review.summaryModel(snap, g) : null;
+    D.Review.summary($("post-draft-summary"), summary, true);
+
+    $("identity").disabled = identityPending || !!summary;
+    $("shake").disabled = identityPending || !!summary;
+    $("plan").disabled = planPending || !!summary;
+
+    if (summary) {
+      $("order").hidden = true;
+      $("alternatives").hidden = true;
+      $("change").hidden = true;
+      if (panel.dataset.kind !== "swap") {
+        $("statekey").textContent = summary.phase;
+        setLead("summary:" + summary.title, summary.title);
+        setReason("Check champion trades before loading in.");
+      }
+    } else if (!$("actions").hidden) {
+      $("alternatives").hidden = true;
+    }
```

Clear stale summary content on disconnect:

```diff
-      renderAlternatives([]); $("change").hidden = true;
+      renderAlternatives([]); $("change").hidden = true;
+      $("order").hidden = true;
+      D.Review.summary($("post-draft-summary"), null, true);
```

Append this to `draftcoach/static/mm/draft-overlay.css`:

```css
/* A fixed-size panel may scroll secondary content; it never clips actions. */
.ov-head {
  flex-basis: 44px;
  min-height: 44px;
  padding-inline: 12px;
}
.ov-wordmark {
  font-size: 11px;
  letter-spacing: .8px;
}
.ov-brand .mm-meta {
  font-size: 11px;
  line-height: 14px;
}
.ov-controls {
  flex-basis: auto;
  min-height: 48px;
  gap: 10px;
  padding: 4px 14px 10px;
}
.ov-controls > .fd-button {
  flex: none;
  min-height: 36px;
  font-size: 13px;
}
.ov-settings-state {
  min-width: 0;
  flex: 1;
  color: var(--mm-secondary);
  font-size: 12px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.ov-clock [role="timer"] {
  font-size: 22px;
}
.ov-body {
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  padding: 14px;
  border-left-width: 3px;
}
.ov-phase {
  font: 12px/16px var(--mm-mono);
  margin-bottom: 10px;
}
.ov-hero {
  align-items: flex-start;
}
.ov-hero .portrait {
  width: 48px;
  height: 48px;
}
.ov-hero h1 {
  display: block;
  overflow: visible;
  font-size: 26px;
  line-height: 1.15;
  letter-spacing: -.4px;
}
.ov-recipient {
  font-size: 12px;
  line-height: 16px;
}
.ov-reason {
  font-size: 14px;
  line-height: 20px;
}
.ov-actions {
  margin-top: 14px;
}
.fd-overlay .ov-actions .fd-button {
  min-height: 42px;
  height: auto;
  font-size: 15px;
  line-height: 20px;
  padding: 9px 10px;
  white-space: normal;
  overflow: visible;
}
.ov-actions #hover-btn {
  flex-basis: 88px;
}
.ov-action-note {
  font-size: 12px;
  line-height: 16px;
}
.ov-action-note:empty {
  display: none;
}
.ov-list-label {
  font-size: 11px;
  line-height: 15px;
}
.ov-order-item {
  font-size: 13px;
  line-height: 18px;
}
.ov-order-item small {
  font-size: 11px;
}
.ov-alternatives li {
  grid-template-columns: 28px minmax(0, 1fr) minmax(0, 145px);
  min-height: 40px;
}
.ov-alternatives .portrait {
  width: 28px;
  height: 28px;
}
.ov-alt-name {
  font-size: 14px;
}
.ov-alt-role, .ov-alt-reason {
  font-size: 12px;
}
.ov-change-slot {
  min-height: 0;
  margin-top: 10px;
  padding-top: 0;
}
.ov-change-slot:has([hidden]) {
  display: none;
}
.ov-change {
  font-size: 12px;
  line-height: 16px;
}
.ov-foot {
  padding: 9px 14px;
}
.ov-foot-meta .mm-meta {
  font-size: 10px;
  line-height: 14px;
}
.ov-open-coach {
  margin-top: 6px;
  font-size: 13px;
  line-height: 18px;
}
.draft-overlay[data-started="true"] .ov-seats,
.draft-overlay[data-started="true"] #lobby-count {
  display: none;
}
.draft-overlay[data-started="true"] .ov-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.draft-overlay[data-started="true"] .ov-open-coach {
  margin-top: 0;
}
.ov-settings {
  width: min(400px, calc(100vw - 24px));
  max-height: calc(100dvh - 24px);
  margin: auto;
  padding: 20px;
  overflow-y: auto;
  overscroll-behavior: contain;
  border: 1px solid var(--fd-border);
  border-radius: 6px;
  background: var(--mm-surface);
  color: var(--mm-ink);
}
.ov-settings::backdrop {
  background: #0009;
}
.ov-settings-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ov-settings-head h2 {
  font-size: 22px;
  font-weight: 600;
}
.ov-settings-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 12px;
  margin-top: 20px;
}
.ov-settings .ov-format {
  display: grid;
  gap: 6px;
}
.ov-settings .ov-format > .mm-meta {
  font-size: 12px;
}
.ov-settings .ov-format select {
  width: 100%;
  max-width: none;
  min-height: 42px;
  height: auto;
  padding: 8px;
  font-size: 14px;
}
.ov-settings-wide {
  grid-column: 1 / -1;
}
#settings-feedback {
  min-height: 20px;
  color: var(--mm-warn);
  font-size: 13px;
}
.fd-overlay .summary-five {
  gap: 8px;
}
.fd-overlay .summary-five strong {
  font-size: 13px;
}
.fd-overlay .summary-player {
  font-size: 11px;
}
.fd-overlay .summary-carry {
  font-size: 14px;
}
.fd-overlay .summary-detail > summary {
  font-size: 13px;
}
```

D. Put the palette in one place.

Replace `draftcoach/static/mm/tokens.css` completely:

```css
:root {
  color-scheme: light;

  --mm-paper: #f4f6f8;
  --mm-surface: #ffffff;
  --mm-ink: #17212e;
  --mm-secondary: #526172;
  --mm-line: #d3dae2;
  --mm-blue: #08756d;
  --mm-pale: #e5f3ef;
  --mm-warn: #805913;
  --mm-warn-bg: #fff3db;

  --fd-border: #8795a6;
  --fd-error: #a42d45;
  --fd-error-bg: #fce9ee;
  --fd-action-ink: #ffffff;
  --fd-raised: #e9eef3;

  --mm-sans: "Segoe UI", Arial, sans-serif;
  --mm-mono: Consolas, "Liberation Mono", monospace;
  --mm-ease: cubic-bezier(.2, .7, .15, 1);
  --mm-radius: 4px;
  --mm-fast: 120ms;
  --mm-panel: 180ms;
  --mm-overlay-opacity: 1;
}

body.graphite {
  color-scheme: dark;

  --mm-paper: #10151d;
  --mm-surface: #18212c;
  --mm-ink: #f3f5f8;
  --mm-secondary: #b4bfcc;
  --mm-line: #344253;
  --mm-blue: #65d7c7;
  --mm-pale: #203c3a;
  --mm-warn: #e6bd79;
  --mm-warn-bg: #392f20;

  --fd-border: #64758a;
  --fd-error: #ff9aab;
  --fd-error-bg: #3b2730;
  --fd-action-ink: #10201e;
  --fd-raised: #22303f;
}

html:has(body.graphite) {
  color-scheme: dark;
}
```

The dark secondary text is approximately **8.7:1** against the surface; the primary button text is approximately **9.7:1**. Teal means an actionable selection. Amber means a caveat or short timer. Red means a ban, unavailable champion or error. Team ownership is communicated by words.

In `draftcoach/static/mm/draft-ui.css`, replace its initial `:root` and `body.graphite` blocks with:

```css
:root {
  --fd-fast: 120ms;
  --fd-control: 160ms;
  --fd-panel: 180ms;
  --fd-ease: cubic-bezier(.22, 1, .36, 1);
}
```

Append:

```css
/* Shared readable scale and portrait treatment. */
.mm-meta, .mono {
  font-size: 12px;
  line-height: 1.4;
  letter-spacing: .025em;
}
.fd-button {
  min-height: 40px;
  font-size: 14px;
}
.fd-button,
.fd-icon-button,
.fd-text-button,
input[type="search"],
input[type="text"],
select,
textarea {
  border-radius: var(--mm-radius);
}
.fd-button.primary,
body.graphite .fd-button.primary {
  color: var(--fd-action-ink);
}
.fd-button.primary:hover:not(:disabled) {
  color: var(--fd-action-ink);
}
.fd-note {
  font-size: 14px;
  line-height: 1.5;
}
.fd-field > span {
  font-size: 12px;
}
.fd-details > summary {
  min-height: 36px;
  font-size: 14px;
}
.portrait {
  border-radius: 4px;
}
.portrait img {
  filter: none;
}
:where(button, a, input, select, textarea, summary):focus-visible {
  outline: 3px solid var(--mm-blue);
  outline-offset: 3px;
}
::selection {
  background: var(--mm-blue);
  color: var(--fd-action-ink);
}
.summary-five {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  list-style: none;
  margin: 20px 0 16px;
  padding: 0;
}
.summary-five li {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.summary-five strong {
  font-size: 16px;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
.summary-player {
  color: var(--mm-secondary);
  font-size: 12px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.summary-carry {
  margin: 0;
  font-size: 16px;
  line-height: 1.5;
}
.summary-detail {
  margin-top: 12px;
}
```

Keep the existing reduced-motion rule. These focus, labeling and content-overflow choices follow the interface review guidance. [Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)

In `draft-overlay.css`, replace these six declarations inside `body.fd-overlay`:

```diff
-  --ov-local: #087f77;
-  --ov-team: #84612d;
-  --ov-ban: #a73d4a;
-  --ov-action-ink: #fff;
-  --ov-raised: #e8edef;
+  --ov-local: var(--mm-blue);
+  --ov-team: var(--mm-secondary);
+  --ov-ban: var(--fd-error);
+  --ov-action-ink: var(--fd-action-ink);
+  --ov-raised: var(--fd-raised);
```

Replace the complete existing `body.fd-overlay.graphite { … }` palette block with:

```css
body.fd-overlay.graphite {
  color-scheme: dark;
}
```

That removes the competing third web palette.

In `draftcoach/hud_intent.py`, replace the complete `THEMES = { … }` assignment with:

```python
LIGHT_THEME = {
    "surface": "#ffffff",
    "raised": "#e9eef3",
    "border": "#8795a6",
    "ink": "#17212e",
    "secondary": "#526172",
    "action": "#08756d",
    "action_ink": "#ffffff",
    "team": "#526172",
    "warn": "#805913",
    "ban": "#a42d45",
}

DARK_THEME = {
    "surface": "#18212c",
    "raised": "#22303f",
    "border": "#64758a",
    "ink": "#f3f5f8",
    "secondary": "#b4bfcc",
    "action": "#65d7c7",
    "action_ink": "#10201e",
    "team": "#b4bfcc",
    "warn": "#e6bd79",
    "ban": "#ff9aab",
}

THEMES = {
    "porcelain": dict(LIGHT_THEME),
    "graphite": dict(DARK_THEME),
    "client": dict(DARK_THEME),
}
```

Keep `KEY = "#010203"` unchanged. All HUD fills remain opaque six-digit colours.

Increase the actual drawing fonts in `_draw`, where the constructor values are overridden:

```diff
-            family="Segoe UI", size=-px(13), weight="bold"
+            family="Segoe UI", size=-px(16), weight="bold"
```

```diff
-            family="Segoe UI", size=-px(9), weight="normal"
+            family="Segoe UI", size=-px(12), weight="normal"
```

```diff
-            family="Consolas", size=-px(7), weight="normal"
+            family="Consolas", size=-px(10), weight="normal"
```

```diff
-            family="Segoe UI", size=-px(11), weight="bold"
+            family="Segoe UI", size=-px(12), weight="bold"
```

Use accurate, durable wording:

```diff
-            "reason": "All six plans re-ranked around your hover",
+            "reason": "Plans updated for your hover",
```

```diff
-            "meta": "TEAM PICK ? " + role(preview.get("role")),
+            "meta": "TEAM PICK · " + role(preview.get("role")),
```

```diff
-                        "Lock ban" if kind == "ban" else "Lock in",
+                        "Ban" if kind == "ban" else "Lock in",
```

The swap title already separates give/receive in the renderer. Keep that treatment. Do not squeeze the entire exchange into a single ellipsized sentence.

For `tools/intent_comp_overlay.py`, use these exact **global literal replacements within this file only**:

| Old literal | New literal |
|---|---|
| `"#0a1219"` | `"#18212c"` |
| `"#101820"` | `"#22303f"` |
| `"#35434a"` | `"#64758a"` |
| `"#f0e6d2"` | `"#f3f5f8"` |
| `"#b2ada1"` | `"#b4bfcc"` |
| `"#0ac8b9"` | `"#65d7c7"` |
| `"#c8aa6e"` | `"#b4bfcc"` |
| `"#ee7d87"` | `"#ff9aab"` |
| `"#04100f"` | `"#10201e"` |
| `"#0b0f13"` | `"#10151d"` |
| `"#512d32"` | `"#704553"` |
| `"#60666b"` | `"#b4bfcc"` |
| `"#4f555a"` | `"#b4bfcc"` |

Keep `"#010203"` unchanged.

Unify role names:

```diff
-ROLE_SHORT = {"Top": "TOP", "Jungle": "JGL", "Mid": "MID", "ADC": "ADC", "Support": "SUP"}
+ROLE_SHORT = {"Top": "TOP", "Jungle": "JG", "Mid": "MID", "ADC": "BOT", "Support": "SUP"}
```

Remove visible numeric scores from the gallery too. Replace:

```python
            if dead:
                head = f"OUT · WAS #{int(comp.get('_was_rank', i + 1))} · {score:.1f} · {side}"
            else:
                head = f"#{live_rank or first + i + 1} {'BEST' if is_best else 'LIVE'} · {score:.1f} · {side}"
                if lane:
                    head += f" · {lane:+.1f}"
```

With:

```python
            if dead:
                head = f"Unavailable · was #{int(comp.get('_was_rank', i + 1))}"
            else:
                rank = live_rank or first + i + 1
                label = "Leading" if is_best else "Alternative"
                identity = str(comp.get("team_plan") or "Team draft")
                head = f"#{rank} {label} · {identity}"
```

Increase secondary text without shrinking the decision:

```diff
-        self.font_player.configure(size=-px(8))
-        self.font_tiny.configure(size=-px(8))
+        self.font_player.configure(size=-px(11))
+        self.font_tiny.configure(size=-px(10))
```

Delete this arbitrary truncation; `_fit` already measures the available width:

```diff
-                elif len(player) > 10:
-                    player = player[:10]
```

Stop the gallery overlapping League when there is insufficient adjacent space. Replace:

```python
            panel_w = min(420, available)
            if panel_w < 300:
                panel_w = min(300, max(220, cx - virtual_left))
            panel_x = max(virtual_left, cx - panel_w - 4)
```

With:

```python
            if available < 360:
                self.root.withdraw()
                self.root.after(33, self._tick)
                return
            panel_w = min(420, available)
            panel_x = cx - panel_w - 4
```

Only page the gallery while League has focus:

```diff
-        if show and page_down and not self._last_page_down and self.page_index < max_page:
+        if show and self.u.GetForegroundWindow() == self.client and page_down and not self._last_page_down and self.page_index < max_page:
```

```diff
-        if show and page_up and not self._last_page_up and self.page_index > 0:
+        if show and self.u.GetForegroundWindow() == self.client and page_up and not self._last_page_up and self.page_index > 0:
```

All existing coach IDs remain, with the duplicate `order-sub` reduced to one occurrence:

```text
status open-roster lobby-card lobby-title lobby-sub lobby
board board-title turn draft-type side-blue side-red undo redo reset
board-mode ours-side ours-slots ours-bans enemy-side enemy-slots enemy-bans
champion-browser search champion-help grid selected-champion
preview-hover commit-champion order-sub seq
series-tools series-note series-next series-clear events
hero-art statekey timer headline reason change alerts
client-actions accept hover-btn action-note
control-section control-sync control-identity control-shake
control-identity-note control-locks
intent-actions intent-actions-label intent-hover intent-lock
captain-section captain-side captain-phase captain-locks
captain-preference captain-comps
order-section order order-note plans-section plans-sub plans
pick-section pick-pill pick-roles picks
ban-section ban-pill bans other-section other-summary other
likely flex counters comp comp-score ours-score enemy-score
drawer roster-title close-roster dropzone file-input watch-dir
players lineup enemy-lineup opponents policy imports engine-data
app-announcement champion-names
```

Existing dynamically generated lineup-select IDs remain untouched. Added coach IDs are:

```text
board-order-description analysis-section draft-summary team-sheet-fold
pick-section-fold ban-section-fold order-section-fold
captain-section-fold plans-section-fold
```

All existing panel IDs remain:

```text
panel brand head-sub theme minimize close type format-help identity shake plan
clock-wrap timer art statekey hero-portrait lead recipient detail
actions hover-btn accept action-note order order-label order-list
alternatives alternatives-label alts change change-copy
conn lobby-count lobby open-coach decision-announcement
```

Added panel IDs:

```text
settings-open settings-label draft-settings settings-title
settings-feedback post-draft-summary
```

The six protected coach anchors remain byte-for-byte unchanged. Apply these source changes **before** running `patch_flexers_intent_ui.py`; do not apply them against an already patched generated copy.

E. Enforce these UX rules.

- **Two-second scan:** action, champion, recipient, timer. Everything else is subordinate.
- **One local confirmation:** show either pick/ban controls or trade controls. Trades take priority.
- **Explicit verbs:** “Require champion” changes a draft constraint; “Hover” previews; “Lock in” commits in League.
- **Stable interaction:** polling must preserve focused inputs, unsaved text, open details and the card being inspected.
- **Truthful completion:** locked champions come from actual state. A matching draft may supply strategy. Missing protection/trade/next-game data remains explicitly unknown.
- **Motion only for meaning:** a brief recommendation-change transition and restrained drawer transition. No timer pulsing, repeated arrivals, card shuffling beneath the pointer or animated score changes.
- **HUD exclusions:** no scores, probabilities, setup controls, roster problems, room identifiers, model diagnostics, multi-draft comparisons or long explanations.
- **Small windows lose secondary content first:** collapse settings and alternatives; allow secondary scrolling at enlarged text sizes. Never shrink or clip the primary action to preserve decorative content.
