# Verification engineer's guide

A step-by-step walkthrough of **rvgen** for someone who wants
to gate a RISC-V core's sign-off on functional coverage.

---

## 0 — Prerequisites

- A RISC-V GCC (`riscv64-unknown-elf-gcc` or equivalent).
- `spike` (or `spike-vector` for RVV targets).
- Python 3.11+.

```bash
pip install -e .                       # install rvgen in-place
export RISCV_GCC=/path/to/riscv64-unknown-elf-gcc
export SPIKE_PATH=/path/to/spike       # or spike-vector for RVV
```

---

## 1 — First run (30 seconds)

Generate a test, assemble it, run it on spike, and collect coverage — all
in one command:

```bash
python -m rvgen \
    --target rv32imc --test riscv_rand_instr_test \
    --steps gen,gcc_compile,iss_sim,cov --iss spike --iss_trace \
    --output out/first_run --start_seed 100 -i 1
```

Outputs under `out/first_run/`:

- `asm_test/*.S`             — the generated assembly.
- `asm_test/*.o`, `*.bin`    — compiled artefacts.
- `spike_sim/*.log`          — spike stdout.
- `spike_sim/*.trace`        — spike instruction trace (`-l --log-commits`).
- `coverage.json`            — observed coverage (dict-of-dict).
- `coverage_per_test.json`   — same, keyed by test_id.
- `coverage_report.txt`      — human-readable summary.

The log ends with either `ALL GOALS MET` or `N bin(s) missing across M
covergroup(s)`. When goals are unmet, CLI exits **3** — wire this into
your CI to gate PRs on coverage.

---

## 2 — Read the report

```
========================================================================
Coverage Report
========================================================================
covergroups: 27    unique bins hit: 1919    total samples: 69354    grade: 87/100

[opcode_cg]  unique_bins=45  total_hits=10284  21/38 goals met
    NOP                              300
    C_AND                            262
    XOR                              252 / 5
  MISSING (17):
    ! BEQ                                0 / 5
    ! BGE                                0 / 5
    ...
```

Key signals:

- **`grade: 87/100`** — composite quality (60% goals, 20% hazard balance,
  20% opcode breadth). Watch this in your dashboard.
- **Per-covergroup header**: `unique_bins=N  total_hits=H  K/M goals met`.
- **`MISSING` block**: every bin that has `required > 0` in your goals
  file but observed < required. These are the gaps to close.

---

## 3 — Understand what got sampled

The generator collects **24 covergroups**:

### Static (sampled at generation time)

| Covergroup | What it tells you |
|---|---|
| `opcode_cg` | Which mnemonics were emitted. |
| `format_cg` | R/I/S/B/U/J/C*/V* — instruction-encoding diversity. |
| `category_cg` | LOAD/STORE/ARITHMETIC/LOGICAL/COMPARE/SHIFT/BRANCH/JUMP/SYNCH/SYSTEM/CSR/AMO. |
| `group_cg` | RV32I/M/A/C/F/D/RVV/Zve*/Zb*/Zk* — extension mix. |
| `rs1_cg`, `rs2_cg`, `rd_cg` | Per-register usage (reveals stuck registers). |
| `imm_sign_cg`, `imm_range_cg` | Immediate diversity (zero / all_ones / walking_one / ...). |
| `hazard_cg` | RAW/WAR/WAW detection across an 8-instr window. |
| `csr_cg`, `csr_access_cg` | CSR addresses touched + read-vs-write distinction. |
| `mem_align_cg`, `load_store_width_cg` | Alignment + width of every load/store. |
| `load_store_offset_cg` | Offset magnitude (zero / pos_small / pos_medium / ... / neg_large). |
| `category_transition_cg`, `opcode_transition_cg` | Prev→current transitions (pipeline interleaving). |
| `fp_rm_cg`, `fpr_cg` | FP rounding modes + register usage. |
| `vtype_cg`, `vtype_dyn_cg`, `vreg_cg` | Vector config + register mix. |
| `rs1_eq_rs2_cg`, `rs1_eq_rd_cg` | Same-reg and in-place op paths. |
| `directed_stream_cg` | Which directed streams actually contributed. |

### Runtime (from `spike -l --log-commits` trace)

| Covergroup | What it tells you |
|---|---|
| `branch_direction_cg` | taken / not_taken per retired branch. |
| `branch_taken_per_mnem_cg` | Taken rate per branch mnemonic (BEQ__T, BEQ__NT, ...). |
| `exception_cg` | `trap_entered` counts. |
| `privilege_mode_cg` | M/S/U mode executions + MRET/SRET/URET transitions. |
| `pc_reach_cg` | Labels entered (init, mtvec_handler, ...). |
| `csr_value_cg` | Actual values written to each CSR (bucketed). |
| `rs_val_corner_cg` | GPR-write value corners (zero / all_ones / min_signed / ...). |
| `opcode_cg.*__dyn` | Dynamic opcode mix (gap vs static = dead code). |

---

## 4 — Define goals

Ship your required coverage in a CGF-style YAML. Start by copying
`rvgen/coverage/goals/baseline.yaml` and tightening the
counts to what your core needs:

```yaml
opcode_cg:
  ADD: 50      # need ≥50 ADD emissions
  SUB: 20
  FENCE: 2
  MRET: 0      # tracked but not required (0 = optional)
hazard_cg:
  raw: 100
  war: 100
  waw: 50
mem_align_cg:
  word_aligned: 20
  word_unaligned: 5   # want the core to be tested under misalignment
```

Layer a target overlay on top:

```bash
--cov_goals goals/baseline.yaml --cov_goals goals/my_core.yaml
```

Later files override earlier on a per-bin basis (last-writer wins).

If `--cov_goals` is omitted, the CLI auto-resolves `goals/baseline.yaml +
goals/<target>.yaml` when the shipped defaults cover your target.

---

## 5 — Close missing bins

When goals are unmet, three tools help:

### 5a — Coverage-directed auto-regression

```bash
python -m rvgen \
    --target rv32imc --test riscv_rand_instr_test \
    --auto_regress --cov_directed --max_seeds 16 \
    --output out/regress
```

For each seed, the driver inspects the missing-bin set and perturbs the
next seed's `gen_opts`:

- Missing `FENCE` → drop `+no_fence=1`.
- Missing `LB/LH/SB` → inject a `riscv_load_store_rand_instr_stream`
  directed stream.
- Missing `JALR` → inject `riscv_jalr_instr`.
- Missing branches → drop `+no_branch_jump=1`.

Baseline rv32imc goals close in **1 seed** this way (vs 8+ for blind
seed-sweep).

### 5b — Per-test attribution

Does one test over-represent some bins? See which tests own which:

```bash
python -m rvgen.coverage.tools per-test \
    out/regress/coverage_per_test.json
```

Shows tests ranked by "uniquely-owned bins". If one test owns 500 unique
bins, retire it last. If a test owns 0 unique bins, it's redundant.

### 5c — Diff two runs

```bash
python -m rvgen.coverage.tools diff \
    out/baseline/coverage.json out/new/coverage.json
```

Shows per-bin `+/-` deltas. Any bin with a negative delta is a
regression: something stopped emitting it.

---

## 6 — Gate CI on coverage

```yaml
# .github/workflows/regression.yml
- name: Run regression
  id: regress
  run: |
    python -m rvgen --target rv32imc \
        --test riscv_rand_instr_test \
        --auto_regress --cov_directed --max_seeds 32 \
        --output out
  # Exit codes: 0 = goals met, 1 = config error, 2 = ISS failed,
  # 3 = coverage goals unmet.

- name: Baseline protection
  run: |
    python -m rvgen.coverage.tools baseline-check \
        --baseline tests/golden/coverage_golden.json \
        out/coverage.json
```

When running under GitHub Actions, the `cov` step writes:

- `GITHUB_OUTPUT`: `unique_bins=...`, `goals_pct=...`, `grade=...`, ...
- `GITHUB_STEP_SUMMARY`: markdown summary with pass/fail + missing bins.

---

## 7 — Scaling up

Use the shipped parallel runner — it handles matrix execution, merging,
summarisation, and HTML export in one command:

```bash
./scripts/regression.py \
    --targets rv32imc,rv64imc,rv32imcb,rv64imcb \
    --tests riscv_arithmetic_basic_test,riscv_rand_instr_test \
    --seeds 100,200,300 \
    --iss_trace --jobs 8 --emit_html \
    --output out/regression/
```

Under `out/regression/`:

- `per_run/<target>_<test>_<seed>/` — each individual run's full output.
- `combined_coverage.json`          — merged view.
- `summary.txt`, `summary.html`     — reports.
- `regression.log`                  — PASS/FAIL line per run.

Exit 0 iff every run finished without an ISS error (coverage-goals-unmet
is still counted as a passing simulation — use `baseline-check` after to
gate on monotonic coverage). This maps cleanly onto a CI "required check".

The `convergence.json` + `cov_timeline.json` sidecars from each
auto-regress run let external dashboards plot "coverage over time".

## 7.5 — Debugging workflows

| Scenario | Tool |
|---|---|
| Goals YAML typo | `tools lint-goals goals.yaml --strict=error` |
| "Which seed closed X last time?" | `tools suggest-seeds --convergence ... --observed ... --goals ...` |
| "Which test owns which bins?" | `tools per-test coverage_per_test.json` |
| "Did this PR regress coverage?" | `tools baseline-check --baseline golden.json observed.json` |
| Replay a specific seed's `.S` | `asm_test/seed_archive/<test>_seedNNN.S` |

---

## 8 — Common failures

| Symptom | Fix |
|---|---|
| `goals not met` but you don't know why | Look at `coverage_report.txt` for the `MISSING` block. |
| New test regresses | `baseline-check` flags which bins disappeared. |
| One test dominates the regression | `per-test` shows unique-owned bins; retire dupes. |
| Plateau at 90% goals | Switch to `--cov_directed`; also inspect if the goal is reachable (some opcodes need non-default gen_opts). |
| `trace_path` is empty | Did you pass `--iss_trace`? Spike doesn't log by default. |
| Coverage grade below expected | `render_report` shows the three components; the low one tells you where to invest. |

---

## 9 — When none of this fits

The coverage DB is a plain JSON dict-of-dict. You can merge / analyse /
dashboard it with any tool you like (pandas, grafana, custom React).
The shipped tools are a convenience, not a contract.

Define your own covergroups if the shipped 24 aren't enough: add a
constant + `_bump(db, MY_CG, bin_name)` in the appropriate sampler and
declare it in `ALL_COVERGROUPS`. The rest of the pipeline picks it up
automatically.
