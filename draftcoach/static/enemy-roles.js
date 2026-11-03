/* Captain-controlled enemy lane confirmations. Read-only for followers.
   A manual role is valid for one gameId and only when that enemy LOCKED.
   These edits use the existing shared /api/control sync, not an isolated UI.
*/
(function () {
  const D = window.Draft;
  const ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"];
  const NAMES = { Top:"Top", Jungle:"Jungle", Mid:"Mid", ADC:"Bot / ADC", Support:"Support" };

  function rowsFor(state) {
    const seen = new Set();
    return (state?.enemy_team || [])
      .filter(p => p?.locked && !seen.has(p.locked) && seen.add(p.locked))
      .map(p => ({ champion: p.locked, displayedRole: p.role || null }));
  }

  function selections(control, gameId, roster) {
    const raw = control?.enemy_role_overrides || {};
    const allowed = new Set(roster.map(r => r.champion));
    if (!gameId || String(raw.game_id || "") !== String(gameId)) return {};
    const result = {};
    const filled = new Set();
    for (const [champion, role] of Object.entries(raw.assignments || {})) {
      if (allowed.has(champion) && ROLES.includes(role) && !filled.has(role)) {
        result[champion] = role;
        filled.add(role);
      }
    }
    return result;
  }

  function render(box, state, guidance, control, isCaptain, onChange) {
    if (!box) return;
    const gameId = String(state?.game_id || "");
    const roster = rowsFor(state);
    const manual = selections(control, gameId, roster);
    const resolved = guidance?.enemy_roles_resolved || {};
    const key = JSON.stringify([gameId, roster, manual, resolved, !!isCaptain]);
    if (box.dataset.key === key) return;
    // Poll every 0.5s: don't destroy an open dropdown while it's in use.
    if (box.dataset.busy === "true" || box.matches(":focus-within")) return;
    box.dataset.key = key;
    box.hidden = roster.length === 0 || !gameId;
    box.replaceChildren();
    if (box.hidden) return;
    const header = D.el("div", "fd-enemy-role-header");
    header.append(D.el("strong", "", "Enemy lane assignments"),
      D.el("span", "mm-meta", isCaptain ? "Captain can correct flex picks" : "Captain-controlled"));
    box.append(header);
    const hint = D.el("p", "fd-note",
      "Assign only locked champions. Auto uses confirmed/unique roles; unknown flex lanes get no counter score. Changes sync to everyone for this lobby.");
    box.append(hint);
    const list = D.el("div", "fd-enemy-role-list");
    for (const row of roster) {
      const line = D.el("label", "fd-enemy-role-row");
      line.append(D.portrait(row.champion, "tiny"), D.el("span", "fd-enemy-name", row.champion));
      const auto = Object.entries(resolved).find(([, c]) => c === row.champion)?.[0];
      const sel = document.createElement("select");
      sel.setAttribute("aria-label", "Enemy " + row.champion + " lane");
      sel.append(new Option("Auto" + (manual[row.champion] ? "" : " · " + (auto ? NAMES[auto] : "unconfirmed")), ""));
      for (const role of ROLES) sel.append(new Option(NAMES[role], role));
      sel.value = manual[row.champion] || "";
      sel.disabled = !isCaptain;
      sel.onchange = async () => {
        if (!isCaptain) return;
        const next = { ...manual };
        const role = sel.value;
        delete next[row.champion];
        if (role) {
          for (const [name, occupied] of Object.entries(next)) {
            if (occupied === role) delete next[name];
          }
          next[row.champion] = role;
        }
        box.dataset.busy = "true";
        sel.disabled = true;
        try {
          const accepted = await onChange({
            enemy_role_overrides: { game_id: gameId, assignments: next }
          });
          if (accepted === false) throw new Error("Captain update wasn't accepted");
          box.dataset.key = "";
        } catch (error) {
          sel.value = manual[row.champion] || "";
          const feedback = box.querySelector("[role=status]");
          if (feedback) feedback.textContent = "Could not update enemy lane. " + error.message;
        } finally {
          box.dataset.busy = "false";
          sel.disabled = false;
        }
      };
      line.append(sel);
      if (manual[row.champion]) line.title = "Captain assigned " + row.champion + " to " + manual[row.champion];
      else line.title = auto ? "Inferred " + NAMES[auto] : "No confirmed lane yet";
      list.append(line);
    }
    box.append(list);
    const feedback = D.el("p", "fd-enemy-role-feedback");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    box.append(feedback);
  }
  D.EnemyRoles = { render, selections, rowsFor };
})();
