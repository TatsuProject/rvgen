"""Coverage analysis CLI — diff / merge / export / attribute.

Invoke as::

    python -m rvgen.coverage.tools <subcommand> ...

Subcommands:

- ``merge``     : merge two or more coverage JSONs into one.
- ``diff``      : report what's new in A vs B (bins added, deltas).
- ``attribute`` : given a set of per-seed coverage JSONs, show which
                  seed first closed each required bin (ordered input
                  means the *earliest* contributor wins).
- ``export``    : dump coverage as CSV or a self-contained HTML page.
- ``report``    : render the text report (same as the ``cov`` step).

These operations treat a coverage JSON as an opaque dict-of-dict from
covergroup name to bin-count dict; compatible with the collector DB
and riscv-isac-style observed files (as long as they use the same
covergroup-name / bin-name conventions — crosses use ``a__b`` naming).
"""

from __future__ import annotations

import argparse
import csv
import html as _html
import json
import sys
from pathlib import Path
from typing import Iterable

from rvgen.coverage.cgf import Goals, load_goals, load_goals_layered, missing_bins
from rvgen.coverage.collectors import CoverageDB, merge, new_db
from rvgen.coverage.report import render_report


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> CoverageDB:
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level JSON must be an object")
    # Coerce inner values to dicts of int (be forgiving of floats from
    # downstream tools).
    out: CoverageDB = {}
    for cg, bins in raw.items():
        if not isinstance(bins, dict):
            continue
        out[cg] = {str(bn): int(cnt) for bn, cnt in bins.items()}
    return out


def _write(path: Path, db: CoverageDB) -> None:
    with open(path, "w") as f:
        json.dump(db, f, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_merge(args: argparse.Namespace) -> int:
    out_db: CoverageDB = new_db()
    for p in args.inputs:
        src = _read(Path(p))
        merge(out_db, src)
    _write(Path(args.output), out_db)
    print(f"merged {len(args.inputs)} file(s) -> {args.output}")
    return 0


def _compute_diff(a: CoverageDB, b: CoverageDB) -> dict[str, dict[str, int]]:
    """Return new bins and delta counts: ``b - a`` per bin.

    Output shape: ``{cg: {bin_name: delta}}``. A delta is positive if b has
    more hits than a; negative if fewer; zero bins are omitted.
    """
    diff: dict[str, dict[str, int]] = {}
    all_cgs = set(a) | set(b)
    for cg in sorted(all_cgs):
        a_bins = a.get(cg, {})
        b_bins = b.get(cg, {})
        all_bins = set(a_bins) | set(b_bins)
        cg_diff: dict[str, int] = {}
        for bn in sorted(all_bins):
            delta = b_bins.get(bn, 0) - a_bins.get(bn, 0)
            if delta != 0:
                cg_diff[bn] = delta
        if cg_diff:
            diff[cg] = cg_diff
    return diff


def cmd_diff(args: argparse.Namespace) -> int:
    a = _read(Path(args.a))
    b = _read(Path(args.b))
    diff = _compute_diff(a, b)

    if args.json:
        Path(args.json).write_text(json.dumps(diff, indent=2, sort_keys=True))
        print(f"wrote diff -> {args.json}")
    else:
        total_new_bins = sum(
            1 for cg_diff in diff.values() for delta in cg_diff.values() if delta > 0
        )
        total_lost_bins = sum(
            1 for cg_diff in diff.values() for delta in cg_diff.values() if delta < 0
        )
        print(f"=== coverage diff: {args.a} -> {args.b} ===")
        print(f"    {len(diff)} covergroups changed")
        print(f"    +{total_new_bins} new/increased bins")
        print(f"    -{total_lost_bins} decreased bins")
        print()
        for cg, cg_diff in diff.items():
            print(f"  [{cg}]")
            for bn, delta in cg_diff.items():
                sign = "+" if delta > 0 else ""
                print(f"    {bn:<32s} {sign}{delta}")
    return 0


def cmd_attribute(args: argparse.Namespace) -> int:
    """First-closer attribution across a sequence of per-seed coverage files.

    For each required bin in ``--goals``, prints which input file first
    reached the required count when merging in the given order. Useful to
    identify seeds that contributed unique coverage vs redundant seeds.
    """
    goals = load_goals(args.goals)
    accumulated: CoverageDB = new_db()
    first_closer: dict[tuple[str, str], str] = {}
    closed_counts: list[tuple[str, int]] = []

    for path in args.inputs:
        src = _read(Path(path))
        before = {cg: dict(bins) for cg, bins in accumulated.items()}
        merge(accumulated, src)
        closed_this = 0
        for cg, bins in goals.data.items():
            for bn, req in bins.items():
                if req <= 0:
                    continue
                key = (cg, bn)
                if key in first_closer:
                    continue
                if accumulated.get(cg, {}).get(bn, 0) >= req:
                    first_closer[key] = str(path)
                    closed_this += 1
        closed_counts.append((str(path), closed_this))

    total_closed = len(first_closer)
    total_req = sum(1 for b in goals.data.values() for v in b.values() if v > 0)
    print(f"=== coverage attribute: {args.goals} over {len(args.inputs)} file(s) ===")
    print(f"    {total_closed}/{total_req} required bins closed")
    print()
    print("Per-input contribution (bins this file was first to close):")
    for path, n in closed_counts:
        print(f"    {path}: {n}")
    print()
    not_closed = [
        (cg, bn)
        for cg, bins in goals.data.items()
        for bn, req in bins.items()
        if req > 0 and (cg, bn) not in first_closer
    ]
    if not_closed:
        print(f"Not closed ({len(not_closed)}):")
        for cg, bn in not_closed:
            print(f"    {cg}.{bn}")
    return 0 if total_closed == total_req else 1


def cmd_export(args: argparse.Namespace) -> int:
    db = _read(Path(args.input))
    if args.csv:
        _export_csv(db, Path(args.csv))
        print(f"wrote CSV -> {args.csv}")
    if args.html:
        timeline = None
        timeline_path = getattr(args, "timeline", None)
        if timeline_path:
            try:
                with open(timeline_path) as f:
                    timeline = json.load(f)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: failed to load --timeline {timeline_path!r}: {exc}",
                      file=sys.stderr)
        _export_html(db, Path(args.html),
                      goals=load_goals(args.goals) if args.goals else None,
                      timeline=timeline)
        print(f"wrote HTML -> {args.html}")
    if not args.csv and not args.html:
        print("no output format selected (pass --csv <path> and/or --html <path>)",
              file=sys.stderr)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    db = _read(Path(args.input))
    goals = load_goals(args.goals) if args.goals else None
    print(render_report(db, goals))
    return 0


def cmd_baseline_check(args: argparse.Namespace) -> int:
    """Gate CI: every bin hit by ``--baseline`` must also be hit in ``input``.

    Coverage is expected to be monotonic for a healthy regression — a
    change that causes previously-observed bins to disappear is almost
    always a regression (goal removed, stream broken, test disabled).
    This subcommand makes that guarantee testable:

        python -m rvgen.coverage.tools baseline-check \\
            --baseline tests/golden/coverage_golden.json \\
            run/coverage.json

    Exit 0 if every baseline-hit bin is still hit. Exit 1 otherwise,
    listing the lost bins per covergroup.
    """
    observed = _read(Path(args.input))
    baseline = _read(Path(args.baseline))

    lost: dict[str, list[str]] = {}
    for cg, bins in baseline.items():
        obs_bins = observed.get(cg, {})
        for bn, base_cnt in bins.items():
            if base_cnt > 0 and obs_bins.get(bn, 0) == 0:
                lost.setdefault(cg, []).append(bn)

    if lost:
        total = sum(len(v) for v in lost.values())
        print(f"baseline-check: REGRESSION — {total} bin(s) lost across "
              f"{len(lost)} covergroup(s)")
        for cg, bns in sorted(lost.items()):
            print(f"  [{cg}]")
            for bn in sorted(bns):
                print(f"    - {bn}")
        return 1
    print("baseline-check: OK — all previously-hit bins still hit")
    return 0


def cmd_lint_goals(args: argparse.Namespace) -> int:
    """Static-check a goals YAML against the known covergroup catalogue.

    Catches typos ('opcode_cg.AD' instead of 'ADD') that would otherwise
    silently fail — an unknown bin name never gets a hit, so the goal is
    impossible to meet and the user gets confusing "missing" reports
    forever.

    Strict levels:
    - ``--strict=warn``  (default): unknown bins print warnings, exit 0.
    - ``--strict=error``: unknown bins → non-zero exit (CI gate).

    Unknown *covergroups* always warn (they're legal — a user might sample
    their own — but usually indicate a typo).
    """
    from rvgen.coverage.collectors import ALL_COVERGROUPS
    from rvgen.isa.enums import (
        FRoundingMode, PrivilegedReg, RiscvFpr, RiscvInstrCategory,
        RiscvInstrFormat, RiscvInstrGroup, RiscvInstrName, RiscvReg, RiscvVreg,
    )

    # Known bins per covergroup. Crosses (a__b) aren't exhaustively checked
    # — we only verify that each side is plausibly a valid bin.
    instr_names = {n.name for n in RiscvInstrName}
    reg_names = {r.name for r in RiscvReg}
    fpr_names = {r.name for r in RiscvFpr}
    vreg_names = {r.name for r in RiscvVreg}
    format_names = {f.name for f in RiscvInstrFormat}
    category_names = {c.name for c in RiscvInstrCategory}
    group_names = {g.name for g in RiscvInstrGroup}
    csr_names = {c.name for c in PrivilegedReg}
    rm_names = {r.name for r in FRoundingMode}

    known_bins: dict[str, set[str]] = {
        "opcode_cg": instr_names,
        "format_cg": format_names,
        "category_cg": category_names,
        "group_cg": group_names,
        "rs1_cg": reg_names,
        "rs2_cg": reg_names,
        "rd_cg": reg_names,
        "imm_sign_cg": {"pos", "neg", "zero"},
        "imm_range_cg": {"none", "zero", "one", "all_ones", "min_signed",
                          "max_signed", "walking_one", "walking_zero",
                          "alternating", "small", "generic"},
        "hazard_cg": {"raw", "war", "waw", "none"},
        "csr_cg": csr_names,
        "fp_rm_cg": rm_names,
        "fpr_cg": fpr_names,
        "vreg_cg": vreg_names,
        "mem_align_cg": {"byte_aligned", "half_aligned", "half_unaligned",
                          "word_aligned", "word_unaligned",
                          "dword_aligned", "dword_unaligned"},
        "load_store_width_cg": {"byte", "half", "word", "dword"},
        "load_store_offset_cg": {"zero", "pos_small", "pos_medium", "pos_large",
                                   "neg_small", "neg_medium", "neg_large"},
        "rs1_eq_rs2_cg": {"equal", "distinct"},
        "rs1_eq_rd_cg": {"equal", "distinct"},
        "branch_direction_cg": {"taken", "not_taken"},
        "privilege_mode_cg": {"M_entered", "M_return", "S_return", "U_return",
                                "M_mode", "S_mode", "U_mode"},
        "exception_cg": {"trap_entered"},
        # Microarchitectural-relevant covergroups.
        "cache_line_cross_cg": {
            f"{prefix}_w{w}" for prefix in ("cross", "near_end", "in_line")
            for w in (2, 4, 8)
        },
        "page_cross_cg": (
            {"in_page"}
            | {f"cross_w{w}" for w in (2, 4, 8)}
        ),
        "branch_distance_cg": {
            "zero",
            "fwd_short", "fwd_medium", "fwd_long", "fwd_huge",
            "bwd_short", "bwd_medium", "bwd_long", "bwd_huge",
        },
        "branch_pattern_cg": {
            "TTT", "TTN", "TNT", "TNN",
            "NTT", "NTN", "NNT", "NNN",
        },
        # Value-class covergroups (riscv-isac val_comb style). Keep the
        # set the same on rs1/rs2/rd since the classifier is the same.
        # Also accept these names as the bins of the cross covergroup
        # via membership testing in the lint loop (special-cased below).
    }
    from rvgen.coverage.collectors import VALUE_CLASS_BINS
    _val_class_bins = set(VALUE_CLASS_BINS)
    for _cg in ("rs1_val_class_cg", "rs2_val_class_cg", "rd_val_class_cg"):
        known_bins[_cg] = set(_val_class_bins)
    known_bins["rs_val_class_cross_cg"] = {
        f"{a}__{b}" for a in _val_class_bins for b in _val_class_bins
    }

    goals = load_goals(args.input)
    unknown_cgs: list[str] = []
    bad_bins: list[tuple[str, str]] = []

    for cg, bins in goals.data.items():
        if cg not in ALL_COVERGROUPS:
            unknown_cgs.append(cg)
            continue
        valid = known_bins.get(cg)
        if valid is None:
            continue  # cross or dynamic — skip detailed check
        for bn in bins:
            if bn not in valid:
                bad_bins.append((cg, bn))

    print(f"=== lint-goals: {args.input} ===")
    if not unknown_cgs and not bad_bins:
        print("OK — all covergroup names and bin names are recognised.")
        return 0
    if unknown_cgs:
        print(f"\nUnknown covergroup(s) ({len(unknown_cgs)}):")
        for cg in sorted(unknown_cgs):
            print(f"    {cg}")
    if bad_bins:
        print(f"\nUnknown bin name(s) ({len(bad_bins)}):")
        for cg, bn in sorted(bad_bins):
            # Try to offer a suggestion for common typos.
            valid = known_bins.get(cg, set())
            suggestion = ""
            for good in sorted(valid):
                if bn.upper() == good.upper() or good.startswith(bn.upper()):
                    suggestion = f" (did you mean {good!r}?)"
                    break
            print(f"    {cg}.{bn}{suggestion}")
    if args.strict == "error" and (unknown_cgs or bad_bins):
        return 1
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Render an ASCII trend chart from a JSONL history file.

    The history file is appended one record per run via
    ``--cov_history`` on the main CLI. We render:

    1. A small header (file stats — first/last timestamps, n records).
    2. A per-run summary table (newest 20 by default).
    3. An ASCII trend chart of grade + goals% vs time.
    4. A *regression detector* that flags any covergroup whose
       per-run unique-bin count went DOWN compared to the previous
       record (likely a coverage-collection regression in the code).
    """
    history = []
    try:
        with open(args.input) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                history.append(json.loads(line))
    except FileNotFoundError:
        print(f"History file {args.input!r} not found.", file=sys.stderr)
        return 1
    if not history:
        print("History is empty.")
        return 1

    print(f"=== Coverage history: {args.input} ===")
    print(f"  records: {len(history)}")
    print(f"  first:   {history[0]['ts']}")
    print(f"  last:    {history[-1]['ts']}")
    print()

    # Recent records table.
    n_recent = min(args.recent, len(history))
    recent = history[-n_recent:]
    print(f"Recent {n_recent} run(s):")
    print(f"  {'ts':<20s} {'target':<14s} {'seed':>6s} "
          f"{'grade':>5s} {'goals%':>6s} {'bins':>6s} {'samples':>9s}")
    for rec in recent:
        print(
            f"  {rec.get('ts', '?'):<20s} "
            f"{rec.get('target', '?'):<14s} "
            f"{rec.get('start_seed', '?'):>6} "
            f"{rec.get('grade', '?'):>5} "
            f"{rec.get('goals_pct', 0):>6.1f} "
            f"{rec.get('bins_hit', 0):>6} "
            f"{rec.get('total_samples', 0):>9}"
        )
    print()

    # Trend chart — sparkline of `grade` and `goals_pct`.
    grades = [r.get("grade", 0) for r in history]
    goals_pcts = [r.get("goals_pct", 0) for r in history]
    print("Trend (newest on right)")
    print(f"  grade   : {_unicode_sparkline(grades)} "
          f"min={min(grades)}, max={max(grades)}")
    print(f"  goals % : {_unicode_sparkline(goals_pcts)} "
          f"min={min(goals_pcts):.1f}, max={max(goals_pcts):.1f}")
    print()

    # Regression detector — only fires for IDENTICAL (target, test, seed)
    # replays. Seeds differ → bins differ; we can't tell coverage-tooling
    # regression from random variation. So we group by the full key
    # (target, test, start_seed) and only flag drops within a replay.
    print("Regression check (replays of identical target/test/seed):")
    regressions: list[str] = []
    by_replay: dict[tuple, list] = {}
    for r in history:
        key = (r.get("target"), r.get("test"), r.get("start_seed"))
        by_replay.setdefault(key, []).append(r)
    n_replay_groups = 0
    for (tgt, tst, seed), runs in by_replay.items():
        if len(runs) < 2:
            continue
        n_replay_groups += 1
        for prev, cur in zip(runs, runs[1:]):
            for cg, (uniq_cur, _hits) in cur.get("per_cg", {}).items():
                uniq_prev, _ = prev.get("per_cg", {}).get(cg, [0, 0])
                if uniq_cur < uniq_prev:
                    regressions.append(
                        f"  {tgt}/{tst}/seed={seed}: {cg} "
                        f"{uniq_prev} -> {uniq_cur} "
                        f"({prev.get('ts')} -> {cur.get('ts')})"
                    )
    if regressions:
        for r in regressions[: args.max_regressions]:
            print(r)
        if len(regressions) > args.max_regressions:
            print(f"  ... ({len(regressions) - args.max_regressions} more)")
        return 1
    if n_replay_groups == 0:
        print("  no replays in history (each run used a different seed). "
              "Re-run with the same --start_seed to enable regression checks.")
    else:
        print(f"  no regressions detected across {n_replay_groups} replay group(s).")
    return 0


def _unicode_sparkline(values: list[float]) -> str:
    """Return a unicode-block sparkline for ``values``."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return blocks[3] * len(values)
    out = []
    for v in values:
        idx = int((v - lo) / (hi - lo) * (len(blocks) - 1))
        out.append(blocks[idx])
    return "".join(out)


def cmd_cov_explain(args: argparse.Namespace) -> int:
    """Show which directed perturbations would fire for current coverage.

    Reads the observed coverage JSON + the goals YAML, runs the same
    matcher used by ``--cov_directed`` in :mod:`rvgen.auto_regress`, and
    prints the would-be perturbations along with the reason strings.
    Does NOT mutate gen_opts — purely informational.
    """
    from rvgen.coverage.directed import directed_gen_opts, _PERTURBATIONS
    from rvgen.coverage.cgf import missing_bins
    observed = _read(Path(args.observed))
    goals = load_goals(args.goals)
    miss = missing_bins(observed, goals)
    base = args.gen_opts or ""
    out, reasons = directed_gen_opts(base, observed, goals,
                                      max_perturbations=args.max)
    print(f"=== cov-explain: {sum(len(v) for v in miss.values())} missing bin(s) ===\n")
    if reasons:
        print(f"Would apply {len(reasons)} perturbation(s):")
        for r in reasons:
            print(f"  - {r}")
        print(f"\nMutated gen_opts:\n  {out}")
    else:
        print("No perturbations match the current missing bins —")
        print("either all goals met, or none of the matchers fired.")
    # Print which mappings we know about that aren't in the goal/observed
    # set, so users can see what's possible.
    print("\nUnused matchers (no goal asks for these bins):")
    seen = set()
    for key, _pert in _PERTURBATIONS:
        cg, bn = key.split(".", 1)
        cgs_with_goals = set(goals.data.keys())
        in_goals = cg in cgs_with_goals and bn in goals.covergroup(cg)
        if not in_goals and key not in seen:
            seen.add(key)
            print(f"  - {key}")
    return 0


def cmd_auto_goals(args: argparse.Namespace) -> int:
    """Print a goals YAML template scoped to ``args.target``'s ISA.

    Reads ``rvgen.targets.get_target(name)``, walks the supported_isa /
    supported_privileged_mode / vector knobs, and emits goal stubs ONLY for
    the covergroups that the target can actually populate. Skips covergroups
    that depend on knobs the target hasn't enabled (e.g., no vec_amo_wd_cg
    bins for a target without vector_amo_supported).

    Goal targets are heuristic defaults the user can tune. The point is
    that the new user doesn't have to know which covergroup names exist —
    they pick a target name and get a starter file.
    """
    from rvgen.targets import get_target
    from rvgen.isa.enums import RiscvInstrGroup as G, PrivilegedMode

    target = get_target(args.target)
    iso = set(target.supported_isa)
    has_int_M = G.RV32M in iso or G.RV64M in iso
    has_C = bool({G.RV32C, G.RV64C, G.RV32FC, G.RV32DC} & iso)
    has_F = bool({G.RV32F, G.RV64F} & iso)
    has_D = bool({G.RV32D, G.RV64D} & iso)
    has_A = bool({G.RV32A, G.RV64A} & iso)
    has_B = bool(
        {G.RV32B, G.RV32ZBA, G.RV32ZBB, G.RV32ZBC, G.RV32ZBS,
         G.RV64B, G.RV64ZBA, G.RV64ZBB, G.RV64ZBC, G.RV64ZBS} & iso
    )
    has_K = bool(
        {G.RV32ZBKB, G.RV32ZBKX, G.RV32ZKNE, G.RV32ZKND, G.RV32ZKNH,
         G.RV64ZKNE, G.RV64ZKND, G.RV64ZKNH,
         G.RV32ZKSH, G.RV32ZKSED, G.RV64ZKSH, G.RV64ZKSED} & iso
    )
    has_V = G.RVV in iso or bool(
        {G.ZVE32X, G.ZVE32F, G.ZVE64X, G.ZVE64F, G.ZVE64D} & iso
    )
    has_S = PrivilegedMode.SUPERVISOR_MODE in target.supported_privileged_mode
    has_U = PrivilegedMode.USER_MODE in target.supported_privileged_mode

    out: list[str] = []
    out.append(f"# Auto-generated goals for target '{target.name}'.")
    out.append("# Tune the target counts; this is a STARTER, not a final spec.")
    out.append("# Produced by `python -m rvgen.coverage.tools auto-goals --target <name>`.")
    out.append("")

    # group_cg — one bin per ISA family advertised.
    group_lines = ["group_cg:"]
    for grp in sorted(iso, key=lambda g: g.value):
        group_lines.append(f"  {grp.name}: 50")
    group_lines.append("")
    out.extend(group_lines)

    # Always-on covergroups.
    out.append("category_cg:")
    out.append("  ARITHMETIC: 100")
    out.append("  LOGICAL: 50")
    out.append("  COMPARE: 30")
    out.append("  SHIFT: 30")
    out.append("  BRANCH: 30")
    out.append("  JUMP: 10")
    out.append("  LOAD: 30")
    out.append("  STORE: 30")
    if has_int_M:
        out.append("  # M extension present:")
    if has_F or has_D:
        out.append("  # FP loads/stores covered via load_store_width_cg")
    out.append("")

    out.append("rs1_cg: { ZERO: 5, RA: 5, SP: 5, A0: 5, A1: 5, T0: 5, S0: 5 }")
    out.append("rd_cg:  { RA: 5, SP: 5, A0: 5, T0: 5, S0: 5 }")
    out.append("imm_sign_cg: { pos: 30, neg: 30, zero: 5 }")
    out.append("imm_range_cg: { walking_one: 5, walking_zero: 5, all_ones: 5, zero: 5 }")
    out.append("hazard_cg: { raw: 30, waw: 30, war: 10, none: 30 }")
    out.append("rs1_eq_rs2_cg: { equal: 5, distinct: 50 }")
    out.append("rs1_eq_rd_cg: { equal: 5, distinct: 50 }")
    out.append("")

    # Memory alignment / load-store width — only meaningful with loads/stores.
    out.append("load_store_width_cg:")
    out.append("  byte: 10")
    out.append("  half: 10")
    out.append("  word: 10")
    if target.xlen >= 64:
        out.append("  dword: 10")
    out.append("")
    out.append("mem_align_cg:")
    out.append("  byte_aligned: 10")
    out.append("  half_aligned: 5")
    out.append("  word_aligned: 5")
    if target.xlen >= 64:
        out.append("  dword_aligned: 5")
    if target.support_unaligned_load_store:
        out.append("  half_unaligned: 1")
        out.append("  word_unaligned: 1")
    out.append("")

    if has_F or has_D:
        out.append("fp_rm_cg:")
        out.append("  RNE: 5")
        out.append("  RTZ: 5")
        out.append("  RDN: 5")
        out.append("  RUP: 5")
        out.append("  RMM: 5")
        out.append("")

    if has_A:
        out.append("# Atomic / LR/SC-related goals are tracked under opcode_cg.")
        out.append("")

    if has_S or has_U:
        out.append("privilege_mode_cg:")
        out.append("  M_mode: 30      # populated from --iss_trace mcause walks")
        if has_S:
            out.append("  S_mode: 5")
            out.append("  S_return: 1")
        if has_U:
            out.append("  U_mode: 5")
            out.append("  U_return: 1")
        out.append("  M_entered: 1")
        out.append("  M_return: 1")
        out.append("")

    # Vector covergroups gated by V profile.
    if has_V:
        out.append("vec_ls_addr_mode_cg:")
        out.append("  UNIT_STRIDED: 5")
        out.append("  STRIDED: 5")
        out.append("  INDEXED: 5")
        out.append("")
        out.append("vec_eew_cg: { EEW8: 1, EEW16: 1, EEW32: 5 }")
        if target.elen >= 64:
            out.append("# elen >= 64 — also EEW64:")
            out.append("# vec_eew_cg: { EEW64: 1 }")
        out.append("vec_vm_cg: { masked: 30, unmasked: 30 }")
        out.append("vec_va_variant_cg:")
        out.append("  VV: 30")
        out.append("  VX: 20")
        out.append("  VI: 10")
        if has_F:
            out.append("  VF: 5  # vec_fp gate must be on")
        out.append("vec_widening_narrowing_cg:")
        out.append("  widening: 5")
        out.append("  narrowing: 5")
        out.append("  convert: 3")
        out.append("")

        if getattr(target, "enable_zvbb", False) or getattr(target, "enable_zvbc", False) \
                or getattr(target, "enable_zvkn", False):
            out.append("vec_crypto_subext_cg:")
            if getattr(target, "enable_zvbb", False):
                out.append("  zvbb: 50")
            if getattr(target, "enable_zvbc", False):
                out.append("  zvbc: 20")
            if getattr(target, "enable_zvkn", False):
                out.append("  zvkn: 50")
            out.append("")

        if getattr(target, "vector_amo_supported", False):
            out.append("vec_amo_wd_cg: { wd_set: 5, wd_clear: 5 }")
            out.append("")

        # vtype-transition goals — only realistic when the user adds a
        # vsetvli-stress directed stream, but include the goal stub so it
        # shows up in the dashboard.
        out.append("vec_sew_transition_cg:")
        out.append("  # Goals appear once `riscv_vsetvli_stress_instr_stream` is added.")
        out.append("  SEW32__SEW16: 0")
        out.append("  SEW16__SEW32: 0")
        out.append("  SEW32__SEW8: 0")
        out.append("vec_lmul_transition_cg: { M1__M2: 0, M2__M1: 0, M1__MF2: 0 }")
        out.append("")

    # opcode_cg — representative mnemonic seeds per advertised group.
    # Users should expand this list once they see real coverage; the goal
    # is "have at least one bin per major family so a missing extension
    # is visible at first glance".
    opcode_seeds = _opcode_seeds_for(iso, has_S, has_U)
    if opcode_seeds:
        out.append("opcode_cg:")
        for line in opcode_seeds:
            out.append(line)
        out.append("")

    # privileged-state goals if S/U.
    if has_S or has_U:
        out.append("csr_cg:")
        out.append("  MSCRATCH: 5  # writable scalar-CSR")
        if has_S:
            out.append("  SSCRATCH: 5")
            out.append("  STVEC: 1")
        out.append("  MTVEC: 1")
        out.append("  MEPC: 1")
        out.append("")
        out.append("exception_cg:  # populated only with --iss_trace + a fault stream")
        out.append("  trap_entered: 0")
        out.append("")

        # Privileged-event coverage — runtime-sampled from spike trace.
        # Bins live in priv_event_cg; emit the events the target's boot
        # sequence will trigger.
        out.append("priv_event_cg:  # runtime-sampled (--iss_trace required)")
        out.append("  mret_taken: 1")
        out.append("  mtvec_write: 1")
        out.append("  mstatus_write: 1")
        if has_S:
            out.append("  sret_taken: 1")
            out.append("  stvec_write: 1")
        # SATP / paging-related events appear only when the target advertises a
        # non-BARE SATP mode. The Python target may not expose satp_mode;
        # check via the privileged-mode set + presence of S/U as a proxy.
        if has_S:
            out.append("  satp_write: 1")
            out.append("  sfence_vma: 1")
        out.append("")

    # Modern-extension semantic-cluster goals — populated when the target
    # advertises any modern checkbox extension (matches the new
    # modern_ext_cg covergroup).
    from rvgen.isa.enums import RiscvInstrGroup as G
    has_zicond = bool({G.RV32ZICOND, G.RV64ZICOND} & iso)
    has_zicbom = G.RV32ZICBOM in iso
    has_zicboz = G.RV32ZICBOZ in iso
    has_zicbop = G.RV32ZICBOP in iso
    has_zihintpause = G.RV32ZIHINTPAUSE in iso
    has_zihintntl = G.RV32ZIHINTNTL in iso
    has_zimop = bool({G.RV32ZIMOP, G.RV64ZIMOP} & iso)
    has_zcmop = G.RV32ZCMOP in iso
    if any((has_zicond, has_zicbom, has_zicboz, has_zicbop,
            has_zihintpause, has_zihintntl, has_zimop, has_zcmop)):
        out.append("modern_ext_cg:  # semantic clusters of Zicond/Zicbo*/...")
        if has_zicond:
            out.append("  zicond_czero_eqz: 1")
            out.append("  zicond_czero_nez: 1")
        if has_zicbom:
            out.append("  zicbom_clean: 1")
            out.append("  zicbom_flush: 1")
            out.append("  zicbom_inval: 1")
        if has_zicboz:
            out.append("  zicboz_zero: 1")
        if has_zicbop:
            out.append("  zicbop_i: 1")
            out.append("  zicbop_r: 1")
            out.append("  zicbop_w: 1")
        if has_zihintpause:
            out.append("  zihintpause_pause: 1")
        if has_zihintntl:
            out.append("  zihintntl_p1: 1")
            out.append("  zihintntl_pall: 1")
            out.append("  zihintntl_s1: 1")
            out.append("  zihintntl_all: 1")
        if has_zimop:
            out.append("  zimop_r_q0: 1")
            out.append("  zimop_r_q1: 1")
            out.append("  zimop_r_q2: 1")
            out.append("  zimop_r_q3: 1")
            out.append("  zimop_rr: 1")
        if has_zcmop:
            out.append("  zcmop_any: 1")
        out.append("")

    # Memory-ordering coverage — fence pred/succ patterns. Goal counts
    # are achievable by any test that emits at least a few FENCE ops
    # (the random_instr stream does this freely).
    if {G.RV32I, G.RV64I} & iso:
        out.append("fence_cg:  # FENCE pred/succ encoding patterns")
        out.append("  rw__rw: 5     # GCC-default 'fence' is rw,rw")
        out.append("  rw__w: 0      # release-style — opt-in via stream")
        out.append("  r__rw: 0      # acquire-style — opt-in")
        out.append("")

    # Atomics — LR/SC sequence patterns.
    if {G.RV32A, G.RV64A} & iso:
        out.append("lr_sc_pattern_cg:  # LR/SC pairing at sequence level")
        out.append("  paired: 5")
        out.append("  lr_with_intervening_op: 3")
        out.append("  unpaired_sc: 1")
        out.append("  lr_only: 1")
        out.append("")

    text = "\n".join(out)
    if getattr(args, "output", None):
        from pathlib import Path
        Path(args.output).write_text(text + "\n")
        print(f"Wrote starter goals for '{args.target}' to {args.output}",
              file=sys.stderr)
    else:
        print(text)
    return 0


def _opcode_seeds_for(iso: set, has_S: bool, has_U: bool) -> list[str]:
    """Return YAML lines for opcode_cg with one rep per advertised group.

    Goal numbers are deliberately small (1-3) — auto-goals exists to
    *discover* coverage gaps, not to set production thresholds. Users
    bump the numbers after seeing baseline runs.
    """
    from rvgen.isa.enums import RiscvInstrGroup as G
    lines: list[str] = []

    def emit(mnems: tuple[str, ...], goal: int = 3) -> None:
        for m in mnems:
            lines.append(f"  {m}: {goal}")

    # Base I — always present.
    if {G.RV32I, G.RV64I} & iso:
        emit(("LUI", "AUIPC", "JAL", "JALR",
              "BEQ", "BNE", "BLT", "BGE",
              "LB", "LH", "LW", "SB", "SH", "SW",
              "ADDI", "ANDI", "ORI", "XORI",
              "ADD", "SUB", "AND", "OR", "XOR",
              "SLT", "SLTU", "SLL", "SRL", "SRA"))
    if G.RV64I in iso:
        emit(("LD", "SD", "LWU", "ADDIW", "ADDW", "SUBW", "SLLIW", "SRLIW"))
    if {G.RV32M, G.RV64M} & iso:
        emit(("MUL", "MULH", "MULHSU", "MULHU", "DIV", "DIVU", "REM", "REMU"))
    if G.RV64M in iso:
        emit(("MULW", "DIVW", "DIVUW", "REMW", "REMUW"))
    if {G.RV32A, G.RV64A} & iso:
        emit(("LR_W", "SC_W", "AMOSWAP_W", "AMOADD_W", "AMOXOR_W", "AMOAND_W"))
    if G.RV64A in iso:
        emit(("LR_D", "SC_D", "AMOSWAP_D", "AMOADD_D"))
    if {G.RV32C, G.RV64C} & iso:
        emit(("C_ADDI", "C_LI", "C_LUI", "C_ADD", "C_AND", "C_OR",
              "C_BEQZ", "C_BNEZ", "C_J", "C_JR", "C_JALR"))
    if G.RV64C in iso:
        emit(("C_ADDIW", "C_ADDW", "C_LD", "C_SD"))
    if {G.RV32F, G.RV64F} & iso:
        emit(("FLW", "FSW", "FADD_S", "FSUB_S", "FMUL_S", "FDIV_S",
              "FSQRT_S", "FMV_W_X", "FMV_X_W", "FCVT_S_W", "FEQ_S"))
    if {G.RV32D, G.RV64D} & iso:
        emit(("FLD", "FSD", "FADD_D", "FSUB_D", "FMUL_D", "FCVT_D_W"))
    # Bitmanip
    if {G.RV32ZBA, G.RV64ZBA} & iso:
        emit(("SH1ADD", "SH2ADD", "SH3ADD", "SLLI_UW"))
    if {G.RV32ZBB, G.RV64ZBB} & iso:
        emit(("ANDN", "ORN", "XNOR", "CLZ", "CTZ", "CPOP",
              "MAX", "MIN", "ROL", "ROR", "RORI", "ORC_B", "REV8"))
    if {G.RV32ZBC, G.RV64ZBC} & iso:
        emit(("CLMUL", "CLMULH", "CLMULR"))
    if {G.RV32ZBS, G.RV64ZBS} & iso:
        emit(("BCLR", "BEXT", "BINV", "BSET"))
    # Crypto K — mnemonics differ by XLEN (AES32* on RV32, AES64* on RV64).
    if {G.RV32ZBKB, G.RV64ZBKB} & iso:
        emit(("BREV8", "PACK", "PACKH"))
    if G.RV64ZKNE in iso:
        emit(("AES64ES", "AES64ESM", "AES64KS1I", "AES64KS2"))
    elif G.RV32ZKNE in iso:
        emit(("AES32ESI", "AES32ESMI"))
    if G.RV64ZKND in iso:
        emit(("AES64DS", "AES64DSM", "AES64IM"))
    elif G.RV32ZKND in iso:
        emit(("AES32DSI", "AES32DSMI"))
    if {G.RV32ZKNH, G.RV64ZKNH} & iso:
        # SHA-256 is XLEN-agnostic.
        emit(("SHA256SUM0", "SHA256SUM1", "SHA256SIG0", "SHA256SIG1"))
        if G.RV64ZKNH in iso:
            emit(("SHA512SUM0", "SHA512SUM1", "SHA512SIG0", "SHA512SIG1"))
        elif G.RV32ZKNH in iso:
            # RV32 split-pair encodings.
            emit(("SHA512SUM0R", "SHA512SUM1R",
                  "SHA512SIG0L", "SHA512SIG0H",
                  "SHA512SIG1L", "SHA512SIG1H"))
    if {G.RV32ZKSH, G.RV64ZKSH} & iso:
        emit(("SM3P0", "SM3P1"))
    if {G.RV32ZKSED, G.RV64ZKSED} & iso:
        emit(("SM4ED", "SM4KS"))
    # Modern checkbox extensions (Zicond, Zicbom/Zicboz/Zicbop,
    # Zihintpause, Zihintntl, Zimop, Zcmop). These are small extensions;
    # set goal=1 since each opcode has limited semantic variation.
    if {G.RV32ZICOND, G.RV64ZICOND} & iso:
        emit(("CZERO_EQZ", "CZERO_NEZ"), goal=2)
    if G.RV32ZICBOM in iso:
        emit(("CBO_CLEAN", "CBO_FLUSH", "CBO_INVAL"), goal=1)
    if G.RV32ZICBOZ in iso:
        emit(("CBO_ZERO",), goal=1)
    if G.RV32ZICBOP in iso:
        emit(("PREFETCH_I", "PREFETCH_R", "PREFETCH_W"), goal=1)
    if G.RV32ZIHINTPAUSE in iso:
        emit(("PAUSE",), goal=1)
    if G.RV32ZIHINTNTL in iso:
        emit(("NTL_P1", "NTL_PALL", "NTL_S1", "NTL_ALL"), goal=1)
    if {G.RV32ZIMOP, G.RV64ZIMOP} & iso:
        # 32 + 8 reserved encodings; sample-bin a few rather than all 40.
        emit(("MOP_R_0", "MOP_R_15", "MOP_R_31",
              "MOP_RR_0", "MOP_RR_7"), goal=1)
    if G.RV32ZCMOP in iso:
        emit(("C_MOP_1", "C_MOP_15"), goal=1)
    # Privileged-mode opcodes (only meaningful with S/U).
    if has_S or has_U:
        emit(("ECALL", "MRET"))
        if has_S:
            emit(("SRET", "SFENCE_VMA"))
    # Vector — light seed; the per-target rv64gcv*.yaml files have richer
    # opcode goals. We only emit a minimal stub here so the user knows
    # the covergroup exists.
    if any(g in iso for g in (G.RVV, G.ZVE32X, G.ZVE32F, G.ZVE64X, G.ZVE64F, G.ZVE64D)):
        emit(("VADD", "VSUB", "VMUL", "VAND", "VOR", "VXOR",
              "VSLL", "VSRL", "VSRA", "VLE_V", "VSE_V"), goal=2)
    return lines


def cmd_suggest_seeds(args: argparse.Namespace) -> int:
    """Given an historical convergence.json + current goals, suggest the
    seeds most likely to close the *currently-missing* bins.

    The heuristic is simple but effective: if convergence.json[cg.bn] ==
    SEED, then replaying SEED (possibly with its original gen_opts) is
    the strongest move toward closing that bin again.

    Usage:

        python -m rvgen.coverage.tools suggest-seeds \\
            --convergence out/convergence.json \\
            --observed run/coverage.json \\
            --goals rvgen/coverage/goals/baseline.yaml

    Output: a ranked seed list + which bins each seed is expected to close.
    """
    observed = _read(Path(args.observed))
    goals = load_goals(args.goals)
    with open(args.convergence) as f:
        conv = json.load(f)
    first_hit = conv.get("first_hit_seed", {})
    # first_hit keys are "cg.bn" strings; values are seed integers.
    # Build {seed: list[(cg, bn)]} for bins that are *still missing* from
    # observed (so we only suggest useful retries).
    miss = missing_bins(observed, goals)
    missing_keys = {
        f"{cg}.{bn}" for cg, bins in miss.items() for bn in bins
    }
    suggestions: dict[int, list[str]] = {}
    for key, seed in first_hit.items():
        if key in missing_keys:
            suggestions.setdefault(int(seed), []).append(key)
    ranked = sorted(suggestions.items(), key=lambda kv: -len(kv[1]))

    print(f"=== suggest-seeds: {len(missing_keys)} missing bin(s) ===")
    if not ranked:
        print("No historical seed closed any of the currently-missing bins.")
        print("→ Need new seeds or custom streams; try --cov_directed.")
        return 1
    for seed, keys in ranked:
        print(f"\nSeed {seed} previously closed {len(keys)} bin(s):")
        for k in keys[:12]:
            print(f"    {k}")
        if len(keys) > 12:
            print(f"    ... (+{len(keys) - 12} more)")
    # The bins that *nothing* in history closed — genuine gaps.
    never_closed = missing_keys - set(first_hit.keys())
    if never_closed:
        print(f"\nBins never closed by any historical seed ({len(never_closed)}):")
        for k in sorted(never_closed):
            print(f"    {k}")
        print("→ Need a new directed stream or gen_opts perturbation for these.")
    return 0


def cmd_per_test(args: argparse.Namespace) -> int:
    """Analyse a coverage_per_test.json sidecar.

    The sidecar maps test_id → CoverageDB. We print:

    - a ranked list of tests by unique bins owned (bins this test hits
      that no other test does),
    - per-test total hit counts,
    - optionally, per-bin "owner" attribution for a chosen covergroup.
    """
    with open(args.input) as f:
        raw = json.load(f)
    per_test: dict[str, CoverageDB] = {
        k: {cg: dict(bins) for cg, bins in v.items()}
        for k, v in raw.items()
    }
    if not per_test:
        print("coverage_per_test.json is empty")
        return 0

    # Unique-owner analysis: for each (cg, bin), count how many tests hit
    # it; if only one hits it, that test is its "owner".
    owners: dict[str, int] = {k: 0 for k in per_test}
    owner_of: dict[tuple[str, str], str] = {}
    all_bins = set()
    bin_test_count: dict[tuple[str, str], int] = {}
    for tid, db in per_test.items():
        for cg, bins in db.items():
            for bn, cnt in bins.items():
                if cnt <= 0:
                    continue
                key = (cg, bn)
                all_bins.add(key)
                bin_test_count[key] = bin_test_count.get(key, 0) + 1

    # Assign owner to bins hit by exactly one test.
    for tid, db in per_test.items():
        for cg, bins in db.items():
            for bn, cnt in bins.items():
                if cnt <= 0:
                    continue
                key = (cg, bn)
                if bin_test_count.get(key, 0) == 1:
                    owners[tid] += 1
                    owner_of[key] = tid

    # Per-test hit totals.
    totals = {tid: sum(sum(b.values()) for b in db.values()) for tid, db in per_test.items()}

    print(f"=== per-test coverage attribution: {args.input} ===")
    print(f"    {len(per_test)} tests, {len(all_bins)} unique (cg, bin) pairs")
    print()
    print(f"{'Test':<40s} {'unique_owned':>14s} {'total_hits':>12s}")
    for tid in sorted(per_test, key=lambda t: (-owners[t], -totals[t])):
        print(f"{tid:<40s} {owners[tid]:>14d} {totals[tid]:>12d}")

    if args.cg:
        print()
        print(f"=== owners in covergroup {args.cg!r} ===")
        for (cg, bn), tid in sorted(owner_of.items()):
            if cg == args.cg:
                print(f"    {bn:<40s} {tid}")

    return 0


# ---------------------------------------------------------------------------
# CSV / HTML export
# ---------------------------------------------------------------------------


def _export_csv(db: CoverageDB, path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["covergroup", "bin", "hit_count"])
        for cg in sorted(db):
            for bn in sorted(db[cg]):
                w.writerow([cg, bn, db[cg][bn]])


_HTML_STYLE = """\
body { font-family: -apple-system, system-ui, sans-serif; margin: 2em; max-width: 1300px; color: #222; }
h1 { border-bottom: 2px solid #333; padding-bottom: 0.25em; }
.summary { margin: 1em 0; padding: 1em; background: #f8f8f8; border-radius: 4px; }
.summary b { color: #114; }
.bar { background: linear-gradient(to right, #4a6 var(--pct), #eee var(--pct)); height: 0.9em; border-radius: 2px; margin-top: 0.4em; }
.bar.cg { height: 0.5em; flex: 1; min-width: 60px; max-width: 200px; }
details { margin-bottom: 0.5em; border: 1px solid #ddd; border-radius: 4px; }
details > summary {
    cursor: pointer; padding: 0.6em 1em; background: #f0f3fa;
    list-style: none; display: flex; align-items: center; gap: 1em;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: '▶'; font-size: 0.7em; color: #66c; transition: transform 0.15s; }
details[open] > summary::before { transform: rotate(90deg); }
.cg-name { font-weight: 600; min-width: 250px; }
.cg-meta { font-size: 0.85em; color: #666; min-width: 200px; }
.cg-status { font-weight: 600; padding: 0 0.5em; border-radius: 3px; }
.cg-status.met { background: #cfc; color: #060; }
.cg-status.partial { background: #fec; color: #960; }
.cg-status.missed { background: #fcc; color: #900; }
.cg-status.untracked { background: #eee; color: #555; }
table { border-collapse: collapse; margin: 0.5em 1em 1em 1em; width: calc(100% - 2em); }
th, td { padding: 4px 10px; border: 1px solid #ddd; text-align: left; }
th { background: #f5f5f5; cursor: pointer; user-select: none; }
th.sorted-asc::after { content: ' ▲'; color: #66c; }
th.sorted-desc::after { content: ' ▼'; color: #66c; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.missed { background: #fee; color: #900; }
td.ok { background: #efe; color: #060; }
.filter { margin: 0.5em 0; }
.filter input { padding: 0.4em 0.6em; width: 280px; border: 1px solid #aaa; border-radius: 3px; }
.toggle { float: right; padding: 0.4em 0.8em; background: #eef; border: 1px solid #aaf; border-radius: 3px; cursor: pointer; }
.timeline { margin-top: 0.5em; font-family: monospace; font-size: 0.85em; }
"""

_HTML_SCRIPT = """\
<script>
function cgFilter(){
  const q = document.getElementById('cg-filter').value.toLowerCase();
  document.querySelectorAll('details.cg').forEach(d => {
    const name = d.querySelector('.cg-name').textContent.toLowerCase();
    d.style.display = name.includes(q) ? '' : 'none';
  });
}
function expandAll(open){
  document.querySelectorAll('details.cg').forEach(d => d.open = open);
  document.getElementById('toggle-btn').textContent = open ? 'Collapse all' : 'Expand all';
  document.getElementById('toggle-btn').dataset.open = open ? '1' : '0';
}
function toggleAll(){
  const open = document.getElementById('toggle-btn').dataset.open !== '1';
  expandAll(open);
}
function sortTable(th){
  const tbl = th.closest('table');
  const idx = Array.from(th.parentNode.children).indexOf(th);
  const asc = !th.classList.contains('sorted-asc');
  tbl.querySelectorAll('th').forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
  th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');
  const rows = Array.from(tbl.querySelectorAll('tbody tr'));
  rows.sort((a, b) => {
    const av = a.children[idx].dataset.sort || a.children[idx].textContent;
    const bv = b.children[idx].dataset.sort || b.children[idx].textContent;
    const an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  const tb = tbl.querySelector('tbody');
  rows.forEach(r => tb.appendChild(r));
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => sortTable(th)));
});
</script>
"""


def _export_html(db: CoverageDB, path: Path, *, goals: Goals | None = None,
                 timeline: list | None = None) -> None:
    lines: list[str] = []
    lines.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    lines.append("<title>rvgen coverage</title>")
    lines.append(f"<style>{_HTML_STYLE}</style>")
    lines.append(_HTML_SCRIPT)
    lines.append("</head><body>")
    lines.append("<h1>Functional Coverage Report</h1>")

    total_bins_hit = sum(len(b) for b in db.values())
    total_hits = sum(sum(b.values()) for b in db.values())
    lines.append(
        "<div class='summary'>"
        f"<b>{len(db)}</b> covergroups, "
        f"<b>{total_bins_hit}</b> unique bins hit, "
        f"<b>{total_hits}</b> total samples."
        "</div>"
    )

    miss = missing_bins(db, goals) if goals else {}

    if goals is not None:
        total_req = sum(1 for b in goals.data.values() for v in b.values() if v > 0)
        total_missing = sum(len(v) for v in miss.values())
        met = total_req - total_missing
        pct = (met / total_req * 100) if total_req else 100.0
        lines.append(
            "<div class='summary'>"
            f"<b>{met}/{total_req}</b> required bins met "
            f"(<b>{pct:.1f}%</b>)"
            f"<div class='bar' style='--pct: {pct}%'></div>"
            "</div>"
        )

    if timeline:
        # Render an ASCII-ish line per seed showing new-bin counts.
        lines.append("<div class='summary'>")
        lines.append("<b>Convergence timeline</b> (new unique bins per seed):")
        peak = max((t.get("new_bins", 0) for t in timeline), default=1) or 1
        lines.append("<div class='timeline'>")
        for t in timeline:
            new_b = t.get("new_bins", 0)
            seed = t.get("seed", "?")
            bar = "█" * max(1, int(new_b / peak * 40)) if new_b else ""
            lines.append(
                f"seed {seed:>5}: {new_b:>4} new {bar}<br>"
            )
        lines.append("</div></div>")

    lines.append(
        "<div class='filter'>"
        "<input id='cg-filter' placeholder='Filter covergroups...' oninput='cgFilter()'/>"
        "<button id='toggle-btn' class='toggle' data-open='0' onclick='toggleAll()'>"
        "Expand all</button>"
        "</div>"
    )

    for cg in sorted(db):
        bins = db[cg]
        goal_bins = goals.covergroup(cg) if goals else {}
        cg_miss = miss.get(cg, {}) if goals else {}
        if not bins and not goal_bins:
            continue

        # Per-cg status badge.
        if goal_bins:
            n_req = sum(1 for v in goal_bins.values() if v > 0)
            n_miss = len(cg_miss)
            cg_pct = ((n_req - n_miss) / n_req * 100) if n_req else 100.0
            if n_miss == 0:
                badge_cls = "met"; badge_txt = f"MET {n_req}/{n_req}"
            elif n_miss == n_req:
                badge_cls = "missed"; badge_txt = f"MISS 0/{n_req}"
            else:
                badge_cls = "partial"
                badge_txt = f"PART {n_req - n_miss}/{n_req}"
        else:
            cg_pct = 100.0
            badge_cls = "untracked"; badge_txt = "no goals"

        meta = (
            f"{len(bins)} bin(s), {sum(bins.values())} hits"
            + (f" — <b>{len(cg_miss)}</b> missing" if cg_miss else "")
        )
        lines.append("<details class='cg'>")
        lines.append(
            "<summary>"
            f"<span class='cg-name'>{_html.escape(cg)}</span>"
            f"<span class='cg-meta'>{meta}</span>"
            f"<span class='bar cg' style='--pct: {cg_pct}%'></span>"
            f"<span class='cg-status {badge_cls}'>{badge_txt}</span>"
            "</summary>"
        )
        lines.append("<table><thead><tr>"
                     "<th>Bin</th><th>Observed</th><th>Required</th>"
                     "</tr></thead><tbody>")
        sorted_bins = sorted(bins.items(), key=lambda kv: -kv[1])
        observed_names = {bn for bn, _ in sorted_bins}
        for bn, req in goal_bins.items():
            if bn not in observed_names and req > 0:
                sorted_bins.append((bn, 0))
        for bn, cnt in sorted_bins:
            req = goal_bins.get(bn, 0)
            if req > 0:
                cls = "ok" if cnt >= req else "missed"
                row_req = str(req)
            else:
                cls = ""
                row_req = "-"
            lines.append(
                "<tr>"
                f"<td>{_html.escape(bn)}</td>"
                f"<td class='num {cls}' data-sort='{cnt}'>{cnt}</td>"
                f"<td class='num' data-sort='{req}'>{row_req}</td>"
                "</tr>"
            )
        lines.append("</tbody></table>")
        lines.append("</details>")

    lines.append("</body></html>")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Argparse entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m rvgen.coverage.tools",
        description="Coverage analysis CLI — merge / diff / attribute / export.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("merge", help="Merge N coverage JSONs into one.")
    pm.add_argument("inputs", nargs="+")
    pm.add_argument("-o", "--output", required=True)
    pm.set_defaults(func=cmd_merge)

    pd = sub.add_parser("diff", help="Report bin-count deltas from A to B.")
    pd.add_argument("a")
    pd.add_argument("b")
    pd.add_argument("--json", help="Write the diff as JSON here.")
    pd.set_defaults(func=cmd_diff)

    pa = sub.add_parser("attribute",
                         help="For each required bin, show which input first closed it.")
    pa.add_argument("inputs", nargs="+", help="Per-seed coverage JSONs in chronological order.")
    pa.add_argument("--goals", required=True, help="Coverage goals YAML.")
    pa.set_defaults(func=cmd_attribute)

    pe = sub.add_parser("export", help="Export coverage JSON to CSV and/or HTML.")
    pe.add_argument("input")
    pe.add_argument("--csv", help="CSV output path.")
    pe.add_argument("--html", help="HTML output path.")
    pe.add_argument("--goals", help="Optional goals YAML for HTML pass/fail coloring.")
    pe.add_argument("--timeline", help="Optional cov_timeline.json — renders a "
                     "convergence sparkline at the top of the HTML.")
    pe.set_defaults(func=cmd_export)

    pr = sub.add_parser("report", help="Render the text coverage report.")
    pr.add_argument("input")
    pr.add_argument("--goals", help="Optional goals YAML for pass/fail banner.")
    pr.set_defaults(func=cmd_report)

    pt = sub.add_parser("per-test",
                         help="Analyse a coverage_per_test.json sidecar: "
                              "rank tests by unique-owned bins, total hits, "
                              "and optionally dump per-bin owners.")
    pt.add_argument("input", help="Path to coverage_per_test.json.")
    pt.add_argument("--cg", default="",
                     help="Optional covergroup name; print the owner test_id "
                          "for each uniquely-owned bin in this covergroup.")
    pt.set_defaults(func=cmd_per_test)

    pb = sub.add_parser("baseline-check",
                         help="Gate CI: every bin hit in baseline must also "
                              "be hit in the observed run.")
    pb.add_argument("input", help="Observed coverage JSON.")
    pb.add_argument("--baseline", required=True,
                     help="Golden baseline coverage JSON (checked in).")
    pb.set_defaults(func=cmd_baseline_check)

    pl = sub.add_parser("lint-goals",
                         help="Static-check a goals YAML: unknown covergroups "
                              "and unknown bin names warn or error.")
    pl.add_argument("input", help="Goals YAML path.")
    pl.add_argument("--strict", choices=("warn", "error"), default="warn",
                     help="Exit code behavior (default: warn → exit 0).")
    pl.set_defaults(func=cmd_lint_goals)

    ph = sub.add_parser("history",
                         help="Render an ASCII trend chart from a JSONL "
                              "coverage-history file (written by --cov_history).")
    ph.add_argument("input", help="Path to JSONL history file.")
    ph.add_argument("--recent", type=int, default=20,
                     help="Show the most-recent N records in the table "
                          "(default 20).")
    ph.add_argument("--max_regressions", type=int, default=20,
                     help="Limit how many regressions to print (default 20).")
    ph.set_defaults(func=cmd_history)

    pce = sub.add_parser("cov-explain",
                          help="Preview which --cov_directed perturbations "
                               "would fire for the current observed "
                               "coverage. Doesn't mutate anything.")
    pce.add_argument("--observed", required=True,
                      help="Observed coverage JSON.")
    pce.add_argument("--goals", required=True,
                      help="Goals YAML.")
    pce.add_argument("--gen_opts", default="",
                      help="Existing gen_opts to perturb on top of "
                           "(string of +plusargs).")
    pce.add_argument("--max", type=int, default=6,
                      help="Maximum perturbations to suggest (default: 6).")
    pce.set_defaults(func=cmd_cov_explain)

    pag = sub.add_parser("auto-goals",
                          help="Emit a starter goals YAML scoped to a "
                               "target's ISA — covers only the covergroups "
                               "the target can actually populate.")
    pag.add_argument("--target", required=True,
                      help="Target name (e.g., 'rv32imc' or 'rv64gcv_crypto'). "
                           "Resolved via rvgen.targets.get_target().")
    pag.add_argument("-o", "--output", default=None,
                      help="Write to this file instead of stdout.")
    pag.set_defaults(func=cmd_auto_goals)

    ps = sub.add_parser("suggest-seeds",
                         help="Given historical convergence + current goals, "
                              "rank seeds by how many still-missing bins "
                              "they closed in the past.")
    ps.add_argument("--convergence", required=True,
                     help="Path to a prior run's convergence.json.")
    ps.add_argument("--observed", required=True,
                     help="Current observed coverage JSON.")
    ps.add_argument("--goals", required=True, help="Coverage goals YAML.")
    ps.set_defaults(func=cmd_suggest_seeds)

    psv = sub.add_parser("export-sv",
                          help="Export goals as SystemVerilog covergroup "
                               "source. SV-shop verification teams can "
                               "compile this into their UCDB-collecting "
                               "flow without rewriting goals.")
    psv.add_argument("--goals", required=True, action="append",
                      help="Coverage goals YAML. Repeat to layer overlays.")
    psv.add_argument("-o", "--output", required=True,
                      help="Output .sv file path.")
    psv.add_argument("--package", default="rvgen_cov_pkg",
                      help="SV package name (default: rvgen_cov_pkg).")
    psv.set_defaults(func=cmd_export_sv)

    psc = sub.add_parser("scorecard",
                         help="Per-extension / per-subsystem coverage "
                              "rollup. Aggregates bin counts to "
                              "percentage-met against goals so users "
                              "see at a glance which subsystems have "
                              "coverage gaps.")
    psc.add_argument("--db", required=True,
                      help="Coverage JSON (typically <output>/coverage.json).")
    psc.add_argument("--goals", required=True, action="append",
                      help="Coverage goals YAML. Repeat to layer overlays.")
    psc.add_argument("--json", action="store_true",
                      help="Emit a machine-readable JSON scorecard "
                           "instead of the ASCII table.")
    psc.set_defaults(func=cmd_scorecard)

    return p


# ---------------------------------------------------------------------------
# scorecard — per-subsystem rollup of coverage met vs goals
# ---------------------------------------------------------------------------


# Mapping from covergroup → subsystem bucket. Subsystems are coarse
# user-facing labels: "RV32I+M", "Vector", "Bitmanip", "Crypto",
# "Privileged", "Memory ordering", "Modern checkbox", etc. A single
# covergroup can only belong to one subsystem; multi-subsystem groups
# (like ``opcode_cg`` which spans every extension) get classified
# bin-by-bin during the rollup.
_BIN_PREFIX_TO_SUBSYS = (
    # opcode-name prefix matchers — order matters (most specific first).
    # Crypto vector first (longest VAES/VSHA/VCLMUL prefixes).
    ("VAES", "Crypto"), ("VSHA", "Crypto"), ("VCLMUL", "Crypto"),
    # Modern checkbox extensions — also long prefixes.
    ("CZERO", "Modern checkbox"),
    ("CBO_", "Modern checkbox"), ("PREFETCH", "Modern checkbox"),
    ("PAUSE", "Modern checkbox"), ("NTL_", "Modern checkbox"),
    ("MOP_", "Modern checkbox"), ("C_MOP_", "Modern checkbox"),
    # Privileged before SFENCE_VMA gets caught by SFENCE (privileged
    # also comes before "S" for FP-like SHA/SM3/SM4).
    ("EBREAK", "Privileged"), ("ECALL", "Privileged"),
    ("MRET", "Privileged"), ("SRET", "Privileged"),
    ("SFENCE", "Privileged"), ("WFI", "Privileged"),
    ("DRET", "Privileged"),
    # Memory ordering — must precede the bare "F" prefix (else FENCE
    # is mis-classified as floating-point).
    ("FENCE", "Memory ordering"),
    # Atomics — LR_ / SC_ / AMO.
    ("AMO", "Atomics"), ("LR_", "Atomics"), ("SC_", "Atomics"),
    # Compressed — C_ prefix; must precede later catches.
    ("C_", "Compressed"),
    # Crypto scalar (AES/SHA/SM3/SM4/BREV/XPERM).
    ("AES", "Crypto"), ("SHA", "Crypto"), ("SM3", "Crypto"), ("SM4", "Crypto"),
    ("BREV", "Crypto"), ("XPERM", "Crypto"),
    # Vector — broad "V" catch (after VAES/VSHA/VCLMUL specifics already
    # routed to Crypto).
    ("V", "Vector"),
    # Bitmanip — specific mnemonics first.
    ("SH1", "Bitmanip"), ("SH2", "Bitmanip"), ("SH3", "Bitmanip"),
    ("ANDN", "Bitmanip"), ("ORN", "Bitmanip"), ("XNOR", "Bitmanip"),
    ("CLZ", "Bitmanip"), ("CTZ", "Bitmanip"), ("CPOP", "Bitmanip"),
    ("MAX", "Bitmanip"), ("MIN", "Bitmanip"),
    ("ROL", "Bitmanip"), ("ROR", "Bitmanip"),
    ("ORC", "Bitmanip"), ("REV8", "Bitmanip"),
    ("BCLR", "Bitmanip"), ("BSET", "Bitmanip"),
    ("BEXT", "Bitmanip"), ("BINV", "Bitmanip"),
    ("CLMUL", "Bitmanip"), ("PACK", "Bitmanip"),
    # Floating point — F prefix last (most catch-all of the F-words
    # except FENCE which is already routed above).
    ("FLD", "Floating point"), ("FSD", "Floating point"),
    ("F", "Floating point"),
    # M extension last among RV32 (MUL/MULH, DIV, REM).
    ("MULH", "RV32M+RV64M"), ("MUL", "RV32M+RV64M"),
    ("DIV", "RV32M+RV64M"), ("REM", "RV32M+RV64M"),
)


_GROUP_TO_SUBSYS: dict[str, str] = {
    "vec_eew_cg": "Vector", "vec_emul_cg": "Vector",
    "vec_vm_cg": "Vector", "vec_vm_category_cross_cg": "Vector",
    "vec_amo_wd_cg": "Vector", "vec_va_variant_cg": "Vector",
    "vec_nfields_cg": "Vector", "vec_seg_addr_mode_cross_cg": "Vector",
    "vec_widening_narrowing_cg": "Vector", "vec_crypto_subext_cg": "Crypto",
    "vec_sew_transition_cg": "Vector", "vec_lmul_transition_cg": "Vector",
    "vec_vtype_transition_cg": "Vector", "vec_vstart_cg": "Vector",
    "vec_ls_addr_mode_cg": "Vector", "vec_eew_vs_sew_cg": "Vector",
    "vtype_cg": "Vector", "vtype_dyn_cg": "Vector", "vreg_cg": "Vector",
    "fp_rm_cg": "Floating point", "fpr_cg": "Floating point",
    "csr_cg": "Privileged", "csr_access_cg": "Privileged",
    "csr_value_cg": "Privileged", "exception_cg": "Privileged",
    "privilege_mode_cg": "Privileged", "priv_event_cg": "Privileged",
    "modern_ext_cg": "Modern checkbox",
    "fence_cg": "Memory ordering",
    "lr_sc_pattern_cg": "Atomics",
    "branch_direction_cg": "Control flow",
    "branch_taken_per_mnem_cg": "Control flow",
    "branch_pattern_cg": "Control flow",
    "branch_distance_cg": "Control flow",
    "load_store_width_cg": "Memory access", "mem_align_cg": "Memory access",
    "load_store_offset_cg": "Memory access",
    "cache_line_cross_cg": "Memory access", "page_cross_cg": "Memory access",
    "hazard_cg": "Pipeline", "category_transition_cg": "Pipeline",
    "opcode_transition_cg": "Pipeline",
    "rs1_eq_rs2_cg": "Reg-file", "rs1_eq_rd_cg": "Reg-file",
    "rs1_rs2_cross_cg": "Reg-file", "rd_rs1_cross_cg": "Reg-file",
    "rs1_cg": "Reg-file", "rs2_cg": "Reg-file", "rd_cg": "Reg-file",
    "rs1_val_class_cg": "Value class", "rs2_val_class_cg": "Value class",
    "rd_val_class_cg": "Value class", "rs_val_class_cross_cg": "Value class",
    "rs_val_corner_cg": "Value class", "bit_activity_cg": "Value class",
    "imm_sign_cg": "Immediates", "imm_range_cg": "Immediates",
    "directed_stream_cg": "Streams",
    "pc_reach_cg": "Reachability",
    "format_cg": "Misc", "category_cg": "Misc", "group_cg": "Misc",
    "fmt_category_cross": "Misc", "category_group_cross": "Misc",
    "opcode_cg": None,  # multi-subsystem; bin-by-bin classification.
}


def _classify_opcode_bin(bin_name: str) -> str:
    """Classify an opcode_cg bin name (an instruction mnemonic) into a
    subsystem bucket using the prefix table."""
    n = bin_name.upper()
    for prefix, subsys in _BIN_PREFIX_TO_SUBSYS:
        if n.startswith(prefix):
            return subsys
    # Fallthrough — most plain RV32I/64I opcodes (ADD, LW, BEQ, etc.).
    return "RV32I+RV64I"


def _subsys_for_bin(cg_name: str, bin_name: str) -> str:
    """Determine the subsystem a (covergroup, bin) pair belongs to."""
    bucket = _GROUP_TO_SUBSYS.get(cg_name)
    if bucket is not None:
        return bucket
    if cg_name == "opcode_cg":
        # Strip the "_dyn" suffix runtime-sampler appends.
        base = bin_name.replace("__dyn", "")
        return _classify_opcode_bin(base)
    # Unrecognised covergroup → bucket as "Misc".
    return "Misc"


def cmd_export_sv(args: argparse.Namespace) -> int:
    """Emit a SystemVerilog covergroup package for the merged goals.

    Useful when the verification team uses VCS / Xcelium / Questa with
    their own UCDB-collecting flow but wants to reuse rvgen's goals.
    Output is a single .sv file containing one covergroup class per
    covergroup in the goals YAML, wrapped in a package.
    """
    from rvgen.coverage.sv_export import write_sv_package
    goals = load_goals_layered(*[Path(g) for g in args.goals])
    out_path = write_sv_package(goals, args.output, package_name=args.package)
    n_groups = len([cg for cg, bins in goals.data.items() if bins])
    print(f"Wrote SV package '{args.package}' with {n_groups} "
          f"covergroup(s) -> {out_path}", file=sys.stderr)
    return 0


def cmd_scorecard(args: argparse.Namespace) -> int:
    """Per-subsystem coverage rollup.

    For each subsystem bucket: sum required bins (from goals), sum bins
    actually met (from observed db), compute percent. Output is an
    ASCII table sorted by percent ascending so the worst-covered
    subsystem appears first.
    """
    db = _read(Path(args.db))
    goals = load_goals_layered(*[Path(g) for g in args.goals])

    # Aggregate per-subsystem.
    by_subsys: dict[str, dict[str, int]] = {}
    for cg, bin_goals in goals.data.items():
        observed = db.get(cg, {})
        for bn, required in bin_goals.items():
            if required <= 0:
                # Optional bin; don't count toward percentage.
                continue
            subsys = _subsys_for_bin(cg, bn)
            slot = by_subsys.setdefault(subsys, {"required": 0, "met": 0,
                                                  "missing": 0, "extra": 0})
            slot["required"] += 1
            if observed.get(bn, 0) >= required:
                slot["met"] += 1
            else:
                slot["missing"] += 1

    # Also tally bins observed but not in goals — useful "bonus coverage" hint.
    for cg, bins in db.items():
        cg_goals = goals.data.get(cg, {})
        for bn in bins:
            if bn not in cg_goals:
                subsys = _subsys_for_bin(cg, bn)
                slot = by_subsys.setdefault(subsys, {"required": 0, "met": 0,
                                                      "missing": 0, "extra": 0})
                slot["extra"] += 1

    rows = []
    for subsys, counts in by_subsys.items():
        req = counts["required"]
        met = counts["met"]
        pct = 100.0 * met / req if req > 0 else 0.0
        rows.append({
            "subsystem": subsys,
            "required": req, "met": met,
            "missing": counts["missing"], "extra": counts["extra"],
            "percent": pct,
        })

    # Sort: subsystems with required bins first by ascending percent (worst
    # first); subsystems with no required bins (only "extra") last by name.
    rows.sort(key=lambda r: (r["required"] == 0, r["percent"], r["subsystem"]))

    if args.json:
        json.dump({"scorecard": rows}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    # ASCII table.
    print(f"{'Subsystem':<22} {'Met':>5} / {'Req':>5}  {'%':>6}  "
          f"{'Missing':>8}  {'Bonus':>6}")
    print("-" * 70)
    for r in rows:
        if r["required"] == 0 and r["extra"] == 0:
            continue
        bar_len = int(r["percent"] / 5) if r["required"] > 0 else 0
        bar = "█" * bar_len + "·" * (20 - bar_len) if r["required"] > 0 else "(no goals)"
        if r["required"] == 0:
            print(f"{r['subsystem']:<22} {'-':>5} / {'-':>5}  {'-':>6}  "
                  f"{'-':>8}  {r['extra']:>6}")
        else:
            print(f"{r['subsystem']:<22} {r['met']:>5} / {r['required']:>5}  "
                  f"{r['percent']:>5.1f}%  {r['missing']:>8}  {r['extra']:>6}  {bar}")

    # Footer summary.
    total_req = sum(r["required"] for r in rows)
    total_met = sum(r["met"] for r in rows)
    overall_pct = 100.0 * total_met / total_req if total_req else 0.0
    print("-" * 70)
    print(f"{'OVERALL':<22} {total_met:>5} / {total_req:>5}  "
          f"{overall_pct:>5.1f}%")

    # Exit non-zero if any subsystem has < 50% — useful for CI gating.
    bad = [r for r in rows if r["required"] >= 5 and r["percent"] < 50.0]
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
