"""Build a self-contained HTML dashboard from simulation output.

    python scripts/report.py data/sim_*.json            # -> docs/usage_report.html
    python scripts/report.py data/sim_a.json -o out.html

No dependencies: charts are drawn client-side as inline SVG from embedded JSON.
Palette: categorical slots 1-4 (validated with the dataviz validator in both
modes). Series are always direct-labeled and each chart has a table view, since
slots 3-4 (aqua, yellow) are below 3:1 on the light surface.
"""
import argparse
import json
from pathlib import Path

# Fixed order => fixed color per run, whatever subset is loaded.
PREFERRED_ORDER = ["decay_on", "decay_off", "recency_heavy", "no_recency"]

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recall Usage Report</title>
<style>
:root {
  color-scheme: light;
  --surface-1: #fcfcfb; --surface-2: #f4f3f0; --border: #dddcd6; --grid: #ebeae5;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #85847e;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-1: #1a1a19; --surface-2: #242423; --border: #3a3a37; --grid: #2c2c2a;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #1a1a19; --surface-2: #242423; --border: #3a3a37; --grid: #2c2c2a;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-1); color: var(--text-primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 40px 0 12px; }
p.sub { color: var(--text-secondary); margin: 0 0 20px; max-width: 70ch; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0 0 8px; }
.legend span { display: inline-flex; align-items: center; gap: 8px; color: var(--text-secondary); font-size: 14px; }
.legend i { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 20px; }
@media (max-width: 520px) { .grid { grid-template-columns: 1fr; } }
.card { background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 14px 14px 10px; position: relative; }
.card h3 { font-size: 14px; margin: 0; }
.card p { font-size: 12.5px; color: var(--text-secondary); margin: 2px 0 6px; }
svg { width: 100%; height: auto; display: block; overflow: visible; }
svg text { font: 11px system-ui, sans-serif; fill: var(--text-muted); }
svg text.lbl { fill: var(--text-secondary); font-size: 11.5px; }
.tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.18); display: none; z-index: 5; white-space: nowrap; }
.tip b { display: block; margin-bottom: 4px; }
.tip div { display: flex; align-items: center; gap: 8px; color: var(--text-secondary); }
.tip i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
details { margin-top: 6px; font-size: 12px; color: var(--text-secondary); }
summary { cursor: pointer; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 5px 8px; border-bottom: 1px solid var(--border); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-secondary); font-weight: 600; }
.wrap { overflow-x: auto; }
.cfg { font-size: 12.5px; color: var(--text-secondary); }
</style>
</head>
<body>
<main>
  <h1>Recall usage report</h1>
  <p class="sub" id="sub"></p>
  <div class="legend" id="legend"></div>

  <h2>Summary (last 15 simulated days)</h2>
  <div class="wrap"><table id="summary"></table></div>

  <h2>Over simulated time</h2>
  <div class="grid" id="charts"></div>

  <h2>Configuration</h2>
  <div class="wrap"><table id="cfg"></table></div>
</main>
<script>
const DATA = __DATA__;
const CHARTS = [
  {key: "memories_per_user", title: "Memory growth per user", sub: "Rows stored per user. Decay off = unbounded growth.", fmt: v => v.toFixed(0), zero: true},
  {key: "hit_at_5", title: "Hit rate @5", sub: "Share of eval queries with a memory asserting the CURRENT fact in the top 5.", fmt: v => (v*100).toFixed(1)+"%", pct: true},
  {key: "top1_current", title: "Current fact ranked #1", sub: "Share of eval queries where the current fact is the top result.", fmt: v => (v*100).toFixed(1)+"%", pct: true},
  {key: "stale_outranks", title: "Stale value outranks current", sub: "Share of queries where a superseded fact ranks above the current one (or the current one is missing). Lower is better.", fmt: v => (v*100).toFixed(1)+"%", pct: true, zero: true, max: 0.4},
  {key: "search_p95_ms", title: "Search latency p95 (ms)", sub: "In-process, includes embedding + vector scan + access update. Cache off.", fmt: v => v.toFixed(0)+" ms", zero: true},
  {key: "avg_importance", title: "Average importance score", sub: "Access bumps push it up; decay pulls stale memories down.", fmt: v => v.toFixed(3)},
];
const SERIES_VAR = i => `var(--series-${i+1})`;
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };

function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v || 1))); const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

function domain(chart) {
  let lo = Infinity, hi = -Infinity;
  for (const r of DATA) for (const d of r.days) { const v = d[chart.key]; if (v == null) continue; lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (chart.pct) return [0, chart.max || 1];
  if (chart.zero) return [0, niceMax(hi * 1.05)];
  const pad = (hi - lo) * 0.1 || 0.01; return [lo - pad, hi + pad];
}

function drawChart(chart, host) {
  const W = 560, H = 230, m = {l: 44, r: 118, t: 10, b: 26};
  const card = document.createElement("div"); card.className = "card";
  card.innerHTML = `<h3>${chart.title}</h3><p>${chart.sub}</p>`;
  const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": chart.title});
  card.appendChild(svg); host.appendChild(card);
  const [lo, hi] = domain(chart);
  const nDays = Math.max(...DATA.map(r => r.days.length));
  const x = d => m.l + (d - 1) / Math.max(1, nDays - 1) * (W - m.l - m.r);
  const y = v => H - m.b - (v - lo) / (hi - lo) * (H - m.t - m.b);
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + (hi - lo) * i / ticks, yy = y(v);
    el("line", {x1: m.l, x2: W - m.r, y1: yy, y2: yy, stroke: "var(--grid)", "stroke-width": 1}, svg);
    const t = el("text", {x: m.l - 6, y: yy + 4, "text-anchor": "end"}, svg);
    t.textContent = chart.pct ? Math.round(v * 100) + "%" : (hi > 20 ? v.toFixed(0) : v.toFixed(2).replace(/\.?0+$/, "") || "0");
  }
  for (const d of [1, Math.round(nDays / 2), nDays]) {
    const t = el("text", {x: x(d), y: H - 8, "text-anchor": "middle"}, svg); t.textContent = "day " + d;
  }
  const ends = [];
  DATA.forEach((run, i) => {
    const pts = run.days.filter(d => d[chart.key] != null);
    if (!pts.length) return;
    el("path", {d: pts.map((d, j) => (j ? "L" : "M") + x(d.day) + " " + y(d[chart.key])).join(""),
      fill: "none", stroke: SERIES_VAR(i), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}, svg);
    const last = pts[pts.length - 1];
    el("circle", {cx: x(last.day), cy: y(last[chart.key]), r: 4, fill: SERIES_VAR(i), stroke: "var(--surface-2)", "stroke-width": 2}, svg);
    ends.push({i, run, y: y(last[chart.key])});
  });
  // Direct labels at line ends, pushed apart so they never overlap.
  ends.sort((a, b) => a.y - b.y);
  for (let k = 1; k < ends.length; k++) ends[k].y = Math.max(ends[k].y, ends[k - 1].y + 14);
  // Keep the stack inside the plot: if it overflowed the bottom, pull it back up.
  const floor = H - m.b - 2;
  if (ends.length && ends[ends.length - 1].y > floor) {
    ends[ends.length - 1].y = floor;
    for (let k = ends.length - 2; k >= 0; k--) ends[k].y = Math.min(ends[k].y, ends[k + 1].y - 14);
  }
  for (const e of ends) { const t = el("text", {x: W - m.r + 10, y: e.y + 4, class: "lbl"}, svg); t.textContent = e.run.label; }

  // Hover: crosshair + tooltip; hit area is the whole plot, not the marks.
  const cross = el("line", {y1: m.t, y2: H - m.b, stroke: "var(--text-muted)", "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden"}, svg);
  const dots = DATA.map((_, i) => el("circle", {r: 4.5, fill: SERIES_VAR(i), stroke: "var(--surface-2)", "stroke-width": 2, visibility: "hidden"}, svg));
  const tip = document.createElement("div"); tip.className = "tip"; card.appendChild(tip);
  const hit = el("rect", {x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent"}, svg);
  hit.addEventListener("pointermove", ev => {
    const box = svg.getBoundingClientRect(), sx = W / box.width;
    const px = (ev.clientX - box.left) * sx;
    const day = Math.max(1, Math.min(nDays, Math.round(1 + (px - m.l) / (W - m.l - m.r) * (nDays - 1))));
    cross.setAttribute("x1", x(day)); cross.setAttribute("x2", x(day)); cross.setAttribute("visibility", "visible");
    let html = `<b>Day ${day}</b>`;
    DATA.forEach((run, i) => { const d = run.days[day - 1]; if (!d || d[chart.key] == null) { dots[i].setAttribute("visibility", "hidden"); return; }
      dots[i].setAttribute("cx", x(day)); dots[i].setAttribute("cy", y(d[chart.key])); dots[i].setAttribute("visibility", "visible");
      html += `<div><i style="background:${SERIES_VAR(i)}"></i>${run.label}: ${chart.fmt(d[chart.key])}</div>`; });
    tip.innerHTML = html; tip.style.display = "block";
    const cb = card.getBoundingClientRect();
    const left = ev.clientX - cb.left + 14;
    tip.style.left = Math.min(left, cb.width - tip.offsetWidth - 8) + "px";
    tip.style.top = (ev.clientY - cb.top - 10) + "px";
  });
  hit.addEventListener("pointerleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); });

  // Table view (also the accessible / low-contrast fallback).
  const det = document.createElement("details");
  const rows = []; for (let d = 1; d <= nDays; d++) if (d === 1 || d % 5 === 0 || d === nDays) rows.push(d);
  det.innerHTML = `<summary>Table view</summary><div class="wrap"><table><tr><th>Day</th>${DATA.map(r => `<th>${r.label}</th>`).join("")}</tr>` +
    rows.map(d => `<tr><td>${d}</td>${DATA.map(r => { const v = r.days[d - 1] && r.days[d - 1][chart.key]; return `<td>${v == null ? "–" : chart.fmt(v)}</td>`; }).join("")}</tr>`).join("") + `</table></div>`;
  card.appendChild(det);
}

function avg(run, key, n = 15) { const xs = run.days.slice(-n).map(d => d[key]).filter(v => v != null); return xs.reduce((a, b) => a + b, 0) / xs.length; }
const sum = (run, key) => run.days.reduce((a, d) => a + d[key], 0);
const pctf = v => (v * 100).toFixed(1) + "%";

document.getElementById("legend").innerHTML = DATA.map((r, i) => `<span><i style="background:${SERIES_VAR(i)}"></i>${r.label}</span>`).join("");
const c0 = DATA[0].config;
document.getElementById("sub").textContent =
  `${c0.users} simulated users over ${c0.days} days, ${c0.queries_per_day} eval queries per user per day, top-${c0.top_k}. ` +
  `Facts are restated (near-duplicates) and occasionally change; chatter carries a ${c0.noise_ttl_days}-day TTL. Same random seed in every run.`;

const summaryRows = [
  ["Memories per user (final)", r => r.days[r.days.length - 1].memories_per_user.toFixed(1)],
  ["Hit rate @5", r => pctf(avg(r, "hit_at_5"))],
  ["Current fact ranked #1", r => pctf(avg(r, "top1_current"))],
  ["MRR", r => avg(r, "mrr").toFixed(3)],
  ["Stale outranks current", r => pctf(avg(r, "stale_outranks"))],
  ["Search p50 / p95 (ms, whole run)", r => r.overall_latency_ms.search_p50.toFixed(0) + " / " + r.overall_latency_ms.search_p95.toFixed(0)],
  ["Write p50 / p95 (ms, whole run)", r => r.overall_latency_ms.write_p50.toFixed(0) + " / " + r.overall_latency_ms.write_p95.toFixed(0)],
  ["Expired by job (total)", r => sum(r, "expired")],
  ["Duplicates merged away (total)", r => sum(r, "merged_away")],
  ["Importance decayed (row-updates, total)", r => sum(r, "decayed")],
  ["Decay job time per simulated day (s)", r => r.config.decay === "on" ? avg(r, "job_seconds", 45).toFixed(2) : "–"],
];
document.getElementById("summary").innerHTML = `<tr><th>Metric</th>${DATA.map(r => `<th>${r.label}</th>`).join("")}</tr>` +
  summaryRows.map(([n, f]) => `<tr><td>${n}</td>${DATA.map(r => `<td>${f(r)}</td>`).join("")}</tr>`).join("");
const cfgKeys = [["decay job", r => r.config.decay], ["weights (sim / recency / importance)", r => r.config.weights.join(" / ")],
  ["recency half-life (days)", r => r.config.recency_half_life_days], ["decay factor / stale after (days)", r => r.config.decay_factor + " / " + r.config.stale_after_days],
  ["duplicate similarity", r => r.config.duplicate_similarity], ["seed", r => r.config.seed]];
document.getElementById("cfg").innerHTML = `<tr><th>Setting</th>${DATA.map(r => `<th>${r.label}</th>`).join("")}</tr>` +
  cfgKeys.map(([n, f]) => `<tr><td>${n}</td>${DATA.map(r => `<td>${f(r)}</td>`).join("")}</tr>`).join("");
const host = document.getElementById("charts");
CHARTS.forEach(c => drawChart(c, host));
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default="docs/usage_report.html")
    args = ap.parse_args()
    runs = [json.loads(Path(f).read_text()) for f in args.files]
    order = {n: i for i, n in enumerate(PREFERRED_ORDER)}
    runs.sort(key=lambda r: order.get(r["label"], len(order)))
    Path(args.out).write_text(TEMPLATE.replace("__DATA__", json.dumps(runs)))
    print(f"wrote {args.out} ({len(runs)} runs)")


if __name__ == "__main__":
    main()
