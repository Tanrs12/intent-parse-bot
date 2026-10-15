/* Shared rendering for the overlay and the coach panel. */
(function (global) {
  const ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"];
  const ROLE_SHORT = { Top: "TOP", Jungle: "JG", Mid: "MID", ADC: "BOT", Support: "SUP" };
  const idCache = {};

  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function initials(name) {
    if (!name) return "?";
    return name.split(/[\s']+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  }

  function learnIds(map) {
    for (const [name, id] of Object.entries(map || {})) if (id) idCache[name] = id;
  }

  function learnFromGuidance(g) {
    if (!g) return;
    for (const p of g.picks || []) if (p.champion_id) idCache[p.champion] = p.champion_id;
    for (const b of g.bans || []) if (b.champion_id) idCache[b.champion] = b.champion_id;
    for (const c of g.counters || []) {
      if (c.enemy_id) idCache[c.enemy] = c.enemy_id;
      for (const r of c.responses || []) if (r.champion_id) idCache[r.champion] = r.champion_id;
    }
    for (const l of g.likely_enemy || []) if (l.champion_id) idCache[l.champion] = l.champion_id;
    for (const f of g.flex || []) if (f.champion_id) idCache[f.champion] = f.champion_id;
  }

  function idFor(name) { return idCache[name] || null; }

  function portrait(name, cls, id) {
    const wrap = el("div", "portrait " + (cls || ""));
    wrap.textContent = initials(name);
    wrap.title = name || "";
    wrap.setAttribute("aria-hidden", "true");
    const cid = id || idFor(name);
    if (cid) {
      const img = document.createElement("img");
      img.alt = name || "";
      img.loading = "lazy";
      img.src = "/api/champion-icon/" + cid;
      img.onerror = () => img.remove();
      img.onload = () => { wrap.textContent = ""; wrap.appendChild(img); };
    }
    return wrap;
  }

  function fmtTimer(ms) {
    if (!ms || ms <= 0) return "--";
    return String(Math.ceil(ms / 1000));
  }

  function signed(n, digits) { return (n >= 0 ? "+" : "") + n.toFixed(digits === undefined ? 1 : digits); }

  // ---- team columns ---------------------------------------------------------
  function renderTeam(container, slots, ours, activeCells, flashCells, swaps, opts) {
    opts = opts || {};
    container.innerHTML = "";
    const swapTo = {};
    for (const sw of swaps || []) swapTo[sw.from_cell] = sw;
    const sorted = [...slots].sort((a, b) => (ROLES.indexOf(a.role) - ROLES.indexOf(b.role)) || (a.cell_id - b.cell_id));
    for (const slot of sorted) {
      const champ = slot.locked || slot.hover;
      const row = el("div", "slot" + (slot.locked ? " locked" : "") + (activeCells.includes(slot.cell_id) ? " active" : ""));
      if (flashCells && flashCells.has(slot.cell_id)) row.classList.add("flash");
      row.appendChild(portrait(champ, slot.locked ? "" : "hover"));
      const mid = el("div");
      mid.appendChild(el("div", "name", champ || (slot.display_name || "—")));
      let sub;
      if (ours) {
        const poolLabel = { player: "own pool", default: "default pool", payload: "given pool", none: "no pool: history/meta" }[slot.pool_source] || "";
        sub = champ ? slot.display_name : (slot.display_name && slot.player_key ? slot.display_name + " · " + poolLabel : poolLabel);
      } else {
        sub = slot.scouted ? (slot.scouted.name + (slot.scouted.team ? " (" + slot.scouted.team + ")" : "") + " · scouted") : (champ ? slot.display_name : "");
      }
      mid.appendChild(el("div", "sub", sub || ""));
      row.appendChild(mid);
      const right = el("div");
      const role = el("div", "role " + (slot.role_source === "inferred" ? "inferred" : "") + (opts.onRole ? " clickable" : ""), ROLE_SHORT[slot.role] || "?");
      if (opts.onRole) { role.title = "click to change role"; role.onclick = () => opts.onRole(slot); }
      right.appendChild(role);
      const sw = swapTo[slot.cell_id];
      right.appendChild(el("div", "state " + (slot.locked ? (sw ? "hover" : "lock") : champ ? "hover" : ""),
        slot.locked ? (sw ? "→ " + (ROLE_SHORT[sw.to_role] || sw.to_role) : "locked") : champ ? "hover" : ""));
      if (sw) row.title = champ + " is expected to go to " + sw.to_player + " (" + sw.to_role + ")";
      row.appendChild(right);
      container.appendChild(row);
    }
  }

  function renderBans(container, label, names, flashNames) {
    container.innerHTML = "";
    container.appendChild(el("span", "label", label));
    if (!names.length) container.appendChild(el("span", "empty", "none yet"));
    for (const name of names) {
      const b = el("div", "ban" + (flashNames && flashNames.has(name) ? " flash" : ""));
      b.appendChild(portrait(name, "small"));
      b.title = name;
      container.appendChild(b);
    }
  }

  // ---- guidance panels -----------------------------------------------------------
  function renderPicks(list, g, maxPicks, onPick) {
    list.innerHTML = "";
    const picks = (g && g.picks) || [];
    if (g && g.draft_complete) {
      const team = g.team || {};
      const card = el("div", "card top");
      const body = el("div");
      const title = el("div", "title");
      title.appendChild(el("span", "champ", "Draft complete"));
      title.appendChild(el("span", "role", team.team_plan || ""));
      body.appendChild(title);
      const tags = el("div", "tags");
      for (const s of team.strengths || []) tags.appendChild(el("span", "tag good", s));
      for (const gp of team.gaps || []) tags.appendChild(el("span", "tag warn", gp));
      body.appendChild(tags);
      const enemy = g.enemy_team_score;
      body.appendChild(el("div", "why", enemy ? "enemy comp " + enemy.score.toFixed(1) + (enemy.team_plan ? " · " + enemy.team_plan : "") : "waiting for enemy locks"));
      card.appendChild(el("div", "portrait", "✓"));
      card.appendChild(body);
      const score = el("div", "score", team.score !== undefined ? team.score.toFixed(1) : "—");
      score.appendChild(el("small", "", "our comp"));
      card.appendChild(score);
      list.appendChild(card);
      return;
    }
    if (!picks.length) { list.appendChild(el("div", "empty", "No legal picks to rank yet.")); return; }
    picks.slice(0, maxPicks).forEach((p, i) => {
      const card = el("div", "card" + (i === 0 ? " top" : ""));
      if (onPick) { card.style.cursor = "pointer"; card.title = "click to pick " + p.champion; card.onclick = () => onPick(p); }
      card.appendChild(portrait(p.champion, "", p.champion_id));
      const body = el("div");
      const title = el("div", "title");
      title.appendChild(el("span", "champ", p.champion));
      title.appendChild(el("span", "role", (ROLE_SHORT[p.role] || p.role) + (p.player && p.player !== p.role ? " · " + p.player : "")));
      body.appendChild(title);
      const tags = el("div", "tags");
      if (p.familiarity === "comfort") tags.appendChild(el("span", "tag gold", "comfort"));
      else if (p.familiarity === "developing") tags.appendChild(el("span", "tag warn", "developing"));
      else if (p.familiarity === "refresh") tags.appendChild(el("span", "tag", "refresh"));
      if (p.priority === 1) tags.appendChild(el("span", "tag", "preferred"));
      else if (p.priority >= 4) tags.appendChild(el("span", "tag", "p" + p.priority));
      if (p.blind_safe && !(p.lane || []).length) tags.appendChild(el("span", "tag good", "blind-safe"));
      if (p.counter_risk >= 0.5) tags.appendChild(el("span", "tag warn", "answerable"));
      for (const edge of p.lane || []) {
        const adj = Number.isFinite(edge.adjusted) ? edge.adjusted : edge.advantage;
        tags.appendChild(el("span", "tag " + (adj > 0.2 ? "good" : adj < -0.2 ? "bad" : ""), signed(adj) + " adj vs " + edge.enemy));
      }
      if ((p.flex_roles || []).length) tags.appendChild(el("span", "tag purple", "flex " + p.flex_roles.map((r) => ROLE_SHORT[r] || r).join("/")));
      if (p.source === "exhausted") tags.appendChild(el("span", "tag bad", "emergency"));
      else if (p.source === "history" || p.source === "meta") tags.appendChild(el("span", "tag warn", p.source));
      if (p.team_plan) tags.appendChild(el("span", "tag", p.team_plan));
      body.appendChild(tags);
      const why = el("div", "why");
      const bits = (p.why || []).filter((w) => !/^blind-safe$/.test(w) && !/^(confirmed comfort|preferred pick|refresh pick)$/.test(w) && !/^flex /.test(w));
      why.textContent = bits.join(" · ");
      body.appendChild(why);
      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      track.appendChild(fill);
      body.appendChild(track);
      requestAnimationFrame(() => { fill.style.width = Math.max(4, Math.min(100, p.score)) + "%"; });
      card.appendChild(body);
      const score = el("div", "score", p.score.toFixed(1));
      score.appendChild(el("small", "", "comp " + p.comp_score.toFixed(0)));
      card.appendChild(score);
      list.appendChild(card);
    });
  }

  function renderBanTargets(list, g, state, maxBans, onBan) {
    list.innerHTML = "";
    const bans = (g && g.bans) || [];
    const ourTurn = state && state.active && state.active.kind === "ban" && state.active.ours;
    if (!bans.length) { list.appendChild(el("div", "empty", "Nothing worth banning against the planned draft.")); return; }
    bans.slice(0, maxBans).forEach((b, i) => {
      const card = el("div", "card" + (i === 0 && ourTurn ? " top" : ""));
      if (onBan) { card.style.cursor = "pointer"; card.title = "click to ban " + b.champion; card.onclick = () => onBan(b); }
      card.appendChild(portrait(b.champion, "", b.champion_id));
      const body = el("div");
      const title = el("div", "title");
      title.appendChild(el("span", "champ", b.champion));
      body.appendChild(title);
      const tags = el("div", "tags");
      for (const t of b.tags || []) tags.appendChild(el("span", "tag " + ({ scouted: "purple", policy: "gold", hover: "warn" }[t] || ""), t));
      if (tags.childNodes.length) body.appendChild(tags);
      body.appendChild(el("div", "why", (b.reasons || []).join(" · ")));
      card.appendChild(body);
      card.appendChild(el("div", "score", b.score ? b.score.toFixed(1) : "•"));
      list.appendChild(card);
    });
  }

  function renderCounters(list, g) {
    list.innerHTML = "";
    const counters = (g && g.counters) || [];
    if (!counters.length) { list.appendChild(el("div", "empty", "No enemy locks in roles we still have open.")); return; }
    for (const c of counters) {
      const row = el("div", "counter");
      row.appendChild(portrait(c.enemy, "small", c.enemy_id));
      const body = el("div");
      const head = el("div", "enemy", c.enemy);
      head.appendChild(el("small", "", (ROLE_SHORT[c.role] || c.role) + (c.role_source === "inferred" ? " (inferred)" : "")));
      body.appendChild(head);
      const answers = el("div", "answers");
      if (!c.responses.length) answers.appendChild(el("span", "empty", c.has_data ? "no matchup data for our pool" : "no matchup data"));
      for (const r of c.responses) {
        const a = el("span", "answer");
        a.appendChild(portrait(r.champion, "", r.champion_id));
        a.appendChild(el("span", "", r.champion));
        a.appendChild(el("span", "adv " + (r.advantage > 0 ? "pos" : r.advantage < 0 ? "neg" : ""), signed(r.advantage)));
        answers.appendChild(a);
      }
      body.appendChild(answers);
      row.appendChild(body);
      list.appendChild(row);
    }
  }

  function renderChips(box, rows, labelFn, emptyText) {
    box.innerHTML = "";
    if (!rows || !rows.length) { box.appendChild(el("span", "empty", emptyText)); return; }
    for (const r of rows) {
      const chip = el("span", "chip");
      chip.appendChild(portrait(r.champion, "", r.champion_id));
      const b = el("b", "", r.champion);
      chip.appendChild(b);
      const label = labelFn(r);
      if (label) chip.appendChild(el("small", "", label));
      box.appendChild(chip);
    }
  }

  function renderComp(box, scoreEl, g) {
    box.innerHTML = "";
    const comp = g && g.comps && g.comps[0];
    if (!comp) { box.appendChild(el("span", "empty", "—")); if (scoreEl) scoreEl.textContent = ""; return; }
    if (scoreEl) scoreEl.textContent = comp.score.toFixed(1);
    for (const role of ROLES) {
      const name = comp.champions[role];
      const item = el("span", "comp-item");
      item.appendChild(portrait(name));
      item.title = role + ": " + name + (comp.players && comp.players[role] ? " (" + comp.players[role] + ")" : "");
      box.appendChild(item);
      box.appendChild(el("span", "", name));
    }
    box.appendChild(el("span", "plan", comp.team_plan || ""));
  }

  function renderAlerts(box, g) {
    box.innerHTML = "";
    const alerts = (g && g.alerts) || [];
    box.classList.toggle("hidden", !alerts.length);
    for (const a of alerts) box.appendChild(el("div", "alert " + (a.level || "info"), a.text));
  }

  function renderHeadline(node, g, state) {
    if (!node) return;
    node.className = "headline " + ((g && g.phase_kind) || "idle");
    node.textContent = g ? g.headline : (state ? "Scoring…" : "Waiting for champion select");
  }

  // ---- websocket -----------------------------------------------------------------
  function connect(onSnapshot, onStatus) {
    let backoff = 500;
    function open() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(proto + "://" + location.host + "/ws");
      ws.onopen = () => { backoff = 500; if (onStatus) onStatus("open"); };
      ws.onmessage = (ev) => { try { onSnapshot(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
      ws.onclose = () => { if (onStatus) onStatus("closed"); setTimeout(open, backoff); backoff = Math.min(backoff * 2, 5000); };
      ws.onerror = () => ws.close();
    }
    open();
  }

  // Inline the MM icon set so currentColor applies.
  const iconCache = new Map();
  async function hydrateIcons(root) {
    root = root || document;
    await Promise.all([...root.querySelectorAll(".mm-icon[data-icon]")].map(async (node) => {
      const name = node.dataset.icon;
      if (!/^[a-z][a-z0-9-]*$/.test(name) || node.dataset.hydrated === name) return;
      try {
        if (!iconCache.has(name)) {
          iconCache.set(name, fetch("/static/mm/icons/" + name + ".svg").then((r) => { if (!r.ok) throw new Error("icon " + name); return r.text(); }));
        }
        const xml = await iconCache.get(name);
        const svg = new DOMParser().parseFromString(xml, "image/svg+xml").documentElement;
        if (svg.localName !== "svg") return;
        const copy = document.importNode(svg, true);
        copy.setAttribute("aria-hidden", "true"); copy.setAttribute("focusable", "false");
        node.replaceChildren(copy); node.dataset.hydrated = name;
      } catch (e) { iconCache.delete(name); }
    }));
  }

  // One bounded sentence per recommendation (no scores, no probability language).
  function shortPickReason(p) {
    if (!p) return "";
    if (p.source === "exhausted") return "Emergency pick: no prepared champion is left for this role.";
    const risk = p.counter_risk_by && p.counter_risk_by[0] && p.counter_risk_by[0].enemy;
    if (p.counter_risk >= 0.5 && risk) return "Can still be answered by " + risk + ".";
    const edge = p.lane && p.lane[0];
    if (edge && Number.isFinite(edge.adjusted !== undefined ? edge.adjusted : edge.advantage)) {
      const adj = edge.adjusted !== undefined ? edge.adjusted : edge.advantage;
      const target = (edge.locked ? "locked " : "hovered ") + edge.enemy;
      const detail = " (" + signed(adj) + " adjusted, " + edge.games + " games).";
      if (adj > 0.2) return "Favourable matchup signal into " + target + detail;
      if (adj < -0.2) return "Unfavourable matchup signal into " + target + detail;
      return "Even matchup signal into " + target + detail;
    }
    if (p.familiarity === "developing") return "Developing pick: practice assignment, not a comfort champion.";
    if (p.familiarity === "comfort") return "Confirmed comfort" + (p.player && p.player !== p.role ? " for " + p.player : "") + ".";
    if (p.priority === 1) return "Preferred pick" + (p.player && p.player !== p.role ? " for " + p.player : "") + ".";
    if (p.blind_safe) return "Blind-safe option.";
    if (p.risk_known === false && !(p.lane || []).length) return "Blind-pick safety unknown: limited matchup coverage.";
    if (p.flex_roles && p.flex_roles.length) return "Can flex to " + p.flex_roles.map((r) => ROLE_SHORT[r] || r).join(" / ") + ".";
    if (p.source === "history" || p.source === "history-fallback") return "From account history, not a submitted pool.";
    if (p.source === "meta") return "From role meta, not a submitted pool.";
    return (p.why && p.why[0]) ? p.why[0][0].toUpperCase() + p.why[0].slice(1) + "." : "";
  }
  function shortBanReason(b) {
    if (!b) return "";
    const tags = b.tags || [];
    if (tags.includes("hover")) return "Enemy is hovering it.";
    if (tags.includes("ours")) return "Likely enemy pick, but we may want it ourselves.";
    const m = /beats our (\S+(?: \S+)?) (Top|Jungle|Mid|Bot|Support) by (\+?[\d.]+)/.exec((b.reasons || [])[0] || "");
    if (tags.includes("scouted")) {
      const sc = /^(.+?)'s (\w+) pick( \(signature\))?/.exec((b.reasons || []).find((r) => /pick/.test(r)) || "");
      return sc ? "Scouted: " + sc[1] + ", " + sc[2].toLowerCase() + (sc[3] ? ", signature" : "") + "." : "Scouted enemy champion.";
    }
    if (tags.includes("policy")) return "Team ban policy.";
    if (m) return "Strong into our " + m[1] + " (" + m[2].toLowerCase() + ").";
    const r = (b.reasons || [])[0] || "";
    return r ? r[0].toUpperCase() + r.slice(1) + "." : "";
  }

  // Artwork: porcelain / gesture (Matchup Master collection) or splash / centered / tile / portrait (Riot art).
  let artIndex = { porcelain: [], gesture: [], slugs: {} };
  const artReady = fetch("/api/art/index").then((r) => r.json()).then((d) => { artIndex = d; return d; }).catch(() => artIndex);
  function artSlug(name) { return (artIndex.slugs && artIndex.slugs[name]) || String(name || "").toLowerCase().replace(/['.&]/g, "").trim().split(/\s+/).join("-"); }
  function hasArt(kind, name) { return (artIndex[kind] || []).includes(artSlug(name)); }
  function artUrl(kind, name) { const id = idFor(name); return id ? "/api/art/" + kind + "/" + id : null; }

  async function api(method, path, body) {
    const res = await fetch(path, { method, headers: body !== undefined ? { "content-type": "application/json" } : {},
                                    body: body !== undefined ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) throw new Error((data && (data.detail || (data.result && data.result.errors && data.result.errors[0]))) || res.statusText);
    return data;
  }

  global.Draft = { ROLES, ROLE_SHORT, $, el, portrait, idFor, learnIds, learnFromGuidance, fmtTimer, signed, renderTeam, renderBans,
                   renderPicks, renderBanTargets, renderCounters, renderChips, renderComp, renderAlerts, renderHeadline, connect, api,
                   hydrateIcons, shortPickReason, shortBanReason, initials, artReady, artSlug, hasArt, artUrl };
})(window);

window.Draft.overlayPickReason = function (p) {
  if (!p) return "";

  if (p.source === "exhausted") return "No prepared pick remains";

  const risk = p.counter_risk_by?.[0]?.enemy;
  if (p.counter_risk >= 0.5 && risk) {
    return "Can still be answered by " + risk;
  }

  const edge = p.lane?.[0];
  const value = edge && (edge.adjusted ?? edge.advantage);

  if (edge && Number.isFinite(value)) {
    const label = value > 0.2 ? "Favourable"
      : value < -0.2 ? "Unfavourable" : "Even";
    return `${label} into ${edge.locked ? "locked" : "hovered"} ${edge.enemy}`;
  }

  if (p.familiarity === "developing") return "Practice pick";
  if (p.familiarity === "comfort") return "Confirmed comfort";
  if (p.priority === 1) return "Preferred pick";
  if (p.blind_safe) return "Blind-safe option";
  if (p.risk_known === false) return "Blind safety unknown";

  if (p.flex_roles?.length) {
    return "Can flex to " + p.flex_roles
      .map(r => ({ ADC: "Bot" }[r] || r))
      .join(" / ");
  }

  if (["history", "history-fallback"].includes(p.source)) {
    return "From account history";
  }
  if (p.source === "meta") return "Role-meta fallback";

  return "Best available fit for this role";
};

window.Draft.overlayBanReason = function (b) {
  return window.Draft.shortBanReason(b)
    .replace(/\s+by\s+[+-]?\d+(?:\.\d+)?\b.*$/i, "")
    .replace(/\.$/, "");
};
