# chipforge-inst-gen

A pure-Python random instruction generator for RISC-V, structurally modelled
on Google's [riscv-dv](https://github.com/chipsalliance/riscv-dv) but without
any of the SystemVerilog / UVM / PyVSC dependencies. Generates assembly-level
tests for RV32/RV64 cores and validates them against the Spike ISA simulator.

## Table of Contents

- [Highlights](#highlights)
- [Install](#install)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Supported ISA surface](#supported-isa-surface)
- [Targets](#targets)
- [Validation](#validation)
- [Cross-compare against a custom core (chipforge-mcu)](#cross-compare-against-a-custom-core-chipforge-mcu)
- [Testlist format](#testlist-format)
- [Directed instruction streams](#directed-instruction-streams)
- [Disable / feature-gate flags](#disable--feature-gate-flags)
- [Functional coverage](#functional-coverage)
- [Running the test suite](#running-the-test-suite)
- [Project layout](#project-layout)
- [Contributing / continuing development](#contributing--continuing-development)
- [Non-goals](#non-goals)
- [License](#license)

## Highlights

- **233 unit tests** pass in under a second.
- **51/51** end-to-end ISS runs pass on Spike for rv32imc, rv32imafdc,
  rv32imcb, rv64imc, rv64imcb targets (17 tests × 3 seeds).
- **21/21** trace-level matches against the
  [chipforge-mcu](https://chipforge.io) RV32IMC+Zkn silicon-target RTL (7
  tests × 3 seeds, instruction-by-instruction compare vs Spike).
- **301 instructions** registered across RV32I/M/A/C/F/D, RV64I/M/A/C/F/D,
  RV32FC/DC, Zba/Zbb/Zbc/Zbs + draft B, and ratified crypto
  (Zbkb/Zbkc/Zbkx/Zkne/Zknd/Zknh/Zksh/Zksed).
- **22 targets** out of the box, from bare `rv32ui` (no CSR) to full
  `rv64gc` / `rv64gcv` / crypto-capable variants.
- Standards-only ISA name parsing — non-standard strings (e.g. `rv32imck`)
  are rejected so the user can't accidentally ask for what doesn't exist.
- No constraint solver. Random selection is rejection-sampled per
  instruction; generating a 10k-instruction test takes seconds, not minutes.

## Install

Python 3.11+ is required. The only runtime dependency is PyYAML.

```bash
git clone <this repo> chipforge-inst-gen
cd chipforge-inst-gen
pip install -e ".[test]"
```

The binary tool-chain you need at runtime:

| Tool | Used for | Where it's looked up |
|------|----------|----------------------|
| `riscv64-unknown-elf-gcc` (or `riscv32-`) | Assemble + link `.S` → ELF | `$RISCV_GCC` or `$RISCV_TOOLCHAIN/bin` or `PATH` |
| `riscv64-unknown-elf-objcopy` | ELF → raw binary / verilog hex | Resolved next to the GCC binary |
| `spike` (RISC-V ISA simulator) | Execute the ELF, emit golden trace | `$SPIKE_PATH` or `PATH` |

Set `RISCV_GCC`, `RISCV_OBJCOPY`, and `SPIKE_PATH` if they're not already on
your `PATH`.

## Quick start

Generate one arithmetic-basic test for rv32imc, assemble it, run it on Spike:

```bash
python -m chipforge_inst_gen \
    --target rv32imc \
    --test riscv_arithmetic_basic_test \
    --testlist /path/to/riscv-dv/target/rv32imc/testlist.yaml \
    --steps gen,gcc_compile,iss_sim --iss spike \
    --output out --start_seed 100 -i 1
```

You'll see:

```
Generated out/asm_test/riscv_arithmetic_basic_test_0.S (seed=100, 5400 lines)
Compiling out/asm_test/riscv_arithmetic_basic_test_0.S
Running spike: out/asm_test/riscv_arithmetic_basic_test_0.o
2 tests passed ISS sim
```

To only generate `.S` files (skip assemble + simulate):

```bash
python -m chipforge_inst_gen --target rv32imc --test riscv_arithmetic_basic_test \
    --testlist <path> --steps gen --output out -i 1
```

## How it works

```
testlist YAML ──▶ load_testlist ──▶ [TestEntry, ...]
                                         │
                       gen_opts plusargs ▼
                                    ┌─────────────────┐
      target/<name>.py ───▶ TargetCfg ──▶ Config ◀───── --gen_opts CLI
                                    └─────────────────┘
                                             │
           RISCV_INSTR_REGISTRY ──▶ create_instr_list(cfg) ──▶ filtered pool
                                             │
                             random.Random(seed)
                                             │
                                             ▼
              AsmProgramGen.gen_program():
                 • _start / h<N>_start
                 • setup_misa + boot CSR sequence (unless bare)
                 • init section (GPR init, stack, signature)
                 • main RandInstrStream (+ directed streams interleaved)
                 • test_done → ecall
                 • trap handler  (unless bare)
                 • .data / region_N / user_stack / kernel_stack
                                             │
                                             ▼
                                   out/asm_test/*.S
                                             │
                               gcc_compile → *.o / *.bin
                                             │
                                  spike      → spike_sim/*.log
```

Every phase mirrors the SV reference (`src/riscv_asm_program_gen.sv`) exactly
where behaviour matters — same section order, same 18-char label column,
same MTVEC/MEPC/MSTATUS setup, same GPR-init value distribution. Where the
SV reference relies on constrained random (PyVSC) we replace it with
rejection sampling: faster, deterministic-per-seed, no solver.

## Supported ISA surface

| Family | Status | Notes |
|--------|--------|-------|
| RV32I / RV64I | complete | All 54 base integer ops. |
| M | complete | |
| C | complete | Full RVC with NZIMM/NZUIMM, 3-bit reg constraints, no-HINT guards. |
| A (AMO) | complete | aq/rl, LR/SC, AMO streams. |
| F / D | complete | FP operand layout, rounding mode, compressed FP (FC/DC). |
| Zba / Zbb / Zbc / Zbs | complete | Ratified bitmanip. |
| B (draft v0.93) | registered | GORC/CMIX/CMOV/PACK*/SLO/GREV/FSL/CRC32*/SHFL etc. Pool-filtered by target. |
| Zbkb / Zbkc / Zbkx | complete | Ratified crypto bitmanip; adds `brev8`/`zip`/`unzip` (RV32 only). |
| Zkne / Zknd | complete | AES32* on RV32; AES64*/AES64KS1I/AES64KS2/AES64IM on RV64. |
| Zknh | complete | SHA-256 on both widths; SHA-512 single-instr on RV64, H/L split pair on RV32. |
| Zksh / Zksed | complete | SM3, SM4. |
| RVV 1.0 | stub | Base instructions registered but full Phase-2 work. |
| CSR ops | complete | Writes restricted to `{MSCRATCH}` by default (matches SV). |

Directed streams implemented: `riscv_int_numeric_corner_stream`,
`riscv_jal_instr`, `riscv_loop_instr`, `riscv_amo_instr_stream`,
`riscv_lr_sc_instr_stream`, `riscv_load_store_rand_instr_stream` (+ aliases
for hazard / multi-page / mem-region-stress / shared-mem / rand-addr).

## Targets

| Target | XLEN | ISA groups | Privilege | Notes |
|--------|------|-----------|-----------|-------|
| `rv32i` | 32 | I | M | Vanilla. |
| `rv32im` | 32 | I, M | M | SV source marks MUL* unsupported. |
| `rv32imc` | 32 | I, M, C | M | Common baseline. |
| `rv32imac` | 32 | I, M, A, C | M | |
| `rv32imafdc` | 32 | I, M, A, F, FC, D, DC | M | Full G (RV32). |
| `rv32imcb` | 32 | I, M, C, Zba/Zbb/Zbc/Zbs | M | Ratified bitmanip. |
| `rv32imc_sv32` | 32 | I, M, C | M+U, SV32 | Paging target. |
| `rv32imc_zkn` | 32 | I, M, C, Zbkb/Zbkc/Zbkx/Zkne/Zknd/Zknh | M | Matches chipforge-mcu ISA. |
| `rv32imc_zkn_zks` | 32 | + Zksh + Zksed | M | Full ratified K-family. |
| `rv32ui` | 32 | I | — | Bare, no CSR (see §Validation). |
| `rv64imc` / `rv64imcb` | 64 | I, M, C (+ratified Zb*) | M | RV64 baselines. |
| `rv64imc_zkn` | 64 | RV64 IMC + RV64 crypto | M | AES64 / SHA-512 single-instr. |
| `rv64imafdc` | 64 | Full G (RV64) | M | |
| `rv64gc` | 64 | G + C | M+S+U, SV39 | Privileged baseline. |
| `rv64gcv` | 64 | G + V | M | VLEN=512, ELEN=32. |
| `ml` / `multi_harts` | 64 | G + C | M | Specialized variants. |

Non-standard names (e.g. `rv32imck`) are **not accepted** — the CLI's
`--target` argparse choices list fails immediately if the target isn't
registered. Add it explicitly in `chipforge_inst_gen/targets/__init__.py`
to use it.

## Validation

### Unit tests — 233 pass

```bash
python -m pytest tests/ -q
```

### End-to-end on Spike — 51 / 51

```bash
for t in rv32imc:riscv_arithmetic_basic_test rv32imc:riscv_rand_instr_test \
         rv32imc:riscv_jump_stress_test rv32imc:riscv_loop_test \
         rv32imc:riscv_amo_test rv32imc:riscv_rand_jump_test \
         rv32imc:riscv_no_fence_test rv32imc:riscv_mmu_stress_test \
         rv32imc:riscv_unaligned_load_store_test \
         rv32imafdc:riscv_floating_point_arithmetic_test \
         rv32imcb:riscv_b_ext_test rv32imcb:riscv_zbb_zbt_test \
         rv64imc:riscv_arithmetic_basic_test rv64imc:riscv_rand_instr_test \
         rv64imc:riscv_loop_test rv64imc:riscv_jump_stress_test \
         rv64imcb:riscv_b_ext_test; do
  target=${t%%:*}; test=${t##*:}
  for s in 100 200 300; do
    python -m chipforge_inst_gen \
      --target $target --test $test \
      --steps gen,gcc_compile,iss_sim --iss spike \
      --output /tmp/reg_${target}_${test}_${s} --start_seed $s -i 1 2>&1 \
      | grep -qE "tests passed ISS sim" \
      && echo "PASS $target/$test/$s" || echo "FAIL $target/$test/$s"
  done
done
```

Expected: 51 `PASS`, 0 `FAIL`.

## Cross-compare against a custom core (chipforge-mcu)

For an end-to-end proof-of-correctness, tests go through *both* Spike (the
golden reference) and the target-core RTL simulator, and the two traces are
compared instruction-by-instruction. This section shows the flow against
[`chipforge-mcu`](https://chipforge.io), an RV32IMC + Zkn open-source
microcontroller.

Requirements:

- A pre-built `Vtb_top` Verilator binary (built from the MCU repo's
  `verif/gen_sim.py`).
- riscv-dv's `scripts/spike_log_to_trace_csv.py`, `core_log_to_trace_csv.py`,
  and `instr_trace_compare.py` (the MCU repo ships compatible copies).

Flow:

1. **Generate `.S`** for target `rv32imc_zkn` (matches the MCU's ISA
   advertised by its README — no Zbb, ratified K sub-extensions only).
2. **Assemble** with the MCU's `verif/scripts/link.ld`
   (`.text @0x80000000`, `.data @0x80008000`) and the matching
   `-march=rv32imc_zbkb_zbkc_zbkx_zknd_zkne_zknh_zicsr_zifencei`.
3. **`objcopy -O verilog`** the ELF and split the Verilog hex into two
   `.mem` files: 32 KB `imem.mem` (text) and 16 KB `dmem.mem` (data).
4. **Spike** with `--log-commits --misaligned`; strip its bootrom prefix and
   convert the log to CSV via `spike_log_to_trace_csv.py`.
5. **RTL sim** — copy the `.mem` files into the MCU's verif/ dir, run
   `Vtb_top`, convert `trace_core_00000001.log` to CSV via
   `core_log_to_trace_csv.py`.
6. **Diff** the two CSVs with `instr_trace_compare.py`. `[PASSED]: N matched`
   means every committed instruction matched between spike and the RTL.

A reference driver script for all six steps lives in
`scripts/mcu_validate.sh`. Invoke it with the relevant env vars pointing at
your toolchain, the MCU checkout, and a riscv-dv checkout (for testlist
imports):

```bash
WORK_DIR=/var/tmp/mcu/work \
MCU_VERIF=/path/to/chipforge-mcu/verif \
RISCV_DV=/path/to/riscv-dv \
PYTHON=/path/to/python \
RISCV_GCC=/path/to/riscv64-unknown-elf-gcc \
SPIKE=/path/to/spike \
scripts/mcu_validate.sh riscv_arithmetic_basic_test 100
```

Outputs one line: `<test> seed=<seed>: [PASSED]: N matched` or
`[FAILED]: …`. Run it in a loop over `{arithmetic_basic, rand_instr, loop,
jump_stress, amo, rand_jump, no_fence} × {100, 200, 300}` to reproduce the
full 21-run sweep below.

Latest results:

```
riscv_arithmetic_basic_test seed=100: [PASSED]: 4113 matched
riscv_arithmetic_basic_test seed=200: [PASSED]: 4220 matched
riscv_arithmetic_basic_test seed=300: [PASSED]: 4342 matched
riscv_rand_instr_test       seed=100: [PASSED]: 1066 matched
riscv_rand_instr_test       seed=200: [PASSED]: 1048 matched
riscv_rand_instr_test       seed=300: [PASSED]: 1306 matched
riscv_loop_test             seed=100: [PASSED]: 1822 matched
riscv_loop_test             seed=200: [PASSED]: 1411 matched
riscv_loop_test             seed=300: [PASSED]: 1522 matched
riscv_jump_stress_test      seed=100: [PASSED]: 2456 matched
riscv_jump_stress_test      seed=200: [PASSED]: 2358 matched
riscv_jump_stress_test      seed=300: [PASSED]: 2682 matched
riscv_amo_test              seed=100: [PASSED]: 2174 matched
riscv_amo_test              seed=200: [PASSED]: 2440 matched
riscv_amo_test              seed=300: [PASSED]: 2346 matched
riscv_rand_jump_test        seed=100: [PASSED]: 2308 matched
riscv_rand_jump_test        seed=200: [PASSED]: 2332 matched
riscv_rand_jump_test        seed=300: [PASSED]: 2418 matched
riscv_no_fence_test         seed=100: [PASSED]: 121 matched
riscv_no_fence_test         seed=200: [PASSED]: 127 matched
riscv_no_fence_test         seed=300: [PASSED]: 136 matched

→ 21 / 21 PASSED
```

These results confirm that generator output is byte-identical-equivalent on
both Spike and real RV32IMC+Zkn silicon-model RTL — no trap divergences,
no decode disagreements, no illegal-instruction paths.

## Testlist format

Reuses riscv-dv's YAML schema unchanged:

```yaml
- import: <riscv_dv_root>/yaml/base_testlist.yaml

- test: my_test
  description: >
    Human-readable description.
  iterations: 2
  gen_test: riscv_instr_base_test
  gen_opts: >
    +instr_cnt=2000
    +no_fence=1
    +directed_instr_1=riscv_loop_instr,4
    +directed_instr_2=riscv_jal_instr,8
  rtl_test: core_base_test
```

The loader recursively expands `import:` entries and substitutes
`<riscv_dv_root>` with whatever `--riscv_dv_root` the CLI receives (defaults
to the sibling `riscv-dv` checkout if present).

## Directed instruction streams

Set via `+directed_instr_N=<stream_name>,<count>` in a test's `gen_opts`, or
passed at the command line via `--gen_opts`. Examples — see
`chipforge_inst_gen/streams/` for the full list.

```
+directed_instr_0=riscv_int_numeric_corner_stream,4   # corner values
+directed_instr_1=riscv_jal_instr,8                   # shuffled JAL chain
+directed_instr_2=riscv_loop_instr,4                  # nested loops
+directed_instr_3=riscv_load_store_rand_instr_stream,4
+directed_instr_4=riscv_amo_instr_stream,4
+directed_instr_5=riscv_lr_sc_instr_stream,4
```

## Disable / feature-gate flags

All riscv-dv-compatible, set as `+flag=0/1` in `gen_opts` (or via
`--gen_opts` on the CLI):

| Flag | Default | Effect |
|------|---------|--------|
| `+instr_cnt=<n>` | 200 | Main-program instruction count. |
| `+no_branch_jump` | 0 | Suppress BRANCH ops from the random pool. |
| `+no_fence` | 0 | Suppress FENCE / FENCE.I / SFENCE.VMA. |
| `+no_csr_instr` | 0 | Suppress CSRRW/CSRRS/CSRRC(I) from the random pool. |
| `+no_ebreak` | 1 | Include EBREAK in random pool when = 0. |
| `+no_ecall` | 1 | Include ECALL in random pool when = 0. |
| `+no_wfi` | 1 | Include WFI in random pool when = 0. |
| `+no_dret` | 1 | Include DRET in random pool when = 0. |
| `+disable_compressed_instr` | 0 | Drop all RVC ops. |
| `+no_data_page` | 0 | Skip the random data region. |
| `+enable_floating_point` | 0 | Enable RV32F/D / RV32FC/DC groups. |
| `+enable_b_extension` | 0 | Enable draft-B mnemonics. |
| `+bare_program_mode` | 0 | Skip ALL CSR-based boot setup and trap handler (for rv32ui-style no-CSR cores). |
| `+boot_mode=m/s/u` | m | Initial privilege mode. |

## Functional coverage

The generator ships a pure-Python functional-coverage model inspired by
riscv-dv's SV covergroups and riscv-isac's CGF goals-file format.

### What's collected

24 covergroups across two sources:

**Static** (sampled at generation time — no ISS required):

| Covergroup | Bins |
|---|---|
| `opcode_cg` | every RiscvInstrName member |
| `format_cg` | R / I / S / B / U / J / C{R,I,L,S,SS,A,IW,B,J} / V{SET,A,S2,L,S,LX,SX,LS,SS,AMO}_FORMAT |
| `category_cg` | LOAD / STORE / ARITHMETIC / LOGICAL / COMPARE / SHIFT / BRANCH / JUMP / SYNCH / SYSTEM / CSR / AMO |
| `group_cg` | RV32I / RV32M / … / RVV / ZVE32X / ZVE64D |
| `rs1_cg`, `rs2_cg`, `rd_cg` | x0..x31 |
| `imm_sign_cg` | pos / neg / zero |
| `imm_range_cg` | zero / all_ones / walking_one / walking_zero / min_signed / max_signed / generic |
| `hazard_cg` | raw / war / waw / none (8-instruction sliding window) |
| `csr_cg` | PrivilegedReg name per CSR op |
| `fp_rm_cg` | RNE / RTZ / RDN / RUP / RMM |
| `vreg_cg`, `fpr_cg` | v0..v31 / ft0..ft11 |
| `mem_align_cg` | byte / half_{aligned,unaligned} / word_{aligned,unaligned} / dword_{aligned,unaligned} |
| `load_store_width_cg` | byte / half / word / dword |
| `category_transition_cg` | prev_category __ current_category |
| `opcode_transition_cg` | prev_mnem __ current_mnem |
| `fmt_category_cross`, `category_group_cross` | crosses |

**Runtime** (parsed from `spike -l` trace — set `--iss_trace`):

| Covergroup | Bins |
|---|---|
| `branch_direction_cg` | taken / not_taken |
| `exception_cg` | trap_entered (refined in Phase 2) |
| `privilege_mode_cg` | M_entered / M_return / S_return / U_return |
| `pc_reach_cg` | one bin per spike-resolved label |
| `opcode_cg.*__dyn` | dynamic opcodes observed (vs static generation — gap = dead code) |

### Goals (CGF-style YAML)

```yaml
opcode_cg:
  ADD: 20
  SUB: 10
  FENCE: 2
hazard_cg:
  raw: 50
  waw: 30
```

Keys map 1:1 to covergroup + bin names; the int is the required hit count.
`0` marks a bin as tracked but optional (doesn't block "goals met").

Ships four overlay files:

- `chipforge_inst_gen/coverage/goals/baseline.yaml` — rv32imc-focused starter.
- `.../rv64imc.yaml` — adds RV64I/M/C opcodes.
- `.../rv64gcv.yaml` — vector additions.
- `.../coralnpu.yaml` — Zve32x embedded (no FP vector, no S/U).
- `.../no_branch_jump.yaml` — test overlay: zeroes branch/jump goals when the test sets `+no_branch_jump=1`.

Layer via repeated `--cov_goals`:

```bash
--cov_goals goals/baseline.yaml --cov_goals goals/rv64gcv.yaml
```

Last writer wins per bin — overlays can both tighten *and* relax goals.

### CLI workflows

```bash
# Collect static coverage (fast — no GCC/ISS):
python -m chipforge_inst_gen --target rv32imc --test riscv_rand_instr_test \
    --steps gen,cov --output /tmp/run \
    --cov_goals chipforge_inst_gen/coverage/goals/baseline.yaml

# Static + runtime (branch direction, pc_reach from spike trace):
python -m chipforge_inst_gen --target rv32imc --test riscv_rand_instr_test \
    --steps gen,gcc_compile,iss_sim,cov --iss spike --iss_trace \
    --output /tmp/run --cov_goals .../baseline.yaml

# Auto-regression until goals hit (smart: perturbs gen_opts per-seed):
python -m chipforge_inst_gen --target rv32imc --test riscv_rand_instr_test \
    --auto_regress --cov_directed --max_seeds 16 \
    --output /tmp/regress --cov_goals .../baseline.yaml

# Post-run analysis:
python -m chipforge_inst_gen.coverage.tools diff run_a/coverage.json run_b/coverage.json
python -m chipforge_inst_gen.coverage.tools attribute run_*/coverage.json \
    --goals .../baseline.yaml
python -m chipforge_inst_gen.coverage.tools merge run_*/coverage.json -o all.json
python -m chipforge_inst_gen.coverage.tools export all.json \
    --csv cov.csv --html cov.html --goals .../baseline.yaml
```

`--cov_directed` is a heuristic that inspects missing bins and perturbs the
next seed's `gen_opts` — e.g. if `FENCE` is uncovered it drops `+no_fence=1`;
if `LB` is uncovered it injects a `riscv_load_store_rand_instr_stream`
directed-stream. Baseline rv32imc goals close in 1 seed vs 8+ for blind sweep.

## Running the test suite

```bash
python -m pytest tests/ -q
```

Expected: `304 passed in <2s`.

## Project layout

```
chipforge_inst_gen/
  cli.py                  entry point (argparse + top-level pipeline)
  config.py               Config dataclass (every plusarg knob)
  targets/                per-target TargetCfg (XLEN, supported_isa, ...)
  isa/
    enums.py              RiscvInstrName, RiscvInstrGroup, RiscvReg, ...
    csrs.py               per-CSR field table
    base.py               Instr base class (R/I/S/B/U/J + convert2asm)
    factory.py            register / instantiate by name
    filtering.py          create_instr_list + get_rand_instr
    rv32i.py … rv64d.py   per-extension registration
    compressed.py / floating_point.py / amo.py / bitmanip.py / crypto.py
  streams/
    base.py               DirectedInstrStream
    directed.py           JAL chain, numeric corner, load/store
    loop.py               nested countdown loop
    amo_streams.py        LR/SC, AMO
  privileged/
    boot.py               setup_misa + pre_enter_privileged_mode
    trap.py               DIRECT-mode M-mode trap handler
  sections/
    data_page.py          .data / region_N / stacks
    signature.py          CORE_STATUS / TEST_RESULT / WRITE_GPR / WRITE_CSR
  asm_program_gen.py      top-level program composer
  stream.py               RandInstrStream.gen_instr
  sequence.py             label + branch target resolution
  gcc.py                  riscv-gcc invocation
  iss.py                  Spike driver
  testlist.py             riscv-dv YAML testlist loader
  seeding.py              SeedGen (fixed/start/rerun/random)

tests/                    233 unit tests + filtering + e2e
research/                 11 distilled notes from reading riscv-dv SV
scripts/mcu_validate.sh   chipforge-mcu trace-compare driver
CLAUDE.md                 engineering log — current state, next-up queue, resume prompt
```

## Contributing / continuing development

The authoritative engineering log lives in **[`CLAUDE.md`](CLAUDE.md)**.
`§0 — Status and where to pick up` is always kept current with:

- What's finished (per SV-reference step).
- A dated list of every fix that's landed, with root cause.
- A priority-ordered "next-up queue" of open work (vector extension,
  full privileged mode, golden-file diff harness, etc).
- Ready-made prompts for resuming a session either generically ("pick
  the next thing") or for a specific target ("add vector support").

The `research/` directory contains 11 focused notes distilled from the
riscv-dv SV source before any code was written — consult the relevant
note before modifying the corresponding module (pointers are in the
`§11 — Research notes` section of CLAUDE.md).

## Non-goals

- **No constraint solver dependency** (PyVSC, Z3, etc.). Rejection sampling
  is fast enough and debuggable.
- **No RTL simulator integration.** Pipeline stops at `.S` / `.bin` / ISS
  log. RTL hook-up is the user's responsibility (see
  [chipforge-mcu cross-compare](#cross-compare-against-a-custom-core-chipforge-mcu)
  for a worked example).
- **No GUI.**
- **No byte-identical output vs riscv-dv.** Different PRNG, different
  sampling → structurally equivalent, ISS-equivalent, but bytes differ for
  the same seed.

## License

TBD.
