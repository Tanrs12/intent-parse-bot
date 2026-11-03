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
    const advice = guidance?.counter_advice?.lanes || [];
    const proDuos = guidance?.counter_advice?.pro_duos || {};
    const key = JSON.stringify([chosen, [...bans], [...taken], replacements, ownLocks,
      advice, proDuos, guidance?.counter_advice?.live_fetch,
      guidance?.compensation_search, wide]);
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
    const liveFetch = guidance?.counter_advice?.live_fetch;
    if (liveFetch?.state === "fetching") {
      root.append(D.el("p", "fd-sc-reference",
        "OP.GG: loading missing confirmed enemy matchups in background (" +
        liveFetch.queued + " pending). Picks and locks stay responsive."));
    } else if (liveFetch?.state === "daily_limit") {
      root.append(D.el("p", "fd-sc-reference",
        "OP.GG daily request cap reached; using cached stats and historical scouting."));
    } else if (liveFetch?.state === "access_restricted") {
      root.append(D.el("p", "fd-sc-reference",
        "OP.GG access restricted; live lookup paused. Cached scouting remains available."));
    }
    const compensation = guidance?.compensation_search;
    if (compensation?.state === "evaluating" ||
        compensation?.state === "queued_next_state") {
      root.append(D.el("p", "fd-sc-reference",
        "Checking coordinated counter-draft changes across all open lanes. " +
        "The current legal draft remains available while the search runs."));
    } else if (compensation?.state === "ready" &&
               (compensation?.selected_changes || []).length === 2) {
      root.append(D.el("p", "fd-sc-reference",
        "Coordinated change: " + compensation.selected_changes
          .map(role => SHORT[role]).join(" + ") +
        " · counter plus team-role compensation; existing players and locks preserved."));
    }
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
      const lane = advice.find(x => x.role === role);
      const playable = lane?.playable?.[0];
      const learn = lane?.learn?.[0];
      const scout = (lane?.scout || []).slice(0, 2);
      const rolling = lane?.opgg_reference || null;
      const full = lane?.opgg_full_reference || null;
      const historic = lane?.pocket_reference || null;
      const proOrder = lane?.pro_order_reference || null;
      const fullOptions = (full?.alternatives || []).slice(0, 4);
      const historicalOptions = (historic?.alternatives || []).slice(0, 3);
      const rollingOptions = (full?.planned || fullOptions.length)
        ? [] : (rolling?.alternatives || []).slice(0, 3);
      // Same-patch Emerald+ observations are prioritised over unversioned
      // MCP samples. These are reference cards, not legal champion swaps.
      for (const entry of fullOptions) {
        if (entry.champion && !options.includes(entry.champion)) options.push(entry.champion);
      }
      for (const entry of rollingOptions) {
        if (entry.champion && !options.includes(entry.champion)) options.push(entry.champion);
      }
      for (const entry of historicalOptions) {
        if (entry.champion && !options.includes(entry.champion)) options.push(entry.champion);
      }
      for (const entry of scout) {
        if (entry.champion && !options.includes(entry.champion)) options.push(entry.champion);
      }
      if (playable?.champion && !options.includes(playable.champion)) options.push(playable.champion);
      if (learn?.champion && !options.includes(learn.champion)) options.push(learn.champion);
      // An adaptive counter is visually distinct from the original Discord options.
      if (rec?.now && !options.includes(rec.now)) options.push(rec.now);
      const row = D.el("article", "fd-sc-role");
      const label = D.el("div", "fd-sc-role-head");
      label.append(D.el("strong", "", SHORT[role]));
      label.append(D.el("span", "fd-sc-player", chosen.players?.[role] || "Unassigned"));
      row.append(label);

      const picks = D.el("div", "fd-sc-picks");
      for (const champ of options.filter(Boolean)) {
        const isCounter = ((!!rec && champ === rec.now && champ !== primary)
          || (!!playable && champ === playable.champion));
        const scoutEntry = scout.find(entry => entry.champion === champ);
        const historicalEntry = historicalOptions.find(entry => entry.champion === champ) ||
          (historic?.planned?.champion === champ ? historic.planned : null);
        const fullEntry = fullOptions.find(entry => entry.champion === champ) ||
          (full?.planned?.champion === champ ? full.planned : null);
        const referenceEntry = fullEntry ||
          rollingOptions.find(entry => entry.champion === champ) ||
          (rolling?.original?.champion === champ ? rolling.original : null);
        const isScout = !!scoutEntry;
        const isLearn = learning.has(champ) || (!!learn && champ === learn.champion) ||
          (isScout && scoutEntry.type === "learn") ||
          (!!fullEntry && fullEntry.type === "learn") ||
          (!!referenceEntry && referenceEntry.type === "learn") ||
          (!!historicalEntry && historicalEntry.type === "learn");
        const isLocked = ownLocks[role] === champ;
        const unavailable = !isLocked && (bans.has(champ) || taken.has(champ));
        const item = D.el("div", "fd-sc-option" +
          (champ === primary ? " primary" : "") +
          (isLearn ? " learn" : "") +
          (isCounter ? " counter" : "") +
          (isScout ? " scout" : "") +
          (referenceEntry || historicalEntry ? " reference" : "") +
          (isLocked ? " locked" : "") +
          (unavailable ? " unavailable" : ""));
        item.title = [champ, isScout ? "SCOUT ONLY · " + scoutEntry.summary +
          " · " + (scoutEntry.sources || []).map(s => s.provider).join(" / ") +
          (scoutEntry.team_fit === "weakens_comp" ? " · weakens the current five" : "") +
          (scoutEntry.historical_hint ? " · older " + scoutEntry.historical_hint.patch +
            " sample: " + scoutEntry.historical_hint.games + " games, too few for a scored counter" : "")
          : isLearn ? "Learning suggestion" : isCounter ? "Qualified matchup counter"
          : champ === primary ? "Selected plan" : "Same-plan owned option",
          referenceEntry ? "OP.GG observed " + referenceEntry.head_to_head_win_rate +
            "% in " + referenceEntry.games + " games · " +
            (fullEntry ? fullEntry.source_patch + " Emerald+ · " +
              (Number.isFinite(fullEntry.adjusted_matchup_lift_pp)
                ? (fullEntry.adjusted_matchup_lift_pp >= 0 ? "+" : "") +
                    fullEntry.adjusted_matchup_lift_pp +
                    " pp vs own role baseline · " : "") +
              (fullEntry.source_direction === "reverse_from_enemy_page"
                ? "estimated from enemy-side results · " : "") +
              (fullEntry.team_fit === "needs_compensation"
                ? "may need a coordinated frontline/team adjustment · " : "") +
              "observational data"
              : "rank/patch unknown") + " · not a forced pick" : "",
          historicalEntry ? "PocketPick historical 16.16 EUW Silver–Platinum: " +
            historicalEntry.head_to_head_win_rate.toFixed(1) +
            "% in " + historicalEntry.games + " games, " +
            (historicalEntry.adjusted_matchup_lift_pp >= 0 ? "+" : "") +
            historicalEntry.adjusted_matchup_lift_pp +
            " pp versus own historical baseline · not current patch · no automatic swap" : "",
          unavailable ? "Unavailable" : isLocked ? "Locked in League" : ""].filter(Boolean).join(" · ");
        item.append(D.portrait(champ, "small"));
        item.append(D.el("span", "fd-sc-name", champ));
        if (scoutEntry) item.append(D.el("span", "fd-sc-idea-badge",
          scoutEntry.type === "learn" ? "LEARN" : "SCOUT"));
        else if (learn?.champion === champ) item.append(D.el("span", "fd-sc-idea-badge", "LEARN"));
        else if (playable?.champion === champ) item.append(D.el("span", "fd-sc-idea-badge", "COUNTER"));
        else if (referenceEntry) item.append(D.el("span", "fd-sc-idea-badge",
          referenceEntry.type === "learn" ? "LEARN" : fullEntry ? "OP.GG STATS" : "OP.GG"));
        else if (historicalEntry) item.append(D.el("span", "fd-sc-idea-badge",
          historicalEntry.type === "learn" ? "LEARN" : "HISTORIC"));
        if (isLocked) item.append(D.el("span", "fd-sc-lock", "✓"));
        picks.append(item);
      }
      row.append(picks);
      if (rec?.why) row.append(D.el("p", "fd-sc-reason", rec.why));
      else if (lane?.enemy) {
        const item = playable || learn;
        row.append(D.el("p", "fd-sc-reason",
          item ? "Versus " + lane.enemy + ": " + item.champion + (playable ? " (in pool)" : " (LEARN only)") +
            " · " + item.games + " games · " + (item.patch || "patch unspecified") +
            (item.evidence_freshness === "historical" ? " (historical)" : "")
          : scout.length ? "Versus " + lane.enemy + ": " + scout.map(x =>
              x.champion + " (" + (x.type === "learn" ? "LEARN only" : "in pool") +
              (x.team_fit === "weakens_comp" ? ", weaker team fit" : "") + ")").join(" / ") +
              " · reviewed " + scout[0].patch + " scouting, not proven ranked stats"
            : (rolling?.original || rollingOptions.length)
              ? "Versus " + lane.enemy + ": rolling OP.GG references below; no patch/rank-qualified automatic counter"
              : "Versus " + lane.enemy + ": no qualified counter data; keep the chosen plan"));
      }
      else if (options.length > 1) row.append(D.el("p", "fd-sc-reason", "Same-plan options" + (learning.size ? " · learning marked" : "")));
      if (full?.planned || fullOptions.length) {
        const observed = [full.planned, ...fullOptions]
          .filter(Boolean).slice(0, 5);
        row.append(D.el("p", "fd-sc-reference",
          "OP.GG " + full.patch + " " + full.rank + " full matchup data: " + observed.map(entry =>
            entry.champion + " " + entry.head_to_head_win_rate.toFixed(1) +
            "% (n=" + entry.games + ")").join(" · ") +
          " · observations only; player comfort and team composition take priority"));
      }
      if (!full?.planned && !fullOptions.length && (rolling?.original || rollingOptions.length)) {
        const observed = [rolling.original, ...rollingOptions]
          .filter(Boolean).slice(0, 4);
        row.append(D.el("p", "fd-sc-reference",
          "OP.GG observed (patch/rank unknown): " + observed.map(entry =>
            entry.champion + " " + entry.head_to_head_win_rate.toFixed(1) +
            "% (n=" + entry.games + ")").join(" · ")));
      }
      if (!full?.planned && !fullOptions.length &&
          historic && (historic.planned || historicalOptions.length)) {
        const oldRows = [historic.planned, ...historicalOptions].filter(Boolean).slice(0, 4);
        row.append(D.el("p", "fd-sc-reference",
          "PocketPick 16.16 EUW Silver–Platinum (historical only): " +
          oldRows.map(e => e.champion + " " +
            e.head_to_head_win_rate.toFixed(1) + "% (n=" + e.games + ")").join(" · ") +
          " · never interpreted as current-patch high-Elo counter evidence"));
      }
      if (proOrder?.planned?.games || (proOrder?.alternatives || []).length) {
        const patterns = [proOrder.planned, ...(proOrder.alternatives || [])]
          .filter(e => e?.games && e?.ours && e?.enemy).slice(0, 3);
        row.append(D.el("p", "fd-sc-reference",
          "Pro draft ORDER (2026 patches 26.13–26.17): " +
          patterns.map(e => e.ours + " selected AFTER " + e.enemy +
            " in " + e.games + " games").join(" · ") +
          " · positions unverified; historical only; not an automatic counter"));
      }
      if (lane?.planned_risk) {
        row.append(D.el("p", "fd-sc-risk",
          primary + " into " + lane.enemy + ": adverse community matchup report · " +
          lane.planned_risk.summary));
      }
      const currentSource = full?.planned?.source_url || fullOptions[0]?.source_url;
      const proSource = proOrder?.planned?.source_url ||
        (proOrder?.alternatives || [])[0]?.source_url;
      const historicSource = historic?.planned?.source_url ||
        historicalOptions[0]?.source_url;
      const source = scout[0]?.sources?.[0] || lane?.planned_risk?.sources?.[0] ||
        (currentSource ? {url: currentSource, provider: "OP.GG · current patch"} : null) ||
        (rollingOptions[0]?.source_url
          ? {url: rollingOptions[0].source_url, provider: "OP.GG MCP"} : null) ||
        (proSource ? {url: proSource,
            provider: "LoL-Draft / Leaguepedia · CC BY-SA"} : null) ||
        (historicSource ? {url: historicSource,
            provider: "PocketPick · historical"} : null);
      if (source?.url?.startsWith("https://")) {
        const link = D.el("a", "fd-sc-source-link", "Matchup source · " + (source.provider || "reference"));
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.append(link);
      }
      rows.append(row);
    }
    root.append(rows);
    // Read-only ChainCC professional co-occurrence, never an auto-pick signal.
    // A season aggregate is deliberately labelled as such, not as the live patch.
    for (const [packageName, label] of [["Mid+Jungle", "MID + JUNGLE"], ["ADC+Support", "BOT DUO"]]) {
      const reference = proDuos[packageName];
      if (!reference || !reference.games) continue;
      const n = Number(reference.games);
      const winrate = Number(reference.win_rate);
      const period = reference.exact_patch ? "patch " + reference.patch : reference.patch;
      root.append(D.el("p", "fd-sc-reference",
        "PRO REFERENCE · " + label + " · " + reference.first + " + " + reference.second +
        " · " + n + " games, " + (Number.isFinite(winrate) ? winrate.toFixed(1) + "% wins" : "win rate unavailable") +
        " · " + period + " · observational only"));
    }
    root.append(D.el("p", "fd-sc-footnote", chosen.mode === "adaptive"
      ? "Roles fixed · Unpicked champions can adapt to verified enemy lanes"
      : "Selected plan · Actual League locks take priority"));
  }
  window.DraftSelectedComp = { render };
  // Both pages call D.SelectedComp.render(...): without this alias every coach-page render threw
  // before drawing anything (and the panel never drew the chosen comp).
  if (D) D.SelectedComp = window.DraftSelectedComp;
})();
