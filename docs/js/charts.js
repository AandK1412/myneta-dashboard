/* ============================================================
   charts.js - small SVG chart primitives, no dependencies.
   Mark specs follow the house data-viz rules: 2px lines,
   >=8px markers, 4px rounded bar ends anchored to the baseline,
   a 2px surface gap between adjacent fills, solid hairline grid,
   and a hover/focus tooltip on every form.
   ============================================================ */

const SVG_NS = "http://www.w3.org/2000/svg";
const CSS = (name) => getComputedStyle(document.documentElement)
  .getPropertyValue(name).trim();

function el(tag, attrs = {}, parent = null) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  if (parent) parent.appendChild(n);
  return n;
}

/* ---------- formatting ---------- */

export function fmtRupees(v) {
  if (v === null || v === undefined) return "—";
  if (v >= 1e7) return "₹" + (v / 1e7).toFixed(v >= 1e8 ? 0 : 2).replace(/\.00$/, "") + " Cr";
  if (v >= 1e5) return "₹" + (v / 1e5).toFixed(v >= 1e6 ? 0 : 1).replace(/\.0$/, "") + " L";
  if (v >= 1e3) return "₹" + (v / 1e3).toFixed(0) + "K";
  return "₹" + v;
}

export const fmtPct = (v) => (v === null || v === undefined) ? "—" : v.toFixed(1) + "%";
export const fmtInt = (v) => (v === null || v === undefined) ? "—" : v.toLocaleString("en-IN");

/* ---------- shared tooltip ---------- */

let tipEl = null;
function tip() {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "tooltip";
    tipEl.setAttribute("role", "status");
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

function showTip(html, x, y) {
  const t = tip();
  t.innerHTML = html;
  t.classList.add("on");
  const r = t.getBoundingClientRect();
  let left = x + 14, top = y - r.height - 12;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (top < 8) top = y + 18;
  t.style.left = Math.max(8, left) + "px";
  t.style.top = top + "px";
}

export function hideTip() { if (tipEl) tipEl.classList.remove("on"); }
document.addEventListener("scroll", hideTip, true);

const swatch = (c) => `<span class="legend-swatch" style="background:${c}"></span>`;

/* Pick dark or light ink for text sitting on a colored fill. */
function inkFor(hex) {
  const h = (hex || "").replace("#", "");
  if (h.length < 6) return "#0b0b0b";
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16) / 255)
    .map(x => x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4));
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.30 ? "#0b0b0b" : "#fcfcfb";
}

/* ---------- axis helper ---------- */

function niceTicks(min, max, count = 5, { integer = false } = {}) {
  if (min === max) { min = Math.min(0, min); max = max || 1; }
  const span = max - min;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  let step = (norm >= 7.5 ? 10 : norm >= 3.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag;
  // Counts have no fractional values: a "0.4 seats" gridline is nonsense, and
  // it appears whenever a slice is small enough that the max is 1 or 2.
  if (integer) step = Math.max(1, Math.round(step));
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}

/* ============================================================
   Line chart - trend over time, 1-4 series.
   Crosshair + shared tooltip; endpoint direct labels.
   ============================================================ */

export function lineChart(host, { series, xs, yFormat = fmtInt, yLabel = "",
                                  height = 300, keyboardLabel = "chart" }) {
  host.innerHTML = "";
  const W = Math.max(host.clientWidth || 560, 380);
  const H = height;
  const M = { t: 16, r: 74, b: 34, l: 62 };   // right margin holds endpoint labels
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const all = series.flatMap(s => s.points.map(p => p.y)).filter(v => v !== null && v !== undefined);
  if (!all.length) { host.innerHTML = '<p class="desc">No data for this selection.</p>'; return; }

  const ticks = niceTicks(Math.min(...all, 0), Math.max(...all));
  const yMin = ticks[0], yMax = ticks[ticks.length - 1];
  const X = (i) => M.l + (xs.length === 1 ? iw / 2 : (i / (xs.length - 1)) * iw);
  const Y = (v) => M.t + ih - ((v - yMin) / (yMax - yMin || 1)) * ih;

  const svg = el("svg", {
    viewBox: `0 0 ${W} ${H}`, width: W, height: H,
    role: "img", "aria-label": keyboardLabel
  }, host);

  // grid - solid hairlines, one shade off the surface
  for (const t of ticks) {
    el("line", { x1: M.l, x2: M.l + iw, y1: Y(t), y2: Y(t),
      stroke: CSS("--grid"), "stroke-width": 1 }, svg);
    el("text", { x: M.l - 10, y: Y(t) + 4, "text-anchor": "end",
      fill: CSS("--text-muted"), "font-size": 11.5,
      style: "font-variant-numeric:tabular-nums" }, svg)
      .textContent = yFormat(t);
  }
  el("line", { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih,
    stroke: CSS("--axis"), "stroke-width": 1 }, svg);

  xs.forEach((x, i) => {
    el("text", { x: X(i), y: H - 12, "text-anchor": "middle",
      fill: CSS("--text-muted"), "font-size": 11.5 }, svg).textContent = x;
  });

  if (yLabel) {
    el("text", { x: 12, y: M.t + ih / 2, fill: CSS("--text-muted"), "font-size": 11.5,
      "text-anchor": "middle", transform: `rotate(-90 12 ${M.t + ih / 2})` }, svg)
      .textContent = yLabel;
  }

  const crosshair = el("line", { y1: M.t, y2: M.t + ih, stroke: CSS("--axis"),
    "stroke-width": 1, opacity: 0 }, svg);

  // Draw de-emphasised series first so the emphasised one sits on top.
  for (const s of [...series].sort((a, b) => (a.dim === b.dim ? 0 : a.dim ? -1 : 1))) {
    const pts = s.points.map((p, i) => ({ ...p, i })).filter(p => p.y !== null && p.y !== undefined);
    if (!pts.length) continue;
    const op = s.dim ? 0.38 : 1;
    el("path", {
      d: pts.map((p, k) => `${k ? "L" : "M"}${X(p.i)},${Y(p.y)}`).join(" "),
      fill: "none", stroke: s.color, "stroke-width": s.dim ? 1.5 : 2,
      opacity: op, "stroke-linecap": "round", "stroke-linejoin": "round"
    }, svg);
    // 2px surface ring keeps overlapping markers separable
    for (const p of pts) {
      el("circle", { cx: X(p.i), cy: Y(p.y), r: s.dim ? 3.5 : 4.5, fill: s.color,
        opacity: op, stroke: CSS("--surface-1"), "stroke-width": 2 }, svg);
    }
    // selective direct label: endpoint only
    const last = pts[pts.length - 1];
    el("text", { x: X(last.i) + 10, y: Y(last.y) + 4,
      fill: s.dim ? CSS("--text-muted") : CSS("--text-secondary"),
      "font-size": 11.5, "font-weight": s.dim ? 500 : 600 }, svg)
      .textContent = yFormat(last.y);
  }

  // one hit band per x - hit target far wider than the 8px marker
  xs.forEach((x, i) => {
    const bw = iw / Math.max(xs.length - 1, 1);
    const band = el("rect", {
      x: X(i) - bw / 2, y: M.t, width: bw, height: ih,
      fill: "transparent", tabindex: 0, role: "img",
      "aria-label": `${x}: ` + series.map(s => {
        const v = s.points[i]?.y;
        return `${s.name} ${v === null || v === undefined ? "no data" : yFormat(v)}`;
      }).join(", ")
    }, svg);

    const show = (ev) => {
      crosshair.setAttribute("x1", X(i));
      crosshair.setAttribute("x2", X(i));
      crosshair.setAttribute("opacity", 1);
      const rows = series.map(s => {
        const v = s.points[i]?.y;
        return `<div class="tt-row">${swatch(s.color)}<span>${s.name}</span>
                <span class="tt-val">${v === null || v === undefined ? "—" : yFormat(v)}</span></div>`;
      }).join("");
      const r = band.getBoundingClientRect();
      const cx = ev.clientX ?? r.left + r.width / 2;
      const cy = ev.clientY ?? r.top + r.height / 2;
      showTip(`<div class="tt-title">${x}</div>${rows}`, cx, cy);
    };
    const hide = () => { crosshair.setAttribute("opacity", 0); hideTip(); };

    band.addEventListener("mousemove", show);
    band.addEventListener("mouseleave", hide);
    band.addEventListener("focus", show);
    band.addEventListener("blur", hide);
  });
}

/* ============================================================
   Horizontal bar chart - magnitude comparison, one series.
   Long category names read far better on the y-axis.
   ============================================================ */

export function barChart(host, { data, labelKey, valueKey, color, valueFormat = fmtInt,
                                 barHeight = 22, maxBars = 15, note = "",
                                 onClick = null, clickHint = "", integerAxis = false }) {
  host.innerHTML = "";
  const rows = data.slice(0, maxBars);
  if (!rows.length) { host.innerHTML = '<p class="desc">No data for this selection.</p>'; return; }

  const W = Math.max(host.clientWidth || 560, 380);
  // Category labels get a share of the width rather than a fixed slab, so a
  // 375px phone doesn't spend 40% of the chart on the y-axis.
  const labelW = Math.min(152, Math.max(84, W * 0.32));
  const M = { t: 6, r: W < 460 ? 54 : 76, b: 26, l: labelW };
  const maxChars = Math.max(10, Math.floor(labelW / 7));
  const gap = 8;                                   // >=2px surface gap between fills
  const ih = rows.length * (barHeight + gap);
  const H = M.t + ih + M.b;
  const iw = W - M.l - M.r;

  const max = Math.max(...rows.map(r => r[valueKey] ?? 0), 1);
  const ticks = niceTicks(0, max, 4, { integer: integerAxis });
  const scale = (v) => (v / ticks[ticks.length - 1]) * iw;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
    role: "img", "aria-label": note || "bar chart" }, host);

  for (const t of ticks) {
    el("line", { x1: M.l + scale(t), x2: M.l + scale(t), y1: M.t, y2: M.t + ih,
      stroke: CSS("--grid"), "stroke-width": 1 }, svg);
    el("text", { x: M.l + scale(t), y: H - 9, "text-anchor": "middle",
      fill: CSS("--text-muted"), "font-size": 11,
      style: "font-variant-numeric:tabular-nums" }, svg).textContent = valueFormat(t);
  }

  rows.forEach((r, i) => {
    const y = M.t + i * (barHeight + gap);
    const v = r[valueKey] ?? 0;
    const w = Math.max(scale(v), v > 0 ? 2 : 0);

    el("text", { x: M.l - 12, y: y + barHeight / 2 + 4, "text-anchor": "end",
      fill: CSS("--text-secondary"), "font-size": 12.5 }, svg)
      .textContent = String(r[labelKey]).length > maxChars
        ? String(r[labelKey]).slice(0, maxChars - 1) + "…" : r[labelKey];

    // 4px rounded end, anchored to the baseline
    el("rect", { x: M.l, y, width: w, height: barHeight, rx: 4, fill: color }, svg);

    el("text", { x: M.l + w + 9, y: y + barHeight / 2 + 4, fill: CSS("--text-secondary"),
      "font-size": 12, "font-weight": 600,
      style: "font-variant-numeric:tabular-nums" }, svg).textContent = valueFormat(v);

    const hit = el("rect", { x: M.l, y: y - gap / 2, width: iw, height: barHeight + gap,
      fill: "transparent", tabindex: 0,
      role: onClick ? "button" : "img",
      "aria-label": `${r[labelKey]}: ${valueFormat(v)}` +
        (onClick && clickHint ? `. ${clickHint}` : "") }, svg);
    if (onClick) hit.style.cursor = "pointer";
    const show = (ev) => {
      const bb = hit.getBoundingClientRect();
      const hint = onClick && clickHint
        ? `<div class="tt-row" style="opacity:.65"><span>${clickHint}</span></div>` : "";
      showTip(
        `<div class="tt-title">${r[labelKey]}</div>
         <div class="tt-row">${swatch(color)}<span>${note || "Value"}</span>
         <span class="tt-val">${valueFormat(v)}</span></div>${hint}`,
        ev.clientX ?? bb.left + bb.width / 2, ev.clientY ?? bb.top + bb.height / 2);
    };
    hit.addEventListener("mousemove", show);
    hit.addEventListener("mouseleave", hideTip);
    hit.addEventListener("focus", show);
    hit.addEventListener("blur", hideTip);
    if (onClick) {
      hit.addEventListener("click", () => { hideTip(); onClick(r); });
      hit.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); hideTip(); onClick(r); }
      });
    }
  });
}

/* ============================================================
   Stacked bar - part-to-whole across time, ordered categories.
   ============================================================ */

export function stackedBar(host, { xs, stacks, keys, colors, height = 300,
                                   valueFormat = (v) => v.toFixed(1) + "%" }) {
  host.innerHTML = "";
  const W = Math.max(host.clientWidth || 560, 380);
  const H = height;
  const M = { t: 14, r: 16, b: 34, l: 46 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
    role: "img", "aria-label": "stacked distribution" }, host);

  for (const t of [0, 25, 50, 75, 100]) {
    const y = M.t + ih - (t / 100) * ih;
    el("line", { x1: M.l, x2: M.l + iw, y1: y, y2: y,
      stroke: CSS("--grid"), "stroke-width": 1 }, svg);
    el("text", { x: M.l - 9, y: y + 4, "text-anchor": "end", fill: CSS("--text-muted"),
      "font-size": 11.5, style: "font-variant-numeric:tabular-nums" }, svg)
      .textContent = t + "%";
  }

  const slot = iw / xs.length;
  const bw = Math.min(slot * 0.62, 78);
  const GAP = 2;                                   // surface gap between segments

  xs.forEach((x, i) => {
    const cx = M.l + slot * i + slot / 2;
    const col = stacks[i] || {};
    const total = keys.reduce((s, k) => s + (col[k] || 0), 0) || 1;
    let acc = 0;

    keys.forEach((k, ki) => {
      const share = (col[k] || 0) / total * 100;
      if (share <= 0) return;
      const h = (share / 100) * ih;
      const y = M.t + ih - ((acc + share) / 100) * ih;
      acc += share;

      el("rect", { x: cx - bw / 2, y, width: bw, height: Math.max(h - GAP, 1),
        rx: 3, fill: colors[ki] }, svg);

      // label inside only when it comfortably fits; ink flips dark/light
      // with the segment's own luminance so light ramp steps stay readable
      if (h > 26 && bw > 46) {
        el("text", { x: cx, y: y + Math.max(h - GAP, 1) / 2 + 4, "text-anchor": "middle",
          fill: inkFor(colors[ki]), "font-size": 11.5, "font-weight": 700,
          style: "font-variant-numeric:tabular-nums" }, svg)
          .textContent = share.toFixed(0) + "%";
      }
    });

    el("text", { x: cx, y: H - 12, "text-anchor": "middle",
      fill: CSS("--text-muted"), "font-size": 11.5 }, svg).textContent = x;

    const hit = el("rect", { x: M.l + slot * i, y: M.t, width: slot, height: ih,
      fill: "transparent", tabindex: 0, role: "img",
      "aria-label": `${x}: ` + keys.map(k =>
        `${k} ${((col[k] || 0) / total * 100).toFixed(1)}%`).join(", ") }, svg);

    const show = (ev) => {
      const rows = keys.map((k, ki) =>
        `<div class="tt-row">${swatch(colors[ki])}<span>${k}</span>
         <span class="tt-val">${valueFormat((col[k] || 0) / total * 100)}</span></div>`).join("");
      const bb = hit.getBoundingClientRect();
      showTip(`<div class="tt-title">${x}</div>${rows}`,
        ev.clientX ?? bb.left + bb.width / 2, ev.clientY ?? bb.top + 40);
    };
    hit.addEventListener("mousemove", show);
    hit.addEventListener("mouseleave", hideTip);
    hit.addEventListener("focus", show);
    hit.addEventListener("blur", hideTip);
  });
}

/* ---------- legend + table view (every chart has a table twin) ---------- */

export function legend(host, items) {
  host.innerHTML = items.map(i =>
    `<span class="legend-item">${swatch(i.color)}${i.name}</span>`).join("");
}

const _tableSorts = {};   // per-table sort state, keyed by opts.id

export function renderTable(host, cols, rows, opts = {}) {
  const { sortable = false, id = null } = opts;
  const sort = sortable && id ? _tableSorts[id] : null;

  let view = rows;
  if (sort) {
    const col = cols.find(c => c.label === sort.label);
    if (col) {
      const val = col.sortVal || col.get;
      view = [...rows].sort((a, b) => {
        const x = val(a), y = val(b);
        if (x === null || x === undefined || x === "—") return 1;
        if (y === null || y === undefined || y === "—") return -1;
        return (typeof x === "number" && typeof y === "number"
          ? x - y : String(x).localeCompare(String(y))) * sort.dir;
      });
    }
  }

  const head = cols.map(c => {
    if (!sortable) return `<th class="${c.num ? "num" : ""}">${c.label}</th>`;
    const active = sort && sort.label === c.label;
    return `<th class="${c.num ? "num" : ""}" role="button" tabindex="0"
             data-label="${c.label}"
             aria-sort="${active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}"
            >${c.label}${active ? (sort.dir === 1 ? " ↑" : " ↓") : ""}</th>`;
  }).join("");

  const body = view.map(r => "<tr>" + cols.map(c => {
    const v = c.get(r);
    return `<td class="${c.num ? "num" : ""}">${v === null || v === undefined ? "—" : v}</td>`;
  }).join("") + "</tr>").join("");

  host.innerHTML = `<div class="table-host"><table><thead><tr>${head}</tr></thead>
                    <tbody>${body}</tbody></table></div>`;

  if (sortable && id) {
    for (const th of host.querySelectorAll("th[role=button]")) {
      const go = () => {
        const label = th.dataset.label;
        const cur = _tableSorts[id];
        _tableSorts[id] = { label, dir: cur && cur.label === label ? -cur.dir : -1 };
        renderTable(host, cols, rows, opts);
      };
      th.onclick = go;
      th.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
      };
    }
  }
}
