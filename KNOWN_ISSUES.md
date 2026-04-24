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

## Fixed (continued)

### F7 — MultiPage LS stream siblings clobber each other's base · [712daca]
**Symptom:** Random store landed in .text, silently corrupted an
instruction byte, later trap took a wrong branch → 30s livelock. Seed
10059 on fp_mmu_stress reliably reproduced.
**Root cause:** `MultiPageLoadStoreInstrStream` generates N interleaved
sub-streams, each pinning its own `rs1_reg` via `la rs1, region_N`.
Each sub's `_add_mixed_instr` protected ONLY its own rs1_reg from being
overwritten — but not its siblings'. After interleave, sub A's `la t6,
region_0; sh foo, imm(t6)` could have sub B's `mulhu t6, ra, t4`
sandwiched between them, leaving the store pointing anywhere.
**Fix:** New `extra_locked_regs` field on LoadStoreBaseInstrStream;
propagated to mixed-op reserved_rd. `MultiPageLoadStoreInstrStream`
collects all sibling rs1_regs up-front and forbids mixed writes to any
of them across all subs.
**SV divergence:** SV handles this implicitly via constraint-solver
cross-stream hard constraints; we do it explicitly.

### F8 — Compressed 3-bit fallback leaked to full GPR pool · [ccc40bf]
**Symptom:** GCC-15 rejected generated asm:
`c.addi4spn s3, sp, 816` — s3=x19 not in compressed GPR set (x8..x15).
Hit rv32imc/rand_instr and rv64imc/rand_instr reliably at seeds
200/300.
**Root cause:** `randomize_gpr_operands` picks from `_COMPRESSED_REGS ∩
avail_regs`. When `avail_regs` is a tight set (e.g. HazardInstrStream
samples 6 random regs), the intersection is often empty. `_pick`
fallback widened to `_NON_CSR_REGS` — the full 31-reg pool — emitting
physically-illegal encodings.
**Fix:** When `compressed_3bit=True`, fallback widens only to
`_COMPRESSED_REGS`, never to the full pool. Hard constraint from the
encoding.
**SV reference:** rvc_csr_c in riscv_compressed_instr.sv:21.
**Regression:** Canonical sweep 51/51 (was 48/51 pre-fix).

### F9 — Multi-hart double-prefix on mtvec/init labels · [cbe75d7]
**Symptom:** Linker rejects multi_harts build:
`undefined reference to 'h1_h1_mtvec_handler'`. 0/N pass.
**Root cause:** Caller in `asm_program_gen.py` passes
`trap_handler_label=f"{prefix}mtvec_handler"` (already hart-qualified).
`boot.py::_write_xtvec` then prepended `prefix` again, producing
`h1_h1_mtvec_handler`. Same bug for `init_label`.
**Fix:** Trust caller's label — stop re-prefixing inside `_write_xtvec`
and the MEPC write. `stvec_handler` still prefixed (caller doesn't
qualify it).

---

## Unfixed (live bugs)

_None known_ — all diagnosed bugs are in Fixed section. Wide sweep
(2430 runs across 9 targets × 9 tests × 30 seeds) + 1000-seed stress +
targeted rv32imckf sweep all green.

---

## Known architectural gaps (not "bugs" — features not ported yet)

- SV32/SV39/SV48 paging (blocks `riscv_mmu_stress_test` on rv64gc,
  `riscv_page_table_exception_test`).
- PMP cfg packing + NAPOT (blocks `riscv_pmp_test`).
- Debug ROM + DCSR / DPC (blocks `riscv_ebreak_debug_mode_test`).
- H-extension (hypervisor, two-stage translation).
- Zfh / Zvfh (half-precision scalar + vector).
- Smaia / Ssaia (advanced interrupt architecture).

See Tier 1/2 in `docs/research/comparison-and-next-steps.md` for the
ranked port plan.

---

## Cadence / invariants

- Run `python -m pytest tests/` after every change. 402/402 at HEAD.
- Run `canonical sweep` from CLAUDE.md §0 before commits that touch
  trap / boot / streams.
- For every bug, check SV reference at
  `~/Desktop/verif_env_tatsu/riscv-dv/src/` for the original constraint
  — port it faithfully unless SV has the same livelock as us, in which
  case note the divergence here.
