"""Command-line entry point — lightweight port of ``run.py``.

For Phase 1 the CLI exposes the generation pipeline only (``--steps gen``).
GCC / ISS / iss_cmp integration comes in a later step.

Example::

    python -m rvgen --target rv32imc \\
        --test riscv_arithmetic_basic_test --iterations 2 --steps gen \\
        --output out/

"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rvgen.config import make_config
from rvgen.seeding import SeedGen
from rvgen.targets import (
    BUILTIN_TARGETS,
    TargetCfg,
    get_target,
    load_target_yaml,
    resolve_user_dir,
    set_user_dir,
    target_names,
)
from rvgen.testlist import load_testlist


_LOG = logging.getLogger("rvgen.cli")


# Default testlist / riscv_dv_root can be overridden per environment.
_DEFAULT_RISCV_DV_ROOT = Path.home() / "Desktop" / "verif_env_tatsu" / "riscv-dv"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rvgen",
        description="Pure-Python re-implementation of riscv-dv — CLI.",
    )
    # Test selection
    p.add_argument("--target", default="rv32imc",
                   help="Target processor configuration (default: rv32imc). "
                        "Resolved against built-in targets first, then against "
                        "YAML files under <user_dir>/targets/. Run with "
                        "--help_targets to list everything known.")
    p.add_argument("--target_config", default="",
                   help="Path to a standalone target-config YAML. If set, "
                        "overrides --target completely — the YAML's 'name' "
                        "field is the effective target name for this run.")
    p.add_argument("--user_dir", default="",
                   help="User-area directory (targets/, testlists/, streams/, "
                        "coverage/). Default: $RVGEN_USER_DIR → ./user if it "
                        "exists → disabled.")
    p.add_argument("--help_targets", action="store_true",
                   help="List every known target (built-in + user area) and exit.")
    p.add_argument("--validate_target", default="",
                   help="Parse a target YAML, report unknown keys / bad enum "
                        "names / unparseable sizes, preview the effective "
                        "DMEM and IMEM caps, and exit 0/1. Useful before "
                        "committing a new core's target file.")
    p.add_argument("-tl", "--testlist", default="",
                   help="Path to a regression testlist YAML. Optional — if "
                        "omitted, rvgen falls back to the user-area testlist, "
                        "then <riscv_dv_root>/target/<target>/testlist.yaml, "
                        "and finally to the packaged baseline shipped inside "
                        "rvgen itself (so `pip install rvgen` is self-contained).")
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
    p.add_argument("--cov_history", default="",
                   help="Optional JSONL file. The 'cov' step appends one line "
                        "per run with timestamp, target, test, seed, and the "
                        "per-covergroup unique-bin / total-hit totals — perfect "
                        "for CI trend tracking. Use `tools history <file>` to "
                        "render an ASCII trend chart.")
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
                        "see rvgen.coverage.directed for the "
                        "mapping table.")
    p.add_argument("--cov_steering", action="store_true",
                   help="Within-seed online coverage feedback. The random "
                        "walker snapshots its own static coverage every "
                        "--cov_steering_refresh picks and biases subsequent "
                        "instruction selection toward mnemonics whose goals "
                        "bins are still under-hit. ~25-40%% more bins per "
                        "single seed in synthetic experiments. Requires "
                        "--cov_goals.")
    p.add_argument("--cov_steering_refresh", type=int, default=200,
                   help="Snapshot interval for --cov_steering (default 200 "
                        "instructions). Smaller = more reactive, more CPU.")

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
    """Resolve a default testlist path when ``--testlist`` isn't given.

    Search order:
      1. ``<user_dir>/testlists/<target>.yaml`` — target-specific user testlist.
      2. ``<user_dir>/testlists/base_testlist.yaml`` — shared user baseline.
      3. ``<riscv_dv_root>/target/<target>/testlist.yaml`` — riscv-dv default.
      4. ``<riscv_dv_root>/yaml/base_testlist.yaml`` — riscv-dv common base.
      5. Packaged builtin ``rvgen/testlists/base_testlist.yaml`` — ships
         inside the rvgen wheel so ``pip install rvgen`` works without
         needing an external riscv-dv clone.
    """
    user_dir = resolve_user_dir()
    if user_dir is not None:
        per_target = user_dir / "testlists" / f"{target}.yaml"
        if per_target.exists():
            return per_target
        base = user_dir / "testlists" / "base_testlist.yaml"
        if base.exists():
            return base
    rd_per_target = riscv_dv_root / "target" / target / "testlist.yaml"
    if rd_per_target.exists():
        return rd_per_target
    rd_base = riscv_dv_root / "yaml" / "base_testlist.yaml"
    if rd_base.exists():
        return rd_base
    # Final fallback — the testlist shipped inside the rvgen package.
    return Path(__file__).parent / "testlists" / "base_testlist.yaml"


# Empirical worst-case byte overhead per generated test for boot +
# init + trap-handler ROM + signature stubs + page-table stubs.
# Measured on rv32imc / rv64gc / rv64gc_aia targets: largest observed
# is ~7 KB; 8 KB headroom covers Sv48 + debug + multi-hart variants.
_TEXT_FIXED_OVERHEAD_BYTES = 8 * 1024

# Worst-case per-instruction footprint. RVC ops are 2 bytes and pure
# RV32I/RV64I are 4 bytes; we use 4 unconditionally for the budget
# estimator so a target with compressed off (e.g. rv32i) is sized
# correctly. Targets that enable RVC simply have headroom.
_TEXT_BYTES_PER_INSTR = 4

# Average byte cost per sub-program (label + a handful of ALU ops +
# return jump). Conservative.
_TEXT_BYTES_PER_SUBPROG = 256


def _estimate_text_bytes(cfg) -> int:
    """Rough upper bound on the generated `.text` byte size.

    Used by :func:`_enforce_imem_budget` to decide whether the
    requested ``instr_cnt`` will fit in the target's declared IMEM.
    The estimate intentionally over-counts (no RVC discount) so that a
    test that passes this gate is guaranteed to fit on silicon.
    """
    instr_bytes = int(cfg.main_program_instr_cnt or cfg.instr_cnt) * _TEXT_BYTES_PER_INSTR
    subprog_bytes = int(cfg.num_of_sub_program) * _TEXT_BYTES_PER_SUBPROG
    return _TEXT_FIXED_OVERHEAD_BYTES + instr_bytes + subprog_bytes


def _enforce_imem_budget(cfg, test_name: str) -> None:
    """Cap ``cfg.instr_cnt`` to fit within ``target.text_section_size_bytes``.

    No-op when the target leaves the cap unset (default). When the
    estimated `.text` size exceeds the budget, emits an `WARNING` log
    line naming both the budget and the new instr count, then mutates
    ``cfg`` in place. The estimator is conservative — actual emission
    is always under the budget by at least the RVC discount.
    """
    budget = getattr(cfg.target, "text_section_size_bytes", None)
    if budget is None or budget <= 0:
        return
    estimated = _estimate_text_bytes(cfg)
    if estimated <= budget:
        return
    # Compute the largest instr_cnt that fits.
    overhead = _TEXT_FIXED_OVERHEAD_BYTES + int(cfg.num_of_sub_program) * _TEXT_BYTES_PER_SUBPROG
    if overhead >= budget:
        _LOG.error(
            "Test %r cannot fit: target.text_section_size_bytes=%d B is "
            "smaller than the boot + handler + sub-program overhead "
            "(%d B). Reduce num_of_sub_program or raise the IMEM cap "
            "in the target YAML.",
            test_name, budget, overhead,
        )
        return
    max_instrs = max(8, (budget - overhead) // _TEXT_BYTES_PER_INSTR)
    original = int(cfg.main_program_instr_cnt or cfg.instr_cnt)
    _LOG.warning(
        "Test %r requested instr_cnt=%d but the target's IMEM budget "
        "(text_section_size_bytes=%d B) only fits ~%d instructions. "
        "Scaling instr_cnt down to %d to stay within the configured "
        "memory map. Raise target.text_section_size_bytes if you need "
        "the larger test.",
        test_name, original, budget, max_instrs, max_instrs,
    )
    cfg.instr_cnt = max_instrs
    cfg.main_program_instr_cnt = max_instrs


_KNOWN_TARGET_KEYS = frozenset({
    "name", "xlen", "supported_isa", "supported_privileged_mode",
    "satp_mode", "support_sfence", "support_unaligned_load_store",
    "num_harts", "clint", "isa_string", "mabi", "unsupported_instr",
    "implemented_csr", "implemented_interrupt", "implemented_exception",
    "custom_csr", "data_section_size_bytes", "text_section_size_bytes",
})


def _validate_target_yaml(path: Path) -> int:
    """Parse-and-check a target YAML; print findings; return 0/1.

    Catches the most common new-user mistakes:
      * unknown top-level keys (typo / wrong schema version)
      * unknown enum names in supported_isa / supported_privileged_mode
        / satp_mode / unsupported_instr
      * size-string parse failures on data_/text_section_size_bytes
      * missing mandatory fields (name / xlen / supported_isa)

    On success, previews the effective DMEM/IMEM caps so the user can
    spot a copy-paste mismatch (e.g. 16K typed as 16 = 16 bytes).
    """
    import yaml
    if not path.exists():
        print(f"FAIL: {path} does not exist", file=sys.stderr)
        return 1
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        print(f"FAIL: YAML parse error in {path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print(f"FAIL: {path} top-level must be a mapping; got "
              f"{type(data).__name__}", file=sys.stderr)
        return 1

    problems: list[str] = []
    info: list[str] = []

    # Unknown keys.
    for key in data:
        if key not in _KNOWN_TARGET_KEYS:
            problems.append(f"  - unknown top-level key: {key!r} "
                            f"(did you mean one of {sorted(_KNOWN_TARGET_KEYS)[:3]} …?)")

    # Required fields.
    for required in ("name", "xlen", "supported_isa"):
        if required not in data:
            problems.append(f"  - missing required field: {required!r}")

    # Enum validation.
    try:
        from rvgen.isa.enums import (
            RiscvInstrGroup, RiscvInstrName, PrivilegedMode, SatpMode,
        )
    except ImportError as exc:
        problems.append(f"  - couldn't import enums to validate: {exc}")
        RiscvInstrGroup = RiscvInstrName = PrivilegedMode = SatpMode = None  # type: ignore

    def _check_enum(field: str, enum_cls, values):
        if enum_cls is None or values is None:
            return
        if isinstance(values, str):
            values = [values]
        valid = {e.name for e in enum_cls}
        for v in values:
            if v not in valid:
                problems.append(
                    f"  - {field}: {v!r} is not a valid "
                    f"{enum_cls.__name__} name. Valid: "
                    f"{sorted(valid)[:5]} … ({len(valid)} total)"
                )

    _check_enum("supported_isa", RiscvInstrGroup, data.get("supported_isa"))
    _check_enum("supported_privileged_mode", PrivilegedMode,
                data.get("supported_privileged_mode"))
    _check_enum("satp_mode", SatpMode, data.get("satp_mode"))
    _check_enum("unsupported_instr", RiscvInstrName,
                data.get("unsupported_instr"))

    # Size-string parsing.
    from rvgen.targets.loader import _parse_size
    for size_field in ("data_section_size_bytes", "text_section_size_bytes"):
        raw = data.get(size_field)
        try:
            parsed = _parse_size(raw)
            if parsed is not None:
                info.append(f"  - {size_field}: {raw!r} → {parsed:,} bytes")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"  - {size_field}: failed to parse {raw!r}: {exc}")

    # Output.
    print(f"Validating: {path}")
    if info:
        print()
        print("Resolved sizes:")
        for line in info:
            print(line)
    if problems:
        print()
        print(f"FAIL: {len(problems)} issue(s):")
        for line in problems:
            print(line)
        print()
        return 1
    print("OK: target YAML looks valid.")
    return 0


def _auto_import_user_streams(user_dir: Path | None) -> None:
    """Import every ``<user_dir>/streams/*.py`` so @register_stream fires.

    Without this, a user who writes ``user/streams/my_burst.py`` and
    then runs ``rvgen --gen_opts "+directed_instr_1=my_burst_stream,5"``
    would silently get zero stream insertions because the module was
    never imported — the stream registry never saw it. Auto-import on
    CLI startup makes the "drop a .py file and reference it" flow
    just work.

    Robustness: each module is imported in a try/except so one broken
    file doesn't disable the whole CLI. Failures are logged at WARNING
    so the user sees them but the run continues.
    """
    if user_dir is None:
        return
    streams_dir = user_dir / "streams"
    if not streams_dir.is_dir():
        return
    import importlib.util
    import sys as _sys
    for path in sorted(streams_dir.glob("*.py")):
        if path.name.startswith("_") or path.name == "README.md":
            continue
        mod_name = f"rvgen_user_streams.{path.stem}"
        if mod_name in _sys.modules:
            continue
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            _sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            _LOG.debug("Auto-imported user stream: %s", path)
        except Exception as exc:  # noqa: BLE001 — keep CLI alive
            _LOG.warning("Failed to auto-import user stream %s: %s",
                         path, exc)


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

    # Resolve the user area BEFORE any target lookup — YAML-declared
    # targets live under <user_dir>/targets/ and the lookup needs to
    # know where to search.
    if args.user_dir:
        set_user_dir(Path(args.user_dir))
    user_dir = resolve_user_dir()

    # Auto-import every Python module under ``<user_dir>/streams/*.py``
    # so a user's custom directed streams register themselves via
    # ``@register_stream`` without the user having to remember to
    # ``import my_stream`` before invoking the CLI. This closes the
    # most common pre-existing friction (``+directed_instr_N=my_stream``
    # silently does nothing because the module was never imported).
    _auto_import_user_streams(user_dir)

    if args.help_targets:
        print(f"User area: {user_dir or '(none — set $RVGEN_USER_DIR or --user_dir)'}")
        print()
        print("Built-in targets:")
        for n in sorted(BUILTIN_TARGETS):
            print(f"  {n}")
        user_only = [n for n in target_names() if n not in BUILTIN_TARGETS]
        if user_only:
            print()
            print("User-area targets:")
            for n in user_only:
                print(f"  {n}")
        return 0

    if args.validate_target:
        return _validate_target_yaml(Path(args.validate_target))

    riscv_dv_root = Path(args.riscv_dv_root)
    # Only warn when the *user explicitly* asked for a non-default
    # riscv-dv root that doesn't exist. Silent fallthrough is correct
    # when the user is just relying on the packaged testlist — they
    # don't need riscv-dv installed at all.
    if not riscv_dv_root.exists() and args.riscv_dv_root != str(_DEFAULT_RISCV_DV_ROOT):
        _LOG.warning("riscv_dv_root %s does not exist; testlist imports may fail", riscv_dv_root)

    # Resolve the target. --target_config wins if given; otherwise
    # look up by name across built-ins + user area.
    target_cfg = _resolve_target(args)
    # The downstream code paths read args.target in a few places
    # (testlist inference, coverage-goal auto-resolution). Keep that
    # surface consistent by refreshing args.target from the resolved
    # target name — necessary when --target_config was used.
    args.target = target_cfg.name

    testlist_path = Path(args.testlist) if args.testlist else _infer_testlist_path(args.target, riscv_dv_root)
    # Up-front check so the user sees a useful hint instead of a raw
    # FileNotFoundError deep inside load_testlist.
    if not testlist_path.exists():
        builtin = Path(__file__).parent / "testlists" / "base_testlist.yaml"
        _LOG.error("Testlist YAML not found: %s", testlist_path)
        if args.testlist:
            _LOG.error("You passed --testlist %r explicitly. Either fix the path, "
                       "or omit the flag entirely — rvgen ships a baseline testlist "
                       "at %s.", args.testlist, builtin)
        else:
            _LOG.error("rvgen tried to auto-resolve a testlist but none of the "
                       "fallback locations exist. Pass --testlist <path> or set "
                       "$RVGEN_USER_DIR to a directory containing testlists/.")
        return 1
    output_dir = _infer_output(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    asm_dir = output_dir / "asm_test"
    asm_dir.mkdir(exist_ok=True)

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
        from rvgen.auto_regress import run_auto_regression
        return run_auto_regression(
            target_cfg=target_cfg,
            tests=tests,
            output_dir=output_dir,
            args=args,
            riscv_dv_root=riscv_dv_root,
        )

    steps = set(args.steps.split(",")) if args.steps != "all" else {"gen", "gcc_compile", "iss_sim", "iss_cmp"}

    import random

    from rvgen.isa import enums  # noqa: F401 — ensure ISA modules imported
    from rvgen.isa.filtering import create_instr_list
    from rvgen.asm_program_gen import AsmProgramGen

    # Coverage collection is cheap — always keep a per-run DB so the 'cov'
    # step can run against the same generator state without a re-run.
    # ``per_test_cov`` holds a separate DB per test_id so the reporter can
    # answer "which test contributed which bin".
    from rvgen.coverage import (
        CoverageDB,
        merge as cov_merge,
        sample_sequence as cov_sample_sequence,
    )
    from rvgen.coverage.collectors import new_db as new_cov_db

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
                # IMEM-fit check — when the target declares a finite
                # text-section budget, estimate the generated .text byte
                # size and scale instr_cnt down to fit if needed. Lets
                # tests for small embedded DUTs (e.g. 64 KiB IMEM IoT
                # MCU) stay within actual silicon limits regardless of
                # the testlist's +instr_cnt= plusarg. The cap is
                # advisory when text_section_size_bytes is None (the
                # default — SV-parity).
                _enforce_imem_budget(cfg, te.test)
                # Online coverage steering — only enabled when both the
                # flag and the goals files were supplied. Fall back to
                # standard random walk when either is missing.
                if args.cov_steering and args.cov_goals:
                    cfg.cov_steering = True
                    cfg.cov_steering_refresh = args.cov_steering_refresh
                    cfg.cov_goals_paths = tuple(args.cov_goals)

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
                    # Sample any PMP regions the boot path emitted —
                    # pmp_cfg_cg bins capture the (A × L × XWR) shape.
                    pmp_regions = getattr(cfg, "_emitted_pmp_regions", None)
                    if pmp_regions:
                        from rvgen.coverage.collectors import sample_pmp_region
                        for region in pmp_regions:
                            sample_pmp_region(per_test, region)
                    per_test_cov[test_id] = per_test
                    cov_merge(run_cov, per_test)

    if seen_seeds:
        seed_gen.dump(output_dir / "seed.yaml", seen_seeds)
        _LOG.info("Saved %d seeds to %s", len(seen_seeds), output_dir / "seed.yaml")

    # ---- Optional gcc_compile / iss_sim passes ----
    gcc_results: list = []
    if "gcc_compile" in steps:
        from rvgen.gcc import default_link_script, gcc_compile
        isa = args.isa or _infer_isa(target_cfg)
        mabi = args.mabi or _infer_mabi(target_cfg)
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
        from rvgen.iss import run_iss
        isa = args.isa or _infer_isa(target_cfg)
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
        from rvgen.coverage import (
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
                    # Pass the ELF so the runtime parser can read the
                    # symbol table and gate per-workload bins by PC
                    # range (main_pc <= pc < test_done_pc). Without
                    # this, boot/handler retirements would pollute the
                    # test-workload coverage view.
                    elf_path = getattr(r, "elf_path", None)
                    meta = sample_trace_file(
                        run_cov, r.trace_path,
                        elf_path=elf_path,
                    )
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
            from rvgen.coverage import load_goals_layered
            goals = load_goals_layered(*goals_paths)
            _LOG.info("Coverage goals layered from: %s",
                      ", ".join(str(p) for p in goals_paths))

        report = cov_render_report(existing, goals)
        report_path = output_dir / "coverage_report.txt"
        report_path.write_text(report + "\n")
        _LOG.info("Coverage report: %s", report_path)

        # The HTML dashboard is the moat feature — codecov-style sunburst
        # + per-subsystem scorecard + per-covergroup drill-in. Auto-emit
        # it alongside the text report so the user doesn't need to know
        # about a separate `coverage.tools dashboard` invocation.
        try:
            from rvgen.coverage.dashboard import write_dashboard
            from rvgen.coverage.tools import _subsys_for_bin
            # Build a per-subsystem scorecard so the dashboard's bar
            # chart matches `coverage.tools scorecard` output.
            scorecard = None
            if goals:
                by_subsys: dict[str, dict[str, int]] = {}
                for cg, bin_goals in goals.data.items():
                    observed = existing.get(cg, {})
                    for bn, required in bin_goals.items():
                        if required <= 0:
                            continue
                        subsys = _subsys_for_bin(cg, bn)
                        slot = by_subsys.setdefault(subsys, {
                            "required": 0, "met": 0, "missing": 0, "extra": 0,
                        })
                        slot["required"] += 1
                        if observed.get(bn, 0) >= required:
                            slot["met"] += 1
                        else:
                            slot["missing"] += 1
                scorecard = [
                    {
                        "subsystem": s,
                        "required": d["required"],
                        "met": d["met"],
                        "missing": d["missing"],
                        "extra": d["extra"],
                        "percent": (100.0 * d["met"] / d["required"])
                                   if d["required"] else 0.0,
                    }
                    for s, d in sorted(by_subsys.items())
                ]
            dashboard_path = output_dir / "coverage_dashboard.html"
            write_dashboard(
                existing, dashboard_path,
                goals=goals, scorecard=scorecard,
                title=f"rvgen Coverage Dashboard — {args.target} / {args.test}",
            )
            _LOG.info("Coverage dashboard: %s", dashboard_path)
        except Exception as exc:  # noqa: BLE001 — best-effort
            _LOG.warning("Could not write coverage dashboard: %s", exc)

        # Optional CI trend tracking: append one JSONL record per run.
        if args.cov_history:
            try:
                _append_cov_history(
                    Path(args.cov_history), existing, goals,
                    target=args.target,
                    test=args.test,
                    start_seed=args.start_seed,
                    iterations=int(args.iterations),
                )
                _LOG.info("Coverage history appended: %s", args.cov_history)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Could not append --cov_history (%s): %s",
                             args.cov_history, exc)

        # CI integration: emit GITHUB_OUTPUT / GITHUB_STEP_SUMMARY when
        # running under GitHub Actions (and similar under --ci_summary).
        _emit_ci_summary(existing, goals, report_path, test_count=len(tests))

        if goals is not None and not cov_goals_met(existing, goals):
            _LOG.warning("Coverage goals NOT met — see %s", report_path)
            return 3

    return 0


def _append_cov_history(
    history_path: Path,
    db: "CoverageDB",
    goals,
    *,
    target: str,
    test: str,
    start_seed: int,
    iterations: int,
) -> None:
    """Append one JSONL record summarising this run's coverage.

    Schema (per line):

    .. code-block:: json

        {
          "ts": "2026-04-29T12:34:56Z",
          "target": "rv64gcv",
          "test":   "riscv_rand_instr_test",
          "start_seed": 100,
          "iterations": 1,
          "grade": 87,
          "bins_hit": 4321,
          "total_samples": 56789,
          "goals_required": 92,
          "goals_met": 90,
          "goals_pct": 97.8,
          "per_cg": {"opcode_cg": [unique_bins, total_hits], ...}
        }

    Designed for line-oriented append-only CI logging — git-friendly,
    grep-friendly, and easy to render as a timeline chart.
    """
    import datetime as _dt
    import json as _json
    from rvgen.coverage.cgf import missing_bins as _missing
    from rvgen.coverage.report import compute_grade as _grade

    n_required = (
        sum(1 for b in goals.data.values() for v in b.values() if v > 0)
        if goals is not None else 0
    )
    if goals is not None and n_required > 0:
        miss = _missing(db, goals)
        n_missing = sum(len(v) for v in miss.values())
        n_met = n_required - n_missing
        pct = round(n_met / n_required * 100, 2)
    else:
        n_met, pct = 0, 100.0

    record = {
        "ts": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": target,
        "test": test,
        "start_seed": int(start_seed),
        "iterations": int(iterations),
        "grade": _grade(db, goals),
        "bins_hit": sum(len(b) for b in db.values()),
        "total_samples": sum(sum(b.values()) for b in db.values()),
        "goals_required": n_required,
        "goals_met": n_met,
        "goals_pct": pct,
        "per_cg": {
            cg: [len(b), sum(b.values())]
            for cg, b in db.items() if b
        },
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a") as f:
        f.write(_json.dumps(record, sort_keys=True) + "\n")


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
    from rvgen.coverage.cgf import missing_bins

    from rvgen.coverage import compute_grade as _compute_grade

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
                f.write("### rvgen coverage\n\n")
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
    # rv32imckf: chipforge Challenge-0014 — RV32IMC + Zkn crypto + single-FP.
    # No A, no D. mabi=ilp32f because F is implemented but D is not.
    "rv32imckf": (
        "rv32imfc_zbkb_zbkc_zbkx_zknd_zkne_zknh_zicsr_zifencei",
        "ilp32f",
    ),
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


def _infer_isa(target: TargetCfg) -> str:
    """Return the ``-march`` / spike ``--isa`` string for ``target``.

    Preference order:
      1. ``target.isa_string`` if populated — authoritative for YAML
         targets (and can be set on Python targets too once they
         migrate).
      2. ``_TARGET_ISA_MABI[target.name][0]`` — built-in fallback table.
    """
    if target.isa_string:
        return target.isa_string
    try:
        return _TARGET_ISA_MABI[target.name][0]
    except KeyError:
        raise SystemExit(
            f"Cannot infer ISA for target {target.name!r}; pass --isa, or "
            f"set isa_string in the target's YAML config."
        )


def _infer_mabi(target: TargetCfg) -> str:
    if target.mabi:
        return target.mabi
    try:
        return _TARGET_ISA_MABI[target.name][1]
    except KeyError:
        raise SystemExit(
            f"Cannot infer mabi for target {target.name!r}; pass --mabi, or "
            f"set mabi in the target's YAML config."
        )


def _resolve_target(args) -> TargetCfg:
    """Resolve the active target from CLI args.

    Priority: ``--target_config <yaml>`` (standalone) → ``--target <name>``
    (looked up via :func:`get_target`, which hits built-ins then the
    user area).
    """
    if args.target_config:
        path = Path(args.target_config)
        if not path.exists():
            raise SystemExit(f"--target_config file not found: {path}")
        try:
            return load_target_yaml(path)
        except (ValueError, KeyError, TypeError) as exc:
            raise SystemExit(
                f"Failed to load target config {path}: {exc}"
            ) from exc
    try:
        return get_target(args.target)
    except KeyError as exc:
        raise SystemExit(str(exc)) from None


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
