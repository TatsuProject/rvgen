"""Human-readable coverage reporter.

Renders a CoverageDB plus an optional Goals into a monospace-friendly
summary suitable for stdout / log files. JSON / YAML machine-readable
outputs are produced via the standard library (``json.dumps`` / ``yaml.dump``)
— no wrapper needed.
"""

from __future__ import annotations

from chipforge_inst_gen.coverage.cgf import Goals, missing_bins
from chipforge_inst_gen.coverage.collectors import ALL_COVERGROUPS, CoverageDB


def render_report(db: CoverageDB, goals: Goals | None = None, *, top: int = 15) -> str:
    """Return a multi-line report string.

    Parameters
    ----------
    db : CoverageDB
        The observed coverage.
    goals : Goals, optional
        If provided, adds a pass/fail column and a "missing bins" summary.
    top : int, default 15
        For covergroups with many bins (e.g. opcode_cg has 485 possible
        bins), show the top-N most-hit bins and summarise the rest.
    """
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append("Coverage Report")
    lines.append("=" * 72)

    total_bins_hit = sum(len(b) for b in db.values())
    total_hits = sum(sum(b.values()) for b in db.values())
    lines.append(f"covergroups: {len(db)}    unique bins hit: {total_bins_hit}    total samples: {total_hits}")
    lines.append("")

    miss = missing_bins(db, goals) if goals else {}

    for cg in ALL_COVERGROUPS:
        bins = db.get(cg, {})
        goal_bins = goals.covergroup(cg) if goals else {}

        total_observed = sum(bins.values())
        n_unique = len(bins)

        if goals is not None:
            n_goal_bins = sum(1 for v in goal_bins.values() if v > 0)
            n_met = n_goal_bins - len(miss.get(cg, {}))
            status = f"{n_met}/{n_goal_bins} goals met" if n_goal_bins else ""
        else:
            status = ""

        header = f"[{cg}]  unique_bins={n_unique}  total_hits={total_observed}"
        if status:
            header += f"  {status}"
        lines.append(header)

        # Show the top bins (most-hit first).
        sorted_bins = sorted(bins.items(), key=lambda kv: -kv[1])
        shown = sorted_bins[:top]
        for bn, cnt in shown:
            req = goal_bins.get(bn, 0)
            if req > 0:
                flag = " " if cnt >= req else "!"
                lines.append(f"  {flag} {bn:<28s} {cnt:>7d} / {req}")
            else:
                lines.append(f"    {bn:<28s} {cnt:>7d}")

        if len(sorted_bins) > top:
            more = len(sorted_bins) - top
            lines.append(f"    ... ({more} more bins)")

        # Missing goal bins (goal > 0 but observed < required).
        cg_miss = miss.get(cg, {})
        if cg_miss:
            lines.append(f"  MISSING ({len(cg_miss)}):")
            for bn, (obs, req) in sorted(cg_miss.items()):
                lines.append(f"    ! {bn:<28s} {obs:>7d} / {req}")
        lines.append("")

    # Overall summary
    if goals is not None:
        if not miss:
            lines.append(">>> ALL GOALS MET <<<")
        else:
            total_missing_cg = len(miss)
            total_missing_bins = sum(len(v) for v in miss.values())
            lines.append(
                f">>> {total_missing_bins} bin(s) missing across {total_missing_cg} covergroup(s) <<<"
            )

    return "\n".join(lines)
