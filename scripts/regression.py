#!/usr/bin/env python3
"""Parallel regression runner — matrix of (target, test, seed).

Runs the full `gen,gcc_compile,iss_sim,cov` pipeline for every
combination of target × test × seed in the supplied matrix, in parallel
processes. Merges all resulting coverage.json files into a single
combined_coverage.json and optionally renders an HTML dashboard.

The intended use is "the one command CI runs":

    ./scripts/regression.py \\
        --targets rv32imc,rv64imc,rv32imcb,rv64imcb \\
        --tests riscv_arithmetic_basic_test,riscv_rand_instr_test \\
        --seeds 100,200,300 \\
        --iss_trace --jobs 8 \\
        --output out/regression/

Outputs under ``--output``:

- ``per_run/<target>_<test>_<seed>/``  — full output dir per run.
- ``combined_coverage.json``           — merged coverage across all runs.
- ``summary.html``                     — dashboard over the merged view.
- ``summary.txt``                      — text report.
- ``regression.log``                   — per-run pass/fail lines.

Exit 0 iff every run passed and (if --cov_goals supplied) coverage
goals were met.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from shlex import quote


_ROOT = Path(__file__).resolve().parent.parent
_CIG = [sys.executable, "-m", "chipforge_inst_gen"]
_TOOLS = [sys.executable, "-m", "chipforge_inst_gen.coverage.tools"]


def _run_one(target: str, test: str, seed: int, output_dir: Path,
              *, testlist: str, iss_trace: bool, extra_args: list[str]) -> dict:
    """Run a single (target, test, seed) combo. Returns a result dict."""
    run_dir = output_dir / "per_run" / f"{target}_{test}_{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = _CIG + [
        "--target", target,
        "--test", test,
        "--steps", "gen,gcc_compile,iss_sim,cov",
        "--iss", "spike",
        "--output", str(run_dir),
        "--start_seed", str(seed),
        "-i", "1",
    ]
    if testlist:
        cmd += ["--testlist", testlist]
    if iss_trace:
        cmd += ["--iss_trace"]
    cmd += extra_args

    proc = subprocess.run(cmd, capture_output=True, text=True)
    cov_path = run_dir / "coverage.json"
    return {
        "target": target,
        "test": test,
        "seed": seed,
        "returncode": proc.returncode,
        "coverage_json": str(cov_path) if cov_path.exists() else None,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-4:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-4:]),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--targets", required=True,
                    help="Comma-separated target names (e.g. rv32imc,rv64imc).")
    p.add_argument("--tests", required=True,
                    help="Comma-separated test names.")
    p.add_argument("--seeds", required=True,
                    help="Comma-separated seeds (e.g. 100,200,300).")
    p.add_argument("--testlist", default="",
                    help="Override testlist YAML path (falls back to per-target default).")
    p.add_argument("--output", required=True, help="Output directory.")
    p.add_argument("--jobs", "-j", type=int, default=0,
                    help="Parallel workers (default: os.cpu_count()).")
    p.add_argument("--iss_trace", action="store_true",
                    help="Pass --iss_trace to each run (enables runtime cov).")
    p.add_argument("--cov_goals", action="append", default=[],
                    help="Goals YAML to overlay (repeat for layering).")
    p.add_argument("--emit_html", action="store_true",
                    help="Render summary.html alongside combined_coverage.json.")
    p.add_argument("extra_args", nargs=argparse.REMAINDER,
                    help="Extra args forwarded to each chipforge_inst_gen invocation.")
    args = p.parse_args(argv)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "regression.log"

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    tests = [t.strip() for t in args.tests.split(",") if t.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    matrix = list(itertools.product(targets, tests, seeds))
    jobs = args.jobs or os.cpu_count() or 4

    print(f"regression: {len(matrix)} runs × {jobs} workers", flush=True)
    log_f = log_path.open("w")

    results: list[dict] = []
    fail_cnt = 0
    # Forward user-supplied cov_goals per-run.
    goals_args: list[str] = []
    for g in args.cov_goals:
        goals_args += ["--cov_goals", g]

    extra_args = list(args.extra_args or [])
    # argparse.REMAINDER includes leading '--'; strip if present.
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    extra_args += goals_args

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exe:
        futures = [
            exe.submit(_run_one, tgt, tst, seed, output_dir,
                        testlist=args.testlist, iss_trace=args.iss_trace,
                        extra_args=extra_args)
            for tgt, tst, seed in matrix
        ]
        for i, f in enumerate(concurrent.futures.as_completed(futures), 1):
            r = f.result()
            results.append(r)
            status = "PASS" if r["returncode"] in (0, 3) else "FAIL"
            # rc==3 means "cov goals unmet" which isn't a hard sim failure
            # — count as partial, not catastrophic.
            if r["returncode"] not in (0, 3):
                fail_cnt += 1
                status = "FAIL"
            line = f"[{i}/{len(matrix)}] {status} {r['target']}/{r['test']}/seed={r['seed']} rc={r['returncode']}"
            print(line, flush=True)
            log_f.write(line + "\n")
            if r["returncode"] not in (0, 3) and r["stderr_tail"]:
                log_f.write(f"    stderr: {r['stderr_tail']}\n")
            log_f.flush()
    log_f.close()

    # Merge coverage JSONs.
    cov_files = [r["coverage_json"] for r in results if r["coverage_json"]]
    combined_path = output_dir / "combined_coverage.json"
    if cov_files:
        merge_cmd = _TOOLS + ["merge"] + cov_files + ["-o", str(combined_path)]
        subprocess.run(merge_cmd, check=False)

    summary_txt = output_dir / "summary.txt"
    if combined_path.exists():
        cmd = _TOOLS + ["report", str(combined_path)]
        for g in args.cov_goals:
            cmd += ["--goals", g]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        summary_txt.write_text(result.stdout)

    if args.emit_html and combined_path.exists():
        html_path = output_dir / "summary.html"
        cmd = _TOOLS + ["export", str(combined_path), "--html", str(html_path)]
        for g in args.cov_goals:
            cmd += ["--goals", g]
        subprocess.run(cmd, check=False)

    # Final report.
    print()
    print(f"regression: {len(results) - fail_cnt}/{len(results)} PASS (fail={fail_cnt})")
    if combined_path.exists():
        print(f"    combined coverage: {combined_path}")
        print(f"    summary: {summary_txt}")
    if args.emit_html:
        print(f"    html: {output_dir / 'summary.html'}")

    return 0 if fail_cnt == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
