# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Open-source preparation — no functional changes expected before v0.1.0.

## [0.1.0] — 2026-04-23 — initial open-source release

First public release. The project has been developed in private against
the chipforge-mcu verification flow; this release packages it for
broader use.

### Added — generator core

- Pure-Python re-implementation of riscv-dv's instruction-generation
  pipeline. No SystemVerilog, UVM, or PyVSC dependencies.
- **486 instructions** registered across RV32I/M/A/C/F/FC/D/DC,
  RV64 counterparts, Zba/Zbb/Zbc/Zbs, draft RV32B,
  Zbkb/Zbkc/Zbkx/Zkne/Zknd/Zknh/Zksh/Zksed (ratified crypto),
  and **RVV 1.0 (184 opcodes)**.
- **27 targets** — rv32i through rv64gcv, plus bare rv32ui, 4 ratified
  crypto variants, and 5 Zve* embedded-vector profiles (incl. the
  **Coral NPU** rv32imf_zve32x_zbb configuration).
- **16 directed-stream classes** — JAL chain, JALR pairs, nested loop,
  LR/SC, AMO, plus the full SV-faithful scalar load/store family
  (LoadStoreBase + 8 locality/hazard/multi-page variants).
- **ISA-string strict validation** — nonsensical targets (e.g. `rv32imck`)
  are rejected by argparse `choices=` rather than silently accepted.

### Added — functional coverage

- **32 covergroups** (18 static + 10 runtime + 4 crosses). See
  `docs/coverage.md` for the full catalogue.
- **CGF-style YAML goals** with layered overlays (12 shipped —
  baseline + per-target + per-test-scenario).
- **Coverage-directed auto-regression** — `--auto_regress --cov_directed`
  perturbs `gen_opts` per seed based on the currently-missing bin set.
  Baseline rv32imc goals close in 1 seed vs 8+ for blind seed-sweep.
- **Plateau detection** — bails early when seeds stop adding new bins.
- **Convergence tracking** — per-bin first-hit seed + per-seed new-bin
  counts + ASCII sparkline + `cov_timeline.json` for dashboards.
- **Per-test attribution** — `coverage_per_test.json` sidecar shows
  which test contributes which bins; `tools per-test` ranks by
  uniquely-owned bins.
- **CI integration** — `GITHUB_OUTPUT` + `GITHUB_STEP_SUMMARY` +
  composite 0-100 quality grade.
- **Coverage tools CLI** — `merge`, `diff`, `attribute`, `export`
  (CSV + HTML), `report`, `per-test`, `baseline-check`, `suggest-seeds`,
  `lint-goals`.

### Added — infrastructure

- **Parallel regression runner** (`scripts/regression.py`) — matrix
  (target × test × seed) runner with parallel workers + merged coverage
  + HTML dashboard.
- **Per-seed .S archive** — rotating buffer of per-seed assembly
  snapshots under `asm_test/seed_archive/` for replay.
- **chipforge-mcu trace-compare driver** (`scripts/mcu_validate.sh`) —
  instruction-by-instruction compare vs Spike on 7 tests × 3 seeds.

### Added — documentation

- `README.md` — quick start + feature tour + comparison table.
- `docs/verification-guide.md` — 9-section tutorial for verification
  engineers.
- `docs/coverage.md` — comprehensive reference (covergroups, goals,
  tools, worked examples).
- `docs/architecture.md` — module / data-flow overview with diagrams.
- `docs/testlist.md` — gen_opts + directed-stream reference.
- `docs/examples/` — annotated goals, custom-stream template,
  custom-testlist example, real rendered HTML coverage report.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CITATION.cff`.

### Validation

- 332 unit tests passing (`python -m pytest tests/ -q`).
- 51/51 end-to-end Spike runs on 17 tests × 3 seeds across rv32imc,
  rv32imafdc, rv32imcb, rv64imc, rv64imcb.
- 18/18 end-to-end spike-vector runs on rv64gcv (6 tests × 3 seeds).
- 5/5 Zve*/coralnpu runs on spike-vector.
- 21/21 instruction-by-instruction trace matches against chipforge-mcu
  RTL (7 tests × 3 seeds).
- 1 integration-regression test (golden-coverage floor) anchoring
  the fixed-seed rv32imc run.

[0.1.0]: https://github.com/<org>/rvgen/releases/tag/v0.1.0
[Unreleased]: https://github.com/<org>/rvgen/compare/v0.1.0...HEAD
