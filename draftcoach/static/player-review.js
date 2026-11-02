/* Player review: this player's own account, read from their own League client (/api/stats/me).
   OP.GG / Blitz / League of Graphs style: rank, record, form, champions, roles, how you compare
   with the same-role players in your games, recent games, matchups, tilt signals and insights.
   Coach page only; the panel and HUD never show it. */
(function (D) {
  const $ = D.$, el = D.el;
  let loaded = false, loading = false;

  function pct(v) { return v === null || v === undefined ? "–" : Math.round(v) + "%"; }
  function num(v, d) { return v === null || v === undefined ? "–" : Number(v).toFixed(d === undefined ? 1 : d); }
  function wl(r) { return (r.wins ?? "–") + "–" + (r.losses ?? "–"); }
  function ago(iso) {
    if (!iso) return "";
    const h = (Date.now() - new Date(iso).getTime()) / 36e5;
    return h < 1 ? "just now" : h < 24 ? Math.round(h) + " h ago" : Math.round(h / 24) + " d ago";
  }
  function fmtDuration(s) { return s ? Math.floor(s / 60) + ":" + String(Math.round(s % 60)).padStart(2, "0") : ""; }

  function statTile(label, value, sub) {
    const tile = el("div", "pr-tile");
    tile.append(el("span", "mm-meta", label), el("strong", "", value));
    if (sub) tile.append(el("span", "pr-tile-sub", sub));
    return tile;
  }

  function rankChips(ranked) {
    const row = el("div", "pr-ranks");
    for (const q of ranked || []) {
      const chip = el("div", "pr-rank");
      chip.dataset.tier = (q.tier || "").toLowerCase();
      chip.append(el("span", "mm-meta", q.queue), el("strong", "", q.tier + (q.division ? " " + q.division : "")),
                  el("span", "pr-rank-sub", q.tier === "Unranked" ? "" : q.lp + " LP · " + wl(q) + (q.winrate !== null ? " · " + pct(q.winrate) : "")));
      row.append(chip);
    }
    if (!row.childElementCount) row.append(el("p", "fd-note", "No ranked standing reported by the client."));
    return row;
  }

  function formStrip(form) {
    const strip = el("div", "pr-form");
    for (const ch of form || "") {
      const dot = el("span", "pr-form-dot", ch);
      dot.dataset.result = ch;
      strip.append(dot);
    }
    return strip;
  }

  function table(headers, rows, render) {
    const t = el("table", "pr-table");
    const thead = el("thead"), tr = el("tr");
    for (const h of headers) tr.append(el("th", "", h));
    thead.append(tr); t.append(thead);
    const tbody = el("tbody");
    for (const r of rows) tbody.append(render(r));
    t.append(tbody);
    return t;
  }

  function championRow(c) {
    const tr = el("tr");
    tr.dataset.low = String(!!c.low_sample);
    const name = el("td", "pr-champ");
    name.append(D.portrait(c.label, "small", D.idFor(c.label)), el("span", "", c.label));
    tr.append(name, el("td", "", wl(c)), el("td", "", pct(c.winrate)), el("td", "", num(c.kda, 2)),
              el("td", "", num(c.cs_per_min)), el("td", "", num(c.damage_per_min, 0)), el("td", "", pct(c.kp)));
    return tr;
  }

  function roleBars(roles) {
    const box = el("div", "pr-roles");
    const total = roles.reduce((n, r) => n + (r.games || 0), 0) || 1;
    for (const r of roles) {
      const row = el("div", "pr-role");
      const bar = el("div", "pr-role-bar");
      const fill = el("div", "pr-role-fill");
      fill.style.width = Math.round(100 * (r.games || 0) / total) + "%";
      bar.append(fill);
      row.append(el("span", "pr-role-name", r.label), bar, el("span", "pr-role-stat", r.games + " games · " + pct(r.winrate)));
      box.append(row);
    }
    return box;
  }

  function compareRows(compare) {
    const box = el("div", "pr-compare");
    for (const c of compare || []) {
      if (c.percentile === null || c.percentile === undefined) continue;
      const row = el("div", "pr-cmp");
      const bar = el("div", "pr-cmp-bar");
      const fill = el("div", "pr-cmp-fill");
      const p = Math.max(2, Math.min(100, Number(c.percentile)));
      fill.style.width = p + "%";
      fill.dataset.band = p >= 75 ? "top" : p <= 25 ? "bottom" : "mid";
      bar.append(fill);
      row.append(el("span", "pr-cmp-label", c.label), bar,
                 el("span", "pr-cmp-val", num(c.you, c.key === "kda" ? 2 : c.key === "vision_per_min" ? 2 : 1) + " vs " + num(c.peers, c.key === "kda" ? 2 : 1) + " · " + Math.round(p) + (c.lower_is_better ? "th (fewer is better)" : "th")));
      box.append(row);
    }
    if (!box.childElementCount) box.append(el("p", "fd-note", "Not enough same-role players in these games to compare."));
    return box;
  }

  function recentRow(g) {
    const tr = el("tr");
    tr.dataset.result = g.win === true ? "W" : g.win === false ? "L" : "?";
    const name = el("td", "pr-champ");
    name.append(D.portrait(g.champion, "small", D.idFor(g.champion)), el("span", "", g.champion + (g.opponent ? " vs " + g.opponent : "")));
    tr.append(el("td", "pr-result", g.win === true ? "Win" : g.win === false ? "Loss" : "–"), name, el("td", "", g.role),
              el("td", "", g.kills + " / " + g.deaths + " / " + g.assists), el("td", "", num(g.cs_per_min)), el("td", "", num(g.damage_per_min, 0)),
              el("td", "", fmtDuration(g.duration)), el("td", "pr-when", ago(g.date)));
    return tr;
  }

  function render(data) {
    const box = $("review-body");
    box.replaceChildren();
    const r = data.review;
    $("review-sub").textContent = r ? r.account.name + " · " + r.overview.games + " games · refreshed " + (data.refreshed_at ? ago(new Date(data.refreshed_at * 1000).toISOString()) : "from cache")
                                    : data.error ? "Unavailable: " + data.error : data.client ? "Press Refresh to read your account" : "Open League on this PC to read your account";
    if (!r) {
      box.append(el("p", "fd-note", data.error || "Your own ranked standing, champions, form and insights appear here once this app has read your League client."));
      return;
    }
    const head = el("div", "pr-head");
    head.append(rankChips(r.ranked));
    const tiles = el("div", "pr-tiles");
    tiles.append(statTile("Record", wl(r.overview), pct(r.overview.winrate) + " win rate"), statTile("KDA", num(r.overview.kda, 2), "kills + assists per death"),
                 statTile("CS / min", num(r.overview.cs_per_min), ""), statTile("Damage / min", num(r.overview.damage_per_min, 0), ""),
                 statTile("Kill share", pct(r.overview.kp), "of team kills"), statTile("Main role", r.overview.main_role || "–",
                 r.overview.streak && r.overview.streak.current >= 2 ? r.overview.streak.current + "-game " + (r.overview.streak.result || "").toLowerCase() + " streak" : ""));
    head.append(tiles);
    const form = el("div", "pr-form-row");
    form.append(el("span", "mm-meta", "Last ten"), formStrip(r.overview.form));
    head.append(form);
    box.append(head);

    if (r.insights && r.insights.length) {
      const ins = el("ul", "pr-insights");
      for (const i of r.insights) { const li = el("li", "", i.text); li.dataset.level = i.level; ins.append(li); }
      box.append(section("Insights", ins));
    }
    box.append(section("Champions", table(["Champion", "W–L", "Win", "KDA", "CS/m", "DPM", "KP"], r.champions, championRow)));
    const two = el("div", "pr-two");
    two.append(section("Roles", roleBars(r.roles)), section("You vs the same role in your games", compareRows(r.compare)));
    box.append(two);
    box.append(section("Recent games", table(["", "Champion", "Role", "K / D / A", "CS/m", "DPM", "Length", "When"], r.recent, recentRow)));
    if (r.matchups && r.matchups.length) {
      box.append(section("Matchups on record", table(["Matchup", "Games", "Win"], r.matchups, (m) => {
        const tr = el("tr"); tr.append(el("td", "", m.label), el("td", "", String(m.games)), el("td", "", pct(m.winrate))); return tr;
      })));
    }
    const tilt = el("div", "pr-tilt");
    for (const [label, rows] of [["After a win / loss", r.tilt.after_loss], ["Game of the session", r.tilt.by_session], ["Game length", r.tilt.by_duration], ["Side", r.tilt.by_side]]) {
      const known = (rows || []).filter((x) => x.games);
      if (!known.length) continue;
      const card = el("div", "pr-tilt-card");
      card.append(el("span", "mm-meta", label));
      for (const x of known) card.append(el("div", "pr-tilt-row", x.label + ": " + wl(x) + " (" + pct(x.winrate) + ")"));
      tilt.append(card);
    }
    if (tilt.childElementCount) box.append(section("When you win and lose", tilt));
    const notes = el("details", "fd-details");
    notes.append(el("summary", "", "How these numbers are made"));
    const ul = el("ul", "pr-notes");
    for (const n of r.notes || []) ul.append(el("li", "", n));
    notes.append(ul);
    box.append(notes);
    D.hydrateIcons(box);
  }

  function section(title, body) {
    const s = el("section", "pr-section");
    s.append(el("h3", "", title), body);
    return s;
  }

  async function load(refresh) {
    if (loading) return;
    loading = true;
    $("review-refresh").disabled = true;
    $("review-refresh").textContent = refresh ? "Reading your client…" : "Loading…";
    try {
      const res = await fetch("/api/stats/me" + (refresh ? "?refresh=1" : ""), { cache: "no-store" });
      render(await res.json());
      loaded = true;
    } catch (e) {
      $("review-sub").textContent = "Could not load the review: " + e.message;
    } finally {
      loading = false;
      $("review-refresh").disabled = false;
      $("review-refresh").textContent = "Refresh from my client";
    }
  }

  function mount() {
    const card = $("review-card");
    if (!card) return;
    document.querySelector(".coach-main").append(card);
    card.addEventListener("toggle", () => { if (card.open && !loaded) load(false); });
    $("review-refresh").onclick = (event) => { event.preventDefault(); load(true); };
    if (new URLSearchParams(location.search).has("review")) card.open = true;
    load(false);
  }

  D.PlayerReview = { mount, load };
})(window.Draft);
