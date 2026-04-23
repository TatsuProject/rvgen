"""Command-line entry point — lightweight port of ``run.py``.

For Phase 1 the CLI exposes the generation pipeline only (``--steps gen``).
GCC / ISS / iss_cmp integration comes in a later step.

Example::

    python -m chipforge_inst_gen --target rv32imc \\
        --test riscv_arithmetic_basic_test --iterations 2 --steps gen \\
        --output out/

"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from chipforge_inst_gen.config import make_config
from chipforge_inst_gen.seeding import SeedGen
from chipforge_inst_gen.targets import get_target, target_names
from chipforge_inst_gen.testlist import load_testlist


_LOG = logging.getLogger("chipforge_inst_gen.cli")


# Default testlist / riscv_dv_root can be overridden per environment.
_DEFAULT_RISCV_DV_ROOT = Path.home() / "Desktop" / "verif_env_tatsu" / "riscv-dv"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chipforge-inst-gen",
        description="Pure-Python re-implementation of riscv-dv — CLI.",
    )
    # Test selection
    p.add_argument("--target", default="rv32imc", choices=target_names(),
                   help="Target processor configuration (default: rv32imc).")
    p.add_argument("-tl", "--testlist", default="",
                   help="Path to a regression testlist YAML. "
                        "Defaults to <riscv_dv_root>/target/<target>/testlist.yaml.")
    p.add_argument("-tn", "--test", default="all",
                   help="Comma-separated test names, or 'all'.")
    p.add_argument("-i", "--iterations", type=int, default=0,
                   help="Override the testlist iterations count (0 = use yaml).")

    # Seeding (mutually exclusive)
    seed_group = p.add_mutually_exclusive_group()
    seed_group.add_argument("--seed", type=int, default=None,
                            help="Fixed seed for every test (implies iterations=1).")
    seed_group.add_argument("--start_seed", type=int, default=None,
                            help="Starting seed; increments by iteration.")
    seed_group.add_argument("--seed_yaml", type=str, default=None,
                            help="Replay seeds from a previously-saved seed.yaml.")

    # Flow control
    p.add_argument("-s", "--steps", default="all",
                   help="Comma-separated: gen,gcc_compile,iss_sim,iss_cmp,cov, or 'all'.")
    p.add_argument("-o", "--output", default="",
                   help="Output directory (default: out_<date>).")
    p.add_argument("--noclean", action="store_true", default=True,
                   help="Do not clean the output of previous runs (default: true, same as run.py).")

    # Coverage
    p.add_argument("--cov_goals", action="append", default=[],
                   help="Path to a coverage-goals YAML. Repeat to layer "
                        "overlays (later files override earlier on a "
                        "per-bin basis — last-writer wins, 0 marks optional). "
                        "If set, the 'cov' step compares observed coverage "
                        "against the merged goals and returns non-zero if unmet.")
    p.add_argument("--cov_db", default="",
                   help="Path to a cumulative coverage DB (JSON). The 'cov' step "
                        "merges this run's coverage into this file; auto-regress "
                        "carries it across seeds. Defaults to <output>/coverage.json.")
    p.add_argument("--auto_regress", action="store_true",
                   help="Loop seeds (start_seed..start_seed+max_seeds-1) until "
                        "coverage goals are met. Requires --cov_goals.")
    p.add_argument("--max_seeds", type=int, default=64,
                   help="Upper bound on seeds tried by --auto_regress (default 64).")
    p.add_argument("--plateau_window", type=int, default=4,
                   help="Auto-regress bails if the last N seeds added zero "
                        "new bins AND goals still unmet (default 4).")
    p.add_argument("--asm_archive_keep", type=int, default=16,
                   help="Auto-regress keeps the last N per-seed .S snapshots "
                        "under asm_test/seed_archive/ (default 16).")
    p.add_argument("--iss_trace", action="store_true",
                   help="Enable spike -l trace output; the 'cov' step will "
                        "parse the trace for runtime coverage (branch taken/"
                        "not-taken, pc_reach, privilege transitions).")
    p.add_argument("--cov_directed", action="store_true",
                   help="When auto-regressing, perturb gen_opts each seed "
                        "based on currently-missing bins (e.g. drop "
                        "+no_fence=1 if FENCE is uncovered). Heuristic — "
                        "see chipforge_inst_gen.coverage.directed for the "
                        "mapping table.")

    # ISA
    p.add_argument("--isa", default="",
                   help="ISA string override (inferred from target when empty).")
    p.add_argument("-m", "--mabi", default="",
                   help="ABI override (inferred from target when empty).")

    # ISS / GCC
    p.add_argument("--iss", default="spike",
                   help="ISS to run (Phase 1: 'spike' only).")
    p.add_argument("--iss_timeout", type=int, default=30,
                   help="ISS timeout in seconds (default 30).")
    p.add_argument("--priv", default="m",
                   help="Privilege modes string for ISS (m/s/u/su).")
    p.add_argument("--gcc_opts", default="",
                   help="Extra options passed to riscv-gcc.")
    p.add_argument("--gen_opts", default="",
                   help="Plus-arg string appended to every test's gen_opts, "
                        "e.g. '+bare_program_mode=1 +no_csr_instr=1' for rv32ui.")

    # Paths / roots
    p.add_argument("--riscv_dv_root", type=str, default=str(_DEFAULT_RISCV_DV_ROOT),
                   help="Root directory of riscv-dv for <riscv_dv_root> substitution in testlists.")

    # Verbose / debug
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable debug logging.")
    p.add_argument("-d", "--debug", type=str, default="",
                   help="Write the generated commands to this file without executing them.")
    return p


def _infer_testlist_path(target: str, riscv_dv_root: Path) -> Path:
    return riscv_dv_root / "target" / target / "testlist.yaml"


def _infer_output(out: str) -> Path:
    if out:
        return Path(out)
    from datetime import date
    return Path(f"out_{date.today().isoformat()}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    riscv_dv_root = Path(args.riscv_dv_root)
    if not riscv_dv_root.exists():
        _LOG.warning("riscv_dv_root %s does not exist; testlist imports may fail", riscv_dv_root)

    testlist_path = Path(args.testlist) if args.testlist else _infer_testlist_path(args.target, riscv_dv_root)
    output_dir = _infer_output(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    asm_dir = output_dir / "asm_test"
    asm_dir.mkdir(exist_ok=True)

    target_cfg = get_target(args.target)

    # Build the seed generator. --seed forces iterations=1 (run.py semantics).
    if args.seed is not None:
        if args.iterations > 1:
            _LOG.error("--seed is incompatible with --iterations > 1")
            return 1
        seed_gen = SeedGen(fixed_seed=args.seed)
        iteration_override = 1
    elif args.seed_yaml is not None:
        seed_gen = SeedGen.from_yaml(args.seed_yaml)
        iteration_override = args.iterations
    else:
        seed_gen = SeedGen(start_seed=args.start_seed)
        iteration_override = args.iterations

    # Load the testlist.
    tests = load_testlist(
        testlist_path,
        riscv_dv_root=riscv_dv_root,
        test_filter=args.test,
        iteration_override=iteration_override,
    )
    if not tests:
        _LOG.error("No tests matched %r in %s", args.test, testlist_path)
        return 1

    # Auto-regression mode dispatches to a dedicated driver. Goals are
    # auto-resolved from the shipped defaults if --cov_goals wasn't given.
    if args.auto_regress:
        from chipforge_inst_gen.auto_regress import run_auto_regression
        return run_auto_regression(
            target_cfg=target_cfg,
            tests=tests,
            output_dir=output_dir,
            args=args,
            riscv_dv_root=riscv_dv_root,
        )

    steps = set(args.steps.split(",")) if args.steps != "all" else {"gen", "gcc_compile", "iss_sim", "iss_cmp"}

    import random

    from chipforge_inst_gen.isa import enums  # noqa: F401 — ensure ISA modules imported
    from chipforge_inst_gen.isa.filtering import create_instr_list
    from chipforge_inst_gen.asm_program_gen import AsmProgramGen

    # Coverage collection is cheap — always keep a per-run DB so the 'cov'
    # step can run against the same generator state without a re-run.
    # ``per_test_cov`` holds a separate DB per test_id so the reporter can
    # answer "which test contributed which bin".
    from chipforge_inst_gen.coverage import (
        CoverageDB,
        merge as cov_merge,
        sample_sequence as cov_sample_sequence,
    )
    from chipforge_inst_gen.coverage.collectors import new_db as new_cov_db

    run_cov: CoverageDB = new_cov_db()
    per_test_cov: dict[str, CoverageDB] = {}

    seen_seeds: dict[str, int] = {}
    if "gen" in steps:
        for te in tests:
            for it in range(te.iterations):
                test_id = f"{te.test}_{it}"
                seed = seed_gen.get(test_id, it)
                seen_seeds[test_id] = seed

                merged_gen_opts = (te.gen_opts or "") + " " + (args.gen_opts or "")
                cfg = make_config(target_cfg, gen_opts=merged_gen_opts)
                cfg.seed = seed

                avail = create_instr_list(cfg)
                rng = random.Random(seed)

                gen = AsmProgramGen(cfg=cfg, avail=avail, rng=rng)
                lines = gen.gen_program()

                asm_path = asm_dir / f"{te.test}_{it}.S"
                asm_path.write_text("\n".join(lines) + "\n")
                _LOG.info("Generated %s (seed=%d, %d lines)",
                          asm_path, seed, len(lines))

                # Sample the main sequence into both the per-test + run DB.
                # Fresh DB per test_id — caller can ask "which test closed
                # which bin" by inspecting per_test_cov afterwards. Forward
                # the active vector_cfg so vtype_dyn_cg gets populated.
                if gen.main_sequence is not None and gen.main_sequence.instr_stream is not None:
                    per_test = new_cov_db()
                    cov_sample_sequence(
                        per_test,
                        gen.main_sequence.instr_stream.instr_list,
                        vector_cfg=cfg.vector_cfg,
                    )
                    per_test_cov[test_id] = per_test
                    cov_merge(run_cov, per_test)

    if seen_seeds:
        seed_gen.dump(output_dir / "seed.yaml", seen_seeds)
        _LOG.info("Saved %d seeds to %s", len(seen_seeds), output_dir / "seed.yaml")

    # ---- Optional gcc_compile / iss_sim passes ----
    gcc_results: list = []
    if "gcc_compile" in steps:
        from chipforge_inst_gen.gcc import default_link_script, gcc_compile
        isa = args.isa or _infer_isa(args.target)
        mabi = args.mabi or _infer_mabi(args.target)
        link_script = default_link_script(output_dir)
        gcc_results = gcc_compile(
            tests,
            output_dir=output_dir,
            riscv_dv_root=riscv_dv_root,
            isa=isa,
            mabi=mabi,
            extra_gcc_opts=args.gcc_opts,
            link_script=link_script,
        )
        fails = [r for r in gcc_results if r.returncode != 0]
        if fails:
            _LOG.error("%d/%d tests failed to compile", len(fails), len(gcc_results))
            for r in fails:
                _LOG.error("  %s", r.test_id)

    iss_results: list = []
    if "iss_sim" in steps and gcc_results:
        from chipforge_inst_gen.iss import run_iss
        isa = args.isa or _infer_isa(args.target)
        iss_results = run_iss(
            args.iss,
            [r for r in gcc_results if r.returncode == 0],
            output_dir=output_dir,
            isa=isa,
            priv=args.priv,
            timeout_s=args.iss_timeout,
            enable_trace=args.iss_trace,
        )
        fails = [r for r in iss_results if r.returncode != 0]
        if fails:
            _LOG.error("%d/%d tests failed ISS sim", len(fails), len(iss_results))
            for r in fails:
                _LOG.error("  %s (rc=%d)", r.test_id, r.returncode)
            return 2
        _LOG.info("%d tests passed ISS sim", len(iss_results))

    # ---- Optional coverage step ----
    if "cov" in steps or "all" in (args.steps,):
        import json as _json
        from chipforge_inst_gen.coverage import (
            goals_met as cov_goals_met,
            load_goals as cov_load_goals,
            render_report as cov_render_report,
            sample_trace_file,
        )

        # Runtime coverage — if we enabled iss_trace and have traces, parse
        # them into the per-run DB before we merge-up into the cumulative.
        if args.iss_trace and iss_results:
            for r in iss_results:
                if r.trace_path and r.returncode == 0:
                    meta = sample_trace_file(run_cov, r.trace_path)
                    _LOG.info(
                        "runtime cov: %s -> %d lines, %d labels, %d branches",
                        r.test_id, meta["lines_parsed"],
                        meta["pc_reach_labels"], meta["branches_observed"],
                    )

        # Cumulative DB path — either explicit or per-output-dir.
        cum_path = Path(args.cov_db) if args.cov_db else output_dir / "coverage.json"
        existing: CoverageDB = new_cov_db()
        if cum_path.exists():
            try:
                existing = _json.loads(cum_path.read_text())
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Could not read %s (%s); starting fresh", cum_path, exc)
                existing = new_cov_db()
        cov_merge(existing, run_cov)
        cum_path.write_text(_json.dumps(existing, indent=2, sort_keys=True))
        _LOG.info("Coverage DB updated: %s", cum_path)

        # Per-test attribution sidecar — one JSON dict keyed by test_id.
        if per_test_cov:
            per_test_path = output_dir / "coverage_per_test.json"
            per_test_path.write_text(
                _json.dumps(
                    {tid: db for tid, db in sorted(per_test_cov.items())},
                    indent=2, sort_keys=True,
                )
            )
            _LOG.info("Per-test coverage: %s", per_test_path)

        goals = None
        goals_paths = _resolve_cov_goals(args.cov_goals, args.target)
        if goals_paths:
            from chipforge_inst_gen.coverage import load_goals_layered
            goals = load_goals_layered(*goals_paths)
            _LOG.info("Coverage goals layered from: %s",
                      ", ".join(str(p) for p in goals_paths))

        report = cov_render_report(existing, goals)
        report_path = output_dir / "coverage_report.txt"
        report_path.write_text(report + "\n")
        _LOG.info("Coverage report: %s", report_path)

        # CI integration: emit GITHUB_OUTPUT / GITHUB_STEP_SUMMARY when
        # running under GitHub Actions (and similar under --ci_summary).
        _emit_ci_summary(existing, goals, report_path, test_count=len(tests))

        if goals is not None and not cov_goals_met(existing, goals):
            _LOG.warning("Coverage goals NOT met — see %s", report_path)
            return 3

    return 0


def _emit_ci_summary(
    db: "CoverageDB", goals, report_path: Path, *, test_count: int
) -> None:
    """Emit CI-friendly outputs when running under a CI system.

    Honors:

    - ``$GITHUB_OUTPUT`` — writes ``key=value`` lines consumed by
      subsequent steps via ``${{ steps.X.outputs.Y }}``.
    - ``$GITHUB_STEP_SUMMARY`` — writes a markdown table to the Job
      Summary panel. Makes every PR show the coverage delta inline.

    Silent when neither env var is set. Safe to call with goals=None.
    """
    import os as _os
    from chipforge_inst_gen.coverage.cgf import missing_bins

    from chipforge_inst_gen.coverage import compute_grade as _compute_grade

    total_unique = sum(
        1 for cg in db for bn, cnt in db.get(cg, {}).items() if cnt > 0
    )
    total_hits = sum(sum(b.values()) for b in db.values())
    miss = missing_bins(db, goals) if goals else {}
    total_missing = sum(len(v) for v in miss.values())
    required = sum(1 for b in goals.data.values() for v in b.values() if v > 0) if goals else 0
    met = required - total_missing
    pct = (met / required * 100.0) if required else 100.0
    grade = _compute_grade(db, goals)

    gh_output = _os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        try:
            with open(gh_output, "a") as f:
                f.write(f"unique_bins={total_unique}\n")
                f.write(f"total_hits={total_hits}\n")
                f.write(f"goals_met={met}\n")
                f.write(f"goals_total={required}\n")
                f.write(f"goals_pct={pct:.1f}\n")
                f.write(f"missing_bins={total_missing}\n")
                f.write(f"tests={test_count}\n")
                f.write(f"grade={grade}\n")
        except OSError:
            pass  # CI env may sandbox writes; don't crash.

    gh_summary = _os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        try:
            with open(gh_summary, "a") as f:
                f.write("### chipforge-inst-gen coverage\n\n")
                f.write(f"- **Grade: {grade}/100**\n")
                f.write(f"- Tests run: **{test_count}**\n")
                f.write(f"- Unique bins hit: **{total_unique}**\n")
                f.write(f"- Total samples: **{total_hits}**\n")
                if goals:
                    f.write(
                        f"- Goals met: **{met} / {required}** "
                        f"({pct:.1f}%) &mdash; "
                        f"{'✅' if total_missing == 0 else f'❌ {total_missing} missing'}\n"
                    )
                    if miss:
                        f.write("\n<details><summary>Missing bins</summary>\n\n")
                        for cg, bins in sorted(miss.items()):
                            f.write(f"- **{cg}**: " +
                                    ", ".join(f"`{bn}` ({o}/{r})"
                                              for bn, (o, r) in sorted(bins.items())) + "\n")
                        f.write("\n</details>\n")
                f.write(f"\nFull report: `{report_path}`\n")
        except OSError:
            pass


# Target → (isa, mabi) mapping matching run.py::load_config's dispatch.
_TARGET_ISA_MABI: dict[str, tuple[str, str]] = {
    "rv32i": ("rv32i_zicsr_zifencei", "ilp32"),
    # rv32ui: pure user-mode RV32I, NO zicsr / zifencei (core has no CSRs).
    "rv32ui": ("rv32i", "ilp32"),
    # rv32imc_zkn: RV32IMC + ratified Zkn crypto umbrella (chipforge MCU ISA).
    "rv32imc_zkn": ("rv32imc_zbkb_zbkc_zbkx_zknd_zkne_zknh_zicsr_zifencei", "ilp32"),
    # Full-crypto rv32 target used for stress regressions.
    "rv32imc_zkn_zks": (
        "rv32imc_zba_zbb_zbc_zbs_zbkb_zbkc_zbkx_zkn_zks_zicsr_zifencei",
        "ilp32",
    ),
    # RV64 crypto baseline.
    "rv64imc_zkn": ("rv64imc_zbkb_zbkc_zbkx_zkn_zicsr_zifencei", "lp64"),
    "rv32im": ("rv32im_zicsr_zifencei", "ilp32"),
    "rv32ic": ("rv32ic_zicsr_zifencei", "ilp32"),
    "rv32ia": ("rv32ia_zicsr_zifencei", "ilp32"),
    "rv32iac": ("rv32iac_zicsr_zifencei", "ilp32"),
    "rv32imac": ("rv32imac_zicsr_zifencei", "ilp32"),
    "rv32imafdc": ("rv32imafdc_zicsr_zifencei", "ilp32d"),
    "rv32if": ("rv32if_zicsr_zifencei", "ilp32f"),
    "rv32imcb": ("rv32imc_zba_zbb_zbc_zbs_zicsr_zifencei", "ilp32"),
    "rv32imc": ("rv32imc_zicsr_zifencei", "ilp32"),
    "rv32imc_sv32": ("rv32imc_zicsr_zifencei", "ilp32"),
    "multi_harts": ("rv32gc_zicsr_zifencei", "ilp32"),
    "rv64imc": ("rv64imc_zicsr_zifencei", "lp64"),
    "rv64imcb": ("rv64imc_zba_zbb_zbc_zbs_zicsr_zifencei", "lp64"),
    "rv64gc": ("rv64gc_zicsr_zifencei", "lp64"),
    "rv64gcv": ("rv64gcv_zicsr_zifencei", "lp64"),
    # Embedded vector (Zve*) profiles
    "coralnpu": ("rv32imf_zve32x_zicsr_zifencei_zbb", "ilp32f"),
    "rv32imc_zve32x": ("rv32imc_zve32x_zicsr_zifencei", "ilp32"),
    "rv32imfc_zve32f": ("rv32imfc_zve32f_zicsr_zifencei", "ilp32f"),
    "rv64imc_zve64x": ("rv64imc_zve64x_zicsr_zifencei", "lp64"),
    "rv64imafdc_zve64d": ("rv64imafdc_zve64d_zicsr_zifencei", "lp64d"),
    "rv64imafdc": ("rv64imafdc_zicsr_zifencei", "lp64"),
    "ml": ("rv64imc_zicsr_zifencei", "lp64"),
}


def _infer_isa(target: str) -> str:
    try:
        return _TARGET_ISA_MABI[target][0]
    except KeyError:
        raise SystemExit(f"Cannot infer ISA for target {target!r}; pass --isa.")


def _infer_mabi(target: str) -> str:
    try:
        return _TARGET_ISA_MABI[target][1]
    except KeyError:
        raise SystemExit(f"Cannot infer mabi for target {target!r}; pass --mabi.")


def _resolve_cov_goals(explicit: list[str], target: str) -> list[str]:
    """Return the effective goals-file list for this run.

    If ``explicit`` is non-empty, use exactly those (user knows best).
    Otherwise, look up the shipped defaults: always layer
    ``coverage/goals/baseline.yaml`` and, if present,
    ``coverage/goals/<target>.yaml`` on top. Returns an empty list if
    there are no shipped defaults either, meaning the 'cov' step will
    run without goals-based pass/fail (DB still written).
    """
    if explicit:
        return list(explicit)
    root = Path(__file__).parent / "coverage" / "goals"
    out: list[str] = []
    base = root / "baseline.yaml"
    if base.exists():
        out.append(str(base))
    tgt = root / f"{target}.yaml"
    if tgt.exists():
        out.append(str(tgt))
    return out


# _emit_wrapped_asm removed — AsmProgramGen now emits the full program.


if __name__ == "__main__":
    sys.exit(main())
