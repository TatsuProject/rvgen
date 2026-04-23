"""Coverage analysis CLI — diff / merge / export / attribute.

Invoke as::

    python -m chipforge_inst_gen.coverage.tools <subcommand> ...

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

from chipforge_inst_gen.coverage.cgf import Goals, load_goals, missing_bins
from chipforge_inst_gen.coverage.collectors import CoverageDB, merge, new_db
from chipforge_inst_gen.coverage.report import render_report


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
        _export_html(db, Path(args.html),
                      goals=load_goals(args.goals) if args.goals else None)
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

        python -m chipforge_inst_gen.coverage.tools baseline-check \\
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
    from chipforge_inst_gen.coverage.collectors import ALL_COVERGROUPS
    from chipforge_inst_gen.isa.enums import (
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
        "imm_range_cg": {"zero", "all_ones", "walking_one", "walking_zero",
                          "min_signed", "max_signed", "generic"},
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


def cmd_suggest_seeds(args: argparse.Namespace) -> int:
    """Given an historical convergence.json + current goals, suggest the
    seeds most likely to close the *currently-missing* bins.

    The heuristic is simple but effective: if convergence.json[cg.bn] ==
    SEED, then replaying SEED (possibly with its original gen_opts) is
    the strongest move toward closing that bin again.

    Usage:

        python -m chipforge_inst_gen.coverage.tools suggest-seeds \\
            --convergence out/convergence.json \\
            --observed run/coverage.json \\
            --goals chipforge_inst_gen/coverage/goals/baseline.yaml

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
body { font-family: -apple-system, system-ui, sans-serif; margin: 2em; max-width: 1100px; }
h1 { border-bottom: 2px solid #333; padding-bottom: 0.25em; }
h2 { background: #eef; padding: 0.25em 0.5em; border-left: 4px solid #55a; }
table { border-collapse: collapse; margin-bottom: 1.5em; }
th, td { padding: 4px 10px; border: 1px solid #ccc; text-align: left; }
th { background: #f5f5f5; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.missed { background: #fee; color: #900; }
td.ok { background: #efe; color: #060; }
.summary { margin: 1em 0; padding: 1em; background: #f8f8f8; border-radius: 4px; }
.bar { background: linear-gradient(to right, #4a6 var(--pct), #eee var(--pct)); height: 1em; border-radius: 2px; }
"""


def _export_html(db: CoverageDB, path: Path, *, goals: Goals | None = None) -> None:
    lines: list[str] = []
    lines.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    lines.append("<title>chipforge-inst-gen coverage</title>")
    lines.append(f"<style>{_HTML_STYLE}</style></head><body>")
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

    for cg in sorted(db):
        bins = db[cg]
        if not bins:
            continue
        goal_bins = goals.covergroup(cg) if goals else {}
        cg_miss = miss.get(cg, {}) if goals else {}
        lines.append(f"<h2>{_html.escape(cg)}</h2>")
        lines.append(
            f"<div>unique bins: {len(bins)}&nbsp;|&nbsp;total hits: {sum(bins.values())}"
            + (f"&nbsp;|&nbsp;missing: <b>{len(cg_miss)}</b>" if cg_miss else "")
            + "</div>"
        )
        lines.append("<table><tr><th>Bin</th><th>Observed</th><th>Required</th></tr>")
        sorted_bins = sorted(bins.items(), key=lambda kv: -kv[1])
        # also include goal bins not yet observed
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
                f"<tr><td>{_html.escape(bn)}</td>"
                f"<td class='num {cls}'>{cnt}</td>"
                f"<td class='num'>{row_req}</td></tr>"
            )
        lines.append("</table>")

    lines.append("</body></html>")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Argparse entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m chipforge_inst_gen.coverage.tools",
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

    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
