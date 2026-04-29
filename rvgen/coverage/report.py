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


# Covergroups grouped by tier — used for the per-tier health roll-up
# and for ordering the per-covergroup section so related groups stay
# adjacent. Verif teams typically want to see "ISA breadth" before
# "microarchitectural depth" before "value distributions".
_TIER_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ISA breadth", (
        "opcode_cg", "format_cg", "category_cg", "group_cg",
        "rs1_cg", "rs2_cg", "rd_cg", "fpr_cg", "vreg_cg",
    )),
    ("Operand patterns", (
        "imm_sign_cg", "imm_range_cg", "rs1_eq_rs2_cg", "rs1_eq_rd_cg",
        "rs1_rs2_cross_cg", "rd_rs1_cross_cg",
    )),
    ("Hazards / sequencing", (
        "hazard_cg", "category_transition_cg", "opcode_transition_cg",
    )),
    ("Memory / load-store", (
        "load_store_width_cg", "mem_align_cg", "load_store_offset_cg",
        "cache_line_cross_cg", "page_cross_cg",
    )),
    ("Branches", (
        "branch_direction_cg", "branch_taken_per_mnem_cg",
        "branch_distance_cg", "branch_pattern_cg",
    )),
    ("FP / CSR / privileged", (
        "fp_rm_cg", "csr_cg", "csr_access_cg", "csr_value_cg",
        "privilege_mode_cg", "exception_cg", "pc_reach_cg",
    )),
    ("Value distributions", (
        "rs_val_corner_cg", "rd_val_class_cg",
        "rs1_val_class_cg", "rs2_val_class_cg", "rs_val_class_cross_cg",
        "bit_activity_cg",
    )),
    ("Vector", (
        "vtype_cg", "vtype_dyn_cg",
        "vec_ls_addr_mode_cg", "vec_eew_cg", "vec_eew_vs_sew_cg",
        "vec_emul_cg", "vec_vm_cg", "vec_vm_category_cross_cg",
        "vec_amo_wd_cg", "vec_va_variant_cg",
        "vec_nfields_cg", "vec_seg_addr_mode_cross_cg",
        "vec_widening_narrowing_cg", "vec_crypto_subext_cg",
        "vec_sew_transition_cg", "vec_lmul_transition_cg",
        "vec_vtype_transition_cg", "vec_vstart_cg",
    )),
    ("Streams", (
        "directed_stream_cg",
    )),
    ("Crosses", (
        "fmt_category_cross", "category_group_cross",
    )),
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


def _bar(pct: float, width: int = 20) -> str:
    """Return an ASCII progress bar of width ``width`` for ``pct`` in [0,100]."""
    filled = int(round(pct / 100 * width))
    return "[" + "#" * filled + "·" * (width - filled) + "]"


def _cg_completion_pct(bins: dict, goal_bins: dict) -> float:
    """Return the per-covergroup completion percentage.

    If a goals entry exists for the covergroup, return the fraction of
    required bins met. Otherwise return ``len(bins) / max(len(bins), 1) * 100``
    which collapses to 100% if any bin was hit at all (covergroups
    without goals are "informational").
    """
    n_req = sum(1 for v in goal_bins.values() if v > 0)
    if n_req == 0:
        return 100.0 if bins else 0.0
    n_met = sum(1 for bn, req in goal_bins.items()
                if req > 0 and bins.get(bn, 0) >= req)
    return n_met / n_req * 100.0


def render_report(db: CoverageDB, goals: Goals | None = None, *, top: int = 15) -> str:
    """Return a multi-line report string.

    Now emits, in order:

    1. **Header** — totals + composite grade.
    2. **Tier roll-up** — completion bar per logical tier (ISA breadth,
       microarchitectural, etc.) with the per-tier missing-bin count.
    3. **Hotspots** — top-5 covergroups by *missing bin count*. Where
       the work is.
    4. **Per-covergroup detail** — bins + completion bar, grouped by
       tier so related groups stay adjacent.
    """
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append("Coverage Report")
    lines.append("=" * 72)

    total_bins_hit = sum(len(b) for b in db.values())
    total_hits = sum(sum(b.values()) for b in db.values())
    grade = compute_grade(db, goals)
    lines.append(
        f"covergroups: {len(db)}    "
        f"unique bins hit: {total_bins_hit}    "
        f"total samples: {total_hits}    "
        f"grade: {grade}/100"
    )

    miss = missing_bins(db, goals) if goals else {}

    if goals is not None:
        total_req = sum(1 for b in goals.data.values() for v in b.values() if v > 0)
        total_missing = sum(len(v) for v in miss.values())
        met = total_req - total_missing
        pct = (met / total_req * 100) if total_req else 100.0
        lines.append(
            f"required bins: {total_req}    met: {met}    missing: {total_missing}    "
            f"{_bar(pct)} {pct:5.1f}%"
        )
    lines.append("")

    # ---- Tier roll-up ----
    if goals is not None:
        lines.append("Tier roll-up")
        lines.append("-" * 72)
        for tier_name, cgs in _TIER_ORDER:
            tier_req = 0
            tier_miss = 0
            for cg in cgs:
                gb = goals.covergroup(cg)
                tier_req += sum(1 for v in gb.values() if v > 0)
                tier_miss += len(miss.get(cg, {}))
            if tier_req == 0:
                # Nothing graded; skip rather than render meaningless bar.
                continue
            tier_pct = (tier_req - tier_miss) / tier_req * 100
            lines.append(
                f"  {tier_name:<24s} {_bar(tier_pct)} "
                f"{tier_pct:5.1f}%   "
                f"{tier_req - tier_miss}/{tier_req} bins met "
                f"({tier_miss} missing)"
            )
        lines.append("")

        # ---- Hotspots ----
        hotspots = sorted(
            ((cg, len(bn)) for cg, bn in miss.items() if bn),
            key=lambda kv: -kv[1],
        )[:5]
        if hotspots:
            lines.append("Hotspots — covergroups with the most missing bins")
            lines.append("-" * 72)
            for cg, n_miss in hotspots:
                gb = goals.covergroup(cg)
                n_req = sum(1 for v in gb.values() if v > 0)
                pct = (n_req - n_miss) / n_req * 100 if n_req else 0
                lines.append(
                    f"  {cg:<32s} {_bar(pct, 12)}  "
                    f"{n_miss:>3d} missing ({n_req - n_miss}/{n_req})"
                )
            lines.append("")

    # ---- Per-covergroup detail (in tier order) ----
    seen: set[str] = set()
    cg_order: list[str] = []
    for _, cgs in _TIER_ORDER:
        for cg in cgs:
            if cg not in seen:
                cg_order.append(cg)
                seen.add(cg)
    # Anything in ALL_COVERGROUPS not in any tier — append at the end.
    for cg in ALL_COVERGROUPS:
        if cg not in seen:
            cg_order.append(cg)

    for cg in cg_order:
        bins = db.get(cg, {})
        goal_bins = goals.covergroup(cg) if goals else {}

        total_observed = sum(bins.values())
        n_unique = len(bins)

        if goals is not None:
            n_goal_bins = sum(1 for v in goal_bins.values() if v > 0)
            cg_miss = miss.get(cg, {})
            n_met = n_goal_bins - len(cg_miss)
            pct = _cg_completion_pct(bins, goal_bins)
            status_line = (
                f" {_bar(pct, 12)} {pct:5.1f}%   "
                f"goals met: {n_met}/{n_goal_bins}   "
                f"missing: {len(cg_miss)}"
            )
        else:
            cg_miss = {}
            status_line = ""

        # Always emit a header per covergroup so the report acts as a
        # complete catalogue (verif teams scan for "[<cg>]" patterns).
        lines.append(
            f"[{cg}]  unique_bins={n_unique}  total_hits={total_observed}{status_line}"
        )
        if not bins and not goal_bins:
            lines.append("    (empty)")
            lines.append("")
            continue

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
            total_missing_cg = len([1 for v in miss.values() if v])
            total_missing_bins = sum(len(v) for v in miss.values())
            lines.append(
                f">>> {total_missing_bins} bin(s) missing "
                f"across {total_missing_cg} covergroup(s) <<<"
            )

    return "\n".join(lines)
