# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-04-29 — initial open-source release

First public release. The project has been developed in private against
the chipforge-mcu verification flow; this release packages it for
broader use.

### Added — generator core

- Pure-Python re-implementation of riscv-dv's instruction-generation
  pipeline. No SystemVerilog, UVM, or PyVSC dependencies.
- **518 instructions** registered across RV32I/M/A/C/F/FC/D/DC,
  RV64 counterparts, Zba/Zbb/Zbc/Zbs, draft RV32B,
  Zbkb/Zbkc/Zbkx/Zkne/Zknd/Zknh/Zksh/Zksed (ratified crypto),
  **RVV 1.0 (184 opcodes)**, and **Zvbb / Zvbc / Zvkn / Zvfh** (32
  ratified vector-bitmanip + crypto opcodes — rvgen-first; riscv-dv
  has no support for these).
- **28 targets** — rv32i through rv64gcv, plus bare rv32ui, 4 ratified
  crypto variants, 5 Zve* embedded-vector profiles (incl. the
  **Coral NPU** rv32imf_zve32x_zbb configuration), and the new
  rv64gcv_crypto with Zvbb/Zvbc/Zvkn/Zvfh enabled.
- **18 directed-stream classes** — JAL chain, JALR pairs, nested loop,
  LR/SC, AMO, the full SV-faithful scalar load/store family
  (LoadStoreBase + 8 locality/hazard/multi-page variants), plus
  **`riscv_vector_load_store_instr_stream`** (UNIT_STRIDED / STRIDED /
  INDEXED) and **`riscv_vector_amo_instr_stream`** — ports of the
  matching SV stream classes.
- **Vector knobs** plumbed through `Config.apply_plusarg`: `+vec_fp`,
  `+vec_narrowing_widening`, `+vec_quad_widening`,
  `+allow_illegal_vec_instr`, `+vec_reg_hazards`, `+enable_zvlsseg`,
  `+enable_fault_only_first_load`. Per-target `vector_amo_supported`
  flag gates pre-1.0 Zvamo opcodes (off by default since RVV-1.0
  toolchains reject them).
- **ISA-string strict validation** — nonsensical targets (e.g. `rv32imck`)
  are rejected by argparse `choices=` rather than silently accepted.

### Added — functional coverage

- **44 covergroups** (18 static + 10 runtime + 4 crosses + 12 vector-
  focused). See `docs/coverage.md` for the full catalogue. The 12
  vector covergroups (`vec_ls_addr_mode_cg`, `vec_eew_cg`,
  `vec_eew_vs_sew_cg`, `vec_emul_cg`, `vec_vm_cg`,
  `vec_vm_category_cross_cg`, `vec_amo_wd_cg`, `vec_va_variant_cg`,
  `vec_nfields_cg`, `vec_seg_addr_mode_cross_cg`,
  `vec_widening_narrowing_cg`, `vec_crypto_subext_cg`) are
  rvgen-first — no SV reference exists for them.
- **CGF-style YAML goals** with layered overlays (13 shipped —
  baseline + per-target including rv64gcv_crypto + per-test-scenario).
- **Coverage-directed auto-regression** — `--auto_regress --cov_directed`
  perturbs `gen_opts` per seed based on the currently-missing bin set.
  Baseline rv32imc goals close in 1 seed vs 8+ for blind seed-sweep.
  12 vector-aware perturbation rules drive vector coverage without
  manual seed sweeps.
- **Plateau detection** — bails early when seeds stop adding new bins.
- **Convergence tracking** — per-bin first-hit seed + per-seed new-bin
  counts + ASCII sparkline + `cov_timeline.json` for dashboards.
- **Per-test attribution** — `coverage_per_test.json` sidecar shows
  which test contributes which bins; `tools per-test` ranks by
  uniquely-owned bins.
- **Interactive HTML dashboard** — collapsible per-covergroup sections
  with progress bars + status badges, live filter input, sortable
  bin tables (click any column header), optional `--timeline` flag
  inlines a convergence sparkline. Single-file HTML, no JS deps.
- **CI integration** — `GITHUB_OUTPUT` + `GITHUB_STEP_SUMMARY` +
  composite 0-100 quality grade.
- **Coverage tools CLI** — `merge`, `diff`, `attribute`, `export`
  (CSV + HTML), `report`, `per-test`, `baseline-check`,
  `suggest-seeds`, `lint-goals`, `auto-goals` (auto-derive starter
  goals YAML from a target), `cov-explain` (preview which
  `--cov_directed` perturbations would fire for current coverage).

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

- 406 unit tests passing (`python -m pytest tests/ -q`).
- 51/51 end-to-end Spike runs on 17 tests × 3 seeds across rv32imc,
  rv32imafdc, rv32imcb, rv64imc, rv64imcb.
- 12/12 end-to-end spike-vector runs on rv64gcv covering arithmetic /
  arithmetic_stress / load_store / vector_amo (4 tests × 3 seeds).
- 5/5 rv64gcv_crypto runs with thousands of Zvbb/Zvbc/Zvkn ops
  emitted per 8000-instr test.
- 5/5 Zve*/coralnpu runs on spike-vector.
- 21/21 instruction-by-instruction trace matches against chipforge-mcu
  RTL (7 tests × 3 seeds).
- 1 integration-regression test (golden-coverage floor) anchoring
  the fixed-seed rv32imc run.

[0.1.0]: https://github.com/LogicX-Tatsu/rvgen/releases/tag/v0.1.0
[Unreleased]: https://github.com/LogicX-Tatsu/rvgen/compare/v0.1.0...HEAD
