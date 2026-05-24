# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — `--help_tests` CLI flag (discoverability trio)

- New `rvgen --target <T> --help_tests` lists every test in the resolved
  testlist (per-target overlay → packaged baseline) with iterations and
  description, plus a runnable example command. Completes the
  discoverability trio alongside `--help_targets` and `--help_streams`.
  Closes the #1 onboarding question — "what tests can I run on this
  target?" — without grepping `yaml/`.
- Dedups imported entries (per-target testlists `import`
  `base_testlist.yaml` so the same test name often appears twice).
- 2 new unit tests cover output structure + dedup behaviour.

### Added — Cache-conflict directed stream + `cache_conflict_cg` (Tier-1 deep-coverage)

- New `CacheConflictInstrStream` (`rvgen/streams/load_store.py`,
  registered as `riscv_cache_conflict_instr_stream`). Generates loads
  and stores whose offsets share the cache-set bits modulo a tunable
  geometry (default 256 B / 4-way / 16 B-line, fitting in the default
  ~3 KB `region_0`), so a set-associative L1/L2 sees forced
  way-pressure and eviction patterns. Round-robins through `num_sets`
  distinct sets and pushes `ways + extra_per_set` accesses into each,
  guaranteeing at least `extra_per_set` evictions per set.
- New `cache_conflict_cg` covergroup with `way_pressure_1..way_pressure_8`,
  `eviction`, and `set_<N>` bins. Sampled per-instr from
  `_cache_conflict_pressure` / `_cache_conflict_set` attributes the
  stream stamps on each ld/st. Wired through the static collector,
  baseline goals, scorecard / dashboard / `report` subsystem grouping
  (Memory access).
- Steering perturbation: `cache_conflict_cg.eviction` and
  `way_pressure_8` empty bins trigger injection of
  `riscv_cache_conflict_instr_stream` via the auto-regress driver.
- 4 new unit tests in `tests/unit/test_streams.py` covering offset
  congruence, eviction-pressure guarantee, base-register pinning, and
  end-to-end coverage sampling.

## [0.2.0] — 2026-05-13

Second public release. Closes the largest credibility gap with
riscv-dv (paging, PMP, debug ROM), adds every modern checkbox
extension (Zicond, Zicbo*, Zihint*, Zimop, Zcmop), ships ~40 new
covergroups closing the riscv-isac / core-v-verif / ARM-DV gap
analysis, lands real bug fixes in the A-extension stream, and
makes the install self-contained (no external riscv-dv clone
required). Now published under the **Tatsu** GitHub organization
at https://github.com/TatsuProject/rvgen.

### Fixed — A-extension spec-compliance bugs (reported by external user)

A head-of-verification bug-hunt against the generator surfaced three
silent-but-real bugs in LR/SC/AMO emission that Spike tolerated but
that defeated A-ext coverage and produced non-spec-compliant sequences:

- **Mismatched LR/SC width.** `LrScInstrStream` picked `lr_name` and
  `sc_name` independently from the supported-ISA list, so on rv64gc
  ~48% of `sc.d` instructions paired with `lr.w` (or vice versa). Per
  RISC-V Unprivileged §9.1 the SC reservation must cover the LR's
  access size — every such pair was dead code. Fix in
  `rvgen/streams/amo_streams.py`: pick the width first, derive matched
  mnemonics.
- **`sc.rd == sc.rs1` aliasing.** The base / data / dest / status regs
  were drawn independently from the same pool, so SC's `rd` could
  alias its `rs1`. The constrained-LR/SC sequence rule (priv-arch
  §9.1) forfeits forward-progress under retry-loop conditions when
  this happens — real hardware livelocks. Fix: pick `base` first, then
  draw the others from `pool − {base}`.
- **`.aqrl` never emitted.** `AmoInstr.randomize_imm` rolled
  `rng.randint(0, 2)` and mapped to `{neither, aq, rl}` — the
  spec-legal `aqrl` ordering (sequentially-consistent SC-strong) was
  unreachable, leaving `amo_aqrl_cg.aq_and_rl` permanently empty. Fix:
  `randint(0, 3)` with bit-decomposition `aq = r & 1`, `rl = r & 2`,
  plus the missing `.aqrl` suffix branch in `get_instr_name`. Also
  fixed a related asm-emission bug where 14-character mnemonics like
  `amoswap.d.aqrl` were emitted without a trailing space (gas:
  "unrecognized opcode").

End-to-end: rv64gc / riscv_amo_test passes 3/3 on Spike across seeds
{100, 500, 999}; 0 width-mismatches and 0 alias-violations across 5
seeds of generated AMO blocks; `.aqrl` mnemonic appears 69× in the
same sample.

### Fixed — coverage report polluted by boot/handler infrastructure

Runtime-sampled coverage (`mem_align_cg`, `ea_align_cg`,
`mstatus_field_cg`, `walking_ones_cg`, `mxr_sum_mprv_cross_cg`,
`csr_value_cg`, …) was being populated by every retired instruction
in the spike trace — including the boot prologue's MSTATUS / MTVEC
writes and the trap handler's mandatory GPR push/pop. An arithmetic-
only test on an arithmetic-only core would falsely show MSTATUS and
memory-alignment bins covered.

Fix: workload-region PC-range filter in `rvgen/coverage/runtime.py`.
The runtime parser now reads the ELF symbol table (`main` and
`test_done` addresses) and only samples test-workload bins when
`main_pc <= pc < test_done_pc`. Trap-cause / nested-trap / priv-mode
bins remain always-on (they're test-caused events). Override with
`sample_handler_workload=True` to disable the filter.

After the fix, `riscv_arithmetic_basic_test` on rv32imc shows zero
`mstatus_field`, `csr_value`, `mem_align`, `ea_align`, `mxr_sum_mprv`
hits — accurate to what the test actually exercised.

Companion improvements: testlist plusargs (`+no_load_store=1` added
to `riscv_arithmetic_basic_test` and `riscv_floating_point_arithmetic_test`
to match the description "no load/store"); `.globl main` /
`.globl test_done` directives in generated asm so spike's symbolizer
can resolve workload boundaries.

### Changed — baseline.yaml coverage goals are now meaningful

Sprint-2 added ~40 new covergroups with `required: 0` placeholders so
the dashboard would *track* them without *demanding* them. The result
was that 95% of the dashboard showed "no goals" / "untracked" — no
useful pass/fail signal. baseline.yaml goals for bins that naturally
hit in any rv32imc workload (pipeline-distance, branch-shadow,
walking-ones bit positions 0/7/15/31, leading-trailing run-lengths,
op_comb special-register patterns, RAS classifications, branch-loop
direction) now use small positive `required` counts so dashboards
show real MET / MISSING status. Result: a typical 2-iteration
rv32imc/riscv_rand_instr_test run reports **94.4% closure
(136/144 bins met, grade 86/100)** instead of "no goals" everywhere.

### Added — privileged subsystem

- **SV32 + SV39 paging** (`rvgen/privileged/paging.py`): PTE bit-packing,
  page-table topology generator (1+2 tables for SV32, 1+2+4 for SV39),
  identity-mapped happy-path leaves, link-PTE boot-time fix-up that
  resolves child-table runtime addresses, SATP programming with
  mode|PPN + sfence.vma. Page-table data section (`.h<N>_page_table`)
  is automatically emitted when `target.satp_mode != BARE`.
- **PMP** (`rvgen/privileged/pmp.py`): `cfg_byte()` packing
  (`L | 00 | A[1:0] | X | W | R`), `pack_addr` per RV32 / RV64 widths,
  NAPOT + TOR helpers, opt-in boot CSR sequence. Configurable via
  `cfg.enable_pmp_setup` + `cfg.pmp_num_regions`.
- **Debug ROM** (`rvgen/privileged/debug_rom.py`): per-hart
  `debug_rom:` / `debug_end:` / `debug_exception:` sections, optional
  DCSR.ebreak{m,s,u} programming, single-step logic via
  DSCRATCH0 counter, DPC bump when cause==ebreak. Opt-in via
  `cfg.gen_debug_section`.

### Added — modern checkbox extensions

- **Zicond** (`czero.eqz` / `czero.nez`).
- **Zicbom** (`cbo.clean` / `cbo.flush` / `cbo.inval`).
- **Zicboz** (`cbo.zero`).
- **Zicbop** (`prefetch.i` / `prefetch.r` / `prefetch.w`).
- **Zihintpause** (`pause`).
- **Zihintntl** (`ntl.p1` / `ntl.pall` / `ntl.s1` / `ntl.all`).
- **Zimop** (32 `mop.r.N` + 8 `mop.rr.N` reserved encodings).
- **Zcmop** (8 `c.mop.{1,3,...,15}` compressed reserved encodings).
- New target `rv64gc_modern` showcases all of the above on top of
  the rv64gc privileged base. Verified end-to-end on Spike.

### Added — vector + multi-hart

- **vec_fp default-on** for FP-vector-capable targets (full RVV,
  Zve32f, Zve64f, Zve64d). The canonical `rv64gcv` regression now
  emits ~74 FP-vector ops per run (was 0). Plusarg-overridable.
- **Multi-hart shared-memory race region**: `MemRegion.shared` flag
  + new `DEFAULT_SHARED_REGIONS` / `shared_region_0` section emitted
  once for all harts. `riscv_load_store_shared_mem_stream` now
  references the shared label so harts genuinely race on the same
  addresses.

### Added — coverage tooling

- **`auto-goals -o PATH`** writes a starter goals YAML to a file.
  Modern-extension opcodes (CZERO_*, CBO_*, PREFETCH_*, PAUSE, NTL_*,
  MOP_*, C_MOP_*) appear in the seed list so generated goals cover
  them out of the box.

### Added — Sprint-2 deep-coverage gap closure (~40 new covergroups)

A head-of-verification gap analysis vs riscv-isac CGF, riscvISACOV,
core-v-verif #575, ARM/Imperas DV plans, and the LLVM-RVV gap notes
surfaced ~40 covergroups industry tools track that rvgen did not.
All are now wired into `collectors.py` + `runtime.py`, sampled
per-instruction and per-spike-trace, with goals in `baseline.yaml`
and dashboard subsystem mappings:

- **Pipeline depth** — `hazard_distance_cg` (RAW producer-consumer
  cycle distance bins 1..8+), `load_use_dist_cg`, `mc_producer_use_dist_cg`
  (multi-cycle MUL/DIV/FDIV/AMO), `branch_shadow_cg` (category in the
  slot following a branch), `mem_alias_cg` (static store-load aliasing
  in 8-instr window). Closes Tier-1 task #1.
- **Branch prediction** — `branch_pattern4_cg` (4-gram T/N pattern),
  `branch_loop_cg` (fwd/bwd × taken/fall_through), `ras_cg` (call /
  return / coroutine_swap / computed / tail_call from JAL/JALR rd/rs1),
  `jalr_target_class_cg` (ABI class of indirect target).
- **Atomics ordering** — `amo_aqrl_cg` (4 bins), `amo_op_width_cg`,
  `amo_op_aqrl_cross_cg`, `atomic_alignment_cg` (runtime EA alignment
  per LR/SC/AMO).
- **FP semantic** — `fp_op_class_cg` (13 op families), `fp_rm_op_cross_cg`,
  `fp_precision_op_cross_cg` (H/S/D), `fp_src_class_cg` (runtime nan/
  inf/subnormal/zero/normal source class), `fcvt_corner_cg` (saturation
  corners NaN→max+1 etc.).
- **Vector** — `vsetvl_avl_path_cg` (normal / set_vlmax / keep_vl /
  vsetivli), `vreg_overlap_cg` (full / partial / no-overlap with EMUL).
- **Privileged depth** — `mstatus_field_cg` (MIE/MPIE/MPP/MPRV/SUM/MXR/
  TVM/TW/TSR/FS/VS bit-level decode), `xtvec_mode_cg` (DIRECT/VECTORED),
  `delegation_cg` (per-cause medeleg/mideleg bit decode), `hpm_access_cg`
  (mhpmcounterN/mhpmeventN/mcycle/minstret), `misa_cg` (per-letter
  bits set), `mip_field_cg` (per-bit pending decode),
  `mxr_sum_mprv_cross_cg` (MMU policy cross at every load/store —
  catches "supervisor accesses user-page" corner #1 cause of broken
  ships), `virtual_instr_trap_cg` (H-ext cause=22), `wfi_corner_cg`
  (WFI × MSTATUS.TW), `nested_trap_cg` (trap during trap handler),
  `dcsr_cause_cg` (Debug spec §A.4 cause field).
- **Corner values** — `mul_div_corner_cg` (div_by_zero, signed_overflow,
  mul_max_pair, mul_neg_one_pair), `shamt_corner_cg` (shamt 0/1/XLEN-1/
  XLEN), `bitmanip_op_cg` (12 semantic op classes for B-ext),
  `c_imm_corner_cg` (RVC zero/one/large imm corners), `op_comb_cg`
  extensions (sp/ra/gp/zero usage as src/dst).
- **Abstract bins (riscv-isac CGF compatibility)** —
  `walking_ones_cg` / `walking_zeros_cg` (per-bit-position
  set/clear coverage), `alternating_pattern_cg` (5555/AAAA/byte_A5/
  byte_5A), `leading_trailing_cg` (run-length buckets for clz/ctz/
  cpop coverage). All sampled at runtime from every observed GPR
  write — adds dozens of bins per seed via the existing online-steering
  machinery.

Test count: 949 → 982 unit tests passing (+136 in
`tests/unit/test_coverage_sprint2.py`). Total covergroups: 78 → 116.
Canonical regression sweep (21/21 phase-A scalar combos) green on
Spike. The four headline-gap covergroups
(`hazard_distance_cg`, `load_use_dist_cg`, `mc_producer_use_dist_cg`,
`branch_shadow_cg`) directly close Tier-1 task #1 from §0.5
"Next-up queue".

### Verified

- 490 / 490 unit tests pass.
- Canonical regression sweep: 51 / 51 PASS.
- rv64gcv vector sweep: 18 / 18 PASS.
- New targets: rv64gc_modern + multi_harts shared-mem stream — all
  pass `gen + gcc_compile + iss_sim` on Spike.

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

[0.2.0]: https://github.com/TatsuProject/rvgen/releases/tag/v0.2.0
[0.1.0]: https://github.com/TatsuProject/rvgen/releases/tag/v0.1.0
[Unreleased]: https://github.com/TatsuProject/rvgen/compare/v0.2.0...HEAD
