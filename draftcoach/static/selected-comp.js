/* Compact visual mirror of a Discord-selected five; no draft logic lives here.
   The relay owns the selected options, Draft Coach owns live locks/counters. */
(() => {
  "use strict";
  const D = window.Draft;
  const ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"];
  const SHORT = { Top: "TOP", Jungle: "JUNGLE", Mid: "MID", ADC: "BOT", Support: "SUPPORT" };
  let iconsRequested = false;

  function ensureIcons() {
    if (iconsRequested) return;
    iconsRequested = true;
    fetch("/api/champions").then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.champions) {
        D.learnIds(data.champions);
        // Initial standby renders can precede the icon index download.
        document.querySelectorAll(".fd-selected-comp").forEach(el => { el.dataset.model = ""; });
      } })
      .catch(() => {});
  }

  function render(root, chosen, state, guidance, wide = false) {
    if (!root) return;
    const bans = new Set([...(state?.our_bans || []), ...(state?.enemy_bans || [])]);
    const taken = new Set((state?.enemy_team || []).map(x => x.locked).filter(Boolean));
    const replacements = guidance?.chosen?.replaced || {};
    const ownLocks = {};
    for (const seat of state?.our_team || []) {
      if (ROLES.includes(seat.role) && seat.locked) ownLocks[seat.role] = seat.locked;
    }
    const key = JSON.stringify([chosen, [...bans], [...taken], replacements, ownLocks, wide]);
    if (root.dataset.model === key) return;
    root.dataset.model = key;
    root.hidden = !chosen;
    root.replaceChildren();
    if (!chosen) return;
    ensureIcons();
    root.className = "fd-selected-comp " + (wide ? "wide" : "narrow");
    const head = D.el("div", "fd-sc-header");
    head.append(D.el("b", "", (chosen.source === "discord" ? "DISCORD" : "TEAM") + "  /  " + (chosen.label || "Team comp").toUpperCase()));
    head.append(D.el("span", "fd-sc-mode", chosen.mode === "adaptive" ? "ADAPTIVE" : "SELECTED"));
    root.append(head);
    if (chosen.plan) root.append(D.el("p", "fd-sc-plan", chosen.plan));

    const rows = D.el("div", "fd-sc-roles");
    for (const role of ROLES) {
      const primary = chosen.champions?.[role] || "";
      const options = [primary];
      for (const champ of chosen.options?.[role] || []) {
        if (champ && !options.includes(champ)) options.push(champ);
      }
      const learning = new Set(chosen.learn_options?.[role] || []);
      const rec = replacements[role];
      // An adaptive counter is visually distinct from the original Discord options.
      if (rec?.now && !options.includes(rec.now)) options.push(rec.now);
      const row = D.el("article", "fd-sc-role");
      const label = D.el("div", "fd-sc-role-head");
      label.append(D.el("strong", "", SHORT[role]));
      label.append(D.el("span", "fd-sc-player", chosen.players?.[role] || "Unassigned"));
      row.append(label);

      const picks = D.el("div", "fd-sc-picks");
      for (const champ of options.filter(Boolean)) {
        const isCounter = !!rec && champ === rec.now && champ !== primary;
        const isLearn = learning.has(champ);
        const isLocked = ownLocks[role] === champ;
        const unavailable = !isLocked && (bans.has(champ) || taken.has(champ));
        const item = D.el("div", "fd-sc-option" +
          (champ === primary ? " primary" : "") +
          (isLearn ? " learn" : "") +
          (isCounter ? " counter" : "") +
          (isLocked ? " locked" : "") +
          (unavailable ? " unavailable" : ""));
        item.title = [champ, isLearn ? "Learning suggestion" : isCounter ? "Adaptive counter" : champ === primary ? "Selected plan" : "Same-plan owned option",
          unavailable ? "Unavailable" : isLocked ? "Locked in League" : ""].filter(Boolean).join(" · ");
        item.append(D.portrait(champ, "small"));
        item.append(D.el("span", "fd-sc-name", champ));
        if (isLocked) item.append(D.el("span", "fd-sc-lock", "✓"));
        picks.append(item);
      }
      row.append(picks);
      if (rec?.why) row.append(D.el("p", "fd-sc-reason", rec.why));
      else if (options.length > 1) row.append(D.el("p", "fd-sc-reason", "Same-plan options" + (learning.size ? " · learning marked" : "")));
      rows.append(row);
    }
    root.append(rows);
    root.append(D.el("p", "fd-sc-footnote", chosen.mode === "adaptive"
      ? "Roles fixed · Unpicked champions can adapt to verified enemy lanes"
      : "Selected plan · Actual League locks take priority"));
  }
  window.DraftSelectedComp = { render };
  // Both pages call D.SelectedComp.render(...): without this alias every coach-page render threw
  // before drawing anything (and the panel never drew the chosen comp).
  if (D) D.SelectedComp = window.DraftSelectedComp;
})();
