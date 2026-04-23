"""Auto-regression driver — loop seeds until coverage goals are met.

Invoked from the CLI via ``--auto_regress``. For each seed in
``[start_seed, start_seed + max_seeds)``:

1. Generate the selected tests with that seed.
2. Sample the emitted sequences into the cumulative CoverageDB.
3. Check the goals; if every required bin is hit, stop and exit 0.

The driver uses *static* coverage only — it does not invoke GCC or ISS on
each seed (would be 10-100x slower). This is enough to tune the generator
to hit a coverage target; the caller can then re-run a full
``gen,gcc_compile,iss_sim`` pass for the chosen seeds if they want runtime
validation.

Output files (all under ``output_dir``):

- ``coverage.json``         — cumulative observed coverage.
- ``coverage_report.txt``   — human-readable summary (always written).
- ``auto_regress.log``      — per-seed progress log.
- ``asm_test/<test>_<it>.S`` — the *last* generated seed's assembly,
  so follow-up tooling has something to act on.

Exit codes:
  0 — goals met within max_seeds
  1 — goals not met after max_seeds (or no tests matched)
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from pathlib import Path
from typing import Iterable

from chipforge_inst_gen.config import make_config
from chipforge_inst_gen.coverage import (
    goals_met,
    load_goals,
    render_report,
)
from chipforge_inst_gen.coverage.collectors import (
    ALL_COVERGROUPS,
    new_db,
    sample_sequence,
)
from chipforge_inst_gen.coverage.collectors import CoverageDB, merge as cov_merge
from chipforge_inst_gen.testlist import TestEntry


_LOG = logging.getLogger("chipforge_inst_gen.auto_regress")


def _pct_bins_met(db: CoverageDB, goals) -> tuple[int, int, float]:
    """Return (met, total, pct) for the required (non-zero) bins in goals."""
    total_required = 0
    met = 0
    for cg, bins in goals.data.items():
        db_bins = db.get(cg, {})
        for bn, req in bins.items():
            if req <= 0:
                continue
            total_required += 1
            if db_bins.get(bn, 0) >= req:
                met += 1
    if total_required == 0:
        return 0, 0, 1.0
    return met, total_required, met / total_required


def run_auto_regression(
    *,
    target_cfg,
    tests: list[TestEntry],
    output_dir: Path,
    args: argparse.Namespace,
    riscv_dv_root: Path,
) -> int:
    """Entry point called from :mod:`chipforge_inst_gen.cli`."""
    from chipforge_inst_gen.asm_program_gen import AsmProgramGen
    from chipforge_inst_gen.isa import enums  # noqa: F401 — trigger registrations
    from chipforge_inst_gen.isa.filtering import create_instr_list

    goals = load_goals(args.cov_goals)

    # Cumulative DB path — re-use any existing DB so sequential runs chain.
    cum_path = Path(args.cov_db) if args.cov_db else output_dir / "coverage.json"
    if cum_path.exists():
        try:
            cum_db: CoverageDB = json.loads(cum_path.read_text())
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Could not parse existing %s (%s); starting fresh", cum_path, exc)
            cum_db = new_db()
    else:
        cum_db = new_db()

    asm_dir = output_dir / "asm_test"
    asm_dir.mkdir(parents=True, exist_ok=True)

    start_seed = args.start_seed if args.start_seed is not None else 0
    max_seeds = max(1, args.max_seeds)

    log_path = output_dir / "auto_regress.log"
    log_file = log_path.open("w")

    def _log(msg: str) -> None:
        _LOG.info(msg)
        log_file.write(msg + "\n")
        log_file.flush()

    _log(f"auto-regress: start_seed={start_seed} max_seeds={max_seeds} target={target_cfg.name}")
    _log(f"auto-regress: {len(tests)} test(s), goals={args.cov_goals}")

    if not tests:
        _LOG.error("No tests matched; nothing to do.")
        log_file.close()
        return 1

    for offset in range(max_seeds):
        seed = start_seed + offset
        rng = random.Random(seed)
        for te in tests:
            merged_gen_opts = (te.gen_opts or "") + " " + (args.gen_opts or "")
            cfg = make_config(target_cfg, gen_opts=merged_gen_opts)
            cfg.seed = seed
            avail = create_instr_list(cfg)
            rng_i = random.Random(seed ^ hash(te.test) & 0xFFFF_FFFF)

            gen = AsmProgramGen(cfg=cfg, avail=avail, rng=rng_i)
            lines = gen.gen_program()

            asm_path = asm_dir / f"{te.test}_0.S"
            asm_path.write_text("\n".join(lines) + "\n")

            if gen.main_sequence is not None and gen.main_sequence.instr_stream is not None:
                run_db = new_db()
                sample_sequence(run_db, gen.main_sequence.instr_stream.instr_list)
                cov_merge(cum_db, run_db)

        met, total, pct = _pct_bins_met(cum_db, goals)
        _log(f"  seed={seed} goals_met={met}/{total} ({pct*100:.1f}%)")

        # Periodic save so a kill doesn't lose progress.
        if offset % 8 == 0:
            cum_path.write_text(json.dumps(cum_db, indent=2, sort_keys=True))

        if goals_met(cum_db, goals):
            _log(f"auto-regress: goals met at seed={seed} ({offset + 1} seeds tried)")
            break
    else:
        _log(f"auto-regress: EXHAUSTED {max_seeds} seeds; goals NOT met")

    # Final persistence + report.
    cum_path.write_text(json.dumps(cum_db, indent=2, sort_keys=True))
    report_path = output_dir / "coverage_report.txt"
    report_path.write_text(render_report(cum_db, goals) + "\n")
    _log(f"auto-regress: wrote {cum_path} and {report_path}")
    log_file.close()

    return 0 if goals_met(cum_db, goals) else 1
