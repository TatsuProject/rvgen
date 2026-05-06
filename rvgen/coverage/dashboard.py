"""Self-contained interactive coverage dashboard — SOTA layout.

Generates one HTML file with vanilla SVG charts + JS — no external CDN,
no plotly, no chart.js. Works offline. Single drop into a CI artifact
upload step or `python -m http.server`.

Design references
-----------------

* Cadence IMC: hierarchical Groups page with score / goal / weight columns.
* Synopsys Verdi vCoverage: per-cover-point status + drill-down.
* Mentor Questa Visualizer: pie chart of covered/missed; filter by status.
* Istanbul/nyc: per-row colored bars; sortable file tree.
* coverage.py: green/yellow/red highlight; print-friendly stylesheet.

Sections
--------

The dashboard ships as a tabbed single-page app:

1. **Summary** — pie chart of bin status; KPI tiles; per-subsystem bar
   chart; top-25 missing.
2. **Covergroups** — sortable / filterable hierarchical table. Each
   covergroup expands to a per-bin table with red/yellow/green bars,
   hits, goals, percentage, status badge.
3. **Cross-coverage** — heatmap matrix for ``*_cross_cg`` covergroups.
4. **Misses** — flat ranked list of all unmet bins with required
   counts and "why" tooltips when ``cov-explain`` matchers apply.
5. **Convergence** — line chart of new-bins-per-seed (when timeline JSON
   is provided).

Public API
----------

  dashboard_html(db, goals=None, timeline=None, scorecard=None,
                 title='...', explanations=None) -> str
  write_dashboard(db, output_path, ...) -> Path
"""

from __future__ import annotations

import html as _html
import json
import math
import re
from pathlib import Path

from rvgen.coverage.cgf import Goals, missing_bins
from rvgen.coverage.collectors import CoverageDB


# ---------------------------------------------------------------------------
# CSS — themed, print-friendly, light/dark via [data-theme] attribute.
# ---------------------------------------------------------------------------

_CSS = """
:root, [data-theme="dark"] {
  --bg: #0d1117;
  --bg-sec: #161b22;
  --bg-card: #1f242c;
  --bg-hover: #22272e;
  --fg: #e6edf3;
  --fg-mute: #8b949e;
  --fg-faint: #484f58;
  --accent: #58a6ff;
  --good: #3fb950;
  --good-fade: #1f4128;
  --warn: #d29922;
  --warn-fade: #4d3a13;
  --bad: #f85149;
  --bad-fade: #4a2127;
  --info: #58a6ff;
  --grid: #30363d;
  --grid-soft: #21262d;
}
[data-theme="light"] {
  --bg: #ffffff;
  --bg-sec: #f6f8fa;
  --bg-card: #ffffff;
  --bg-hover: #f0f3f6;
  --fg: #1f2328;
  --fg-mute: #59636e;
  --fg-faint: #818b98;
  --accent: #0969da;
  --good: #1a7f37;
  --good-fade: #d2f4de;
  --warn: #9a6700;
  --warn-fade: #fff8c5;
  --bad: #cf222e;
  --bad-fade: #ffebe9;
  --info: #0969da;
  --grid: #d1d9e0;
  --grid-soft: #eaeef2;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter,
               Roboto, ui-sans-serif, sans-serif;
  font-size: 13px;
  line-height: 1.5;
}
code, .mono {
  font-family: 'SF Mono', Monaco, Menlo, ui-monospace, Consolas, monospace;
  font-size: 12px;
}

/* ---------- Top bar ---------- */
.topbar {
  position: sticky; top: 0; z-index: 100;
  background: var(--bg-sec);
  border-bottom: 1px solid var(--grid);
  padding: 12px 24px;
  display: flex; align-items: center; gap: 16px;
}
.topbar h1 {
  margin: 0; font-size: 16px; font-weight: 600;
  display: flex; align-items: center; gap: 8px;
}
.topbar h1 .logo {
  display: inline-block; width: 22px; height: 22px;
  background: linear-gradient(135deg, var(--accent), var(--good));
  border-radius: 4px;
  text-align: center; line-height: 22px; color: #fff;
  font-size: 12px; font-weight: 700;
}
.topbar .meta { color: var(--fg-mute); font-size: 11px; }
.topbar .spacer { flex: 1; }
.topbar .btn {
  background: transparent; color: var(--fg);
  border: 1px solid var(--grid); border-radius: 6px;
  padding: 5px 10px; font-size: 12px; cursor: pointer;
}
.topbar .btn:hover { background: var(--bg-hover); }

/* ---------- Tab strip ---------- */
.tabs {
  position: sticky; top: 49px; z-index: 99;
  display: flex; gap: 4px;
  padding: 0 24px;
  background: var(--bg);
  border-bottom: 1px solid var(--grid);
}
.tab {
  padding: 10px 14px;
  font-size: 13px;
  border-bottom: 2px solid transparent;
  color: var(--fg-mute); cursor: pointer;
  transition: color 0.1s, border-color 0.1s;
  user-select: none;
}
.tab:hover { color: var(--fg); }
.tab.active { color: var(--fg); border-bottom-color: var(--accent); }
.tab .badge {
  display: inline-block; margin-left: 6px;
  background: var(--bg-card); color: var(--fg-mute);
  border-radius: 8px; padding: 1px 6px; font-size: 11px;
}
.tab.active .badge { background: var(--accent); color: #fff; }

/* ---------- Panels ---------- */
.panel { display: none; padding: 20px 24px 64px; }
.panel.active { display: block; }
section { margin: 0 0 28px; }
section > h2 {
  margin: 0 0 12px;
  font-size: 13px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.6px;
  color: var(--fg-mute);
}

/* ---------- KPI tiles ---------- */
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.tile {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 10px;
  padding: 14px 16px 16px;
  position: relative;
  overflow: hidden;
  transition: border-color 0.15s, transform 0.15s;
}
.tile::before {
  content: ''; position: absolute;
  left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--accent);
  opacity: 0.7;
}
.tile.good::before { background: var(--good); }
.tile.warn::before { background: var(--warn); }
.tile.bad::before  { background: var(--bad); }
.tile:hover { border-color: var(--accent); }
.tile .label { color: var(--fg-mute); font-size: 10px;
               text-transform: uppercase; letter-spacing: 0.7px;
               font-weight: 600; }
.tile .value { font-size: 26px; font-weight: 700;
               margin-top: 4px; line-height: 1.1;
               font-variant-numeric: tabular-nums; letter-spacing: -0.2px; }
.tile .sub { color: var(--fg-mute); font-size: 11px; margin-top: 4px; }
.tile.good .value { color: var(--good); }
.tile.warn .value { color: var(--warn); }
.tile.bad  .value { color: var(--bad); }

/* ---------- Charts row ---------- */
.charts-row {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 2fr;
  gap: 16px;
  align-items: stretch;
}
.chart-card {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 8px;
  padding: 16px;
}
.chart-card h3 {
  margin: 0 0 10px;
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.6px;
  color: var(--fg-mute);
}

/* ---------- Pie chart ---------- */
.pie-wrap { display: flex; align-items: center; gap: 16px; }
.pie-legend { display: flex; flex-direction: column; gap: 8px;
              font-size: 12px; }
.pie-legend .row { display: flex; align-items: center; gap: 8px; }
.pie-legend .swatch {
  display: inline-block; width: 12px; height: 12px; border-radius: 2px;
}
.pie-legend .pct { color: var(--fg-mute); margin-left: auto; }

/* ---------- Toolbar (filter, search, sort) ---------- */
.toolbar {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-bottom: 14px; align-items: center;
}
.toolbar input.search {
  background: var(--bg-card); color: var(--fg);
  border: 1px solid var(--grid); border-radius: 6px;
  padding: 6px 10px; font-size: 12px;
  min-width: 240px; flex: 1; max-width: 400px;
}
.toolbar input.search::placeholder { color: var(--fg-faint); }
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border-radius: 14px;
  font-size: 12px; cursor: pointer; user-select: none;
  background: var(--bg-card); color: var(--fg-mute);
  border: 1px solid var(--grid);
}
.chip:hover { color: var(--fg); }
.chip.active { color: var(--fg); background: var(--bg-hover);
               border-color: var(--accent); }
.chip .dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
}
.chip.met .dot, .chip.hit .dot { background: var(--good); }
.chip.partial .dot                { background: var(--warn); }
.chip.missed .dot                 { background: var(--bad); }
.chip.untracked .dot              { background: var(--fg-faint); }

.toolbar button {
  background: var(--bg-card); color: var(--fg);
  border: 1px solid var(--grid); border-radius: 6px;
  padding: 5px 10px; font-size: 12px; cursor: pointer;
}
.toolbar button:hover { background: var(--bg-hover); }

/* ---------- Tables ---------- */
table {
  width: 100%; border-collapse: collapse;
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 8px; overflow: hidden;
  font-size: 12px;
}
thead th {
  background: var(--bg-sec); color: var(--fg-mute);
  text-transform: uppercase; letter-spacing: 0.5px;
  font-size: 11px; font-weight: 600;
  text-align: left; padding: 8px 10px;
  border-bottom: 1px solid var(--grid);
  position: sticky; top: 0; z-index: 1;
  cursor: pointer;
  user-select: none;
}
thead th.sortable::after {
  content: ' \\2195'; color: var(--fg-faint); font-size: 10px;
}
thead th.sort-asc::after  { content: ' \\2191'; color: var(--accent); }
thead th.sort-desc::after { content: ' \\2193'; color: var(--accent); }
tbody td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--grid-soft);
  vertical-align: middle;
}
tbody tr:hover td { background: var(--bg-hover); }
tbody tr:last-child td { border-bottom: none; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.bin-name { font-family: 'SF Mono', Monaco, Menlo, ui-monospace, Consolas, monospace; font-size: 12px; }

/* ---------- Per-bin progress bar ---------- */
.bar {
  display: inline-block;
  position: relative;
  width: 130px; height: 14px;
  background: var(--grid-soft);
  border-radius: 4px; overflow: hidden;
  vertical-align: middle;
}
.bar .fill {
  position: absolute; left: 0; top: 0; bottom: 0;
  background: var(--good);
  transition: width 0.3s;
}
.bar.partial .fill { background: var(--warn); }
.bar.missed  .fill { background: var(--bad); }
.bar .label {
  position: absolute; left: 0; right: 0; top: 0; bottom: 0;
  text-align: center; line-height: 14px; font-size: 10px;
  color: var(--fg); font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 2px rgba(0,0,0,0.4);
}
[data-theme="light"] .bar .label { text-shadow: 0 0 2px rgba(255,255,255,0.6); }

/* ---------- Status badges ---------- */
.badge {
  display: inline-block;
  padding: 2px 8px; border-radius: 10px;
  font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.4px;
  font-variant-numeric: tabular-nums;
}
.badge.met,    .badge.hit  { background: var(--good-fade); color: var(--good); }
.badge.partial             { background: var(--warn-fade); color: var(--warn); }
.badge.missed              { background: var(--bad-fade);  color: var(--bad); }
.badge.untracked           { background: var(--bg-hover);  color: var(--fg-mute); }

/* ---------- Covergroup expandables ---------- */
details.cg {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 8px;
  margin-bottom: 6px;
  overflow: hidden;
}
details.cg[hidden] { display: none; }
details.cg > summary {
  list-style: none;
  cursor: pointer;
  padding: 10px 14px;
  display: grid;
  grid-template-columns: minmax(220px, 2fr) minmax(150px, 1fr) 140px 90px;
  align-items: center; gap: 12px;
}
details.cg > summary::-webkit-details-marker { display: none; }
details.cg > summary:hover { background: var(--bg-hover); }
details.cg .cg-name {
  font-weight: 600; font-size: 13px; color: var(--fg);
  display: flex; align-items: center; gap: 8px;
}
details.cg .cg-name::before {
  content: '▶'; font-size: 8px; color: var(--fg-mute);
  display: inline-block;
  transition: transform 0.15s;
}
details.cg[open] .cg-name::before { transform: rotate(90deg); }
details.cg .cg-meta { color: var(--fg-mute); font-size: 12px; }
details.cg .pct-text { font-variant-numeric: tabular-nums;
                       text-align: right; }
details.cg .body { border-top: 1px solid var(--grid); }
details.cg .body table { border: none; border-radius: 0; }
details.cg .body table thead th { background: var(--bg-card); }

/* ---------- Sidebar nav ---------- */
.layout {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 20px;
  align-items: start;
}
.sidebar {
  position: sticky; top: 96px;
  max-height: calc(100vh - 110px);
  overflow-y: auto;
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 8px;
  padding: 8px;
  font-size: 12px;
}
.sidebar h4 {
  margin: 6px 6px 4px; font-size: 10px;
  color: var(--fg-mute);
  text-transform: uppercase; letter-spacing: 0.5px;
}
.sidebar a {
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 8px; border-radius: 4px;
  color: var(--fg-mute); text-decoration: none; font-size: 11px;
}
.sidebar a:hover { background: var(--bg-hover); color: var(--fg); }
.sidebar a .pct { font-variant-numeric: tabular-nums;
                  font-size: 10px; }

/* ---------- Cross heatmap ---------- */
.heatmap {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 12px;
}
.heatmap h3 { margin: 0 0 10px; font-size: 13px; font-weight: 600; }
.heatmap .meta { color: var(--fg-mute); font-size: 11px; margin-bottom: 8px; }
.heatmap-grid {
  display: grid; gap: 1px;
  background: var(--grid-soft);
  padding: 1px;
  border-radius: 4px;
  overflow: auto;
}
.heatmap-cell {
  background: var(--bg-card);
  font-size: 9px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  min-width: 24px; min-height: 22px;
  position: relative;
}
.heatmap-cell.hit { color: #fff; }
.heatmap-cell.zero { color: var(--fg-faint); }
.heatmap-cell.label {
  background: var(--bg-sec); color: var(--fg-mute);
  font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.4px;
}

/* ---------- Sunburst hero ---------- */
.hero {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 360px);
  gap: 18px;
  align-items: stretch;
  margin-bottom: 18px;
}
@media (max-width: 1080px) {
  .hero { grid-template-columns: 1fr; }
}
.hero .sunburst-card {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 10px;
  padding: 18px 18px 12px;
  position: relative;
  min-height: 600px;
  display: flex; flex-direction: column;
}
.hero .sunburst-card .header {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 6px;
}
.hero .sunburst-card h3 {
  margin: 0; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.6px;
  color: var(--fg-mute); font-weight: 600;
}
.hero .sunburst-card .legend {
  display: flex; gap: 12px; font-size: 10px;
  color: var(--fg-mute);
}
.hero .sunburst-card .legend .row {
  display: flex; align-items: center; gap: 4px;
}
.hero .sunburst-card .legend .swatch {
  display: inline-block; width: 9px; height: 9px; border-radius: 2px;
}
svg.sunburst {
  width: 100%; height: auto; display: block;
  max-width: 760px;
  margin: 0 auto;
  flex: 1;
}
svg.sunburst .sb-arc {
  cursor: pointer;
  transition: opacity 0.15s, transform 0.15s;
  transform-origin: center;
}
svg.sunburst .sb-arc:hover { opacity: 0.78; }
svg.sunburst .sb-arc.dimmed { opacity: 0.18; }
svg.sunburst .sb-arc.highlighted { opacity: 1; filter: brightness(1.1); }
svg.sunburst .sb-label {
  font-size: 13px; font-weight: 700; fill: #fff;
  paint-order: stroke fill;
  stroke: rgba(0,0,0,0.85);
  stroke-width: 4px;
  stroke-linejoin: round;
  letter-spacing: 0.3px;
  pointer-events: none;
}
[data-theme="light"] svg.sunburst .sb-label {
  fill: #fff;
  stroke: rgba(0,0,0,0.7);
}
[data-theme="light"] svg.sunburst .sb-label {
  fill: #fff;
  text-shadow: 0 0 2px rgba(0,0,0,0.5);
}
svg.sunburst .sb-center-num {
  font-size: 56px; font-weight: 700; fill: var(--fg);
  font-variant-numeric: tabular-nums; letter-spacing: -1px;
}
svg.sunburst .sb-center-sub {
  font-size: 13px; fill: var(--fg-mute);
  text-transform: uppercase; letter-spacing: 0.9px; font-weight: 600;
}
svg.sunburst .sb-center-tiny {
  font-size: 13px; fill: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}

/* ---------- Detail panel (sunburst select) ---------- */
.detail-card {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 10px;
  padding: 16px;
  display: flex; flex-direction: column;
  min-height: 360px;
}
.detail-card .lead { color: var(--fg-mute); font-size: 11px;
                     text-transform: uppercase; letter-spacing: 0.6px; }
.detail-card h3.target {
  margin: 4px 0 0;
  font-size: 22px; font-weight: 600; color: var(--fg);
  word-break: break-all; line-height: 1.2;
}
.detail-card .nums {
  display: flex; gap: 24px; margin-top: 14px;
  padding: 12px 0; border-top: 1px solid var(--grid-soft);
  border-bottom: 1px solid var(--grid-soft);
}
.detail-card .nums .num {
  font-size: 22px; font-weight: 600; line-height: 1;
  font-variant-numeric: tabular-nums;
}
.detail-card .nums .num-label {
  font-size: 10px; color: var(--fg-mute);
  text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px;
}
.detail-card ul.misses {
  list-style: none; padding: 0; margin: 14px 0 0;
  font-family: 'SF Mono', Monaco, Menlo, ui-monospace, Consolas, monospace;
  font-size: 11px;
  display: flex; flex-direction: column; gap: 4px;
  max-height: 200px; overflow-y: auto;
}
.detail-card ul.misses li {
  display: flex; justify-content: space-between;
  padding: 4px 6px; border-radius: 4px;
}
.detail-card ul.misses li:hover { background: var(--bg-hover); }
.detail-card ul.misses li .req {
  color: var(--fg-faint); font-variant-numeric: tabular-nums;
}
.detail-card .hint {
  margin-top: auto; padding-top: 12px;
  color: var(--fg-faint); font-size: 11px;
  border-top: 1px solid var(--grid-soft);
}

/* ---------- SVG charts ---------- */
svg.scorecard-svg, svg.timeline-svg, svg.pie-svg {
  width: 100%; height: auto; display: block;
}
.scorecard-svg .row-bg { fill: var(--grid-soft); }
.scorecard-svg .row-fg.good   { fill: var(--good); }
.scorecard-svg .row-fg.warn   { fill: var(--warn); }
.scorecard-svg .row-fg.bad    { fill: var(--bad); }
.scorecard-svg .row-label,
.scorecard-svg .row-value     { fill: var(--fg); font-size: 11px; }
.timeline-svg .axis           { stroke: var(--grid); stroke-width: 1; }
.timeline-svg .axis-text      { fill: var(--fg-mute); font-size: 10px; }
.timeline-svg .line           { stroke: var(--accent); stroke-width: 2;
                                fill: none; }
.timeline-svg .point          { fill: var(--accent); cursor: pointer; }
.pie-svg .slice-good { fill: var(--good); }
.pie-svg .slice-warn { fill: var(--warn); }
.pie-svg .slice-bad  { fill: var(--bad); }
.pie-svg .slice-na   { fill: var(--fg-faint); }
.pie-svg .ring       { fill: none; stroke: var(--bg-card); stroke-width: 2; }

/* ---------- Misses table ---------- */
.miss-row .why {
  font-size: 11px; color: var(--fg-mute);
  font-style: italic; margin-top: 2px;
}

/* ---------- Print styles ---------- */
@media print {
  body { background: #fff; color: #000; font-size: 10pt; }
  .topbar, .tabs, .toolbar, .sidebar { display: none !important; }
  .panel { display: block !important; padding: 0; page-break-inside: avoid; }
  details.cg { page-break-inside: avoid; }
  details.cg > summary > * { color: #000 !important; }
  table, thead th { background: #fff !important; color: #000 !important; }
  .bar { background: #eee; }
  .bar .fill { background: #888 !important; }
}

/* ---------- Misc ---------- */
.empty {
  text-align: center; padding: 32px;
  color: var(--fg-mute); font-style: italic;
}
.kbd {
  font-family: 'SF Mono', Monaco, Menlo, ui-monospace, Consolas, monospace;
  font-size: 10px;
  background: var(--bg-sec); border: 1px solid var(--grid);
  border-bottom-width: 2px;
  border-radius: 3px; padding: 1px 5px;
  color: var(--fg-mute);
}
"""


# ---------------------------------------------------------------------------
# JavaScript — sort, filter, tab, theme toggle. Vanilla, no deps.
# ---------------------------------------------------------------------------

_JS = r"""
(() => {
  // ---------- Theme toggle ----------
  const root = document.documentElement;
  const stored = localStorage.getItem('rvgenTheme');
  if (stored) root.setAttribute('data-theme', stored);
  document.getElementById('theme-toggle').addEventListener('click', () => {
    const cur = root.getAttribute('data-theme') || 'dark';
    const next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('rvgenTheme', next);
  });

  // ---------- Tab switching ----------
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.panel');
  function show(id) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === id));
    panels.forEach(p => p.classList.toggle('active', p.dataset.panel === id));
    location.hash = '#' + id;
  }
  tabs.forEach(t => t.addEventListener('click', () => show(t.dataset.tab)));
  // Initial tab from URL hash, default 'summary'.
  const initial = (location.hash || '#summary').replace('#', '');
  show(document.querySelector('.tab[data-tab="' + initial + '"]') ? initial : 'summary');

  // ---------- Search + status filter ----------
  const search = document.getElementById('cg-search');
  const chips = document.querySelectorAll('.chip[data-status]');
  let activeStatus = 'all';
  chips.forEach(c => c.addEventListener('click', () => {
    chips.forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    activeStatus = c.dataset.status;
    apply();
  }));

  function apply() {
    const q = (search?.value || '').toLowerCase();
    document.querySelectorAll('details.cg').forEach(el => {
      const name = (el.dataset.name || '').toLowerCase();
      const status = el.dataset.status || 'untracked';
      const matchSearch = !q || name.includes(q);
      const matchStatus = activeStatus === 'all' || status === activeStatus;
      el.hidden = !(matchSearch && matchStatus);
    });
  }
  search?.addEventListener('input', apply);

  // ---------- Expand / collapse all ----------
  document.getElementById('expand-all')?.addEventListener('click',
    () => document.querySelectorAll('details.cg').forEach(d => { if (!d.hidden) d.open = true; }));
  document.getElementById('collapse-all')?.addEventListener('click',
    () => document.querySelectorAll('details.cg').forEach(d => d.open = false));

  // ---------- Sortable column headers ----------
  document.querySelectorAll('table.sortable').forEach(table => {
    const ths = table.querySelectorAll('thead th');
    ths.forEach((th, idx) => {
      th.classList.add('sortable');
      th.addEventListener('click', () => {
        const dir = th.classList.contains('sort-asc') ? 'desc' : 'asc';
        ths.forEach(x => x.classList.remove('sort-asc', 'sort-desc'));
        th.classList.add('sort-' + dir);
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort((a, b) => {
          const av = a.children[idx]?.dataset?.sort ?? a.children[idx]?.innerText ?? '';
          const bv = b.children[idx]?.dataset?.sort ?? b.children[idx]?.innerText ?? '';
          const an = parseFloat(av);
          const bn = parseFloat(bv);
          if (!isNaN(an) && !isNaN(bn)) {
            return dir === 'asc' ? an - bn : bn - an;
          }
          return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        rows.forEach(r => tbody.appendChild(r));
      });
    });
  });

  // ---------- Sunburst interaction ----------
  const arcs = document.querySelectorAll('svg.sunburst .sb-arc');
  const detailLead   = document.getElementById('detail-lead');
  const detailTitle  = document.getElementById('detail-title');
  const detailNums   = document.getElementById('detail-nums');
  const detailMisses = document.getElementById('detail-misses');

  function setDetail(data) {
    if (!detailTitle) return;
    detailLead.textContent  = data.lead || 'Coverage focus';
    detailTitle.textContent = data.title || '—';
    detailNums.innerHTML = (data.metrics || []).map(m => (
      `<div><div class="num" style="color:${m.color || 'var(--fg)'}">${m.value}</div>
       <div class="num-label">${m.label}</div></div>`
    )).join('');
    detailMisses.innerHTML = (data.misses && data.misses.length)
      ? data.misses.map(m => (
          `<li><span>${m.bin}</span><span class="req">req ${m.req}</span></li>`
        )).join('')
      : '<li style="color:var(--fg-faint)">All bins met (or no goals)</li>';
  }

  arcs.forEach(arc => {
    arc.addEventListener('mouseenter', () => {
      arcs.forEach(a => a.classList.add('dimmed'));
      arc.classList.remove('dimmed');
      arc.classList.add('highlighted');
      // Also highlight sibling cgs under the same subsystem.
      const sub = arc.dataset.sub;
      const cgName = arc.dataset.cg;
      if (sub) {
        // hovering an inner-ring subsystem: keep all CG arcs in that
        // subsystem visible.
        document.querySelectorAll('svg.sunburst .sb-cg').forEach(a => {
          if (window._sbCgToSub && window._sbCgToSub[a.dataset.cg] === sub) {
            a.classList.remove('dimmed');
          }
        });
      } else if (cgName && window._sbCgToSub) {
        const parent = window._sbCgToSub[cgName];
        document.querySelector(`svg.sunburst .sb-sub[data-sub="${parent}"]`)?.classList.remove('dimmed');
      }
    });
    arc.addEventListener('mouseleave', () => {
      arcs.forEach(a => a.classList.remove('dimmed', 'highlighted'));
    });
    arc.addEventListener('click', () => {
      const data = window._sbDetail || {};
      const sub = arc.dataset.sub;
      const cgName = arc.dataset.cg;
      if (sub && data.subsys && data.subsys[sub]) {
        setDetail(data.subsys[sub]);
      } else if (cgName && data.cgs && data.cgs[cgName]) {
        setDetail(data.cgs[cgName]);
        // Also offer a "View bins" jump.
        document.getElementById('detail-jump').onclick = () => {
          document.querySelector('.tab[data-tab="covergroups"]').click();
          const target = document.getElementById('cg-' + cgName);
          if (target) {
            target.open = true;
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        };
        document.getElementById('detail-jump').style.display = 'inline-block';
      }
    });
  });

  // ---------- Keyboard: '/' focuses search ----------
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      search?.focus();
    } else if (e.key === 'Escape' && document.activeElement === search) {
      search.value = '';
      apply();
      search.blur();
    }
  });
})();
"""


# ---------------------------------------------------------------------------
# Data shaping helpers.
# ---------------------------------------------------------------------------


def _summarise_cg(
    bins: dict[str, int],
    goal_bins: dict[str, int],
) -> dict:
    """Compute the per-covergroup KPIs.

    Returns a dict with: status (met/partial/missed/untracked), pct
    (0-100), required (count), met (count), missing (count), n_hit
    (unique bins observed), total_hits.
    """
    n_hit = len(bins)
    total_hits = sum(bins.values())

    required_bins = {bn: req for bn, req in goal_bins.items() if req > 0}
    if not required_bins:
        return {
            "status": "untracked",
            "pct": 100.0 if n_hit > 0 else 0.0,
            "required": 0, "met": 0, "missing": 0,
            "n_hit": n_hit, "total_hits": total_hits,
        }

    met = sum(1 for bn, req in required_bins.items()
              if bins.get(bn, 0) >= req)
    missing = len(required_bins) - met
    pct = (met / len(required_bins) * 100.0) if required_bins else 0.0
    if missing == 0:
        status = "met"
    elif met == 0:
        status = "missed"
    else:
        status = "partial"
    return {
        "status": status, "pct": pct,
        "required": len(required_bins), "met": met, "missing": missing,
        "n_hit": n_hit, "total_hits": total_hits,
    }


def _bin_status(observed: int, required: int) -> tuple[str, float]:
    """Return (status, percent) for a single bin.

    - required==0: untracked. percent = 100 if observed>0 else 0.
    - observed>=required: hit (percent = 100).
    - observed==0: missed (percent = 0).
    - else: partial (percent = observed/required*100).
    """
    if required == 0:
        return ("hit" if observed > 0 else "untracked",
                100.0 if observed > 0 else 0.0)
    pct = min(100.0, observed / required * 100.0)
    if observed >= required:
        return "hit", 100.0
    if observed == 0:
        return "missed", 0.0
    return "partial", pct


def _bar_html(observed: int, required: int) -> str:
    """Render the inline coverage bar with hit/required label."""
    status, pct = _bin_status(observed, required)
    label = f"{observed}/{required}" if required > 0 else f"{observed}"
    klass = "" if status == "hit" else f"{status}"
    return (
        f'<span class="bar {klass}">'
        f'<span class="fill" style="width: {pct:.1f}%"></span>'
        f'<span class="label">{_html.escape(label)}</span>'
        f'</span>'
    )


def _badge(status: str, text: str | None = None) -> str:
    label = text or status.upper()
    return f'<span class="badge {status}">{_html.escape(label)}</span>'


def _is_cross(cg_name: str) -> bool:
    """Heuristic: covergroups named *_cross_cg or *__* with two parts."""
    return cg_name.endswith("_cross_cg") or cg_name.endswith("_cross")


# ---------------------------------------------------------------------------
# SVG charts.
# ---------------------------------------------------------------------------


def _classify_cg_to_subsys(cg_name: str) -> str:
    """Coarse-classify a covergroup into a subsystem bucket.

    Mirrors the table in :mod:`rvgen.coverage.tools` but scoped down to
    avoid the import cycle (and to keep the dashboard module standalone
    if reused elsewhere). When in doubt, returns ``"Misc"``.
    """
    n = cg_name.lower()
    if n.startswith("vec_") or n in ("vtype_cg", "vtype_dyn_cg", "vreg_cg"):
        return "Vector"
    if n.startswith("fp_") or n in ("fp_rm_cg", "fpr_cg", "fp_dataset_cg",
                                     "fp_fflags_cg"):
        return "Floating point"
    if n.startswith("csr_") or n in ("priv_event_cg", "privilege_mode_cg",
                                      "exception_cg", "trap_cause_cg",
                                      "pmp_cfg_cg"):
        return "Privileged"
    if n in ("modern_ext_cg",):
        return "Modern checkbox"
    if n == "fence_cg":
        return "Memory ordering"
    if n == "lr_sc_pattern_cg":
        return "Atomics"
    if n.startswith("branch_") or n == "branch_direction_cg":
        return "Control flow"
    if n in ("load_store_width_cg", "load_store_offset_cg",
             "mem_align_cg", "ea_align_cg",
             "cache_line_cross_cg", "page_cross_cg"):
        return "Memory access"
    if n in ("hazard_cg", "category_transition_cg",
             "opcode_transition_cg"):
        return "Pipeline"
    if n in ("rs1_cg", "rs2_cg", "rd_cg", "rs1_eq_rs2_cg", "rs1_eq_rd_cg",
             "rs1_rs2_cross_cg", "rd_rs1_cross_cg", "op_comb_cg"):
        return "Reg-file"
    if n in ("rs1_val_class_cg", "rs2_val_class_cg", "rd_val_class_cg",
             "rs_val_class_cross_cg", "rs_val_corner_cg",
             "bit_activity_cg"):
        return "Value class"
    if n in ("imm_sign_cg", "imm_range_cg"):
        return "Immediates"
    if n == "directed_stream_cg":
        return "Streams"
    if n == "pc_reach_cg":
        return "Reachability"
    if n == "multi_hart_race_cg":
        return "Multi-hart"
    if n == "csr_read_cg":
        return "Privileged"
    if n in ("opcode_cg", "format_cg", "category_cg", "group_cg",
             "fmt_category_cross", "category_group_cross"):
        return "Instruction mix"
    return "Misc"


def _heat_color(pct: float) -> str:
    """Coverage-percent → CSS variable. Red < 60, yellow < 85, green ≥ 85."""
    if pct >= 85:
        return "var(--good)"
    if pct >= 60:
        return "var(--warn)"
    return "var(--bad)"


def _polar(cx: float, cy: float, r: float, angle_rad: float) -> tuple[float, float]:
    return cx + r * math.cos(angle_rad), cy + r * math.sin(angle_rad)


def _arc_path(cx: float, cy: float, r_inner: float, r_outer: float,
              a0: float, a1: float) -> str:
    """Build an SVG path for a ring-segment (annular arc)."""
    large = 1 if (a1 - a0) > math.pi else 0
    x0o, y0o = _polar(cx, cy, r_outer, a0)
    x1o, y1o = _polar(cx, cy, r_outer, a1)
    x0i, y0i = _polar(cx, cy, r_inner, a0)
    x1i, y1i = _polar(cx, cy, r_inner, a1)
    return (
        f"M{x0o:.2f},{y0o:.2f} "
        f"A{r_outer:.2f},{r_outer:.2f} 0 {large} 1 {x1o:.2f},{y1o:.2f} "
        f"L{x1i:.2f},{y1i:.2f} "
        f"A{r_inner:.2f},{r_inner:.2f} 0 {large} 0 {x0i:.2f},{y0i:.2f} "
        f"Z"
    )


def _sunburst_svg(
    db: CoverageDB,
    goals: Goals | None,
    cg_summaries: dict[str, dict],
    *,
    size: int = 760,
) -> str:
    """Two-level sunburst: subsystems (inner ring) → covergroups (outer ring).

    Segment size scales with required bin count (falls back to bin
    count when no goals). Color is the red/yellow/green coverage heat.
    Click → drills into that covergroup on the Hierarchy tab via the
    embedded JS handler.

    Returns the SVG string. Empty SVG when the DB is empty.
    """
    # Build per-subsystem aggregates.
    subsys: dict[str, dict] = {}
    for cg, s in cg_summaries.items():
        if s["n_hit"] == 0 and s["required"] == 0:
            continue
        bucket = _classify_cg_to_subsys(cg)
        b = subsys.setdefault(bucket, {
            "weight": 0, "met": 0, "required": 0,
            "cgs": [],
        })
        # Use required count as the segment weight when available, else
        # fall back to unique-bins-hit. Guarantees no zero-weight slices.
        w = max(s["required"], s["n_hit"], 1)
        b["weight"] += w
        b["met"] += s["met"]
        b["required"] += s["required"]
        b["cgs"].append((cg, s, w))

    if not subsys:
        return ('<div class="empty" style="height:480px;'
                'display:flex;align-items:center;justify-content:center;">'
                'No coverage data to plot.</div>')

    cx = cy = size / 2
    r_inner_hole = size * 0.18      # white center
    r_inner_ring = size * 0.34      # subsystem ring outer edge
    r_outer_ring = size * 0.48      # covergroup ring outer edge

    total_weight = sum(b["weight"] for b in subsys.values())
    if total_weight == 0:
        total_weight = 1

    # Order subsystems by weight desc for stable, readable layout.
    ordered = sorted(subsys.items(), key=lambda kv: -kv[1]["weight"])

    out: list[str] = [
        f'<svg class="sunburst" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Coverage sunburst">'
    ]
    # Defer label drawing until after all arcs so pills paint above arcs.
    label_rows: list[tuple[str, float, float, float]] = []

    angle = -math.pi / 2  # start at 12 o'clock
    for sub_name, b in ordered:
        sweep = b["weight"] / total_weight * 2 * math.pi
        a0, a1 = angle, angle + sweep
        pct = (b["met"] / b["required"] * 100.0) if b["required"] else (
            100.0 if any(s["n_hit"] > 0 for _, s, _ in b["cgs"]) else 0.0
        )
        color = _heat_color(pct)
        path = _arc_path(cx, cy, r_inner_hole, r_inner_ring, a0, a1)
        title = (f"{sub_name}: {b['met']}/{b['required']} bins "
                 f"({pct:.1f}%)") if b["required"] else (
            f"{sub_name}: untracked ({sum(s['n_hit'] for _, s, _ in b['cgs'])} bins hit)"
        )
        out.append(
            f'<path class="sb-arc sb-sub" data-sub="{_html.escape(sub_name)}" '
            f'fill="{color}" stroke="var(--bg-card)" stroke-width="2" '
            f'd="{path}"><title>{_html.escape(title)}</title></path>'
        )
        # Subsystem label — radial along the angle-bisector ray with a
        # solid dark pill background so it stays crisp regardless of
        # the arc colour underneath.
        if sweep > math.radians(6):
            label_rows.append((sub_name, (a0 + a1) / 2,
                               r_inner_hole, r_inner_ring))

        # Outer ring: per-covergroup arcs inside this subsystem's wedge.
        cg_total = sum(w for _, _, w in b["cgs"]) or 1
        ang2 = a0
        for cg, s, w in sorted(b["cgs"], key=lambda x: -x[2]):
            cg_sweep = w / cg_total * sweep
            ca0, ca1 = ang2, ang2 + cg_sweep
            cg_pct = s["pct"]
            cg_color = _heat_color(cg_pct)
            cg_path = _arc_path(cx, cy, r_inner_ring, r_outer_ring, ca0, ca1)
            cg_title = (f"{cg}: {s['met']}/{s['required']} bins "
                        f"({cg_pct:.1f}%)") if s["required"] else (
                f"{cg}: {s['n_hit']} bins observed (no goals)"
            )
            out.append(
                f'<path class="sb-arc sb-cg" data-cg="{_html.escape(cg)}" '
                f'data-pct="{cg_pct:.1f}" '
                f'fill="{cg_color}" stroke="var(--bg-card)" stroke-width="1" '
                f'd="{cg_path}"><title>{_html.escape(cg_title)}</title></path>'
            )
            ang2 = ca1
        angle = a1

    # Center: total %.
    total_met = sum(s["met"] for s in cg_summaries.values())
    total_req = sum(s["required"] for s in cg_summaries.values())
    overall_pct = (total_met / total_req * 100.0) if total_req else 0.0

    # ---- Subsystem labels (inner ring only) ----
    # Plain white text with a paint-order stroke for contrast. The text
    # baseline is anchored at the midpoint of the inner ring along each
    # slice's bisector ray, and `textLength` constrains rendering width
    # to ring-width minus padding so labels can never spill into the
    # outer ring. Outer ring (covergroups) is unlabelled — covergroup
    # name shows up in the detail card on click.
    for label, mid, r_inner, r_outer in label_rows:
        ring_w = r_outer - r_inner
        max_text_w = ring_w - 16  # 8px padding each end
        # Anchor at the midpoint of the inner ring along the bisector.
        r_anchor = (r_inner + r_outer) / 2
        ax, ay = _polar(cx, cy, r_anchor, mid)
        # Rotate so the text reads outward; flip 180° on the left half
        # so it's always upright.
        rot_deg = math.degrees(mid)
        if math.cos(mid) < 0:
            rot_deg += 180
        out.append(
            f'<text class="sb-label" x="{ax:.1f}" y="{ay:.1f}" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'transform="rotate({rot_deg:.1f} {ax:.1f} {ay:.1f})" '
            f'textLength="{max_text_w:.0f}" lengthAdjust="spacingAndGlyphs" '
            f'pointer-events="none">'
            f'{_html.escape(label)}</text>'
        )

    out.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r_inner_hole - 2}" '
        f'fill="var(--bg-card)" stroke="var(--grid)" stroke-width="1" />'
    )
    out.append(
        f'<text class="sb-center-num" x="{cx}" y="{cy - 6}" '
        f'text-anchor="middle">{overall_pct:.0f}%</text>'
    )
    out.append(
        f'<text class="sb-center-sub" x="{cx}" y="{cy + 22}" '
        f'text-anchor="middle">overall closed</text>'
    )
    if total_req:
        out.append(
            f'<text class="sb-center-tiny" x="{cx}" y="{cy + 44}" '
            f'text-anchor="middle">{total_met:,} / {total_req:,} bins</text>'
        )
    out.append('</svg>')
    return "\n".join(out)


def _pie_svg(hit: int, partial: int, missed: int, untracked: int) -> str:
    """Render a donut pie of the four bin-status totals."""
    total = hit + partial + missed + untracked
    if total == 0:
        return '<svg viewBox="0 0 200 200"></svg>'
    cx, cy, r, w = 80, 80, 70, 22
    parts = [
        ("good", hit), ("warn", partial),
        ("bad", missed), ("na", untracked),
    ]
    out = ['<svg class="pie-svg" viewBox="0 0 160 160" xmlns="http://www.w3.org/2000/svg">']
    angle = -math.pi / 2  # start at top
    for klass, val in parts:
        if val <= 0:
            continue
        theta = val / total * 2 * math.pi
        a0, a1 = angle, angle + theta
        x0 = cx + r * math.cos(a0)
        y0 = cy + r * math.sin(a0)
        x1 = cx + r * math.cos(a1)
        y1 = cy + r * math.sin(a1)
        large = 1 if theta > math.pi else 0
        out.append(
            f'<path class="slice-{klass}" d="M{cx},{cy} L{x0:.1f},{y0:.1f} '
            f'A{r},{r} 0 {large} 1 {x1:.1f},{y1:.1f} Z" />'
        )
        angle = a1
    # Inner circle (donut hole) — uses bg-card colour
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{r - w}" '
               f'fill="var(--bg-card)" />')
    # Center label: total covered percent.
    covered = hit
    pct = covered / total * 100.0 if total else 0
    out.append(f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" '
               f'fill="var(--fg)" font-size="22" font-weight="600">'
               f'{pct:.0f}%</text>')
    out.append(f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" '
               f'fill="var(--fg-mute)" font-size="10">covered</text>')
    out.append("</svg>")
    return "\n".join(out)


def _scorecard_svg(rows: list[dict]) -> str:
    """Horizontal bar chart per subsystem.

    Each row dict: {subsystem, met, required, percent}.
    """
    visible = [r for r in rows if (r.get("required") or 0) > 0]
    if not visible:
        return '<div class="empty">No subsystem rows to plot.</div>'

    width = 720
    row_h = 26
    gap = 6
    label_w = 160
    pct_w = 80
    chart_w = width - label_w - pct_w - 24
    height = len(visible) * (row_h + gap) + 12
    out = [f'<svg class="scorecard-svg" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg">']
    for i, r in enumerate(visible):
        y = i * (row_h + gap) + 4
        pct = float(r.get("percent", 0.0))
        klass = "good" if pct >= 80 else ("warn" if pct >= 40 else "bad")
        out.append(
            f'<rect class="row-bg" x="{label_w}" y="{y}" '
            f'width="{chart_w}" height="{row_h}" rx="4" />'
        )
        out.append(
            f'<rect class="row-fg {klass}" x="{label_w}" y="{y}" '
            f'width="{chart_w * pct / 100.0:.1f}" height="{row_h}" rx="4" />'
        )
        out.append(
            f'<text class="row-label" x="{label_w - 10}" y="{y + 17}" '
            f'text-anchor="end">'
            f'{_html.escape(str(r.get("subsystem", "")))}</text>'
        )
        out.append(
            f'<text class="row-value" x="{label_w + chart_w + 8}" '
            f'y="{y + 17}">'
            f'{r.get("met", 0)}/{r.get("required", 0)} ({pct:.1f}%)</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


def _timeline_svg(timeline: list) -> str:
    """Convergence timeline: new bins per seed."""
    pts = []
    for entry in timeline or []:
        if isinstance(entry, dict):
            seed = entry.get("seed", entry.get("idx"))
            new_bins = entry.get("new_bins", entry.get("delta", 0))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            seed, new_bins = entry[0], entry[1]
        else:
            continue
        if seed is None:
            continue
        pts.append((int(seed), int(new_bins)))

    if not pts:
        return '<div class="empty">No timeline data.</div>'

    width, height = 760, 200
    margin_l, margin_t, margin_r, margin_b = 40, 12, 12, 26
    chart_w = width - margin_l - margin_r
    chart_h = height - margin_t - margin_b

    n = len(pts)
    max_y = max((p[1] for p in pts), default=1)
    if max_y < 1:
        max_y = 1
    x_step = chart_w if n == 1 else chart_w / (n - 1)

    poly_pts = []
    circles = []
    for i, (seed, new_bins) in enumerate(pts):
        x = margin_l + i * x_step
        y = margin_t + chart_h - (new_bins / max_y * chart_h)
        poly_pts.append(f"{x:.1f},{y:.1f}")
        circles.append(
            f'<circle class="point" cx="{x:.1f}" cy="{y:.1f}" r="3">'
            f'<title>seed {seed}: {new_bins} new bins</title></circle>'
        )

    out = [f'<svg class="timeline-svg" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg">']
    out.append(f'<line class="axis" x1="{margin_l}" y1="{margin_t}" '
               f'x2="{margin_l}" y2="{margin_t + chart_h}" />')
    out.append(f'<line class="axis" x1="{margin_l}" y1="{margin_t + chart_h}" '
               f'x2="{margin_l + chart_w}" y2="{margin_t + chart_h}" />')
    out.append(f'<text class="axis-text" x="{margin_l - 6}" y="{margin_t + 4}" '
               f'text-anchor="end">{max_y}</text>')
    out.append(f'<text class="axis-text" x="{margin_l - 6}" '
               f'y="{margin_t + chart_h + 4}" text-anchor="end">0</text>')
    out.append(f'<text class="axis-text" x="{margin_l}" '
               f'y="{margin_t + chart_h + 18}">seed {pts[0][0]}</text>')
    if n > 1:
        out.append(f'<text class="axis-text" x="{margin_l + chart_w}" '
                   f'y="{margin_t + chart_h + 18}" text-anchor="end">'
                   f'seed {pts[-1][0]}</text>')
    out.append(f'<polyline class="line" points="{" ".join(poly_pts)}" />')
    out.extend(circles)
    out.append("</svg>")
    return "\n".join(out)


def _heatmap_html(name: str, bins: dict[str, int],
                  goal_bins: dict[str, int]) -> str:
    """Render a cross-coverage covergroup as a 2-D heatmap.

    Cross bins are formatted ``A__B`` (riscv-isac convention). We
    pivot into a matrix; rows = unique A, cols = unique B. Cell
    intensity scales with hit count.
    """
    rows: dict[str, dict[str, int]] = {}
    for bn in set(bins) | set(goal_bins):
        if "__" not in bn:
            continue
        a, b = bn.split("__", 1)
        rows.setdefault(a, {})[b] = bins.get(bn, 0)

    if not rows:
        return ""

    row_keys = sorted(rows.keys())
    col_keys = sorted({c for v in rows.values() for c in v})
    if not col_keys or not row_keys:
        return ""

    # Cap heatmap at 24x24 to keep DOM size sane on huge crosses.
    if len(row_keys) > 24:
        row_keys = row_keys[:24]
    if len(col_keys) > 24:
        col_keys = col_keys[:24]

    max_val = max(
        (rows.get(r, {}).get(c, 0) for r in row_keys for c in col_keys),
        default=0,
    )
    if max_val == 0:
        max_val = 1

    out = [f'<div class="heatmap"><h3>{_html.escape(name)}</h3>',
           f'<div class="meta">{len(row_keys)}×{len(col_keys)} matrix · '
           f'max hits {max_val}</div>']
    cols = len(col_keys) + 1
    out.append(
        f'<div class="heatmap-grid" '
        f'style="grid-template-columns: 80px repeat({len(col_keys)}, 1fr)">'
    )
    out.append('<div class="heatmap-cell label"></div>')
    for c in col_keys:
        out.append(f'<div class="heatmap-cell label">{_html.escape(c)}</div>')
    for r in row_keys:
        out.append(f'<div class="heatmap-cell label">{_html.escape(r)}</div>')
        for c in col_keys:
            v = rows.get(r, {}).get(c, 0)
            if v == 0:
                klass = "zero"
                bg = ""
                txt = "·"
            else:
                klass = "hit"
                # Heat scaled to log to keep the gradient readable.
                intensity = math.log1p(v) / math.log1p(max_val)
                # Interpolate from green to yellow to red as intensity
                # rises (high hits = green, near-zero = warn).
                # Use HSL: 120 (green) at intensity=1 → 0 (red) at low.
                # We want HIGH hits to be VIBRANT GREEN.
                hue = 120
                lightness = 18 + 28 * intensity  # 18% to 46%
                bg = f'background:hsl({hue},55%,{lightness:.0f}%)'
                txt = str(v)
            out.append(
                f'<div class="heatmap-cell {klass}" style="{bg}" '
                f'title="{_html.escape(r)}__{_html.escape(c)}: {v}">'
                f'{_html.escape(txt)}</div>'
            )
    out.append("</div></div>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Section renderers.
# ---------------------------------------------------------------------------


def n_active_or(n: int) -> str:
    """Format the active-covergroups number for compact display."""
    return f"{n}"


def _build_sunburst_detail_payload(
    db: CoverageDB, goals: Goals | None,
    cg_summaries: dict[str, dict],
) -> dict:
    """Pre-compute the per-segment detail payload for the click handler.

    Returns ``{subsys: {sub_name: detail}, cgs: {cg_name: detail}}``.
    Each detail has: lead, title, metrics list, misses list (top 5).
    """
    sub_to_cgs: dict[str, list[tuple[str, dict]]] = {}
    cg_to_sub: dict[str, str] = {}
    for cg, s in cg_summaries.items():
        if s["n_hit"] == 0 and s["required"] == 0:
            continue
        sub = _classify_cg_to_subsys(cg)
        sub_to_cgs.setdefault(sub, []).append((cg, s))
        cg_to_sub[cg] = sub

    out: dict = {"subsys": {}, "cgs": {}, "cg_to_sub": cg_to_sub}

    for sub, cgs in sub_to_cgs.items():
        s_met = sum(s["met"] for _, s in cgs)
        s_req = sum(s["required"] for _, s in cgs)
        s_hit = sum(s["n_hit"] for _, s in cgs)
        s_pct = (s_met / s_req * 100.0) if s_req else (
            100.0 if s_hit > 0 else 0.0)
        # Find top missing bins inside this subsystem.
        top_miss = []
        if goals:
            for cg, _s in cgs:
                gb = goals.covergroup(cg)
                bins = db.get(cg, {})
                for bn, req in gb.items():
                    if req <= 0:
                        continue
                    obs = bins.get(bn, 0)
                    if obs < req:
                        top_miss.append((cg, bn, req, obs))
        top_miss.sort(key=lambda r: -r[2])
        misses = [{"bin": f"{cg}.{bn}", "req": req}
                  for cg, bn, req, _obs in top_miss[:5]]
        out["subsys"][sub] = {
            "lead": "Subsystem",
            "title": sub,
            "metrics": [
                {"value": f"{len(cgs)}", "label": "covergroups"},
                {"value": f"{s_hit:,}", "label": "bins hit"},
                {"value": f"{s_pct:.1f}%", "label": "closed",
                 "color": _heat_color(s_pct)},
            ] + ([{"value": f"{s_req - s_met}",
                   "label": "missing required"}]
                 if goals and s_req else []),
            "misses": misses,
        }

    for cg, s in cg_summaries.items():
        if s["n_hit"] == 0 and s["required"] == 0:
            continue
        bins = db.get(cg, {})
        gb = goals.covergroup(cg) if goals else {}
        top_miss = []
        for bn, req in gb.items():
            if req <= 0:
                continue
            obs = bins.get(bn, 0)
            if obs < req:
                top_miss.append((bn, req, obs))
        top_miss.sort(key=lambda r: -r[1])
        misses = [{"bin": bn, "req": req}
                  for bn, req, _obs in top_miss[:8]]
        out["cgs"][cg] = {
            "lead": f"Covergroup · {cg_to_sub.get(cg, 'Misc')}",
            "title": cg,
            "metrics": [
                {"value": f"{s['n_hit']:,}", "label": "bins hit"},
                {"value": f"{s['total_hits']:,}", "label": "samples"},
                {"value": f"{s['pct']:.1f}%", "label": "closed",
                 "color": _heat_color(s["pct"])},
            ] + ([{"value": f"{s['missing']}", "label": "missing"}]
                 if s["required"] else []),
            "misses": misses,
        }

    return out


def _summary_panel(
    db: CoverageDB,
    goals: Goals | None,
    miss: dict,
    cg_summaries: dict[str, dict],
    scorecard: list[dict] | None,
) -> str:
    total_cgs = sum(1 for s in cg_summaries.values()
                    if s["n_hit"] > 0 or s["required"] > 0)
    n_hit_bins = sum(s["n_hit"] for s in cg_summaries.values())
    n_total_hits = sum(s["total_hits"] for s in cg_summaries.values())

    if goals:
        total_req = sum(s["required"] for s in cg_summaries.values())
        total_met = sum(s["met"] for s in cg_summaries.values())
        total_missing = total_req - total_met
        pct = (total_met / total_req * 100.0) if total_req else 100.0
    else:
        total_req = total_met = total_missing = 0
        pct = 100.0

    # Per-bin pie counts.
    pie_hit = pie_part = pie_miss = pie_untracked = 0
    for cg, bins in db.items():
        gb = goals.covergroup(cg) if goals else {}
        all_bins = set(bins) | set(gb)
        for bn in all_bins:
            obs = bins.get(bn, 0)
            req = gb.get(bn, 0)
            status, _ = _bin_status(obs, req)
            if status == "hit":
                pie_hit += 1
            elif status == "partial":
                pie_part += 1
            elif status == "missed":
                pie_miss += 1
            else:
                pie_untracked += 1

    out: list[str] = []

    # KPI strip — Z-pattern (Grafana best practice).
    out.append('<section><h2>At a glance</h2><div class="tiles">')
    out.append(_tile("Active covergroups", str(total_cgs)))
    out.append(_tile("Unique bins hit", str(n_hit_bins),
                     sub=f"{n_total_hits:,} total samples"))
    if goals:
        klass = "good" if pct >= 95 else ("warn" if pct >= 70 else "bad")
        out.append(_tile("Goals met", f"{total_met}/{total_req}",
                         sub=f"{pct:.1f}% closed",
                         klass=klass))
        out.append(_tile("Missing required", str(total_missing),
                         sub="bins below quota",
                         klass="warn" if total_missing else "good"))
        grade = pct
        klass = "good" if grade >= 85 else ("warn" if grade >= 60 else "bad")
        out.append(_tile("Grade", f"{grade:.0f}/100",
                         sub="composite goal-closure",
                         klass=klass))
    out.append("</div></section>")

    # Hero — sunburst + selected-segment detail card.
    out.append('<section class="hero-section"><h2>Coverage map</h2>'
               '<div class="hero">')
    out.append(
        '<div class="sunburst-card">'
        '<div class="header"><h3>Hierarchical coverage</h3>'
        '<div class="legend">'
        '<span class="row"><span class="swatch" style="background:var(--good)"></span>≥85%</span>'
        '<span class="row"><span class="swatch" style="background:var(--warn)"></span>60–85%</span>'
        '<span class="row"><span class="swatch" style="background:var(--bad)"></span>&lt;60%</span>'
        '</div></div>'
    )
    out.append(_sunburst_svg(db, goals, cg_summaries))
    out.append(
        '<div style="text-align:center;margin-top:6px;'
        'color:var(--fg-faint);font-size:10px;">'
        'Hover an arc for details · click to drill in · inner ring = subsystem · outer = covergroup'
        '</div>'
    )
    out.append('</div>')

    # Detail card.
    out.append(
        '<div class="detail-card">'
        '<div class="lead" id="detail-lead">Click any arc to inspect</div>'
        '<h3 class="target" id="detail-title">Coverage map</h3>'
        '<div class="nums" id="detail-nums">'
        f'<div><div class="num">{n_active_or(total_cgs)}</div>'
        '<div class="num-label">covergroups</div></div>'
        f'<div><div class="num">{n_hit_bins:,}</div>'
        '<div class="num-label">bins hit</div></div>'
        + (f'<div><div class="num" style="color:{_heat_color(pct)}">{pct:.1f}%</div>'
           f'<div class="num-label">closed</div></div>' if goals else '')
        + '</div>'
        '<ul class="misses" id="detail-misses">'
        '<li style="color:var(--fg-faint)">Hover or click an arc to see top missing bins</li>'
        '</ul>'
        '<div class="hint"><button id="detail-jump" '
        'style="display:none" class="btn">View bins →</button>'
        ' Press <span class="kbd">/</span> to search · <span class="kbd">Esc</span> to clear</div>'
        '</div>'
    )
    out.append('</div></section>')

    # Pie + scorecard side by side
    out.append('<section><h2>Coverage breakdown</h2>'
               '<div class="charts-row">')
    out.append('<div class="chart-card"><h3>Bin status distribution</h3>'
               '<div class="pie-wrap">')
    out.append(_pie_svg(pie_hit, pie_part, pie_miss, pie_untracked))
    out.append('<div class="pie-legend">')
    pie_total = pie_hit + pie_part + pie_miss + pie_untracked
    _swatch_var = {"good": "good", "warn": "warn",
                   "bad": "bad", "na": "fg-faint"}
    for label, val, klass in (("Hit", pie_hit, "good"),
                              ("Partial", pie_part, "warn"),
                              ("Missed", pie_miss, "bad"),
                              ("Untracked", pie_untracked, "na")):
        if val == 0 and label != "Hit":
            continue
        p = (val / pie_total * 100.0) if pie_total else 0.0
        var_name = _swatch_var[klass]
        out.append(
            f'<div class="row"><span class="swatch" '
            f'style="background:var(--{var_name})"></span>'
            f'<span>{label}</span>'
            f'<span class="pct">{val:,} ({p:.1f}%)</span></div>'
        )
    out.append('</div></div></div>')

    if scorecard:
        out.append('<div class="chart-card"><h3>Per-subsystem closure</h3>')
        out.append(_scorecard_svg(scorecard))
        out.append('</div>')
    out.append('</div></section>')

    # Top missing
    if goals:
        rows = []
        for cg, bins in miss.items():
            for bn in bins:
                req = goals.covergroup(cg).get(bn, 0)
                obs = db.get(cg, {}).get(bn, 0)
                if req > 0:
                    rows.append((cg, bn, obs, req))
        rows.sort(key=lambda r: -r[3])
        rows = rows[:25]
        if rows:
            out.append('<section><h2>Top missing bins</h2>')
            out.append('<table class="sortable"><thead><tr>'
                       '<th>Covergroup</th><th>Bin</th>'
                       '<th>Coverage</th>'
                       '<th>Status</th></tr></thead><tbody>')
            for cg, bn, obs, req in rows:
                status, _pct = _bin_status(obs, req)
                out.append(
                    '<tr class="miss-row">'
                    f'<td><a href="#cg-{_html.escape(cg)}" onclick="document.querySelector(\'.tab[data-tab=covergroups]\').click();">{_html.escape(cg)}</a></td>'
                    f'<td class="bin-name">{_html.escape(bn)}</td>'
                    f'<td>{_bar_html(obs, req)}</td>'
                    f'<td>{_badge(status)}</td>'
                    '</tr>'
                )
            out.append('</tbody></table></section>')

    return "\n".join(out)


def _covergroups_panel(
    db: CoverageDB,
    goals: Goals | None,
    miss: dict,
    cg_summaries: dict[str, dict],
) -> str:
    out: list[str] = []

    # Toolbar: search + status chips + expand/collapse.
    out.append('<div class="toolbar">')
    out.append('<input id="cg-search" class="search" type="search" '
               'placeholder="Filter covergroups (press / to focus)" />')
    counts = {"all": 0, "met": 0, "partial": 0, "missed": 0, "untracked": 0}
    for s in cg_summaries.values():
        counts["all"] += 1
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    for k, label in (("all", "All"), ("met", "Met"),
                     ("partial", "Partial"), ("missed", "Missed"),
                     ("untracked", "Untracked")):
        if counts.get(k, 0) == 0 and k != "all":
            continue
        active = " active" if k == "all" else ""
        cls = "" if k == "all" else f" {k}"
        out.append(
            f'<span class="chip{cls}{active}" data-status="{k}">'
            f'<span class="dot"></span>{label}'
            f' <span style="color:var(--fg-faint)">{counts[k]}</span>'
            f'</span>'
        )
    out.append('<span style="flex:1"></span>')
    out.append('<button id="expand-all">Expand all</button>')
    out.append('<button id="collapse-all">Collapse all</button>')
    out.append('</div>')

    # Two-column layout: sidebar nav + main list.
    out.append('<div class="layout">')

    # Sidebar nav with anchor links.
    out.append('<nav class="sidebar"><h4>Jump to</h4>')
    for name in sorted(cg_summaries.keys()):
        s = cg_summaries[name]
        if s["n_hit"] == 0 and s["required"] == 0:
            continue
        out.append(
            f'<a href="#cg-{_html.escape(name)}">'
            f'<span style="overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{_html.escape(name)}</span>'
            f'<span class="pct">{s["pct"]:.0f}%</span></a>'
        )
    out.append('</nav>')

    out.append('<div class="cg-list">')
    for name in sorted(cg_summaries.keys()):
        s = cg_summaries[name]
        if s["n_hit"] == 0 and s["required"] == 0:
            continue
        bins = db.get(name, {})
        gb = goals.covergroup(name) if goals else {}
        out.append(_render_cg(name, s, bins, gb))
    out.append('</div></div>')
    return "\n".join(out)


def _render_cg(name: str, s: dict, bins: dict[str, int],
               gb: dict[str, int]) -> str:
    """Render one covergroup as a <details> with a sortable bin table."""
    if s["status"] == "untracked":
        badge_text = "no goals"
    elif s["status"] == "met":
        badge_text = f"MET {s['met']}/{s['required']}"
    elif s["status"] == "missed":
        badge_text = f"MISS 0/{s['required']}"
    else:
        badge_text = f"PART {s['met']}/{s['required']}"

    out: list[str] = []
    out.append(
        f'<details class="cg" id="cg-{_html.escape(name)}" '
        f'data-name="{_html.escape(name)}" '
        f'data-status="{s["status"]}">'
    )
    out.append('<summary>')
    out.append(f'<span class="cg-name">{_html.escape(name)}</span>')
    out.append(
        f'<span class="cg-meta">{s["n_hit"]} bin(s) · '
        f'{s["total_hits"]:,} hits'
        + (f' · <b>{s["missing"]}</b> missing' if s["missing"] else "")
        + '</span>'
    )
    # Big bar with percent text.
    klass = ("hit" if s["status"] == "met"
             else ("partial" if s["status"] == "partial"
                   else ("missed" if s["status"] == "missed"
                         else "")))
    out.append(
        f'<span class="bar {klass}" style="width: 100%;">'
        f'<span class="fill" style="width: {s["pct"]:.1f}%"></span>'
        f'<span class="label">{s["pct"]:.1f}%</span></span>'
    )
    out.append(_badge(s["status"], badge_text))
    out.append('</summary>')

    # Body: sortable bin table.
    out.append('<div class="body"><table class="sortable">')
    out.append('<thead><tr>'
               '<th>Bin</th>'
               '<th>Coverage</th>'
               '<th class="num">Hits</th>'
               '<th class="num">Required</th>'
               '<th>Status</th>'
               '</tr></thead><tbody>')
    all_bins = set(bins) | set(gb)
    # Initial sort: missing/under-quota first, then hits desc.
    def _key(bn):
        obs = bins.get(bn, 0)
        req = gb.get(bn, 0)
        status, _ = _bin_status(obs, req)
        prio = {"missed": 0, "partial": 1, "untracked": 2, "hit": 3}[status]
        return (prio, -obs)

    for bn in sorted(all_bins, key=_key):
        obs = bins.get(bn, 0)
        req = gb.get(bn, 0)
        status, pct = _bin_status(obs, req)
        out.append(
            '<tr>'
            f'<td class="bin-name" data-sort="{_html.escape(bn)}">{_html.escape(bn)}</td>'
            f'<td data-sort="{pct:.2f}">{_bar_html(obs, req)}</td>'
            f'<td class="num" data-sort="{obs}">{obs:,}</td>'
            f'<td class="num" data-sort="{req}">{req if req else "—"}</td>'
            f'<td data-sort="{status}">{_badge(status)}</td>'
            '</tr>'
        )
    out.append('</tbody></table></div></details>')
    return "\n".join(out)


def _crosses_panel(db: CoverageDB, goals: Goals | None) -> str:
    out: list[str] = []
    crosses = sorted(cg for cg in db if _is_cross(cg))
    if goals:
        crosses += sorted(cg for cg in goals.data if _is_cross(cg)
                          and cg not in db)
    crosses = list(dict.fromkeys(crosses))  # dedupe preserving order
    if not crosses:
        out.append('<div class="empty">No cross-coverage covergroups found.</div>')
        return "\n".join(out)
    out.append('<section><h2>Cross-coverage matrices</h2>')
    out.append('<p style="color:var(--fg-mute);font-size:12px;'
               'margin:0 0 12px;">Each cell = (rs1__rs2) hit count. '
               'Empty cells (·) never observed.</p>')
    for cg in crosses:
        bins = db.get(cg, {})
        gb = goals.covergroup(cg) if goals else {}
        hm = _heatmap_html(cg, bins, gb)
        if hm:
            out.append(hm)
    out.append('</section>')
    return "\n".join(out)


def _misses_panel(db: CoverageDB, goals: Goals | None,
                  miss: dict, explanations: dict | None) -> str:
    if not goals:
        return '<div class="empty">No goals provided — no misses to report.</div>'

    rows = []
    for cg, bins in miss.items():
        for bn in bins:
            req = goals.covergroup(cg).get(bn, 0)
            obs = db.get(cg, {}).get(bn, 0)
            if req > 0:
                why = ""
                if explanations:
                    why = explanations.get(f"{cg}.{bn}", "")
                rows.append((cg, bn, obs, req, why))
    if not rows:
        return ('<div class="empty" style="color:var(--good)">'
                'All required bins met! ✓</div>')
    rows.sort(key=lambda r: -r[3])

    out = ['<section><h2>All missing bins</h2>',
           f'<p style="color:var(--fg-mute);font-size:12px;'
           f'margin:0 0 12px;">{len(rows)} unmet bin(s), '
           f'sorted by required count.</p>',
           '<table class="sortable"><thead><tr>'
           '<th>Covergroup</th><th>Bin</th><th>Coverage</th>'
           '<th class="num">Hits</th>'
           '<th class="num">Required</th>'
           '<th>Status</th></tr></thead><tbody>']
    for cg, bn, obs, req, why in rows:
        status, pct = _bin_status(obs, req)
        why_html = (f'<div class="why">{_html.escape(why)}</div>'
                    if why else "")
        out.append(
            '<tr class="miss-row">'
            f'<td>{_html.escape(cg)}</td>'
            f'<td class="bin-name">{_html.escape(bn)}{why_html}</td>'
            f'<td data-sort="{pct:.2f}">{_bar_html(obs, req)}</td>'
            f'<td class="num" data-sort="{obs}">{obs}</td>'
            f'<td class="num" data-sort="{req}">{req}</td>'
            f'<td data-sort="{status}">{_badge(status)}</td>'
            '</tr>'
        )
    out.append('</tbody></table></section>')
    return "\n".join(out)


def _convergence_panel(timeline: list | None) -> str:
    if not timeline:
        return ('<div class="empty">No timeline data — pass '
                '<code>--timeline cov_timeline.json</code>.</div>')
    out = ['<section><h2>Convergence timeline</h2>',
           '<p style="color:var(--fg-mute);font-size:12px;'
           'margin:0 0 12px;">New bins observed per seed across the '
           'regression. Steeper drop-offs indicate plateau.</p>',
           '<div class="chart-card">',
           _timeline_svg(timeline),
           '</div></section>']
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Tile + top-level renderer.
# ---------------------------------------------------------------------------


def _tile(label: str, value: str, *, sub: str | None = None,
          klass: str | None = None) -> str:
    klass_attr = f' class="tile {klass}"' if klass else ' class="tile"'
    sub_html = f'<div class="sub">{_html.escape(sub)}</div>' if sub else ""
    return (
        f'<div{klass_attr}>'
        f'<div class="label">{_html.escape(label)}</div>'
        f'<div class="value">{_html.escape(value)}</div>'
        f'{sub_html}</div>'
    )


def dashboard_html(
    db: CoverageDB,
    goals: Goals | None = None,
    timeline: list | None = None,
    scorecard: list[dict] | None = None,
    title: str = "rvgen Coverage Dashboard",
    explanations: dict[str, str] | None = None,
) -> str:
    """Render the full self-contained HTML dashboard.

    Layout: tabbed single-page app — Summary / Covergroups / Crosses /
    Misses / Convergence — with per-bin red/yellow/green progress bars,
    sortable column headers, search + filter chips, sticky sidebar nav,
    and dark/light theme toggle.

    Parameters
    ----------
    db : CoverageDB
        ``{covergroup: {bin: count}}``.
    goals : Goals, optional
        Loaded coverage goals. When omitted, badges + percentages show
        as "untracked".
    timeline : list, optional
        Convergence-tracking data: list of ``{seed, new_bins}`` dicts
        or ``(seed, new_bins)`` tuples.
    scorecard : list of dict, optional
        Per-subsystem closure rows: ``{subsystem, met, required,
        percent}``. Typically produced by
        ``rvgen.coverage.tools.scorecard``.
    title : str
        Page title.
    explanations : dict, optional
        ``{covergroup.bin_name: why-string}`` from cov-explain matchers.
    """
    miss = missing_bins(db, goals) if goals else {}
    cg_summaries = {
        cg: _summarise_cg(bins, goals.covergroup(cg) if goals else {})
        for cg, bins in db.items()
    }
    if goals:
        for cg in goals.data:
            if cg not in cg_summaries:
                cg_summaries[cg] = _summarise_cg(
                    {}, goals.covergroup(cg)
                )

    n_hit_bins = sum(s["n_hit"] for s in cg_summaries.values())
    n_total_hits = sum(s["total_hits"] for s in cg_summaries.values())
    n_active = sum(1 for s in cg_summaries.values()
                   if s["n_hit"] > 0 or s["required"] > 0)
    n_total_req = sum(s["required"] for s in cg_summaries.values())
    n_missing = sum(s["missing"] for s in cg_summaries.values())
    n_cross = sum(1 for cg in cg_summaries if _is_cross(cg))

    out: list[str] = []
    out.append("<!DOCTYPE html><html lang='en' data-theme='dark'>")
    out.append("<head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    out.append(f"<title>{_html.escape(title)}</title>")
    out.append(f"<style>{_CSS}</style></head><body>")

    # Top bar.
    out.append('<div class="topbar">')
    out.append(f'<h1><span class="logo">rv</span>{_html.escape(title)}</h1>')
    out.append(
        f'<div class="meta">{n_active} covergroups · '
        f'{n_hit_bins:,} bins hit · {n_total_hits:,} samples'
        + (f' · {n_total_req:,} required, {n_missing:,} missing'
           if goals else '')
        + '</div>'
    )
    out.append('<span class="spacer"></span>')
    out.append('<button id="theme-toggle" class="btn" title="Toggle theme">'
               '◐ Theme</button>')
    out.append('<button class="btn" onclick="window.print()" '
               'title="Print or save PDF">⎙ Print</button>')
    out.append('</div>')

    # Tab strip.
    out.append('<nav class="tabs">')
    out.append('<div class="tab" data-tab="summary">Summary</div>')
    out.append(
        f'<div class="tab" data-tab="covergroups">Covergroups'
        f'<span class="badge">{n_active}</span></div>'
    )
    if n_cross:
        out.append(
            f'<div class="tab" data-tab="crosses">Cross-coverage'
            f'<span class="badge">{n_cross}</span></div>'
        )
    if goals:
        out.append(
            f'<div class="tab" data-tab="misses">Misses'
            f'<span class="badge">{n_missing}</span></div>'
        )
    if timeline:
        out.append('<div class="tab" data-tab="convergence">Convergence</div>')
    out.append('</nav>')

    # Panels.
    out.append('<div class="panel" data-panel="summary">')
    out.append(_summary_panel(db, goals, miss, cg_summaries, scorecard))
    out.append('</div>')

    # Sunburst detail payload — read by the click handler.
    sb_payload = _build_sunburst_detail_payload(db, goals, cg_summaries)
    out.append('<script>')
    out.append('window._sbDetail = '
               + json.dumps({"subsys": sb_payload["subsys"],
                             "cgs": sb_payload["cgs"]}) + ';')
    out.append('window._sbCgToSub = '
               + json.dumps(sb_payload["cg_to_sub"]) + ';')
    out.append('</script>')

    out.append('<div class="panel" data-panel="covergroups">')
    out.append(_covergroups_panel(db, goals, miss, cg_summaries))
    out.append('</div>')

    if n_cross:
        out.append('<div class="panel" data-panel="crosses">')
        out.append(_crosses_panel(db, goals))
        out.append('</div>')

    if goals:
        out.append('<div class="panel" data-panel="misses">')
        out.append(_misses_panel(db, goals, miss, explanations))
        out.append('</div>')

    if timeline:
        out.append('<div class="panel" data-panel="convergence">')
        out.append(_convergence_panel(timeline))
        out.append('</div>')

    out.append(f'<script>{_JS}</script>')
    out.append('</body></html>')
    return "\n".join(out)


def write_dashboard(
    db: CoverageDB,
    output_path: Path | str,
    *,
    goals: Goals | None = None,
    timeline: list | None = None,
    scorecard: list[dict] | None = None,
    title: str = "rvgen Coverage Dashboard",
    explanations: dict[str, str] | None = None,
) -> Path:
    """Render the dashboard and write it to ``output_path``."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dashboard_html(
        db, goals=goals, timeline=timeline,
        scorecard=scorecard, title=title,
        explanations=explanations,
    ))
    return p
