# rvgen

A **pure-Python** re-implementation of [riscv-dv](https://github.com/chipsalliance/riscv-dv),
with a stronger functional-coverage story and zero SystemVerilog / UVM dependencies.

## Why rvgen

- **`pip install rvgen`** — first `.S` in 30 seconds. No SV simulator licence.
- **Built-in coverage** — 65+ covergroups, CGF goals, auto-regression, scorecard,
  per-extension rollup, single-file HTML dashboard. None of this needs an SV simulator.
- **581+ instructions** across RV32/RV64 I/M/A/C/F/FC/D/DC, Zba/Zbb/Zbc/Zbs,
  Zbkb/Zbkc/Zbkx/Zkne/Zknd/Zknh/Zksh/Zksed, RVV 1.0, Zvbb/Zvbc/Zvkn/Zvfh,
  **Zfh half-precision**, Zicond, Zicbom/Zicboz/Zicbop, Zihintpause/Zihintntl,
  Zimop, Zcmop.
- **Privileged subsystem**: trap handler, interrupt dispatch, **Sv32/Sv39/Sv48 paging**,
  PMP cfg packing + NAPOT/TOR, debug ROM with single-step.
- **Moat features no other generator has**: failure-minimizer (delta-debug a
  failing `.S` to ≤ 5 instructions), genetic-algorithm seed search, coverage-
  directed auto-regression, SystemVerilog covergroup export, riscv-isac CGF
  round-trip.

## Quick start

```bash
pip install rvgen
python -m rvgen --target rv32imc --test riscv_rand_instr_test \
    --steps gen,gcc_compile,iss_sim --iss spike \
    --output ./out --start_seed 100 -i 1
```

Or use the library API:

```python
from rvgen import Generator
g = Generator(target="rv64gcv_crypto", test="riscv_rand_instr_test", iterations=4)
for asm in g.generate():
    print(f"{asm.test_id}: {len(asm.lines)} lines, seed {asm.seed}")
```

## Coverage at a glance

```bash
# Run a test, collect coverage, build a dashboard.
python -m rvgen \
    --target rv64gc --test riscv_rand_instr_test --priv msu \
    --steps gen,gcc_compile,iss_sim,cov --iss spike --iss_trace \
    --output run

python -m rvgen.coverage.tools dashboard \
    --db run/coverage.json --goals rvgen/coverage/goals/baseline.yaml \
    --goals rvgen/coverage/goals/rv64gc.yaml \
    -o coverage.html
```

The dashboard ships with summary tiles, per-subsystem bar chart, convergence
timeline, top-25 missing bins, and a filterable per-covergroup breakdown — all
in a single self-contained HTML file. No CDN, no plotly.

## Where to next

- [**Architecture overview**](architecture.md) — how the generator is structured,
  module-by-module.
- [**Coverage workflow**](coverage.md) — goals, auto-regression, scorecard,
  cov-explain.
- [**Verification guide**](verification-guide.md) — bring up your core with rvgen.
- [**User-area targets**](user-area.md) — drop-in YAML targets, no Python edits.
- [**Releasing**](releasing.md) — packaging + PyPI publish checklist.
