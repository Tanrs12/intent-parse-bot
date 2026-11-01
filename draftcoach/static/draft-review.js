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
    // The bridge's guidance wins when it carries drafts or picks. When the bridge is active but
    // empty (scripted simulation, a lobby it cannot rank) the app's own guidance, which comes from
    // the same backend, stays on screen rather than a dead "no recommendation".
    const live = bridge?.active ? bridge.guidance : null;
    const hasContent = live && ((live.picks || []).length || (live.comps || []).length);
    const g = hasContent ? live : (snapshot.guidance || null);
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
