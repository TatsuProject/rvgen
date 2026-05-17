# Nightly regression — `scripts/regression.py`

A standalone, parallel regression driver for `(target × test × seed)`
matrices. Use it instead of hand-rolling a bash loop over `rvgen`
invocations — it merges coverage across all runs, emits one dashboard,
and exits non-zero on any failure (CI-friendly).

## When to use it

- **One-off** runs of "is this PR green?" → just call `rvgen` directly.
- **Nightly** matrix runs of "do all my targets / tests / seeds close
  coverage?" → `scripts/regression.py`. Parallelism + merged
  dashboard + single exit code make it CI-ready.

## Smallest example

```bash
./scripts/regression.py \
    --targets rv32imc \
    --tests riscv_arithmetic_basic_test \
    --seeds 100,200,300 \
    --output out/nightly/
```

Runs 3 jobs (1 × 1 × 3) in parallel and writes:

| Path | Contents |
|------|---------|
| `out/nightly/per_run/<target>_<test>_<seed>/` | The full output dir for that single run — same shape as a regular `rvgen` invocation. |
| `out/nightly/combined_coverage.json` | Merged coverage across every run. |
| `out/nightly/summary.txt` | Human-readable per-covergroup summary on the merged view. |
| `out/nightly/regression.log` | Per-run `PASS` / `FAIL` lines. |

Exit code: `0` iff every run succeeded.

## Full matrix example (what we run nightly)

```bash
./scripts/regression.py \
    --targets rv32imc,rv32imac,rv64imc,rv64imafdc,rv64gc \
    --tests riscv_arithmetic_basic_test,riscv_rand_instr_test,riscv_jump_stress_test \
    --seeds 100,200,300,400,500 \
    --iss_trace \
    --jobs 8 \
    --emit_html \
    --cov_goals rvgen/coverage/goals/baseline.yaml \
    --output out/nightly/
```

`5 × 3 × 5 = 75` jobs across 8 worker processes. Adds:

- `--iss_trace` — collects runtime coverage from spike's `--log-commits` trace.
- `--emit_html` — produces `summary.html` (the same sunburst dashboard
  the `cov` step emits per-run, but over the merged DB).
- `--cov_goals` — layered goals so the dashboard reports MET / MISSING
  status against your team's required bins.

Exit code: `0` iff every run passed **and** every required bin met its goal.

## CI integration — GitHub Actions

```yaml
name: nightly-regression
on:
  schedule: [{cron: "0 6 * * *"}]   # 06:00 UTC daily
  workflow_dispatch:
jobs:
  regression:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: |
          sudo apt-get update && sudo apt-get install -y \
              riscv64-unknown-elf-gcc spike
          pip install -e .
      - name: Run regression matrix
        run: |
          ./scripts/regression.py \
              --targets rv32imc,rv64imc,rv64gc \
              --tests riscv_rand_instr_test \
              --seeds 100,200,300 \
              --iss_trace --emit_html \
              --cov_goals rvgen/coverage/goals/baseline.yaml \
              --output out/regression/
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: regression-${{ github.run_number }}
          path: out/regression/
```

## Flags reference

| Flag | Default | Notes |
|------|---------|-------|
| `--targets <csv>` | — required | Comma-separated target names. |
| `--tests <csv>` | — required | Comma-separated test names. Must exist in the resolved testlist. |
| `--seeds <csv>` | — required | Comma-separated seeds. |
| `--testlist <path>` | per-target default | Override; otherwise the [packaged testlist fallback chain](testlist.md) applies. |
| `--output <dir>` | — required | All artefacts go here. |
| `--jobs N` | `os.cpu_count()` | Parallel worker processes. |
| `--iss_trace` | off | Enables runtime coverage from spike's `--log-commits`. |
| `--cov_goals <path>` | — | Repeat for layered overlays (`--cov_goals base.yaml --cov_goals my_core.yaml`). |
| `--emit_html` | off | Renders `summary.html` (sunburst dashboard). |
| `extra_args` | — | Positional. Forwarded as-is to each `rvgen` invocation. Use for one-off plusargs: `./scripts/regression.py … -- +disable_compressed_instr=1`. |

## Trend tracking

Pair with `--cov_history` on the underlying `rvgen` call to append a
JSONL record per run — over time you get a coverage trend line for
free. See [`docs/coverage.md` §9 (CI integration)](coverage.md) for
the schema and a sample matplotlib plot.

## Comparison with riscv-dv's `run.py`

| | rvgen `regression.py` | riscv-dv `run.py` |
|---|---|---|
| Matrix definition | comma-separated CLI flags | testlist YAML + plusarg-by-plusarg |
| Parallelism | `--jobs N` (subprocess pool) | external `make -j` typically |
| Merged coverage | built-in `combined_coverage.json` | none — coverage is per-run |
| HTML dashboard | one merged dashboard | UCIS + vendor viewer |
| Exit code semantics | 0 iff every run + goal met | 0 iff every run passes (no coverage gate) |

If you're migrating off riscv-dv, the closest one-to-one mapping is:
each `--targets` entry maps to `--target`, each `--tests` to `--test`,
each `--seeds` to a `--seed`-with-iterations sweep.
