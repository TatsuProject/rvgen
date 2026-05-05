# rvgen Verification Test Plan

Goal: prove every extension the generator advertises produces Spike-clean
`.S` files across multiple seeds, with every privileged feature exercised.
No code work — pure regression methodology.

## 0. Pre-flight (one-time setup)

- Toolchain: `riscv64-unknown-elf-gcc` 14.x available; `spike` baseline;
  `spike-vector` at `$SPIKE_PATH=/home/qamar/tools/spike-vector/bin/spike`
- Sanity: `python -m pytest tests/` → 847/847 pass
- Sanity: `python -m rvgen --help_targets` lists 29 targets
- Logging: every phase emits `pass.txt` / `fail.txt` per test under
  `/tmp/rvgen_verify/<phase>/`

## 1. Pass criteria (every phase)

Each generated `.S` must:

1. Generate without Python exception
2. Compile with `riscv64-unknown-elf-gcc -march=<isa> -mabi=<abi>` clean
3. Run on Spike (or `spike-vector`) and reach `test_done` (gp=1, ecall,
   tohost write)
4. Trace shows the targeted extension's mnemonics actually appeared
5. No unexpected exception (only configured-as-allowed traps)

Per phase: ≥3 seeds × ≥1 test × ≥pass criteria 1–5. Phase passes when
**100%** of test×seed combos pass.

---

## Phase A — Scalar integer base (RV32I + RV64I)

| Target | Tests | Seeds |
|---|---|---|
| `rv32i` | arithmetic_basic, rand_instr | 100,200,300 |
| `rv32im` | arithmetic_basic, rand_instr, no_fence | 100,200,300 |
| `rv32imc` | arithmetic_basic, rand_instr, jump_stress, loop, rand_jump, no_fence, unaligned_ls | 100,200,300,400,500 |
| `rv64imc` | arithmetic_basic, rand_instr, loop, jump_stress | 100,200,300 |
| `rv64imafdc` | arithmetic_basic, rand_instr | 100,200,300 |
| `rv32ui` (bare) | arithmetic_basic, rand_instr | 100,200,300 |

Verify: `mul`, `div`, `rem` for M; aligned/unaligned LD/ST; branch density;
JAL/JALR coverage.

## Phase B — Atomic (A extension)

| Target | Tests | Seeds |
|---|---|---|
| `rv32imac`, `rv32imc`, `rv64imc` | amo_test (LR/SC + AMO\*.W/D + AMO\*.AQ.RL ordering) | 100,200,300,400 |

Verify trace contains: `lr.w`, `sc.w`, `amoadd/and/or/xor/min/max/swap.{w,d}`
across 4 ordering combos.

## Phase C — Floating-point scalar

| Target | Tests | Seeds | Coverage focus |
|---|---|---|---|
| `rv32if`, `rv32ifc` | floating_point_arithmetic, fp_rand | 100,200,300 | F |
| `rv32imafdc` | fp_arithmetic, fp_rand, fp_mmu_stress | 100..500 | F+D |
| `rv64imafdc` | fp_arithmetic, fp_rand | 100..500 | RV64 F+D |
| `rv32imafdc_zfh` | fp_arithmetic, fp_rand | 100,200,300 | Zfh half-precision |
| `rv64imafdc_zfh` | fp_arithmetic, fp_rand | 100,200,300 | Zfh on RV64 |

Verify per target: trace contains FLW/FSW, FLD/FSD, FLH/FSH, FADD/FSUB/FMUL/
FDIV/FSQRT, FMA family (4 variants × precision), FCVT family, FMV.X / FMV
bit-moves, FSGNJ/FSGNJN/FSGNJX, FMIN/FMAX, FEQ/FLT/FLE, FCLASS, all 5
rounding modes (RNE/RTZ/RDN/RUP/RMM).

## Phase D — Bit-manipulation (B = Zba+Zbb+Zbc+Zbs)

| Target | Tests | Seeds |
|---|---|---|
| `rv32imcb` | b_ext_test, zbb_zbt_test | 100,200,300 |
| `rv64imcb` | b_ext_test, zbb_zbt_test | 100,200,300 |

Verify: ANDN/ORN/XNOR, ROL/ROR/RORI, CLZ/CTZ/CPOP, MIN/MAX, SEXT.B/H,
ZEXT.H, REV8, ORC.B (Zbb); SH1ADD/SH2ADD/SH3ADD, ADD.UW (Zba); CLMUL/
CLMULH/CLMULR (Zbc); BCLR/BSET/BINV/BEXT + I-form (Zbs).

## Phase E — Crypto (K family)

| Target | Tests | Seeds |
|---|---|---|
| `rv32imc_zkn` | rand_instr, b_ext_test | 100,200,300 |
| `rv32imc_zkn_zks` | rand_instr | 100,200,300 |
| `rv64imc_zkn` | rand_instr | 100,200,300 |

Verify per target trace contains: AES32ESI/ESMI/DSI/DSMI (RV32 Zkne/Zknd),
AES64ES/ESM/DS/DSM/KS1I/KS2 (RV64), SHA256 (Zknh), SHA512 split L/H pairs
(RV32) or single-instr forms (RV64), SM3 (Zksh: SM3P0/P1), SM4 (Zksed:
SM4ED/KS), Zbkb (PACK/PACKH/REV8), Zbkc (CLMUL/CLMULH), Zbkx (XPERM4/
XPERM8).

## Phase F — Modern checkbox extensions

Single combined target: `rv64gc_modern`.

| Tests | Seeds |
|---|---|
| rand_instr, arithmetic_basic + `+directed_instr_0=riscv_int_numeric_corner_stream,4` | 100..500 |

Verify trace contains: CZERO.EQZ/NEZ (Zicond), CBO.CLEAN/FLUSH/INVAL/ZERO
(Zicbom/Zicboz), PREFETCH.I/R/W (Zicbop), PAUSE (Zihintpause), NTL.P1/PALL/
S1/ALL (Zihintntl), MOP.R.0..31 + MOP.RR.0..7 (Zimop), C.MOP.1/3/5/7/9/11/
13/15 (Zcmop).

## Phase G — Vector (the big one)

Sub-divided because of extension density. Baseline VLEN=512, ELEN=32,
MAX_LMUL=8 unless noted.

### G1 — RVV 1.0 baseline integer
| Target | Tests | Seeds |
|---|---|---|
| `rv64gcv` | vector_arithmetic, rand_instr, stress (`+vector_instr_only=1`) | 100,200,300,400 |

Verify: vsetvli/vsetivli/vsetvl emit; SEW ∈ {8,16,32,64}; LMUL ∈ {1,2,4,8,
1/2,1/4,1/8}; integer vop families: vadd, vsub, vrsub, vand/or/xor, vmin/
max(u), vmul/mulh/mulhu/mulhsu, vdiv/rem(u), vsll/srl/sra, vmseq/sne/ltu/
lt/leu/le/gtu/gt, vredsum/and/or/xor/min/max(u), vmv.v.x/i, vmv1r..vmv8r
whole-register moves, vid.v, viota.m, vfirst.m, vmsbf.m, vmsif.m, vmsof.m,
vcompress.vm, vslideup/down, vrgather, vmerge.

### G2 — Vector load/store
| Target | Tests | Seeds |
|---|---|---|
| `rv64gcv` | rand_instr + `+directed_instr_0=riscv_vector_load_store_instr_stream,8` | 100,200,300 |

Verify per address mode (UNIT_STRIDED, STRIDED, INDEXED, FAULT_ONLY_FIRST):
vle{8,16,32,64}.v / vse\*.v / vlse\*.v / vsse\*.v / vlxei\*.v / vsxei\*.v /
vsuxei\*.v; segmented vlseg/vsseg with NF ∈ {1..8}.

### G3 — Vector AMO
| Target | Tests | Seeds |
|---|---|---|
| `rv64gcv` | rand_instr + `+directed_instr_0=riscv_vector_amo_instr_stream,4` | 100,200,300 |

Verify: vamoaddei\*/vamoxorei\*/vamoorei\*/vamoandei\*/vamominei\*/vamomaxei\*/
vamoswapei\* with wd=0 and wd=1.

### G4 — Vector FP
| Target | Tests | Seeds |
|---|---|---|
| `rv64gcv` | rand_instr + `+vec_fp=1` | 100,200,300 |

Verify: vfadd/sub/mul/div/sqrt/rec7/rsqrt7/min/max/sgnj{,n,x}/cvt.x.f/cvt.f.x/
feq/flt/fle/fmadd/fmsub/fnmadd/fnmsub families.

### G5 — Vector widening / narrowing / quad
| Target | Tests | Seeds |
|---|---|---|
| `rv64gcv` | rand_instr + `+vec_narrowing_widening=1 +vec_quad_widening=1` | 100,200,300 |

Verify: vwadd, vwsub, vwmul, vwmulu, vwmulsu, vnsrl, vnsra, vnclip(u), vfwadd,
vfwsub, vfwmul, vfwmacc, vqmacc family.

### G6 — Vector crypto
| Target | Tests | Seeds |
|---|---|---|
| `rv64gcv_crypto` | rand_instr | 100,200,300 |

Verify: Zvbb (vandn, vbrev{,8}, vrev8, vclz/ctz/cpop, vrol/ror, vwsll), Zvbc
(vclmul/vclmulh), Zvkn (vaesef/vaesem/vaesz/vsha2ms/vsha2c/vsha2ch/vsha2cl),
Zvfh present in this target (vfwcvt.f.f.v half→single etc).

### G7 — Embedded vector profiles
| Target | SEW limit | Tests | Seeds |
|---|---|---|---|
| `rv32imc_zve32x` | int-only ≤32 | rand_instr | 100,200,300 |
| `rv32imfc_zve32f` | int+f32 ≤32 | rand_instr, vfp | 100,200,300 |
| `rv64imc_zve64x` | int ≤64 | rand_instr | 100,200,300 |
| `rv64imafdc_zve64d` | int+f64 | rand_instr, vfp | 100,200,300 |
| `coralnpu` | NPU-shaped | rand_instr | 100,200,300 |

Verify each target rejects out-of-profile SEW/LMUL combos; no vfadd.vf on
Zve32x.

### G8 — Vector hazard / vstart corner / vsetvli stress
| Tests | Seeds |
|---|---|
| Each of: `+directed_instr_0=riscv_vector_hazard_instr_stream,4`, `riscv_vstart_corner_instr_stream`, `riscv_vsetvli_stress_instr_stream` | 100,200,300 |

## Phase H — Privileged / Machine mode

| Target | Tests | Boot mode | Seeds |
|---|---|---|---|
| `rv32imc` | csr_test, rand_instr | M | 100,200,300 |
| `rv64gc` | csr_test | M / S / U via `--priv` | 100,200,300 |
| `rv64gc` | privileged_mode_rand, u_mode_rand | U,SU | 100,200,300 |

Verify: boot-CSR sequence emits MSTATUS / MISA / MTVEC / MIE / MEDELEG /
MIDELEG / SATP per `init_privileged_mode`; mret to target mode;
`--cov_goals goals/rv64gc.yaml` closes ≥95% of CSR bins;
+include_write_reg=A,B widens whitelist correctly.

## Phase I — Exceptions

| Target | Tests | Seeds | Cause expected |
|---|---|---|---|
| `rv32imc` | ebreak_test | 100,200,300 | breakpoint (3) |
| `rv32imc` | illegal_instr_test | 100,200,300 | illegal_instruction (2) |
| `rv64gc` | ecall_test (M/S/U variants) | 100,200,300 | ecall_m/s/u (8/9/11) |
| `rv64gc` | unaligned_load_store_test | 100,200,300 | load/store_addr_misaligned (4/6) |
| `rv64gc` | invalid_csr_test | 100,200,300 | illegal_instruction |
| `rv64gc_sv48` | page_table_exception_test | 100,200,300 | inst/load/store_page_fault (12/13/15) |

Verify new `trap_cause_cg` covergroup hits each cause bin; trap handler
returns cleanly; gp=1 on test_done.

## Phase J — Interrupts

| Target | Tests | Seeds |
|---|---|---|
| `rv32imc` | full_interrupt_test (timer + software) | 100,200,300 |
| `rv64gc` | full_interrupt_test, software_interrupt_test | 100,200,300 |

Verify: CLINT MTIMECMP/MSIP arming code emits; trap dispatched to vectored vs
direct mode (both layouts); `priv_event_cg.mret_taken` ≥ N;
`trap_cause_cg.interrupt_07_m_timer` and `interrupt_03_m_software` hit.

## Phase K — Paging (Sv32 / Sv39 / Sv48 / Svnapot / Svpbmt)

| Target | SATP | Tests | Seeds |
|---|---|---|---|
| `rv32imc_sv32` | SV32 | mmu_stress, sfence_exception | 100,200,300 |
| `rv64gc` | SV39 | mmu_stress, page_table_exception | 100,200,300 |
| `rv64gc_sv48` | SV48 | mmu_stress | 100,200,300,400 |

Verify: page-table topology depth (Sv32=3, Sv39=7, Sv48=15 tables); satp PPN
+MODE programmed; SFENCE.VMA emitted; Svnapot bit-63 NAPOT entries appear
when enabled; Svpbmt PBMT bits 62:61 in PTE when enabled; page-fault routine
functional.

## Phase L — PMP

| Target | Tests | Seeds |
|---|---|---|
| `rv64gc` (with `+enable_pmp_setup=1`) | pmp_test, mmu_stress | 100,200,300 |

Verify: pmpcfg byte packing (RV32 4/CSR, RV64 8/CSR); A field across OFF/TOR/
NA4/NAPOT; locked bit; `pmp_cfg_cg` covergroup populated across ≥6 distinct
(mode × lock × xwr) combos; NAPOT addr encoding `addr>>2 | ((1<<g)-1)`.

## Phase M — Debug ROM

| Target | Tests | Seeds |
|---|---|---|
| `rv64gc` (`+gen_debug_section=1`) | ebreak_debug_mode_test | 100,200,300 |

Verify: DCSR programmed; DPC increment when cause==ebreak; DSCRATCH0
single-step bit; `priv_event_cg.dret_taken` and `dcsr_write` hit.

## Phase N — Hypervisor (H-ext)

| Target | Tests | Seeds |
|---|---|---|
| `rv64gch` | rand_instr + `+directed_instr_0=riscv_hypervisor_instr,4` | 100,200,300 |

Verify trace contains: HFENCE.VVMA / HFENCE.GVMA, HLV.B/BU/H/HU/W/WU/D,
HLVX.HU/WU, HSV.B/H/W/D. (Two-stage translation deferred — only instruction
surface tested.)

## Phase O — AIA (Smaia / Ssaia)

| Target | Tests | Seeds |
|---|---|---|
| `rv64gc_aia` | csr_test (with extended whitelist) | 100,200,300 |
| `rv64gch_aia` | csr_test, rand_instr + hyp directed stream | 100,200,300 |

Verify CSR-write/read trace contains: MISELECT/MIREG/MTOPEI/MTOPI/MVIEN/MVIP
(Smaia), SISELECT/SIREG/STOPEI/STOPI (Ssaia), HVIEN/HVICTL/HVIPRIO1/HVIPRIO2/
VSISELECT/VSIREG/VSTOPEI/VSTOPI (H-AIA).

## Phase P — Multi-hart

| Target | Tests | Seeds |
|---|---|---|
| `multi_harts` (NUM_HARTS=2) | rand_instr, lr_sc shared region | 100,200,300 |

Verify: `_start` MHARTID dispatch; per-hart sections `h0_*` / `h1_*`;
`multi_hart_race_cg.two_harts` hit; LR/SC pairings with shared region.

## Phase Q — Coverage closure

After every functional phase passes, run aggregate:

```
python scripts/regression.py --targets <every-target> --tests <list> \
  --seeds 100,200,300,400,500 --jobs 8 --cov_steering --emit_html
```

Acceptance:
- Per-target: ≥95% of required goals (count>0) bins closed
- New sprint-1 covergroups (`fp_fflags_cg`, `trap_cause_cg`, `op_comb_cg`,
  `ea_align_cg`, `csr_read_cg`, `fp_dataset_cg`) each hit ≥3 distinct bins
  on relevant targets
- Composite grade ≥ 85/100
- Coverage HTML dashboard reviewed for outlier zero-bins → either justified
  (e.g. RV32-only bin on RV64 target) or fed to `--auto_regress
  --cov_directed` for one more pass

## Phase R — Triage flow (when something fails)

1. Reproduce: `python -m rvgen --target X --test Y --start_seed Z -i 1`
2. If Spike trap: inspect `/tmp/.../spike_sim/*.log`, find first unexpected
   mcause
3. If illegal-instr: check `unsupported_instr` set on the target — likely a
   filter bug
4. Minimize: `python -m rvgen.minimize --asm <failing.S> --predicate
   iss_fail` → reduces to <20 lines
5. Bin attribution: `python -m rvgen.coverage.tools attribute --goals
   goals/<t>.yaml run*/coverage.json`
6. If golden mismatch: `tests/golden/` diff vs `2026-04-21/` riscv-dv
   reference
7. File the fix as a focused commit; re-run the affected phase

## Aggregate sign-off

The generator passes verification when:

- **All 18 phases (A through R)** report 100% test×seed pass on Spike
- **All 29 targets** appear in at least one phase
- **Every extension** advertised in CLAUDE.md §0 has a dedicated phase
  confirming its mnemonics actually retire
- **Coverage composite ≥85/100** with steering on
- **Zero unexpected illegal-instr or unhandled-trap** on any seed

Estimated CPU: ~6-8 hours on 8-core for the full sweep at 5 seeds × all
targets × all phases.
