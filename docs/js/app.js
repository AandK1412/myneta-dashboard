/* ============================================================
   app.js - loads the pre-aggregated JSON and wires the dashboard.
   Everything is client-side over static files: no API, no key.

   Scoping rule: the filter row at the top scopes EVERY panel.
   When a state is selected, KPIs, trends, education, age and
   party charts all re-read from the per-state aggregates in
   states.json; only the leaderboard stays national (it is a
   national top-50 by construction, and says so).
   ============================================================ */

import {
  lineChart, barChart, stackedBar, legend, renderTable,
  fmtRupees, fmtPct, fmtInt, hideTip
} from "./charts.js?v=2";

const CSS = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const $ = (id) => document.getElementById(id);

const state = {
  summary: null, winners: [], leaderboards: [], repeats: [], states: null,
  year: null, stateFilter: "", cohort: "All candidates", partyFilter: "",
  board: "Richest candidates", search: "", repeatSearch: "",
  sort: { key: "constituency", dir: 1 },
  tableViews: new Set(),
};

const COHORTS = { all: "All candidates", win: "Winners (MPs)" };
const EDU_KEYS = ["Illiterate / Literate", "Class 5-12", "Graduate or above", "Not disclosed"];
const AGE_KEYS = ["Under 40", "40-54", "55-69", "70+", "Unknown"];
const eduColors = () => [CSS("--edu-1"), CSS("--edu-2"), CSS("--edu-3"), CSS("--edu-na")];
const ageColors = () => [CSS("--age-1"), CSS("--age-2"), CSS("--age-3"), CSS("--age-4"), CSS("--edu-na")];
const cohortColors = () => ({ "All candidates": CSS("--series-1"), "Winners (MPs)": CSS("--series-2") });

/* ---------- boot ---------- */

async function boot() {
  try {
    const [summary, winners, leaderboards, repeats, states] = await Promise.all([
      fetch("data/summary.json").then(r => { if (!r.ok) throw new Error(`summary.json ${r.status}`); return r.json(); }),
      fetch("data/winners.json").then(r => r.ok ? r.json() : { winners: [] }),
      fetch("data/leaderboards.json").then(r => r.ok ? r.json() : { entries: [] }),
      fetch("data/repeat_candidates.json").then(r => r.ok ? r.json() : { candidates: [] })
        .catch(() => ({ candidates: [] })),
      fetch("data/states.json").then(r => r.ok ? r.json() : null).catch(() => null),
    ]);

    state.summary = summary;
    state.winners = winners.winners || [];
    state.leaderboards = leaderboards.entries || [];
    state.repeats = repeats.candidates || [];
    state.states = states;   // null => state-level detail unavailable

    const years = summary.meta.election_years || [];
    state.year = years[years.length - 1];

    mount(years);
    renderAll();
  } catch (err) {
    $("app").className = "";
    $("app").innerHTML = `<div class="error">
      <strong>Could not load the dashboard data.</strong>
      <p>${err.message}</p>
      <p>If you are running this locally, serve the folder over HTTP rather than opening
      the file directly — <code>python -m http.server</code> from <code>docs/</code> —
      because <code>fetch()</code> is blocked on <code>file://</code> URLs.</p>
      <p>If the site is deployed, the pipeline may not have published
      <code>docs/data/summary.json</code> yet.</p></div>`;
    console.error(err);
  }
}

function mount(years) {
  const app = $("app");
  app.className = "";
  app.innerHTML = "";
  app.appendChild($("dashboardTpl").content.cloneNode(true));

  const m = state.summary.meta;
  $("metaLine").textContent =
    `${fmtInt(m.total_candidates)} candidates · ${fmtInt(m.total_winners)} MPs · ` +
    `updated ${new Date(m.generated_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}`;

  const ySel = $("fYear");
  ySel.innerHTML = years.map(y => `<option value="${y}">${y} Lok Sabha</option>`).join("");
  ySel.value = state.year;
  ySel.onchange = () => { state.year = +ySel.value; renderAll(); };

  const states = [...new Set(state.winners.map(w => w.state).filter(Boolean))].sort();
  $("fState").innerHTML = '<option value="">All states</option>' +
    states.map(s => `<option value="${s}">${s}</option>`).join("");
  $("fState").onchange = (e) => { state.stateFilter = e.target.value; renderAll(); };

  $("cohAll").onclick = () => setCohort(COHORTS.all);
  $("cohWin").onclick = () => setCohort(COHORTS.win);

  $("lbRich").onclick = () => setBoard("Richest candidates");
  $("lbCases").onclick = () => setBoard("Most declared cases");

  $("tableSearch").oninput = (e) => { state.search = e.target.value.toLowerCase(); renderWinnersTable(); };
  $("repeatSearch").oninput = (e) => { state.repeatSearch = e.target.value.toLowerCase(); renderRepeats(); };

  $("clearFilters").onclick = clearFilters;
  $("backToStates").onclick = () => { setStateFilter(""); };
  $("downloadFiltered").onclick = downloadFilteredCSV;

  for (const btn of document.querySelectorAll(".view-toggle[data-view]")) {
    btn.onclick = () => {
      const k = btn.dataset.view;
      state.tableViews.has(k) ? state.tableViews.delete(k) : state.tableViews.add(k);
      btn.textContent = state.tableViews.has(k) ? "Chart view" : "Table view";
      renderAll();
    };
  }

  $("themeToggle").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : cur === "light" ? "dark"
      : (matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (_) {}
    renderAll();
  };

  let t;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(renderAll, 180); });
}

function setCohort(c) {
  state.cohort = c;
  $("cohAll").setAttribute("aria-pressed", String(c === COHORTS.all));
  $("cohWin").setAttribute("aria-pressed", String(c === COHORTS.win));
  renderAll();
}

function setBoard(b) {
  state.board = b;
  $("lbRich").setAttribute("aria-pressed", String(b === "Richest candidates"));
  $("lbCases").setAttribute("aria-pressed", String(b === "Most declared cases"));
  renderLeaderboard();
}

function setStateFilter(s) {
  state.stateFilter = s;
  const sel = $("fState");
  if (sel) sel.value = s;
  renderAll();
}

function setPartyFilter(pg) {
  state.partyFilter = pg;
  renderWinnersTable();
  if (pg) $("winnersTable").closest(".card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function clearFilters() {
  state.stateFilter = ""; state.partyFilter = "";
  state.search = ""; state.repeatSearch = "";
  const sel = $("fState"); if (sel) sel.value = "";
  $("tableSearch").value = ""; $("repeatSearch").value = "";
  renderAll();
}

/* ---------- slice helpers ---------- */

const trendsNational = (cohort) => (state.summary.national_trends || [])
  .filter(r => r.cohort === cohort)
  .sort((a, b) => a.election_year - b.election_year);

function trendsSlice(cohort) {
  // Per-state trend rows when a state is selected and states.json is present;
  // national rows otherwise.
  if (state.stateFilter && state.states) {
    return (state.states.state_trends || [])
      .filter(r => r.state === state.stateFilter && r.cohort === cohort)
      .sort((a, b) => a.election_year - b.election_year);
  }
  return trendsNational(cohort);
}

const scopeName = () => state.stateFilter || "All India";

function sliceGaps() {
  // Reasons the current slice may be empty, surfaced instead of a blank page.
  const msgs = [];
  if (!state.stateFilter) return msgs;
  if (!state.states) {
    msgs.push("State-level detail is unavailable (states.json missing) — " +
              "charts show national figures; tables below are still filtered.");
    return msgs;
  }
  const has = trendsSlice(state.cohort).some(r => r.election_year === state.year);
  if (!has) {
    if (state.stateFilter === "Telangana" && state.year < 2014) {
      msgs.push(`<strong>Telangana</strong> was created in 2014 — in ${state.year} these ` +
                `constituencies were part of <strong>Andhra Pradesh</strong>.`);
    } else {
      msgs.push(`No affidavit data for <strong>${state.stateFilter}</strong> in ` +
                `<strong>${state.year}</strong>. Charts show the years that do have data.`);
    }
  }
  return msgs;
}

/* ---------- render ---------- */

function renderAll() {
  hideTip();
  updateChrome();
  renderKPIs();
  renderInsights();
  renderTrends();
  renderEducation();
  renderAge();
  renderParty();
  renderStates();
  renderWinnersTable();
  renderLeaderboard();
  renderRepeats();
}

function updateChrome() {
  $("contextLine").textContent =
    `${state.year} · ${scopeName()} · ${state.cohort}`;

  const active = state.stateFilter || state.partyFilter || state.search || state.repeatSearch;
  $("clearFilters").classList.toggle("hidden", !active);

  const msgs = sliceGaps();
  const notice = $("sliceNotice");
  notice.classList.toggle("hidden", msgs.length === 0);
  notice.innerHTML = msgs.join("<br>");

  $("backToStates").classList.toggle("hidden", !state.stateFilter);
}

function renderKPIs() {
  const rows = trendsSlice(state.cohort);
  const row = rows.find(r => r.election_year === state.year);
  const prevYear = rows.filter(r => r.election_year < state.year).map(r => r.election_year).pop();
  const prev = rows.find(r => r.election_year === prevYear);

  // Delta with optional good/bad valence. Rising criminal share reads as
  // status-critical; falling as status-good. Wealth/age deltas stay neutral.
  const delta = (cur, old, fmt, valence = null) => {
    if (cur === null || cur === undefined || old === null || old === undefined) return "";
    const d = cur - old;
    if (Math.abs(d) < 1e-9) return `<div class="kpi-note">unchanged vs ${prevYear}</div>`;
    const arrow = d > 0 ? "▲" : "▼";
    let color = "";
    if (valence === "up-bad") color = d > 0 ? "var(--critical)" : "var(--good)";
    return `<div class="kpi-note"><span style="color:${color || "inherit"}">${arrow}</span> ` +
           `${d > 0 ? "+" : ""}${fmt(d)} vs ${prevYear}</div>`;
  };

  const cards = [
    { label: state.cohort === COHORTS.win ? "MPs elected" : "Candidates",
      value: fmtInt(row?.n), note: `${state.year} · ${scopeName()}` },
    { label: "Facing criminal cases", value: fmtPct(row?.pct_criminal),
      note: delta(row?.pct_criminal, prev?.pct_criminal, v => v.toFixed(1) + " pts", "up-bad") },
    { label: "Crorepatis", value: fmtPct(row?.pct_crorepati),
      note: delta(row?.pct_crorepati, prev?.pct_crorepati, v => v.toFixed(1) + " pts") },
    { label: "Median assets", value: fmtRupees(row?.median_assets),
      note: prev?.median_assets
        ? `<div class="kpi-note">was ${fmtRupees(prev.median_assets)} in ${prevYear}</div>` : "" },
    { label: "Average age", value: row?.avg_age ? row.avg_age.toFixed(1) : "—",
      note: `<div class="kpi-note">${fmtPct(row?.pct_graduate_plus)} graduate or above</div>` },
  ];

  $("kpiRow").innerHTML = cards.map(c => `
    <div class="kpi kpi-in">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      ${c.note?.startsWith("<") ? c.note : `<div class="kpi-note">${c.note || ""}</div>`}
    </div>`).join("");
}

/* ---------- insights ---------- */

function renderInsights() {
  const cohortNoun = state.cohort === COHORTS.win ? "MPs" : "candidates";
  const rows = trendsSlice(state.cohort).filter(r => r.n);
  const items = [];
  $("insightsTitle").textContent = `What stands out — ${scopeName()}`;

  if (rows.length >= 2) {
    const first = rows[0], last = rows[rows.length - 1];

    if (first.pct_criminal !== null && last.pct_criminal !== null) {
      const dir = last.pct_criminal > first.pct_criminal ? "up from" : "down from";
      items.push(`<strong>${fmtPct(last.pct_criminal)}</strong> of ${cohortNoun} in ` +
        `${last.election_year} declared pending criminal cases — ${dir} ` +
        `<strong>${fmtPct(first.pct_criminal)}</strong> in ${first.election_year}.`);
    }
    if (first.median_assets && last.median_assets) {
      const mult = last.median_assets / first.median_assets;
      items.push(`Median declared assets reached <strong>${fmtRupees(last.median_assets)}</strong> — ` +
        `<strong>${mult >= 10 ? mult.toFixed(0) : mult.toFixed(1)}×</strong> the ` +
        `${first.election_year} figure, in nominal rupees (not inflation-adjusted).`);
    }
  }

  // Winner premium: winners vs the field they beat, latest common year.
  const win = trendsSlice(COHORTS.win), all = trendsSlice(COHORTS.all);
  const wLast = [...win].reverse().find(r => r.median_assets);
  const aMatch = wLast && all.find(r => r.election_year === wLast.election_year && r.median_assets);
  if (wLast && aMatch) {
    const k = wLast.median_assets / aMatch.median_assets;
    items.push(`Winning pays: in ${wLast.election_year}, elected MPs declared a median of ` +
      `<strong>${fmtRupees(wLast.median_assets)}</strong> — <strong>${k.toFixed(1)}×</strong> ` +
      `the median across everyone who stood.`);
  }
  const wYear = [...win].reverse().find(r => r.pct_crorepati !== null);
  const aYear = wYear && all.find(r => r.election_year === wYear.election_year && r.pct_crorepati !== null);
  if (wYear && aYear) {
    items.push(`<strong>${fmtPct(wYear.pct_crorepati)}</strong> of ${wYear.election_year}'s winners ` +
      `are crorepatis, against <strong>${fmtPct(aYear.pct_crorepati)}</strong> of the full field.`);
  }

  $("insightsList").innerHTML = items.length
    ? items.map(i => `<li>${i}</li>`).join("")
    : `<li>Not enough data in this slice for trend insights — try clearing the state filter.</li>`;
}

/* ---------- trend charts ---------- */

function trendSeries(field) {
  const cols = cohortColors();
  const years = state.summary.meta.election_years;
  return Object.values(COHORTS).map(c => {
    const rows = trendsSlice(c);
    return {
      name: c, color: cols[c],
      points: years.map(y => ({ x: y, y: rows.find(r => r.election_year === y)?.[field] ?? null })),
    };
  });
}

function drawTrend(hostId, field, fmt, yLabel, label) {
  const years = state.summary.meta.election_years;
  const series = trendSeries(field);
  const host = $(hostId);
  const legendHost = $(hostId + "Legend");

  if (state.tableViews.has(hostId)) {
    renderTable(host,
      [{ label: "Election", get: r => r.year },
       ...series.map(s => ({ label: s.name, num: true, get: r => r[s.name] === null ? "—" : fmt(r[s.name]) }))],
      years.map((y, i) => {
        const row = { year: y };
        for (const s of series) row[s.name] = s.points[i].y;
        return row;
      }));
    legendHost.innerHTML = "";
    return;
  }
  lineChart(host, { series, xs: years, yFormat: fmt, yLabel,
    keyboardLabel: `${label} — ${scopeName()}` });
  legend(legendHost, series.map(s => ({ name: s.name, color: s.color })));
}

function renderTrends() {
  drawTrend("crimeTrend", "pct_criminal", v => v.toFixed(0) + "%", "% of cohort",
    "Share facing criminal cases by election year");
  drawTrend("assetTrend", "median_assets", fmtRupees, "",
    "Median declared assets by election year");
  drawTrend("croreTrend", "pct_crorepati", v => v.toFixed(0) + "%", "% of cohort",
    "Share of crorepati candidates by election year");
}

/* ---------- distribution charts (education, age) ---------- */

function distSource(nationalRows, stateRows, groupKey) {
  // Returns rows shaped {election_year, <groupKey>, candidates, winners}
  if (state.stateFilter && state.states) {
    return (stateRows || []).filter(r => r.state === state.stateFilter);
  }
  return nationalRows || [];
}

function renderDist(hostId, legendId, keys, colors, nationalRows, stateRows, groupKey) {
  const years = state.summary.meta.election_years;
  const dist = distSource(nationalRows, stateRows, groupKey);
  const useWinners = state.cohort === COHORTS.win;

  const stacks = years.map(y => {
    const o = {};
    for (const k of keys) {
      const r = dist.find(d => d.election_year === y && d[groupKey] === k);
      o[k] = r ? (useWinners ? r.winners : r.candidates) : 0;
    }
    return o;
  });

  const host = $(hostId);
  const hasAny = stacks.some(s => keys.some(k => s[k] > 0));
  if (state.tableViews.has(hostId)) {
    renderTable(host,
      [{ label: "Election", get: r => r.year },
       ...keys.map(k => ({ label: k, num: true, get: r => fmtInt(r[k]) }))],
      years.map((y, i) => ({ year: y, ...stacks[i] })));
    $(legendId).innerHTML = "";
    return;
  }
  if (!hasAny) {
    host.innerHTML = '<p class="desc">No data for this selection.</p>';
    $(legendId).innerHTML = "";
    return;
  }
  stackedBar(host, { xs: years, stacks, keys, colors });
  legend($(legendId), keys.map((k, i) => ({ name: k, color: colors[i] })));
}

function renderEducation() {
  renderDist("eduDist", "eduDistLegend", EDU_KEYS, eduColors(),
    state.summary.education_dist, state.states?.education_by_state, "education_group");
}

function renderAge() {
  $("ageTitle").textContent = state.stateFilter
    ? `Age profile — ${state.stateFilter}` : "Age profile";
  renderDist("ageDist", "ageDistLegend", AGE_KEYS, ageColors(),
    state.summary.age_dist, state.states?.age_by_state, "age_band");
}

/* ---------- party chart ---------- */

function renderParty() {
  let rows;
  if (state.stateFilter && state.states) {
    rows = (state.states.party_by_state || [])
      .filter(r => r.state === state.stateFilter && r.election_year === state.year);
    $("partyTitle").textContent = `Seats won by party — ${state.stateFilter}, ${state.year}`;
  } else {
    rows = (state.summary.party_summary || []).filter(r => r.election_year === state.year);
    $("partyTitle").textContent = `Seats won by party, ${state.year}`;
  }
  rows = rows.filter(r => r.seats_won > 0).sort((a, b) => b.seats_won - a.seats_won);

  const host = $("partyChart");
  if (state.tableViews.has("partyChart")) {
    renderTable(host, [
      { label: "Party", get: r => r.party_group },
      { label: "Seats", num: true, get: r => fmtInt(r.seats_won), sortVal: r => r.seats_won },
      { label: "Candidates", num: true, get: r => fmtInt(r.candidates), sortVal: r => r.candidates },
      { label: "% w/ cases", num: true, get: r => fmtPct(r.pct_criminal), sortVal: r => r.pct_criminal },
      { label: "Median assets", num: true, get: r => fmtRupees(r.median_assets), sortVal: r => r.median_assets },
    ], rows, { sortable: true, id: "partyTable" });
    return;
  }
  if (!rows.length) {
    host.innerHTML = '<p class="desc">No seats in this selection.</p>';
    return;
  }
  barChart(host, { data: rows, labelKey: "party_group", valueKey: "seats_won",
    color: CSS("--series-1"), valueFormat: fmtInt, maxBars: 12, note: "Seats won",
    onClick: (r) => setPartyFilter(r.party_group),
    clickHint: "Click to list these MPs below" });
}

/* ---------- state chart: ranking, or state-vs-India emphasis ---------- */

function renderStates() {
  const host = $("stateChart");
  const legendHost = $("stateChartLegend");

  if (!state.stateFilter) {
    $("stateTitle").textContent = `States by share of MPs facing cases, ${state.year}`;
    $("stateDesc").textContent =
      "Winning MPs only. States with fewer than three seats are excluded — one MP in a " +
      "two-seat state swings the percentage wildly. Click a bar to focus the whole page " +
      "on that state.";
    const rows = (state.summary.state_summary || [])
      .filter(r => r.election_year === state.year && r.seats >= 3)
      .sort((a, b) => (b.pct_criminal ?? 0) - (a.pct_criminal ?? 0));

    if (state.tableViews.has("stateChart")) {
      renderTable(host, [
        { label: "State / UT", get: r => r.state },
        { label: "Seats", num: true, get: r => fmtInt(r.seats), sortVal: r => r.seats },
        { label: "% w/ cases", num: true, get: r => fmtPct(r.pct_criminal), sortVal: r => r.pct_criminal },
        { label: "% crorepati", num: true, get: r => fmtPct(r.pct_crorepati), sortVal: r => r.pct_crorepati },
        { label: "Median assets", num: true, get: r => fmtRupees(r.median_assets), sortVal: r => r.median_assets },
        { label: "Avg age", num: true, get: r => r.avg_age ?? "—", sortVal: r => r.avg_age },
      ], rows, { sortable: true, id: "stateTable" });
      legendHost.innerHTML = "";
      return;
    }
    legendHost.innerHTML = "";
    barChart(host, { data: rows, labelKey: "state", valueKey: "pct_criminal",
      color: CSS("--series-1"), valueFormat: v => v.toFixed(0) + "%",
      maxBars: 36, note: "MPs facing cases",
      onClick: (r) => setStateFilter(r.state),
      clickHint: "Click to focus the dashboard on this state" });
    return;
  }

  // Emphasis mode: this state vs the national line, over time.
  const cohortNoun = state.cohort === COHORTS.win ? "MPs" : "candidates";
  $("stateTitle").textContent =
    `${state.stateFilter} vs all India — ${cohortNoun} facing criminal cases`;
  $("stateDesc").textContent =
    "The selected state against the national share, election by election. " +
    "A line that starts late means the state did not exist yet.";

  const years = state.summary.meta.election_years;
  const stRows = trendsSlice(state.cohort);
  const natRows = trendsNational(state.cohort);
  const series = [
    { name: state.stateFilter, color: CSS("--series-1"),
      points: years.map(y => ({ x: y, y: stRows.find(r => r.election_year === y)?.pct_criminal ?? null })) },
    { name: "All India", color: CSS("--text-secondary"),
      points: years.map(y => ({ x: y, y: natRows.find(r => r.election_year === y)?.pct_criminal ?? null })) },
  ];

  if (state.tableViews.has("stateChart")) {
    renderTable(host,
      [{ label: "Election", get: r => r.year },
       { label: state.stateFilter, num: true, get: r => r.st === null ? "—" : fmtPct(r.st) },
       { label: "All India", num: true, get: r => r.nat === null ? "—" : fmtPct(r.nat) }],
      years.map((y, i) => ({ year: y, st: series[0].points[i].y, nat: series[1].points[i].y })));
    legendHost.innerHTML = "";
    return;
  }
  lineChart(host, { series, xs: years, yFormat: v => v.toFixed(0) + "%",
    yLabel: "% facing cases",
    keyboardLabel: `${state.stateFilter} vs national share of ${cohortNoun} facing criminal cases` });
  legend(legendHost, series.map(s => ({ name: s.name, color: s.color })));
}

/* ---------- winners table ---------- */

const WINNER_COLS = [
  { key: "constituency", label: "Constituency", get: r => r.constituency },
  { key: "state", label: "State", get: r => r.state },
  { key: "name", label: "MP", get: r => r.candidate_id
      ? `<a href="https://www.myneta.info/LokSabha${r.election_year}/candidate.php?candidate_id=${r.candidate_id}"
           target="_blank" rel="noopener noreferrer">${r.name}</a>` : r.name },
  { key: "party", label: "Party", get: r => `<span class="pill">${r.party}</span>` },
  { key: "criminal_cases", label: "Cases", num: true,
    get: r => r.criminal_cases > 0
      ? `<span class="pill flag">${r.criminal_cases}</span>` : "0" },
  { key: "assets", label: "Assets", num: true, get: r => fmtRupees(r.assets) },
  { key: "liabilities", label: "Liabilities", num: true, get: r => fmtRupees(r.liabilities) },
  { key: "education", label: "Education", get: r => r.education },
  { key: "age", label: "Age", num: true, get: r => r.age ?? "—" },
];

function filteredWinners() {
  let rows = state.winners.filter(w => w.election_year === state.year);
  if (state.stateFilter) rows = rows.filter(w => w.state === state.stateFilter);
  if (state.partyFilter) rows = rows.filter(w => w.party_group === state.partyFilter);
  if (state.search) {
    const q = state.search;
    rows = rows.filter(w =>
      (w.name || "").toLowerCase().includes(q) ||
      (w.party || "").toLowerCase().includes(q) ||
      (w.constituency || "").toLowerCase().includes(q) ||
      (w.state || "").toLowerCase().includes(q));
  }
  return rows;
}

function renderWinnersTable() {
  let rows = filteredWinners();

  const { key, dir } = state.sort;
  rows = [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
  });

  $("tableTitle").textContent = state.stateFilter
    ? `MPs elected in ${state.year} — ${state.stateFilter}`
    : `MPs elected in ${state.year}`;
  const total = state.winners.filter(w => w.election_year === state.year).length;
  $("tableCount").textContent = `Showing ${fmtInt(rows.length)} of ${fmtInt(total)} MPs`;

  $("chipBar").innerHTML = state.partyFilter
    ? `<span class="chip">${state.partyFilter}
         <button aria-label="Remove party filter" id="chipPartyOff">✕</button></span>`
    : "";
  if (state.partyFilter) $("chipPartyOff").onclick = () => setPartyFilter("");

  const head = WINNER_COLS.map(c => {
    const active = c.key === key;
    return `<th class="${c.num ? "num" : ""}" role="button" tabindex="0" data-key="${c.key}"
             aria-sort="${active ? (dir === 1 ? "ascending" : "descending") : "none"}"
            >${c.label}${active ? (dir === 1 ? " ↑" : " ↓") : ""}</th>`;
  }).join("");

  const body = rows.length
    ? rows.map(r => "<tr>" + WINNER_COLS.map(c =>
        `<td class="${c.num ? "num" : ""}">${c.get(r) ?? "—"}</td>`).join("") + "</tr>").join("")
    : `<tr><td colspan="${WINNER_COLS.length}" style="text-align:center; padding:26px;
        color:var(--text-muted)">No MPs match this combination of filters.</td></tr>`;

  $("winnersTable").innerHTML =
    `<div class="table-host"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;

  for (const th of $("winnersTable").querySelectorAll("th[role=button]")) {
    const go = () => {
      const k = th.dataset.key;
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : 1 };
      renderWinnersTable();
    };
    th.onclick = go;
    th.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
  }
}

function downloadFilteredCSV() {
  const rows = filteredWinners();
  const cols = ["election_year", "state", "constituency", "seat_category", "name", "party",
                "criminal_cases", "education", "age", "assets", "liabilities"];
  const esc = (v) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const csv = [cols.join(","), ...rows.map(r => cols.map(c => esc(r[c])).join(","))].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  const scope = [state.year, state.stateFilter, state.partyFilter].filter(Boolean)
    .join("_").replace(/\s+/g, "-").toLowerCase() || "all";
  a.download = `mps_${scope}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------- leaderboard (always national, and says so) ---------- */

function renderLeaderboard() {
  const rows = state.leaderboards
    .filter(r => r.board === state.board && r.election_year === state.year)
    .sort((a, b) => a.rank - b.rank);

  $("lbDesc").textContent =
    "Top 50 candidates in the selected election, across the whole field — not just " +
    "those who won." + (state.stateFilter
      ? " This list is national; the state filter does not narrow it." : "");

  const isRich = state.board === "Richest candidates";
  renderTable($("leaderboardTable"), [
    { label: "#", num: true, get: r => r.rank, sortVal: r => r.rank },
    { label: "Candidate", get: r => r.candidate_id
        ? `<a href="https://www.myneta.info/LokSabha${r.election_year}/candidate.php?candidate_id=${r.candidate_id}"
             target="_blank" rel="noopener noreferrer">${r.name}</a>` : r.name,
      sortVal: r => r.name },
    { label: "Party", get: r => `<span class="pill">${r.party}</span>`, sortVal: r => r.party },
    { label: "Constituency", get: r => `${r.constituency}, ${r.state}`, sortVal: r => r.state },
    { label: isRich ? "Assets" : "Cases", num: true,
      get: r => isRich ? fmtRupees(r.assets) : `<span class="pill flag">${r.criminal_cases}</span>`,
      sortVal: r => isRich ? r.assets : r.criminal_cases },
    { label: isRich ? "Cases" : "Assets", num: true,
      get: r => isRich ? r.criminal_cases : fmtRupees(r.assets),
      sortVal: r => isRich ? r.criminal_cases : r.assets },
    { label: "Won?", get: r => r.is_winner ? "Elected" : "—", sortVal: r => r.is_winner ? 1 : 0 },
  ], rows, { sortable: true, id: "lbTable" });
}

/* ---------- repeat candidates ---------- */

function renderRepeats() {
  let rows = state.repeats;
  if (state.stateFilter) rows = rows.filter(r => r.state === state.stateFilter);
  if (state.repeatSearch) {
    const q = state.repeatSearch;
    rows = rows.filter(r => (r.name || "").toLowerCase().includes(q) ||
                            (r.state || "").toLowerCase().includes(q));
  }
  renderTable($("repeatTable"), [
    { label: "Candidate", get: r => r.name, sortVal: r => r.name },
    { label: "State", get: r => r.state, sortVal: r => r.state },
    { label: "Elections", num: true, get: r => r.elections_contested, sortVal: r => r.elections_contested },
    { label: "Won", num: true, get: r => r.elections_won, sortVal: r => r.elections_won },
    { label: "Span", get: r => `${r.first_year}–${r.last_year}`, sortVal: r => r.first_year },
    { label: "Assets, first", num: true, get: r => fmtRupees(r.min_assets), sortVal: r => r.min_assets },
    { label: "Assets, last", num: true, get: r => fmtRupees(r.max_assets), sortVal: r => r.max_assets },
    { label: "Change", num: true,
      get: r => {
        const g = r.asset_growth_pct;
        if (g === null || g === undefined) return "—";
        return (g >= 0 ? "+" : "") + fmtInt(g) + "%";
      },
      sortVal: r => r.asset_growth_pct },
    { label: "Peak cases", num: true,
      get: r => r.peak_cases > 0 ? `<span class="pill flag">${r.peak_cases}</span>` : "0",
      sortVal: r => r.peak_cases },
  ], rows.slice(0, 200), { sortable: true, id: "repeatTbl" });
}

/* ---------- theme persistence ---------- */

try {
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
} catch (_) {}

boot();
