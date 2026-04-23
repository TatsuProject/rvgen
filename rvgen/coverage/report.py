"""Human-readable coverage reporter.

Renders a CoverageDB plus an optional Goals into a monospace-friendly
summary suitable for stdout / log files. JSON / YAML machine-readable
outputs are produced via the standard library (``json.dumps`` / ``yaml.dump``)
— no wrapper needed.
"""

from __future__ import annotations

from rvgen.coverage.cgf import Goals, missing_bins
from rvgen.coverage.collectors import (
    ALL_COVERGROUPS, CG_CATEGORY, CG_HAZARD, CG_OPCODE, CoverageDB,
)


def compute_grade(db: CoverageDB, goals: Goals | None = None) -> int:
    """Return a composite 0-100 coverage quality grade.

    Components:

    - **60% goals met** — primary signal. If no goals file is supplied,
      this component scores 1.0 (no penalty).
    - **20% hazard balance** — min(raw, war, waw) / max(raw, war, waw).
      1.0 means each hazard type is equally represented; 0.0 means at
      least one hazard type wasn't observed at all.
    - **20% opcode diversity** — fraction of "a reasonable regression"
      (60 distinct static opcode bins) that are covered.

    Returns an integer in [0, 100]. Designed to be CI-friendly — pass
    through GITHUB_OUTPUT for PR badges.
    """
    # Goals component (60%)
    if goals is not None:
        required = sum(1 for b in goals.data.values() for v in b.values() if v > 0)
        if required > 0:
            miss = missing_bins(db, goals)
            missing_count = sum(len(v) for v in miss.values())
            goals_score = max(0.0, (required - missing_count) / required)
        else:
            goals_score = 1.0
    else:
        goals_score = 1.0

    # Hazard balance (20%)
    hz = db.get(CG_HAZARD, {})
    raws = [hz.get("raw", 0), hz.get("war", 0), hz.get("waw", 0)]
    if min(raws) == 0:
        hazard_score = 0.0
    else:
        hazard_score = min(raws) / max(raws)

    # Opcode diversity (20%) — count distinct static (non-__dyn) opcode bins.
    opc = db.get(CG_OPCODE, {})
    static_count = sum(
        1 for k, v in opc.items() if v > 0 and not k.endswith("__dyn")
    )
    opcode_score = min(1.0, static_count / 60.0)

    grade = 0.6 * goals_score + 0.2 * hazard_score + 0.2 * opcode_score
    return int(round(grade * 100))


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
    grade = compute_grade(db, goals)
    lines.append(f"covergroups: {len(db)}    unique bins hit: {total_bins_hit}    total samples: {total_hits}    grade: {grade}/100")
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
