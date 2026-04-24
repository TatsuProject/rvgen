# Known Issues — Generator Bug Tracker

Tracks live bugs in rvgen that cause real test failures, plus a log of
what's been fixed. Update whenever a new symptom is diagnosed or a fix
lands. Cross-reference SV riscv-dv source at
`~/Desktop/verif_env_tatsu/riscv-dv/src/` for every entry.

---

## Fixed (2026-04-24 session)

### F1 — MSTATUS.FS not set when `+enable_floating_point=1` · [802fd6a]
**Symptom:** FP op traps as `illegal_instruction`, handler bumps MEPC by
4 forever, MEPC walks off .text, `instruction_access_fault` livelock.
5/100 rc=124 timeouts on `riscv_floating_point_mmu_stress_test`.
**Fix:** `Config._finalize_extension_csr_state()` now forces
`mstatus_fs=01` (INITIAL) when FP enabled, `mstatus_vs=01` when RVV
enabled — mirrors SV `floating_point_c` / vector constraints in
`riscv_instr_gen_config.sv:466`.
**Regression test:** `test_mstatus_fs_set_initial_when_fp_enabled`.

### F2 — `tohost` emitted into `.data` adjacent to `region_0` · [802fd6a]
**Symptom:** Random store with small negative offset from region_0
stomps tohost; spike reads garbage as HTIF device pointer and aborts
(rc=255). 2/100.
**Fix:** Emit `tohost`/`fromhost` into the dedicated `.tohost` output
section (reserved by link script). Now 4 KiB from region_0, unreachable
from ±2 KiB random-store offsets.
**Regression test:** `test_tohost_in_dedicated_section`.
**Note:** SV has the same bug; we diverge here because it's a real
livelock and the fix is local.

### F3 — Empty spike `.log` files on clean passes · [802fd6a]
**Symptom:** Directory clutter — 100 empty 0-byte `.log` files per 100
tests when everything passes.
**Fix:** Only write `.log` when spike produced stdout/stderr content.
Errors, timeouts, and `--iss_trace` still emit logs as before.

### F4 — Exception handler blind `+4` MEPC bump · [1141d1e]
**Symptom:** When faulting instr is 2-byte compressed (e.g. `c.fsw`
trapping as store_access_fault), `+4` skips into the middle of the next
4-byte instruction. Spike re-decodes those middle halfwords as a new
compressed op. When that halfword happened to encode a valid write to a
reserved register (`c.lwsp tp, 124(sp)` in the seed-2556 trace), `tp`
got poisoned. Later trap then infinite-looped in handler prologue. 30s
rc=124 timeout.
**Fix:** Read halfword at MEPC, check bits[1:0]==11 for 4-byte vs
2-byte, bump by correct amount. `t1`/`t2` scratch-safe after
push_gpr_to_kernel_stack.
**SV divergence:** SV also bumps +4 blindly — we improved on it
(`src/riscv_asm_program_gen.sv:1214+`).
**Regression test:** `test_trap_handler_bumps_mepc_by_instr_length`.

### F5 — INSTRUCTION_ACCESS_FAULT / INSTRUCTION_PAGE_FAULT tried to resume · [1141d1e]
**Symptom:** MEPC points at unmapped memory; any attempt to `lhu 0(mepc)`
to determine instruction length itself re-faults → handler recursion.
**Fix:** `instr_fault_handler` now terminates via `write_tohost` instead
of attempting to resume. INSTRUCTION_PAGE_FAULT re-routed to this label.
**SV divergence:** SV attempts to resume (would livelock too); we're
safer.
**Regression test:** `test_instr_fetch_faults_terminate`.

### F6 — No `rv32imckf` target · [485e663]
**Symptom:** FP tests for chipforge Challenge-0014 core (RV32 +
single-precision FP + Zkn crypto, no D, no A) had to use `rv32imafdc`,
polluting traces with D-ops (`fld`, `fsd`, `fmax.d`, etc.) the core
doesn't implement.
**Fix:** New built-in target `rv32imckf` + ISA string
`rv32imfc_zbkb_zbkc_zbkx_zknd_zkne_zknh_zicsr_zifencei` (mabi=ilp32f).

---

## Unfixed (live bugs)

### U1 — Random stores can corrupt `.text`
**Symptom:** Random integer store with rs1=`t6` (or similar) that was
set by a prior `auipc t6, X` or `lui t6, Y` can land inside .text. A
single byte-flip in an instruction corrupts control flow — seed 10059
(old Set 2) flipped an `sb a5, imm(t6)` at `0x800051fa` to write `0xff`
at `0x8000805c`, corrupting the trap dispatch `bne` from
`0x04029f63` → `0x04ff9f63` (different register operands), hanging the
handler. Hit rate ~1/100 on fp_mmu_stress.
**Root cause:** `random_instr_stream` doesn't constrain rs1 of STORE
ops to be "known data-region" registers. SV has the same weakness
(`riscv_instr_stream.sv` random path).
**Candidate fix:** Post-init, load a data-region base address (region_0)
into a known subset of GPRs, flag them as "data-base" in avail_regs,
and force `STORE.rs1 ∈ {data-base} ∪ {sp}` in the random stream. Or
more faithfully: check preceding `auipc`/`lui` writes to the chosen rs1
and re-randomize if the upper bits look like .text (`0x80…`).
**Workaround:** pick seeds that don't hit this (Set 2 uses 20000+ →
100/100).

### U2 — Compressed instructions emitted with non-compressed-GPR destinations
**Symptom:** GCC-15 rejects generated asm:
`c.addi4spn s3, sp, 816` — rd=s3=x19 not in compressed GPR set
(x8..x15). Pre-existing; hits `riscv_rand_instr_test` on
rv32imc/rv64imc seeds 200, 300 reliably.
**Root cause:** Either format classification in `filtering.py` doesn't
flag `C_ADDI4SPN` as 3-bit-rd, or a code path emits instruction bypassing
`randomize_gpr_operands`. Needs audit of every compressed-instr class
against SV `riscv_compressed_instr.sv`.
**Impact:** Blocks rv32imc/rand_instr canonical sweep at ~3/51.

### U3 — Aliased load/store addresses can still occasionally escape directed-stream bounds
(To investigate — placeholder for pattern we've seen in sporadic
timeouts. May overlap with U1.)

---

## Cadence / invariants

- Run `python -m pytest tests/` after every change. 402/402 at HEAD.
- Run `canonical sweep` from CLAUDE.md §0 before commits that touch
  trap / boot / streams.
- For every bug, check SV reference at
  `~/Desktop/verif_env_tatsu/riscv-dv/src/` for the original constraint
  — port it faithfully unless SV has the same livelock as us, in which
  case note the divergence here.
