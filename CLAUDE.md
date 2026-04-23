# chipforge-inst-gen

A pure-Python re-implementation of **riscv-dv**, Google's UVM/SystemVerilog random instruction generator. Phase 1 = exact parity; Phase 2 = beyond. No Verilator, VCS, Questa, UVM, or PyVSC — just Python. SV reference lives at `~/Desktop/verif_env_tatsu/riscv-dv/`.

---

## 0 — Status and where to pick up

**Current phase:** Phase 1 steps 1–7 substantially complete + Phase 2 crypto + **RVV 1.0 baseline** landed. **260 unit tests passing.** End-to-end CLI pipeline (gen → gcc_compile → iss_sim) passes **51/51** non-vector combinations on Spike (rv32imc/rv32imafdc/rv32imcb/rv64imc/rv64imcb) plus **18/18** rv64gcv+vector combinations on spike-vector, plus **21/21 trace-level matches on the chipforge-mcu RTL sim** (`rv32imc_zkn` — RV32IMC + Zkn umbrella). Instruction registry = **485 ops** (184 RVV), stream registry = **11 streams**, **22 targets**. Reproducible via `scripts/mcu_validate.sh` + the vector sweep in §0 below.

Last substantive session (2026-04-23) landed RVV 1.0 baseline: `VectorConfig` (with legal_eew computation + SV-style constraint validation), `VectorInstr` base class (port of `riscv_vector_instr.sv`), ~130 vector mnemonics registered via `define_vector_instr`, vsetvli-v1.0 boot init (`e<SEW>, m<LMUL>, ta, ma`) emitted in the init section, random stream interleaves vector ops on rv64gcv. Key deviations from the raw SV port: clamped legal_eew to ELEN (SV allows illegal EEW for exception-path testing; our first pass keeps output runnable), split .vi immediate rendering by opcode (shifts / slide / rgather need unsigned 0..31; add/sub/compare/logical need signed -16..15 to assemble on GCC 15.1), RVV 1.0 vtypei string (no more EDIV / `d<N>`).

Prior session (2026-04-22, post-commit `ddfe4b8`) cleared a batch of blocking bugs: trap-handler alignment (MTVEC mode-bit masking was jumping into the middle of a compressed instruction), JalInstr Hamiltonian chain rewrite, compressed-FP + Zb* landing, FP unsupported-instr gating, RV64 misaligned-store in trap prologue, RVC loop-counter clobber via rs1, bare_program_mode boot-CSR skip, CSR-write whitelist to MSCRATCH only, LoadStore base-reg rd protection. All verified via the regression sweep + MCU trace compare.

### Canonical regression sweep

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
    rm -rf /tmp/reg_${target}_${test}_${s}
    /home/qamar/anaconda3/bin/python -m chipforge_inst_gen \
      --target $target --test $test \
      --steps gen,gcc_compile,iss_sim --iss spike \
      --output /tmp/reg_${target}_${test}_${s} --start_seed $s -i 1 2>&1 \
      | grep -qE "tests passed ISS sim" \
      && echo "PASS $target/$test/$s" || echo "FAIL $target/$test/$s"
  done
done
```

Expected: 51/51 PASS. A FAIL is the first bread-crumb — inspect `/tmp/reg_.../asm_test/*.S` and `/tmp/reg_.../spike_sim/*.log`. MCU trace-level match uses `scripts/mcu_validate.sh <test> <seed>` (7 tests × seeds 100/200/300 = 21/21).

Vector sweep (requires `SPIKE_PATH=/home/qamar/tools/spike-vector/bin/spike`):

```bash
export SPIKE_PATH=/home/qamar/tools/spike-vector/bin/spike
for t in riscv_rand_instr_test riscv_arithmetic_basic_test riscv_loop_test \
         riscv_jump_stress_test riscv_no_fence_test riscv_rand_jump_test; do
  for s in 100 200 300; do
    rm -rf /tmp/vreg_${t}_${s}
    /home/qamar/anaconda3/bin/python -m chipforge_inst_gen \
      --target rv64gcv --test $t \
      --steps gen,gcc_compile,iss_sim --iss spike \
      --output /tmp/vreg_${t}_${s} --start_seed $s -i 1 2>&1 \
      | grep -qE "tests passed ISS sim" \
      && echo "PASS rv64gcv/$t/$s" || echo "FAIL rv64gcv/$t/$s"
  done
done
```

Expected: 18/18 PASS. Each run emits ~750 vector ops out of ~3000 lines.

### Prompt to resume a fresh session

> Read `CLAUDE.md` §0 in `/home/qamar/chipforge/chipforge-inst-gen/` first. 260 unit tests pass, 51/51 Spike E2E, 18/18 rv64gcv vector E2E, 21/21 chipforge-mcu trace-level match. Pick the next item from §0 "Next-up queue" and work on it. Keep running `python -m pytest tests/` after every change. Update §0 when a major milestone lands.

Rules for either a generic continuation or a specific task: prefer editing existing files, cross-check each change against the SV reference at `~/Desktop/verif_env_tatsu/riscv-dv/`, run `python -m pytest tests/` after every change, update §0 when a major milestone lands.

### Current state (latest — 2026-04-23)

- **Instruction registry: 485 ops** — RV32I/M/A/C/F/FC/D/DC + RV64 counterparts + Zba/Zbb/Zbc/Zbs + draft RV32B + Zbkb/Zbkc/Zbkx + Zkne/Zknd/Zknh + Zksh/Zksed + **RVV 1.0 (184 ops)**.
- **Stream registry: 11** — corner, JAL, loop, LR/SC, AMO, 7 load/store aliases.
- **Targets: 22** — rv32i, rv32im, rv32ic, rv32ia, rv32iac, rv32imac, rv32if, rv32imc, rv32imafdc, rv32imcb, rv32imc_sv32, rv32ui, rv32imc_zkn, rv32imc_zkn_zks, rv64imc, rv64imcb, rv64imc_zkn, rv64imafdc, rv64gc, rv64gcv, ml, multi_harts.
- **Unit tests: 260 passing** (`/home/qamar/anaconda3/bin/python -m pytest tests/`).

Phase-1 steps 1–5 are substantively done; step 6 (M/C/A/F/D/B/Zb/K) is done; step 7 (directed streams) is partial — `IntNumericCornerStream`, `JalInstr`, `LoopInstr`, `LoadStoreRandInstrStream` (aliased 7×), `LrScInstrStream`, `AmoInstrStream` are registered; step-7-proper (NARROW/HIGH/MEDIUM/SPARSE locality variants) is still queued. **Step 9 vector baseline landed**: `chipforge_inst_gen/vector_config.py` (VectorConfig + Vtype dataclass + legal_eew post-init + SV validation), `chipforge_inst_gen/isa/vector.py` (VectorInstr base + `define_vector_instr` factory), `chipforge_inst_gen/isa/rv32v.py` (~130 mnemonic registrations), vsetvli-v1.0 init + `vmv.v.x v<N>, x<N>` vreg init in `asm_program_gen._gen_vector_init`, vector-aware filter guards in `isa/filtering.py` (widening/narrowing/vec_fp/zvlsseg/zvamo + VADC/VSBC/VSETVLI drops), `stream.py` calls `randomize_vector_operands` alongside FP. Zvlsseg and Zvamo classes are registered but filtered off by default — a Phase-1 later flip.

### Next-up queue (priority-ordered)

1. **Vector loads/stores** (step 9 Phase 2). Directed `VectorLoadStoreStream` (unit-stride / strided / indexed) pinning rs1 to a legal memory region — currently the random stream can produce vector loads with rs1=zero that spike happily runs on x0 but wastes RVV coverage. Also wire up a `riscv_vector_arithmetic_test` entry so the canonical testlist picks rv64gcv up automatically.
2. **Full privileged mode** (step 8). Paging (SV32/SV39/SV48), PMP cfg packing + NAPOT encoding, S/U-mode boot, debug ROM (DCSR/DPC/DSCRATCH, single-step). Unlocks `riscv_mmu_stress_test`/`riscv_privileged_mode_rand_test`/`riscv_pmp_test`/`riscv_ebreak_debug_mode_test`.
3. **Distinct load/store stream variants** (step 7 proper). Port SV's NARROW / HIGH / MEDIUM / SPARSE locality variants with alignment-aware instr selection + proper multi-page stream.
4. **Golden-file diff harness** (step 12). Compare our `.S` structurally vs riscv-dv's `2026-04-21/` reference — section order, label presence, instruction-mix distributions.
5. **Widen CSR-write whitelist**. Currently writes only MSCRATCH. Port SV's `+include_write_reg=...` plusarg.
6. **Vector FP / widening / narrowing**. All classes exist but are gated off — flip `vec_fp` / `vec_narrowing_widening` on via a CLI plusarg and wire the additional VMV alignment constraints.
7. **Zfh / Zvfh / Zc* / Zicond / Zimop** (Phase 2 ISA extensions). Same pattern as `isa/crypto.py`.
8. **Cross-ISS compare** (ovpsim + sail + whisper). Port `scripts/instr_trace_compare.py` properly into the library.

---

## 1 — Goal

### Phase 1 — Exact parity with riscv-dv
- Match every test in `target/<T>/testlist.yaml` and `yaml/base_testlist.yaml`.
- Output `.S` files that are **structurally identical** (same section order, label format, register-init pattern, trap-handler shape, signature-handshake format) and **ISS-equivalent** to riscv-dv's output. Byte-identical is not required (seeds/PRNGs differ).
- Same CLI surface + YAML schema as `run.py`; same integrations (spike/ovpsim/sail/whisper + riscv-gcc). We only replace the SV/UVM generator.

### Phase 2 — Beyond riscv-dv
- Add extensions riscv-dv's pygen never ported (full RVV, Zfh, Zvfh, Zc*, Zk*, Smaia/Ssaia, Svnapot, Svpbmt, etc.).
- Declarative composable YAML configs (vs `+plusarg` soup).
- Library API (`from chipforge_inst_gen import Generator`).
- Faster generation (pygen ≈ 12 min for 10K-instr; goal: seconds).

### Non-goals
- No constraint-solver dependency (PyVSC, Z3). Rejection-sampling with `random` is fast enough and debuggable.
- No RTL simulator integration. We stop at `.S` / `.bin` / ISS log.
- No GUI.

---

## 2 — Reference tree (riscv-dv source of truth)

SV source lives at `~/Desktop/verif_env_tatsu/riscv-dv/`. Mapping is one-to-one with our tree:

| SV path | Our mirror |
|---|---|
| `run.py` | `cli.py` |
| `src/riscv_instr_pkg.sv` | `isa/{enums,csrs,utils}.py` |
| `src/riscv_defines.svh` | `isa/factory.py` |
| `src/isa/riscv_instr.sv` | `isa/base.py` |
| `src/isa/rv*_instr.sv` | `isa/rv32i.py` etc. |
| `src/isa/riscv_compressed_instr.sv` | `isa/compressed.py` |
| `src/isa/riscv_floating_point_instr.sv` | `isa/floating_point.py` |
| `src/isa/riscv_vector_instr.sv` | `isa/vector.py` |
| `src/isa/riscv_amo_instr.sv` | `isa/amo.py` |
| `src/isa/riscv_b_instr.sv`, `riscv_zb*_instr.sv` | `isa/bitmanip.py` |
| `src/isa/riscv_csr_instr.sv` | `isa/csr_ops.py` |
| `src/riscv_instr_gen_config.sv` | `config.py` |
| `src/riscv_vector_cfg.sv` | `vector_config.py` |
| `src/riscv_pmp_cfg.sv` | `pmp_config.py` |
| `src/riscv_privil_reg.sv` | `privileged/csr_fields.py` |
| `src/riscv_privileged_common_seq.sv` | `privileged/boot.py` |
| `src/riscv_page_table*.sv` | `privileged/paging.py` |
| `src/riscv_debug_rom_gen.sv` | `privileged/debug_rom.py` |
| `src/riscv_data_page_gen.sv` | `sections/data_page.py` |
| `src/riscv_signature_pkg.sv` | `sections/signature.py` |
| `src/riscv_instr_stream.sv` | `stream.py` |
| `src/riscv_instr_sequence.sv` | `sequence.py` |
| `src/riscv_directed_instr_lib.sv` | `streams/directed.py` |
| `src/riscv_load_store_instr_lib.sv` | `streams/load_store.py` |
| `src/riscv_amo_instr_lib.sv` | `streams/amo.py` |
| `src/riscv_loop_instr.sv` | `streams/loop.py` |
| `src/riscv_illegal_instr.sv` | `streams/illegal.py` |
| `src/riscv_pseudo_instr.sv` | `isa/pseudo.py` |
| `src/riscv_callstack_gen.sv` | `callstack.py` |
| `src/riscv_asm_program_gen.sv` | `asm_program_gen.py` |
| `target/<T>/riscv_core_setting.sv` | `targets/<T>.py` |
| `target/**/testlist.yaml`, `yaml/*.yaml` | reused as-is |
| `pygen/pygen_src/` | Study but don't depend on (PyVSC, incomplete). |
| `2026-04-21/.../asm_test/*.S` | 100 golden `.S` files for diff harness. |

---

## 3 — Architectural invariants (must not drift)

These define "exact parity" — come out of golden `.S` files + SV source.

1. **Section order in `.S`:**
   ```
   .include "user_define.h"
   .globl _start
   .section .text
   [.option norvc;]            ; if disable_compressed_instr
   .include "user_init.s"
   _start:    [MHARTID dispatch]
   h0_start:  [setup_misa → page tables → pre_enter_privileged_mode]
   init:      [FP init → GPR init → SP init → vector init → signature INITIALIZED → dummy CSR writes]
   (if PMP) [trap_handlers, test_done]
   sub_1:, sub_2:, …
   main:     [directed streams interleaved into random stream]
   (if !PMP) test_done:
   (insert sub-programs)
   h<N>_instr_end: nop
   .section .data
   .align 6; .global tohost; tohost: .dword 0;
   .align 6; .global fromhost; fromhost: .dword 0;
   [data pages: .section .h<N>_region_<i>, "aw",@progbits]
   .section .h<N>_user_stack,"aw",@progbits  [stack_len .4byte/.8byte 0x0]
   (if !bare) kernel_instr_* , kernel_data_*, kernel_stack_*
   .section .h<N>_page_table,"aw",@progbits  [if paging]
   ```

2. **Label column** = 18 chars (`LABEL_STR_LEN`). Unlabeled lines = 18 spaces.
3. **Mnemonic column** = 13 chars (`MAX_INSTR_STR_LEN`).
4. **Boot CSR sequence** (`init_privileged_mode`): write MSTATUS (MPP=target_mode, MPIE/SPIE/UPIE per `enable_interrupt`, MPRV/MXR/SUM/TVM/TW/FS/VS per knobs); write MIE if implemented; `gen_csr_instr()` emits `li xgpr0, 0x<val>; csrw 0x<addr>, xgpr0 # <NAME>`; if SATP_MODE != BARE load root page table label, >>12 to PPN, csrs SATP with MODE|PPN; `mret`.
5. **Trap handler (DIRECT mode)** — `push_gpr_to_kernel_stack`: `addi tp,tp,-4; sw sp,0(tp); add sp,tp,zero` if scratch CSR implemented; if MSTATUS && SATP != BARE && MPRV, sign-extend sp via slli/srli by `XLEN - MAX_USED_VADDR_BITS` (=30) when MPP != M; `addi sp,sp,-32*(XLEN/8)`; push x1..x31; `add tp,sp,zero`. Dispatch on xCAUSE bit XLEN-1; `ebreak_handler`/`illegal_instr_handler` bump xEPC by 4 before pop; each ends `pop_gpr_from_kernel_stack` + `mret/sret/uret`. **VECTORED**: 16-entry jump table, entry 0 = exception, 1..15 = interrupts. `mtvec_handler:` must be aligned to `tvec_alignment` (MTVEC low 2 bits are MODE, not PC).
6. **Signature protocol** (32-bit word to `cfg.signature_addr`): CORE_STATUS=00 `[12:8]=status,[7:0]=00`; TEST_RESULT=01 `[8]=result,[7:0]=01`; WRITE_GPR=02 then 32 words; WRITE_CSR=03 `[19:8]=csr,[7:0]=03` then csrr+store.
7. **Reserved registers** (`cfg.reserved_regs` in `post_randomize`): `{tp, sp, scratch_reg}` + `gpr[0..3]`, `pmp_reg[0..1]`, `ra`. GP implicit (holds 1 at test_done).
8. **Stack section**:
   ```
   .section .h<N>user_stack,"aw",@progbits;
   .align <12 if SATP != BARE else 2>
   h<N>user_stack_start:
   .rept <stack_len - 1>
   .8byte 0x0   (or .4byte on XLEN=32)
   .endr
   h<N>user_stack_end:
   .8byte 0x0
   ```
9. **FP init**: `li x<t>, <rand32>; fmv.w.x f<n>, x<t>` for f0..f31; then `fsrmi <rm>`. No FLW/FLD/.rodata constants.
10. **GPR init distribution**: `reg_val dist {0:=1, 0x80000000:=1, [0x1:0xF]:=1, [0x10:0xEFFFFFFF]:=1, [0xF0000000:0xFFFFFFFF]:=1}`.
11. **Branch-target resolution** (`post_process_instr`): numeric labels `0:..N:` in order; each random BRANCH picks step ∈ [1, max_branch_step] (default 20), target clamped to `label_idx-1`; byte offset = Σ per-instr size (2 RVC, 4 else); unused labels erased.
12. **insert_instr_stream(new, idx=-1, replace=False)**: pick random idx; if picked instr has `atomic=True` retry up to 10 times; scan for first non-atomic as fallback. Directed-stream atoms carry `Start <name>` / `End <name>` comments.
13. **Call-stack generation**: program 0 = main at level 0; levels ascend monotonically by ≤1; sub-program IDs shuffled + distributed to callers of prev level; no recursion (`unique sub_program_id`, `!= program_id`). Max depth 20, max sub-programs 20, max calls per func 5.
14. **CSR-write whitelist** (SV `include_write_reg` default): CSRRW/CSRRWI/CSRRS/CSRRC(I) target CSR restricted to `{MSCRATCH}`. Writing random values to MISA/MSTATUS/MTVEC silently bricks the test (e.g. clearing MISA.C → RVC → illegal → handler loop).
15. **LoadStore base-reg protection**: the base register holding the region address must be pinned in a `base_locked` set — never pickable as a load `rd` (would overwrite the base with garbage).

---

## 4 — Enum and CSR reference (condensed — see `research/02_instr_pkg_enums.md` for full)

**Groups:** RV32I, RV64I, RV32M, RV64M, RV32A, RV64A, RV32F, RV32FC, RV64F, RV32D, RV32DC, RV64D, RV32C, RV64C, RV128I, RV128C, RVV, RV32B, RV32ZBA/ZBB/ZBC/ZBS, RV64B, RV64ZBA/ZBB/ZBC/ZBS, RV32X, RV64X.

**Categories:** LOAD, STORE, SHIFT, ARITHMETIC, LOGICAL, COMPARE, BRANCH, JUMP, SYNCH, SYSTEM, COUNTER, CSR, CHANGELEVEL, TRAP, INTERRUPT, AMO.

**Formats:** J, U, I, B, R, S, R4, CI, CB, CJ, CR, CA, CL, CS, CSS, CIW, VSET, VA, VS2, VL, VS, VLX, VSX, VLS, VSS, VAMO.

**Privilege / misc:** `privileged_mode_t` {USER=0, SUPERVISOR=1, RESERVED=2, MACHINE=3}; `f_rounding_mode_t` {RNE=0,RTZ=1,RDN=2,RUP=3,RMM=4}; `satp_mode_t` {BARE=0,SV32=1,SV39=8,SV48=9,SV57=10,SV64=11}; `mtvec_mode_t` {DIRECT=0,VECTORED=1}; `pmp_addr_mode_t` {OFF=00,TOR=01,NA4=10,NAPOT=11}; `pte_permission_t` {NEXT_LEVEL=000,R=001,RW=011,X=100,RX=101,RWX=111}; `exception_cause_t` 0..F = IAM,IAF,ILLEGAL,BREAKPOINT,LAM,LAF,SAM,SAF,ECALL_U,ECALL_S,–,ECALL_M,IPF,LPF,–,SPF.

**CSRs**: standard addresses follow spec — user 0x000–0x044 + 0xC00–0xC9F counters; supervisor 0x100–0x180; machine info 0xF11–0xF15; M-trap setup 0x300–0x310; M-trap handling 0x340–0x344; M-config 0x30A/0x31A/0x747/0x757; PMP 0x3A0–0x3AF cfg, 0x3B0–0x3EF + 0x4C0–0x4DF addr; debug 0x7A0–0x7A5 / 0x7B0–0x7B3; vector 0x008/009/00A + 0xC20–0xC22.

**Constants:** XLEN ∈ {32,64,128}; `MAX_INSTR_STR_LEN=13`; `LABEL_STR_LEN=18`; `MAX_CALLSTACK_DEPTH=20`; `MAX_SUB_PROGRAM_CNT=20`; `MAX_CALL_PER_FUNC=5`; `compressed_gpr={S0,S1,A0..A5}` (x8..x15); default writeable CSR set `{MSCRATCH}`; `MPRV_MASK=1<<17`; `SUM_MASK=1<<18`; `MPP_MASK=3<<11`.

---

## 5 — Targets and tests (condensed — see `research/01_targets_and_testlists.md`)

**Targets** → (XLEN, supported_isa, privilege, SATP, HARTS):
- rv32i/im/ic/if/ia/iac/imac/imc/imcb/imafdc: 32, varies, M (or M+U for sv32), BARE (SV32 for _sv32), 1.
- rv32imc_zkn / rv32imc_zkn_zks / rv32ui: 32, RV32IMC + crypto variants (bare for `rv32ui`), M, BARE, 1.
- rv64imc/imcb/imc_zkn/imafdc: 64, varies, M, BARE.
- rv64gc: 64, G, U+S+M, SV39.
- rv64gcv: 64, G+RVV (VLEN=512, ELEN=32, MAX_LMUL=8), M, BARE.
- ml, multi_harts: special.

**Base testlist (14):** riscv_arithmetic_basic_test, riscv_rand_instr_test, riscv_jump_stress_test, riscv_loop_test, riscv_rand_jump_test, riscv_mmu_stress_test, riscv_no_fence_test, riscv_illegal_instr_test, riscv_ebreak_test, riscv_ebreak_debug_mode_test, riscv_full_interrupt_test, riscv_csr_test, riscv_unaligned_load_store_test, riscv_amo_test.

**FP tests:** `riscv_floating_point_arithmetic_test` (iters=1, instr_cnt=10000, +enable_floating_point=1, +no_fence=1, +no_data_page=1, +no_branch_jump=1, +boot_mode=m); `riscv_floating_point_rand_test` (+directed 0..4, +no_fence=0); `riscv_floating_point_mmu_stress_test` (+directed 0..3, instr_cnt=5000).

**Vector (rv64gcv):** `riscv_vector_arithmetic_test`, `_stress_test` (+vector_instr_only=1), `_load_store_test`, `_amo_test`.

**Privileged / CSR / PMP / B:** `riscv_privileged_mode_rand_test`, `riscv_invalid_csr_test`, `riscv_page_table_exception_test`, `riscv_sfence_exception_test`, `riscv_u_mode_rand_test` (+boot_mode=u), `riscv_pmp_test`, `riscv_b_ext_test`, `riscv_zbb_zbt_test`.

**Config flags (verified)**: `+no_csr_instr`, `+no_fence`, `+no_ebreak`, `+no_ecall`, `+no_wfi`, `+no_branch_jump`, `+disable_compressed_instr`, `+no_data_page`, `+bare_program_mode` all suppress the targeted instructions. Default EBREAK/ECALL/WFI/DRET are off — pass `+no_ebreak=0` to enable. `bare_program_mode=1` skips setup_misa + pre_enter_privileged_mode.

---

## 6 — Phase-1 remaining work (Steps 1–7 done; 8–12 outstanding)

1–5: done (enums/CSRs/helpers, base Instr + factory + RV32I, Config/targets/testlist/CLI/seeding, InstrStream/Sequence/branch resolution, asm_program_gen + data_page + signature + M-mode DIRECT boot + trap).
6: done (M, C, A, F, D, B, Zb*, K crypto).
7: partial — core directed streams registered; SV's NARROW/HIGH/MEDIUM/SPARSE locality variants + proper multi-page stream not yet ported.

**Step 8 — Privileged: boot, trap, paging, PMP, debug.**
- Boot CSR sequence per §3-4; MRET mode transition already landed for M; add S/U.
- Trap handlers: DIRECT done; VECTORED + exception dispatch table for non-M targets outstanding.
- Paging SV32/SV39/SV48: PTE layout, page_table_list topology (1 + Link^N leaves), `process_page_table` linker, `gen_page_fault_handling_routine`.
- PMP: pmpcfg packing (RV32: 4/CSR, RV64: 8/CSR), pmpaddr NAPOT `addr>>2 | ((1<<g)-1)`, TOR monotonicity.
- Debug rom: DCSR ebreak bits, DPC increment if cause==ebreak, DSCRATCH0 single-step.
- Exception injection via `riscv_page_table_exception_cfg`.
- Done when: `riscv_mmu_stress_test`, `riscv_invalid_csr_test`, `riscv_privileged_mode_rand_test`, `riscv_u_mode_rand_test`, `riscv_pmp_test`, `riscv_ebreak_debug_mode_test` all run on spike + rv64gc core model.

**Step 9 — Vector (rv64gcv).**
- `VectorConfig`: vtype/vl/vstart/vxrm/vxsat, legal_eew, gates (vec_fp, narrowing/widening/quad, zvlsseg, fault_only_first, reg hazards).
- `vsetvli` boot init (`li vl; vsetvli x0, x1, e<SEW>, m<LMUL>, d<EDIV>`).
- Vector instruction base: vs1..vd, va_variant, vm, widening/narrowing/convert detection, overlap constraints, mask_enable/disable constraint.
- Streams: UNIT_STRIDED/STRIDED/INDEXED load/store, vector AMO.
- Done when: `riscv_vector_arithmetic_test`, `_load_store_test`, `_amo_test` assemble and pass spike `--isa=rv64gcv` without illegal-instruction traps.

**Step 10 — Multi-hart.** `NUM_HARTS>1`: `hart_prefix("h<n>_")` labels; main-entry reads MHARTID + branches; shared-memory LS stream. Done when: `multi_harts` testlist produces correct per-hart `.S`.

**Step 11 — ISS wrapping + comparison + GCC.** Port `scripts/*_log_to_trace_csv.py` and `scripts/instr_trace_compare.py`. GCC + objcopy per `run.py` `gcc_compile`. Pipeline replicates `steps = gen|gcc_compile|iss_sim|iss_cmp|all`. Done when full pipeline emits `iss_regr.log` with PASS lines across spike+ovpsim.

**Step 12 — Golden-file diff.** `tests/golden/` compares our output to `2026-04-21/riscv_floating_point_arithmetic_test/asm_test/*.S` structurally (section order, label presence, instr-mix dist, bootstrap shape) — not byte-for-byte.

---

## 7 — Phase 2 ideas (rough value order)

1. Full RVV 1.0 (pygen has none): Zve*, Zvl*, whole-reg moves, segmented 1..8-field, reductions, permutations.
2. Zfh / Zvfh / Zfinx / Zdinx / Zhinx.
3. Zk* crypto (Zknd, Zkne, Zknh, Zbkb, Zbkc, Zbkx, Zksh, Zksed) — partly done.
4. Zicond, Zimop, Zicfilp/Zicfiss, Svnapot, Svpbmt, Smaia/Ssaia.
5. Declarative YAML configs (replace `+plusarg` soup; keep parser for back-compat).
6. Library API: `Generator(target=..., test=..., iterations=...).generate()` returning `.S` strings + `(asm, bin)` pairs.
7. Faster generation — target <5s for 10k-instr (pygen ≈ 12 min).
8. Extension plugins via `isa/<ext>.py` with `@register_extension(...)`.
9. Structured coverage model replacing `riscv_instr_cover_group.sv` (~8k lines).
10. Per-seed manifest for exact rerun across generator versions.

---

## 8 — Conventions

- **Python 3.11+** (pattern matching, `StrEnum`). Only hard dep is `PyYAML`; optional `numpy`, `pytest`. **No constraint solver.**
- `dataclasses` + `typing`; mutable dataclasses; randomization methods mutate in place.
- No UVM factory: plain `dict[str, Type[Instr]]` registry. Honor riscv-dv class names (`riscv_<instr>_instr`) because `+directed_instr_N=<name>` references them.
- Stream output = `list[str]` lines (not one big string) so tests can match individual lines.
- One `random.Random(seed)` per generator; seed-derivation rules mirror `SeedGen` in `run.py`.
- Import shape: `from chipforge_inst_gen.isa import Instr, InstrName`.
- Enums match SV declaration order exactly.
- `.S` output = spaces only (no tabs), matching golden files.
- ABI register names in asm output (`a0`, `t0`, `fa0`), matching `riscv_reg_t.name()`.

---

## 9 — Testing strategy

- **Unit**: enums, helpers, per-instruction `convert2asm()`, branch-target math, stack layout, CSR field packing, PMP NAPOT encoding, PTE layout.
- **Golden diff** against `2026-04-21/` (100 FP `.S` files) → `tests/golden/`.
- **Assembler round-trip**: every `.S` through `riscv-unknown-elf-gcc`; fail on error.
- **Spike smoke**: every `.o` runs on spike and reaches `test_done` without unexpected trap.
- **Cross-ISS**: spike ↔ ovpsim where available.
- **MCU trace-level**: `scripts/mcu_validate.sh` — 7 tests × 3 seeds on chipforge-mcu Verilator.

---

## 10 — Research notes (under `research/`)

Distilled summaries of the SV source — always re-read before editing the corresponding module. Do not delete from this list.

- `01_targets_and_testlists.md` — target matrix, CSR implementations, testlist tree, yaml asset map.
- `02_instr_pkg_enums.md` — enum catalog, CSR addresses, parameters, helpers.
- `03_isa_class_hierarchy.md` — instr class tree, formats, encoding, per-class constraints.
- `04_asm_program_gen_flow.md` — phase order in `gen_program()`, boot CSR sequence, trap handler shapes, signature emit, stack/data/page sections, callstack, post_process_instr.
- `05_pygen_partial_port.md` — pygen SV→Py class map, gaps (no V, no Zb*, partial S/U/debug/PMP/paging; PyVSC; ~12 min).
- `06_run_py_and_iss.md` — CLI surface, YAML testlist schema, simulator.yaml / iss.yaml templating, seed logic, batching, gcc/objcopy, iss_cmp pipeline, parser regexes.
- `07_config_datapage_signature.md` — config knobs by topic, constraint blocks, data_page format, signature asm.
- `08_directed_instr_libs.md` — every directed-stream class with parameters + integration points.
- `09_privileged_paging_pmp_debug.md` — boot CSR sequence per mode, CSR field table, SATP setup, PTE layouts, page-fault routine, PMP packing + NAPOT, debug ROM.
- `10_vector_cfg_and_cov.md` — VectorCfg fields/constraints, vsetvli/vsetivli emit, legal_eew formula, cov.py orchestration.
- `11_golden_fp_sfiles.md` — exact formatting of golden `.S` (columns, li+fmv.w.x init, fsrmi, test_done trailer, stacks, seed variance vs invariants).

---

## 11 — Open questions / risks

- **Randomization parity.** SV uses constrained randoms; we use rejection sampling → not byte-identical for same seed. Strategy: structurally-equivalent + ISS-equivalent. Document explicitly in golden harness.
- **Custom / user extensions.** riscv-dv has `isa/custom/` and `user_extension/`. Stubbed out for Phase 1; first-class plugin system in Phase 2.
- **Debug ROM placement.** riscv-dv aligns to 4KB; some cores have specific reset-debug addresses — keep as config knob.
- **Vector vlmul** is an exponent (1/2/4/8) but `fractional_lmul` flips to fractional — legal-EEW set is derived, not a range. Preserve SV post-randomize formula.
- **`riscv_csr_test`** is generated by a separate script (`scripts/gen_csr_test.py`), not the main generator. Port as a separate command.
