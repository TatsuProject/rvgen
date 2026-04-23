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

from rvgen.config import make_config
from rvgen.coverage import (
    goals_met,
    load_goals,
    load_goals_layered,
    render_report,
)
from rvgen.coverage.collectors import (
    ALL_COVERGROUPS,
    new_db,
    sample_sequence,
)
from rvgen.coverage.collectors import CoverageDB, merge as cov_merge
from rvgen.testlist import TestEntry


_LOG = logging.getLogger("rvgen.auto_regress")


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


def _count_unique_bins(db: CoverageDB) -> int:
    """Total number of (cg, bin) pairs with at least one hit."""
    return sum(1 for cg in db for bn, cnt in db.get(cg, {}).items() if cnt > 0)


def _sparkline(values: list[int], width: int = 40) -> str:
    """Render a tiny ASCII/Unicode sparkline of per-seed new-bins counts.

    Uses the standard block-element glyphs (▁▂▃▄▅▆▇█) scaled to the max
    value. Downsamples by chunk-averaging when we have more seeds than
    target width; returns an empty-placeholder when values is empty.
    """
    if not values:
        return "(no seeds)"
    peak = max(values) or 1
    blocks = " ▁▂▃▄▅▆▇█"  # idx 0 = zero, 1..8 = scaled
    if len(values) > width:
        chunk = len(values) / width
        values = [
            sum(values[int(i * chunk):int((i + 1) * chunk)])
            // max(1, int((i + 1) * chunk) - int(i * chunk))
            for i in range(width)
        ]
    out = []
    for v in values:
        idx = 0 if v == 0 else 1 + min(7, int((v / peak) * 7.999))
        out.append(blocks[idx])
    return "".join(out) + f"  peak={peak}"


def _convergence_stamp(
    db: CoverageDB, seed: int, convergence: dict[tuple[str, str], int]
) -> int:
    """For every newly-hit bin in ``db``, record ``seed`` as its first-hit
    seed in ``convergence``. Returns the count of bins first hit this seed.
    """
    new = 0
    for cg, bins in db.items():
        for bn, cnt in bins.items():
            if cnt <= 0:
                continue
            key = (cg, bn)
            if key not in convergence:
                convergence[key] = seed
                new += 1
    return new


def run_auto_regression(
    *,
    target_cfg,
    tests: list[TestEntry],
    output_dir: Path,
    args: argparse.Namespace,
    riscv_dv_root: Path,
) -> int:
    """Entry point called from :mod:`rvgen.cli`."""
    from rvgen.asm_program_gen import AsmProgramGen
    from rvgen.isa import enums  # noqa: F401 — trigger registrations
    from rvgen.isa.filtering import create_instr_list

    # Support both repeated --cov_goals (list) and legacy single-path string.
    from rvgen.cli import _resolve_cov_goals  # lazy import — avoids cycle
    explicit = args.cov_goals if isinstance(args.cov_goals, list) else [args.cov_goals]
    explicit = [p for p in explicit if p]
    goals_paths = _resolve_cov_goals(explicit, target_cfg.name)
    if not goals_paths:
        _LOG.error("--auto_regress needs goals; none explicit and no shipped "
                   "goals/%s.yaml or goals/baseline.yaml found.", target_cfg.name)
        return 1
    goals = load_goals_layered(*goals_paths)
    _LOG.info("auto-regress: goals layered from %s", ", ".join(str(p) for p in goals_paths))

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

    # Coverage-directed mode uses the current missing-bin set to perturb
    # per-seed gen_opts. Enabled via args.cov_directed.
    use_directed = bool(getattr(args, "cov_directed", False))
    if use_directed:
        from rvgen.coverage.directed import directed_gen_opts

    # Convergence tracking:
    # - convergence[(cg, bn)] = the seed that first caused this bin to hit.
    # - plateau: if the last `plateau_window` seeds added zero new bins,
    #   bail early (we've converged). This is independent of --max_seeds.
    convergence: dict[tuple[str, str], int] = {}
    plateau_window = max(3, int(getattr(args, "plateau_window", 4) or 4))
    new_bins_by_seed: list[int] = []
    # Stamp any pre-existing bins (carried forward from a prior run) with
    # seed=-1 so the analysis distinguishes "was already there" from "closed
    # in this run".
    _convergence_stamp(cum_db, -1, convergence)

    for offset in range(max_seeds):
        seed = start_seed + offset
        rng = random.Random(seed)
        for te in tests:
            merged_gen_opts = (te.gen_opts or "") + " " + (args.gen_opts or "")
            if use_directed:
                merged_gen_opts, reasons = directed_gen_opts(
                    merged_gen_opts, cum_db, goals,
                )
                for r in reasons:
                    _log(f"    directed: {r}")
            cfg = make_config(target_cfg, gen_opts=merged_gen_opts)
            cfg.seed = seed
            avail = create_instr_list(cfg)
            rng_i = random.Random(seed ^ hash(te.test) & 0xFFFF_FFFF)

            gen = AsmProgramGen(cfg=cfg, avail=avail, rng=rng_i)
            lines = gen.gen_program()

            # Per-seed archive — the "current" file (used by the next
            # iteration) plus a named snapshot per seed so verif engineers
            # can replay a specific seed's exact .S.
            asm_path = asm_dir / f"{te.test}_0.S"
            asm_path.write_text("\n".join(lines) + "\n")
            snapshot_dir = asm_dir / "seed_archive"
            snapshot_dir.mkdir(exist_ok=True)
            snapshot_path = snapshot_dir / f"{te.test}_seed{seed}.S"
            snapshot_path.write_text("\n".join(lines) + "\n")

            # Rotating buffer: keep the last ``asm_archive_keep`` snapshots
            # to avoid blowing up the disk on a 1000-seed run. Default 16.
            keep = max(1, int(getattr(args, "asm_archive_keep", 16) or 16))
            snapshots = sorted(snapshot_dir.glob(f"{te.test}_seed*.S"))
            for old in snapshots[:-keep]:
                try:
                    old.unlink()
                except OSError:
                    pass

            if gen.main_sequence is not None and gen.main_sequence.instr_stream is not None:
                run_db = new_db()
                sample_sequence(
                    run_db,
                    gen.main_sequence.instr_stream.instr_list,
                    vector_cfg=cfg.vector_cfg,
                )
                cov_merge(cum_db, run_db)

        met, total, pct = _pct_bins_met(cum_db, goals)
        new_bins_this_seed = _convergence_stamp(cum_db, seed, convergence)
        new_bins_by_seed.append(new_bins_this_seed)
        total_unique = _count_unique_bins(cum_db)
        _log(f"  seed={seed} goals_met={met}/{total} ({pct*100:.1f}%) "
             f"unique_bins={total_unique} new_this_seed={new_bins_this_seed}")

        # Periodic save so a kill doesn't lose progress.
        if offset % 8 == 0:
            cum_path.write_text(json.dumps(cum_db, indent=2, sort_keys=True))

        if goals_met(cum_db, goals):
            _log(f"auto-regress: goals met at seed={seed} ({offset + 1} seeds tried)")
            break

        # Plateau detection — if the last `plateau_window` seeds added zero
        # new bins AND goals still aren't met, we've converged without
        # closing the goals. Keep going a bit to be sure, then bail.
        if len(new_bins_by_seed) >= plateau_window and \
                all(n == 0 for n in new_bins_by_seed[-plateau_window:]):
            _log(
                f"auto-regress: plateaued at seed={seed} (last "
                f"{plateau_window} seeds added no new bins). Bailing — "
                f"goals NOT met; remaining gap may need target-specific "
                f"streams or --cov_directed mode."
            )
            break
    else:
        _log(f"auto-regress: EXHAUSTED {max_seeds} seeds; goals NOT met")

    # Final persistence + report + convergence sidecar.
    cum_path.write_text(json.dumps(cum_db, indent=2, sort_keys=True))
    report_path = output_dir / "coverage_report.txt"
    report_path.write_text(render_report(cum_db, goals) + "\n")

    # Visual convergence summary — a sparkline of new-bins-per-seed.
    if new_bins_by_seed:
        _log("auto-regress: convergence " + _sparkline(new_bins_by_seed))
        _log(f"auto-regress: seeds tried={len(new_bins_by_seed)} "
             f"total_new_bins={sum(new_bins_by_seed)} "
             f"median_per_seed={sorted(new_bins_by_seed)[len(new_bins_by_seed)//2]}")

    # Timeline sidecar — a time-series view of coverage accumulation for
    # external dashboards (D3 / matplotlib / grafana CSV import).
    timeline_path = output_dir / "cov_timeline.json"
    timeline = {
        "start_seed": start_seed,
        "per_seed": [
            {"seed_offset": i,
             "seed": start_seed + i,
             "new_bins": n,
             "cumulative_bins": sum(new_bins_by_seed[:i + 1])}
            for i, n in enumerate(new_bins_by_seed)
        ],
    }
    timeline_path.write_text(json.dumps(timeline, indent=2))

    # Convergence sidecar: per-bin first-hit seed + per-seed new-bin counts.
    conv_path = output_dir / "convergence.json"
    # Serialise the (cg, bn) tuple as "cg.bn" string key.
    conv_serialised = {
        f"{cg}.{bn}": seed_no for (cg, bn), seed_no in convergence.items()
    }
    conv_path.write_text(json.dumps({
        "first_hit_seed": dict(sorted(conv_serialised.items())),
        "new_bins_per_seed": new_bins_by_seed,
        "start_seed": start_seed,
        "final_goals_met": goals_met(cum_db, goals),
    }, indent=2))
    _log(f"auto-regress: wrote {cum_path} + {report_path} + {conv_path}")
    log_file.close()

    return 0 if goals_met(cum_db, goals) else 1
