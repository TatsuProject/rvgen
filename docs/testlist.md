# Testlist + gen_opts reference

A **test** in rvgen is a named YAML entry that specifies
generation options, iterations, and metadata. The schema is
byte-compatible with [riscv-dv's testlist.yaml
format](https://github.com/chipsalliance/riscv-dv/tree/master/yaml),
so you can point our CLI directly at riscv-dv's existing testlists.

## File shape

```yaml
# my_testlist.yaml
- test: riscv_arithmetic_basic_test
  description: >
    Arithmetic-heavy stream with corner-value register init.
  iterations: 2
  gen_test: riscv_instr_base_test        # SV class name — ignored by us (see below)
  gen_opts: >
    +instr_cnt=10000
    +num_of_sub_program=0
    +directed_instr_0=riscv_int_numeric_corner_stream,4
    +no_fence=1
    +no_data_page=1
    +no_branch_jump=1
    +boot_mode=m
    +no_csr_instr=1
  rtl_test: core_base_test                # RTL-side test name — ignored by us

- test: my_custom_test
  description: Stress CSR + hazards + directed load-store.
  iterations: 4
  gen_test: riscv_instr_base_test
  gen_opts: >
    +instr_cnt=8000
    +no_csr_instr=0
    +directed_instr_0=riscv_load_store_hazard_instr_stream,5
    +directed_instr_1=riscv_jal_instr,10
  rtl_test: core_base_test

- import: <riscv_dv_root>/yaml/base_testlist.yaml   # chain another file
```

**Field reference:**

| Field | Meaning | Honored by us |
|---|---|---|
| `test` | Name matched against CLI `--test`. | ✓ |
| `description` | Free text. | ✓ (shown in logs) |
| `iterations` | Number of seeds to run per test. Overridable via `-i`. | ✓ |
| `gen_test` | SV: selects a UVM subclass of `riscv_instr_base_test`. | ignored (we use one AsmProgramGen for everything; behavioural differences come from gen_opts) |
| `gen_opts` | Multiline plusarg string passed to the generator. | ✓ |
| `rtl_test` | RTL-side test name (Verilator / VCS testbench). | ignored (we don't do RTL sim) |
| `sim_opts`, `compare_opts`, `gcc_opts` | RTL-sim / comparison / compile opts. | only `gcc_opts` honored. |
| `no_iss` | Skip ISS simulation. | ✓ |
| `no_gcc` | Skip GCC compile. | ✓ |
| `import` | Recursively load another testlist. | ✓ — `<riscv_dv_root>` substitution supported. |

## Running

```bash
# Point at your testlist
python -m rvgen \
    --target rv32imc --test my_custom_test \
    --testlist my_testlist.yaml \
    --steps gen,gcc_compile,iss_sim,cov --iss spike \
    --output out/ -i 4 --start_seed 100

# Or use riscv-dv's bundled testlist (default fallback)
python -m rvgen \
    --target rv32imc --test riscv_arithmetic_basic_test \
    --testlist /path/to/riscv-dv/target/rv32imc/testlist.yaml \
    --steps gen --output out/
```

When you omit `--testlist`, the CLI looks up
`<riscv_dv_root>/target/<target>/testlist.yaml` where `<riscv_dv_root>`
defaults to `~/Desktop/verif_env_tatsu/riscv-dv` (override via
`--riscv_dv_root`).

## gen_opts plusargs

Every plusarg honored by the generator. Format: `+key=value` or bare
`+key` (treated as `+key=1` for bools).

### Program shape

| Flag | Default | Effect |
|---|---|---|
| `+instr_cnt=<N>` | 200 | Main-program instruction count. Typical stress values: 2000–10000. |
| `+num_of_sub_program=<N>` | 5 | How many sub-program call targets to emit. 0 = flat program. |
| `+main_program_instr_cnt=<N>` | = instr_cnt | Override main-program count independently. |
| `+max_branch_step=<N>` | 20 | Upper bound on forward branch distance. |
| `+max_directed_instr_stream_seq=<N>` | 20 | Cap on the number of directed-stream slots honored. |

### Instruction family gates

| Flag | Default | Effect |
|---|---|---|
| `+no_branch_jump=<0/1>` | 0 | Suppress BRANCH category from the random pool. |
| `+no_fence=<0/1>` | 0 | Suppress FENCE / FENCE.I / SFENCE.VMA. |
| `+no_csr_instr=<0/1>` | 0 | Suppress CSRRW/CSRRS/CSRRC(I) from the random pool. |
| `+no_ebreak=<0/1>` | 1 | Include EBREAK when = 0. |
| `+no_ecall=<0/1>` | 1 | Include ECALL when = 0. |
| `+no_wfi=<0/1>` | 1 | Include WFI when = 0. |
| `+no_dret=<0/1>` | 1 | Include DRET when = 0. |
| `+disable_compressed_instr=<0/1>` | 0 | Drop all RVC opcodes. |
| `+no_data_page=<0/1>` | 0 | Skip emitting `.section .data` / random data region. |
| `+no_load_store=<0/1>` | 0 | Suppress load/store from random pool. (Still emitted by directed streams.) |
| `+bare_program_mode=<0/1>` | 0 | Skip ALL CSR-based boot setup and trap handler. For rv32ui-style no-CSR cores. |

### Extensions

| Flag | Default | Effect |
|---|---|---|
| `+enable_floating_point=<0/1>` | 0 | Enable RV32F/D / RV32FC/DC groups. |
| `+enable_vector_extension=<0/1>` | 0 | Enable RVV / Zve* groups (auto-set when target has a vector profile). |
| `+vector_instr_only=<0/1>` | 0 | Restrict random pool to vector ops only. |
| `+enable_b_extension=<0/1>` | 0 | Enable draft-B mnemonics. |
| `+enable_zba_extension=<0/1>` | 0 | Enable ratified Zba. Same for `+enable_zbb_extension`, etc. |
| `+enable_unaligned_load_store=<0/1>` | 0 | Allow unaligned offsets in load/store streams. |

### Privilege

| Flag | Default | Effect |
|---|---|---|
| `+boot_mode=<m/s/u>` | m | Initial privilege mode. `s` / `u` require target that supports them. |
| `+enable_interrupt=<0/1>` | 0 | Enable interrupts in MSTATUS. |
| `+enable_timer_irq=<0/1>` | 0 | Arm the timer interrupt. |
| `+enable_illegal_csr_instruction=<0/1>` | 0 | Inject illegal-CSR ops. |
| `+mstatus_mprv=<0/1>` | 0 | Set MSTATUS.MPRV at boot. |
| `+mstatus_fs=<0..3>` | 0 | Set MSTATUS.FS at boot. |

### Directed streams

Directed streams are injected via *indexed* plusargs. Each index is a
(stream_name, count) pair:

```
+directed_instr_0=riscv_int_numeric_corner_stream,4
+directed_instr_1=riscv_jal_instr,8
+directed_instr_2=riscv_load_store_rand_instr_stream,6
```

The index is for uniqueness only — all slots are honored regardless of
index order. 16 slots are available (indices 0–15).

### Signature handshake

For integration with trace-comparison infrastructure:

| Flag | Default | Effect |
|---|---|---|
| `+signature_addr=<hex>` | 0xDEADBEEF | Magic address for signature writes. |
| `+require_signature_addr=<0/1>` | 0 | Emit the INITIALIZED / IN_MACHINE_MODE signature handshake. |

## Directed-stream catalogue

All registered via SV class name. Reference one from gen_opts via
`+directed_instr_N=<stream_name>,<count>`.

| Stream name | Class | What it does |
|---|---|---|
| `riscv_int_numeric_corner_stream` | `IntNumericCornerStream` | Initialises a pool of registers to corner values (0, all-ones, min-signed, random) via `li`, then emits 15–30 random ARITH/LOGICAL/COMPARE/SHIFT ops constrained to that pool. |
| `riscv_jal_instr` | `JalInstr` | Shuffled Hamiltonian chain of JAL ops — visits every target label exactly once via a linear path. |
| `riscv_jalr_instr` | `JalrInstr` | AUIPC + JALR pairs — coverage-driven addition for the JALR opcode which isn't in any other stream. |
| `riscv_loop_instr` | `LoopInstr` | Countdown loop with BNE backward branch; init + body + counter update. |
| `riscv_lr_sc_instr_stream` | `LrScInstrStream` | LR/SC retry loops to an AMO region. |
| `riscv_amo_instr_stream` | `AmoInstrStream` | AMO*.w / .d sequences to the AMO region. |
| `riscv_load_store_base_instr_stream` | `LoadStoreBaseInstrStream` | Base class — locality-aware offset generation (NARROW/HIGH/MEDIUM/SPARSE), alignment-aware width selection (LB everywhere, LH at half-aligned, LW at word-aligned, LD/SD on RV64 at 8-aligned, FLW/FSW when FP enabled). |
| `riscv_load_store_stress_instr_stream` | `LoadStoreStressInstrStream` | Back-to-back loads/stores, no random mix. |
| `riscv_load_store_rand_instr_stream` | `LoadStoreRandInstrStream` | Balanced loads/stores + random arithmetic mix. |
| `riscv_hazard_instr_stream` | `HazardInstrStream` | Restricts the value-register pool to 6 regs to force GPR hazards. |
| `riscv_load_store_hazard_instr_stream` | `LoadStoreHazardInstrStream` | `hazard_ratio` (20–100%) probability of reusing the previous offset — creates RAW/WAW on same address. |
| `riscv_multi_page_load_store_instr_stream` | `MultiPageLoadStoreInstrStream` | 2–8 independent sub-streams across distinct memory regions, interleaved. |
| `riscv_mem_region_stress_test` | `MemRegionStressTest` | Extends multi-page with wider region count. |
| `riscv_load_store_rand_addr_instr_stream` | `LoadStoreRandAddrInstrStream` | SPARSE locality — full signed 12-bit offsets. May take access-fault exceptions (intentional). |
| `riscv_load_store_shared_mem_stream` | `LoadStoreSharedMemStream` | Shared region_0 — for multi-hart testing (deferred). |

## Writing your own stream

Minimal template (drop under `rvgen/streams/my_stream.py`):

```python
from dataclasses import dataclass
from rvgen.isa.factory import get_instr
from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream


@dataclass
class MyBurstStream(DirectedInstrStream):
    """10 back-to-back ADDs with rd == rs1 (in-place accumulation)."""

    def build(self) -> None:
        for _ in range(10):
            instr = get_instr(RiscvInstrName.ADD)
            instr.rs1 = RiscvReg.T0
            instr.rs2 = self.rng.choice([r for r in RiscvReg
                                         if r not in self.cfg.reserved_regs])
            instr.rd = RiscvReg.T0  # in-place
            instr.post_randomize()
            self.instr_list.append(instr)


register_stream("my_burst_stream", MyBurstStream)
```

Reference it from your testlist:

```yaml
- test: my_burst_test
  gen_opts: >
    +instr_cnt=5000
    +directed_instr_0=my_burst_stream,5
```

That's it — no other wiring needed. The stream runs 5 times per test and
gets interleaved into the random main sequence.

## Writing a new test

If you just want to tweak gen_opts, no Python is needed. Add an entry
to your testlist.yaml and invoke:

```yaml
- test: riscv_hazard_heavy_test
  description: "Force hazards via tight reg pool + directed hazard streams."
  iterations: 4
  gen_test: riscv_instr_base_test
  gen_opts: >
    +instr_cnt=8000
    +num_of_sub_program=3
    +directed_instr_0=riscv_hazard_instr_stream,6
    +directed_instr_1=riscv_load_store_hazard_instr_stream,6
    +no_csr_instr=0
  rtl_test: core_base_test
```

```bash
python -m rvgen --target rv32imc --test riscv_hazard_heavy_test \
    --testlist my_tests.yaml --steps gen,gcc_compile,iss_sim,cov \
    --iss spike --iss_trace --output out/ -i 4 --start_seed 100
```

Four seeds × one test = four `.S` files + merged coverage. Compare
against your goals file, iterate.
