"""Self-contained interactive coverage dashboard.

Generates one HTML file with vanilla SVG charts + JS — no external
CDN, no plotly, no chart.js. Works offline. Designed to drop into a
CI artifact upload step or a `python -m http.server` directory.

Sections (top to bottom):

1. **Summary tiles** — total covergroups / bins hit / % met / samples.
2. **Per-subsystem bar chart** — same buckets as the `scorecard` CLI:
   RV32I+M, Vector, Crypto, Bitmanip, Privileged, Modern checkbox,
   etc. Each bar shows % met; missing-bin counts inline.
3. **Convergence timeline** — line chart of new-bins-per-seed if a
   timeline JSON is supplied. Hovering over a point shows the seed
   number + new-bin count.
4. **Top missing bins** — the 25 highest-required missing bins, with
   covergroup + required count + cov-explain hint when one applies.
5. **Per-covergroup details** — collapsible table with hit/required
   per bin, color-coded by status (met/partial/missed).

The whole page is driven by a small embedded ``<script>`` that:
- toggles section open/close
- filters covergroups by name
- redraws the convergence chart on resize

Public API:

  dashboard_html(db, goals=None, timeline=None, scorecard=None) -> str
      Render the full HTML.
  write_dashboard(db, output_path, ...) -> Path
      Write to disk.
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path

from rvgen.coverage.cgf import Goals, missing_bins
from rvgen.coverage.collectors import CoverageDB


# ---------------------------------------------------------------------------
# CSS — one inline blob so the file ships with no external deps.
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #0d1117;
  --bg-sec: #161b22;
  --bg-card: #1f242c;
  --fg: #e6edf3;
  --fg-mute: #8b949e;
  --accent: #58a6ff;
  --good: #3fb950;
  --warn: #d29922;
  --bad: #f85149;
  --grid: #30363d;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont,
               'Segoe UI', Roboto, sans-serif;
  font-size: 14px;
}
header {
  padding: 24px 32px 16px;
  border-bottom: 1px solid var(--grid);
}
header h1 {
  margin: 0 0 4px 0;
  font-size: 24px;
  font-weight: 600;
}
header .meta { color: var(--fg-mute); font-size: 12px; }

main { padding: 24px 32px; max-width: 1400px; margin: 0 auto; }
section { margin-bottom: 32px; }
h2 {
  font-size: 16px;
  margin: 0 0 12px 0;
  color: var(--fg-mute);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}
.tile {
  background: var(--bg-card);
  padding: 16px 20px;
  border-radius: 8px;
  border: 1px solid var(--grid);
}
.tile .label { color: var(--fg-mute); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
.tile .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
.tile .pct-bar {
  height: 4px; background: var(--grid); border-radius: 2px; margin-top: 8px;
  overflow: hidden;
}
.tile .pct-fill { height: 100%; background: var(--good); transition: width 0.5s; }

.scorecard svg { width: 100%; max-width: 100%; height: auto; display: block; }
.scorecard text { fill: var(--fg); font-family: inherit; }
.scorecard .bar-bg { fill: var(--grid); }
.scorecard .bar-fill-good { fill: var(--good); }
.scorecard .bar-fill-warn { fill: var(--warn); }
.scorecard .bar-fill-bad { fill: var(--bad); }
.scorecard .label-text { font-size: 11px; }

.timeline svg { width: 100%; height: 220px; display: block; }
.timeline .axis { stroke: var(--grid); stroke-width: 1; }
.timeline .axis-text { fill: var(--fg-mute); font-size: 10px; }
.timeline .line { fill: none; stroke: var(--accent); stroke-width: 2; }
.timeline .point { fill: var(--accent); }
.timeline .point:hover { fill: var(--good); cursor: pointer; }

.missing-table, .cg-table {
  width: 100%; border-collapse: collapse;
}
.missing-table th, .cg-table th, .missing-table td, .cg-table td {
  padding: 6px 12px; text-align: left;
  border-bottom: 1px solid var(--grid);
  font-size: 13px;
}
.missing-table th, .cg-table th {
  color: var(--fg-mute); font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.04em; font-size: 11px;
}
.missing-table tr:hover, .cg-table tr:hover { background: var(--bg-sec); }

.cg-list details {
  background: var(--bg-card);
  border: 1px solid var(--grid);
  border-radius: 6px;
  margin-bottom: 8px;
  padding: 8px 16px;
}
.cg-list details summary {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
}
.cg-list .cg-name { font-weight: 500; }
.cg-list .cg-meta { color: var(--fg-mute); font-size: 12px; flex: 1; }
.cg-list .badge {
  display: inline-block; padding: 2px 8px;
  border-radius: 4px; font-size: 11px;
}
.cg-list .badge.met { background: var(--good); color: #fff; }
.cg-list .badge.partial { background: var(--warn); color: #000; }
.cg-list .badge.missed { background: var(--bad); color: #fff; }
.cg-list .badge.untracked { background: var(--grid); color: var(--fg-mute); }
.cg-list .mini-bar {
  display: inline-block;
  width: 80px; height: 6px; background: var(--grid); border-radius: 3px;
  position: relative;
  overflow: hidden;
}
.cg-list .mini-bar::after {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0;
  width: var(--pct); background: var(--good);
}

.filter-bar {
  display: flex; gap: 12px; align-items: center; margin-bottom: 16px;
}
.filter-bar input {
  flex: 1; padding: 8px 12px;
  background: var(--bg-card); color: var(--fg);
  border: 1px solid var(--grid); border-radius: 6px;
  font-family: inherit; font-size: 13px;
}
.filter-bar button {
  padding: 8px 16px;
  background: var(--bg-card); color: var(--fg);
  border: 1px solid var(--grid); border-radius: 6px;
  cursor: pointer;
}
.filter-bar button:hover { border-color: var(--accent); }
"""


# ---------------------------------------------------------------------------
# JS — bundled inline.
# ---------------------------------------------------------------------------

_JS = """
function filterCgs(needle) {
  needle = needle.toLowerCase();
  document.querySelectorAll('.cg-list details').forEach(d => {
    const name = d.dataset.name.toLowerCase();
    d.style.display = (name.indexOf(needle) >= 0) ? '' : 'none';
  });
}
function toggleAll(open) {
  const sel = (open === undefined)
    ? !document.querySelector('.cg-list details[open]')
    : open;
  document.querySelectorAll('.cg-list details').forEach(d => {
    if (sel) d.setAttribute('open', '');
    else d.removeAttribute('open');
  });
}
"""


# ---------------------------------------------------------------------------
# SVG bar-chart for the scorecard.
# ---------------------------------------------------------------------------


def _scorecard_svg(scorecard: list[dict], width: int = 900,
                   bar_height: int = 22, gap: int = 8) -> str:
    """Render the per-subsystem scorecard as an SVG bar chart.

    ``scorecard`` is the same shape the ``scorecard`` CLI produces:
    list of dicts with ``subsystem``, ``met``, ``required``,
    ``percent`` keys. Empty subsystems (no goals) are dropped.
    """
    rows = [r for r in scorecard if r["required"] > 0]
    if not rows:
        return "<p style='color:var(--fg-mute)'>No subsystems with required bins.</p>"

    rows.sort(key=lambda r: r["percent"])
    total_height = len(rows) * (bar_height + gap) + 16
    label_pad = 180
    bar_pad_right = 80
    bar_w = width - label_pad - bar_pad_right

    out: list[str] = []
    out.append(f'<svg viewBox="0 0 {width} {total_height}" xmlns="http://www.w3.org/2000/svg">')
    for i, row in enumerate(rows):
        y = i * (bar_height + gap) + 8
        cx = (label_pad + bar_w + 8)
        pct = row["percent"]
        fill_class = ("bar-fill-good" if pct >= 80
                      else "bar-fill-warn" if pct >= 40
                      else "bar-fill-bad")
        # Subsystem label.
        out.append(
            f'<text class="label-text" x="{label_pad - 8}" y="{y + bar_height // 2 + 4}" '
            f'text-anchor="end">{_html.escape(row["subsystem"])}</text>'
        )
        # Bar background.
        out.append(
            f'<rect class="bar-bg" x="{label_pad}" y="{y}" '
            f'width="{bar_w}" height="{bar_height}" rx="3" />'
        )
        # Bar fill.
        fill_w = bar_w * pct / 100
        out.append(
            f'<rect class="{fill_class}" x="{label_pad}" y="{y}" '
            f'width="{fill_w:.1f}" height="{bar_height}" rx="3" />'
        )
        # Right-side stats.
        stats = f'{row["met"]}/{row["required"]}  {pct:.1f}%'
        out.append(
            f'<text class="label-text" x="{cx}" y="{y + bar_height // 2 + 4}">{stats}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# SVG line-chart for the timeline.
# ---------------------------------------------------------------------------


def _timeline_svg(timeline: list[dict], width: int = 900, height: int = 200) -> str:
    """Line chart of new-bins-per-seed.

    ``timeline`` items are dicts with ``seed`` and ``new_bins`` keys.
    """
    if not timeline:
        return "<p style='color:var(--fg-mute)'>No timeline data.</p>"

    pts = [(t.get("seed", i), t.get("new_bins", 0))
           for i, t in enumerate(timeline)]
    if not pts:
        return ""

    margin_l, margin_r = 50, 16
    margin_t, margin_b = 16, 28
    chart_w = width - margin_l - margin_r
    chart_h = height - margin_t - margin_b

    max_y = max(p[1] for p in pts) or 1
    n = len(pts)
    if n == 1:
        # Avoid div-by-zero on single-point timeline.
        x_step = chart_w
    else:
        x_step = chart_w / (n - 1)

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

    out: list[str] = []
    out.append(f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">')
    # Y axis
    out.append(f'<line class="axis" x1="{margin_l}" y1="{margin_t}" '
               f'x2="{margin_l}" y2="{margin_t + chart_h}" />')
    # X axis
    out.append(f'<line class="axis" x1="{margin_l}" y1="{margin_t + chart_h}" '
               f'x2="{margin_l + chart_w}" y2="{margin_t + chart_h}" />')
    # Y axis labels
    out.append(f'<text class="axis-text" x="{margin_l - 6}" y="{margin_t + 4}" '
               f'text-anchor="end">{max_y}</text>')
    out.append(f'<text class="axis-text" x="{margin_l - 6}" y="{margin_t + chart_h + 4}" '
               f'text-anchor="end">0</text>')
    # X axis labels (first/last seed)
    out.append(f'<text class="axis-text" x="{margin_l}" '
               f'y="{margin_t + chart_h + 18}">seed {pts[0][0]}</text>')
    if n > 1:
        out.append(f'<text class="axis-text" x="{margin_l + chart_w}" '
                   f'y="{margin_t + chart_h + 18}" text-anchor="end">'
                   f'seed {pts[-1][0]}</text>')
    # Polyline + points
    out.append(f'<polyline class="line" points="{" ".join(poly_pts)}" />')
    out.extend(circles)
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Top-level renderer.
# ---------------------------------------------------------------------------


def dashboard_html(
    db: CoverageDB,
    goals: Goals | None = None,
    timeline: list | None = None,
    scorecard: list[dict] | None = None,
    title: str = "rvgen Coverage Dashboard",
) -> str:
    """Render the full self-contained HTML dashboard."""
    miss = missing_bins(db, goals) if goals else {}

    total_cgs = len([cg for cg, bins in db.items() if bins])
    total_bins_hit = sum(len(b) for b in db.values())
    total_hits = sum(sum(b.values()) for b in db.values())

    if goals:
        total_req = sum(1 for b in goals.data.values() for v in b.values() if v > 0)
        total_missing = sum(len(v) for v in miss.values())
        met = total_req - total_missing
        pct = (met / total_req * 100) if total_req else 100.0
    else:
        total_req = 0
        met = 0
        pct = 100.0

    out: list[str] = []
    out.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    out.append(f"<title>{_html.escape(title)}</title>")
    out.append(f"<style>{_CSS}</style>")
    out.append("</head><body>")

    out.append(f"<header><h1>{_html.escape(title)}</h1>")
    out.append(f"<div class='meta'>{total_cgs} active covergroup(s) · "
               f"{total_bins_hit} unique bin(s) hit · {total_hits} total samples</div>"
               "</header>")

    out.append("<main>")

    # Summary tiles
    out.append("<section><h2>Summary</h2><div class='tiles'>")
    out.append(_tile("Covergroups", total_cgs))
    out.append(_tile("Bins hit", total_bins_hit))
    out.append(_tile("Total samples", total_hits))
    if goals:
        out.append(_tile("Goals met",
                         f"{met}/{total_req}",
                         pct=pct))
    out.append("</div></section>")

    # Scorecard chart
    if scorecard:
        out.append("<section class='scorecard'><h2>Per-subsystem coverage</h2>")
        out.append(_scorecard_svg(scorecard))
        out.append("</section>")

    # Timeline chart
    if timeline:
        out.append("<section class='timeline'><h2>Convergence timeline</h2>")
        out.append(_timeline_svg(timeline))
        out.append("</section>")

    # Top missing bins
    if miss:
        out.append("<section><h2>Top missing bins</h2>")
        rows = []
        for cg, bins in miss.items():
            for bn in bins:
                req = goals.covergroup(cg).get(bn, 0) if goals else 0
                if req > 0:
                    rows.append((cg, bn, req))
        rows.sort(key=lambda r: -r[2])
        rows = rows[:25]
        if rows:
            out.append("<table class='missing-table'><thead><tr>"
                       "<th>Covergroup</th><th>Bin</th><th>Required</th>"
                       "</tr></thead><tbody>")
            for cg, bn, req in rows:
                out.append(
                    f"<tr><td>{_html.escape(cg)}</td>"
                    f"<td>{_html.escape(bn)}</td>"
                    f"<td>{req}</td></tr>"
                )
            out.append("</tbody></table>")
        else:
            out.append("<p style='color:var(--good)'>All required bins met!</p>")
        out.append("</section>")

    # Per-covergroup details
    out.append("<section><h2>Covergroups</h2>")
    out.append(
        "<div class='filter-bar'>"
        "<input placeholder='Filter covergroups...' oninput='filterCgs(this.value)' />"
        "<button onclick='toggleAll(true)'>Expand all</button>"
        "<button onclick='toggleAll(false)'>Collapse all</button>"
        "</div>"
    )
    out.append("<div class='cg-list'>")
    for cg in sorted(db):
        bins = db[cg]
        goal_bins = goals.covergroup(cg) if goals else {}
        cg_miss = miss.get(cg, {}) if goals else {}
        if not bins and not goal_bins:
            continue

        if goal_bins:
            n_req = sum(1 for v in goal_bins.values() if v > 0)
            n_miss = len(cg_miss)
            cg_pct = ((n_req - n_miss) / n_req * 100) if n_req else 100.0
            if n_miss == 0:
                badge = "met"; badge_text = f"MET {n_req}/{n_req}"
            elif n_miss == n_req:
                badge = "missed"; badge_text = f"MISS 0/{n_req}"
            else:
                badge = "partial"; badge_text = f"PART {n_req - n_miss}/{n_req}"
        else:
            cg_pct = 100.0
            badge = "untracked"; badge_text = "no goals"

        meta = f"{len(bins)} bin(s), {sum(bins.values())} hits"
        if cg_miss:
            meta += f" · <b>{len(cg_miss)}</b> missing"

        out.append(f"<details data-name='{_html.escape(cg)}'>")
        out.append(
            "<summary>"
            f"<span class='cg-name'>{_html.escape(cg)}</span>"
            f"<span class='cg-meta'>{meta}</span>"
            f"<span class='mini-bar' style='--pct: {cg_pct:.1f}%'></span>"
            f"<span class='badge {badge}'>{badge_text}</span>"
            "</summary>"
        )
        out.append("<table class='cg-table'><thead><tr>"
                   "<th>Bin</th><th>Observed</th><th>Required</th>"
                   "</tr></thead><tbody>")
        all_bins = set(bins) | set(goal_bins)
        for bn in sorted(all_bins, key=lambda b: -bins.get(b, 0)):
            obs = bins.get(bn, 0)
            req = goal_bins.get(bn, "")
            out.append(
                f"<tr><td>{_html.escape(bn)}</td>"
                f"<td>{obs}</td><td>{req}</td></tr>"
            )
        out.append("</tbody></table></details>")
    out.append("</div></section>")

    out.append("</main>")
    out.append(f"<script>{_JS}</script>")
    out.append("</body></html>")
    return "\n".join(out)


def _tile(label: str, value, pct: float | None = None) -> str:
    pct_html = ""
    if pct is not None:
        pct_html = (
            f"<div class='pct-bar'><div class='pct-fill' "
            f"style='width: {pct:.1f}%'></div></div>"
        )
    return (
        "<div class='tile'>"
        f"<div class='label'>{_html.escape(label)}</div>"
        f"<div class='value'>{_html.escape(str(value))}</div>"
        f"{pct_html}"
        "</div>"
    )


def write_dashboard(
    db: CoverageDB,
    output_path: Path | str,
    *,
    goals: Goals | None = None,
    timeline: list | None = None,
    scorecard: list[dict] | None = None,
    title: str = "rvgen Coverage Dashboard",
) -> Path:
    """Render the dashboard and write it to ``output_path``."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dashboard_html(
        db, goals=goals, timeline=timeline,
        scorecard=scorecard, title=title,
    ))
    return p
