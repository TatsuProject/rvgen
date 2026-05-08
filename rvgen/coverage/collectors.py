"""Covergroup sampling — static (generator-side) only for Phase 1.

A :class:`CoverageDB` is a dict-of-dict keyed by covergroup name, then bin
name, with observed integer hit counts. This shape:

- serialises trivially to JSON / YAML,
- merges across runs by simple bin-wise addition,
- compares cleanly against a :class:`~rvgen.coverage.cgf.Goals`
  (also a dict-of-dict, but with required hit counts instead of observed).

The covergroups we collect:

======================  =========================================
Covergroup              Bins
======================  =========================================
``opcode_cg``           One bin per :class:`RiscvInstrName` member
``format_cg``           One bin per :class:`RiscvInstrFormat` member
``category_cg``         One bin per :class:`RiscvInstrCategory` member
``group_cg``            One bin per :class:`RiscvInstrGroup` member
``rs1_cg``              One bin per :class:`RiscvReg` member (+ none)
``rs2_cg``              One bin per :class:`RiscvReg` member (+ none)
``rd_cg``               One bin per :class:`RiscvReg` member (+ none)
``imm_sign_cg``         pos / zero / neg (only when has_imm)
``hazard_cg``           raw / war / waw / none (adjacent-instr pairs)
``csr_cg``              One bin per :class:`PrivilegedReg` name seen
``fp_rm_cg``            RNE / RTZ / RDN / RUP / RMM (FP ops only)
``vtype_cg``            ``SEW<w>_LMUL<n>`` (vector ops only)
``vreg_cg``             One bin per :class:`RiscvVreg` member (vector)
``fpr_cg``              One bin per :class:`RiscvFpr` member (FP)
``fmt_category_cross``  ``<format>__<category>`` cross
``category_group_cross`` ``<category>__<group>`` cross
======================  =========================================

Hazards are detected with a per-register "last writer" dictionary, reset
between sequences. Only direct register hazards are flagged (RAW/WAW/WAR
on rs1/rs2/rd), not memory hazards — those need a runtime trace.
"""

from __future__ import annotations

import copy
import re
from collections import deque
from typing import Iterable

from rvgen.isa.base import Instr
from rvgen.isa.csr_ops import CsrInstr
from rvgen.isa.enums import (
    FRoundingMode,
    PrivilegedReg,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)


# A CoverageDB is just ``{covergroup_name: {bin_name: int}}``. We use a
# dedicated type so annotation tooling can distinguish it from a raw dict
# without introducing a class overhead.
CoverageDB = dict[str, dict[str, int]]


# Canonical covergroup names (stable string keys for JSON / YAML output).
CG_OPCODE = "opcode_cg"
CG_FORMAT = "format_cg"
CG_CATEGORY = "category_cg"
CG_GROUP = "group_cg"
CG_RS1 = "rs1_cg"
CG_RS2 = "rs2_cg"
CG_RD = "rd_cg"
CG_IMM_SIGN = "imm_sign_cg"
CG_HAZARD = "hazard_cg"
CG_CSR = "csr_cg"
CG_FP_RM = "fp_rm_cg"
CG_VTYPE = "vtype_cg"
CG_VREG = "vreg_cg"
CG_FPR = "fpr_cg"
CG_FMT_X_CAT = "fmt_category_cross"
CG_CAT_X_GRP = "category_group_cross"
CG_MEM_ALIGN = "mem_align_cg"          # per load/store: byte_aligned/half/word/dword/unaligned
CG_LS_WIDTH = "load_store_width_cg"    # byte/half/word/dword (sign vs zero ext)
CG_CAT_TRANS = "category_transition_cg"  # prev_category -> current_category
CG_OP_TRANS = "opcode_transition_cg"   # adjacent-instr opcode transitions (top-N coverage)
CG_BRANCH_DIR = "branch_direction_cg"  # runtime: taken / not-taken (populated from ISS log)
CG_EXCEPTION = "exception_cg"          # runtime: mcause exception values
CG_PRIV_MODE = "privilege_mode_cg"     # runtime: M/S/U mode observed
CG_REG_VAL_SIGN = "rs_val_sign_cg"     # rs-value sign class (pos/neg/zero) on the fly
CG_IMM_EXT = "imm_range_cg"            # walking-ones / walking-zeros / corner classes
CG_PC_REACH = "pc_reach_cg"            # runtime: unique labels entered
CG_RS1_EQ_RS2 = "rs1_eq_rs2_cg"        # R-format: rs1==rs2 (same-reg path)
CG_RS1_EQ_RD = "rs1_eq_rd_cg"          # rd==rs1 (in-place op)
CG_BR_PER_MNEM = "branch_taken_per_mnem_cg"  # cross: branch mnemonic × taken/not_taken (runtime)
CG_VTYPE_DYN = "vtype_dyn_cg"          # (SEW, LMUL) pair observed when sampling a vector op
CG_CSR_ACCESS = "csr_access_cg"        # cross: CSR name × read/write access type
CG_LS_OFFSET = "load_store_offset_cg"  # offset magnitude bins for load/store ops
CG_STREAM = "directed_stream_cg"       # which directed stream contributed instrs
CG_CSR_VAL = "csr_value_cg"            # runtime: CSR × value-bucket (parsed from spike trace)
CG_RS_VAL_CORNER = "rs_val_corner_cg"  # runtime: GPR write-value corner class
CG_BIT_ACTIVITY = "bit_activity_cg"    # runtime: per-bit GPR-write activity (bit_N_toggled)
CG_RS1_RS2_CROSS = "rs1_rs2_cross_cg"  # explicit rs1 × rs2 cross (for C-extension port-pair coverage)
CG_RD_RS1_CROSS = "rd_rs1_cross_cg"    # rd × rs1 cross (in-place op pattern)
CG_VEC_LS_MODE = "vec_ls_addr_mode_cg"     # UNIT_STRIDED / STRIDED / INDEXED for vector LS
CG_VEC_EEW = "vec_eew_cg"                  # EEW chosen by vector loads/stores (8/16/32/64)
CG_VEC_EEW_VS_SEW = "vec_eew_vs_sew_cg"    # cross: EEW vs current SEW (eq/wider/narrower)
CG_VEC_EMUL = "vec_emul_cg"                # vd alignment / EMUL value used
CG_VEC_VM = "vec_vm_cg"                    # masked vs unmasked vector op
CG_VEC_VM_X_CAT = "vec_vm_category_cross_cg"  # cross: vm × category
CG_VEC_AMO_WD = "vec_amo_wd_cg"            # AMO wd flag (write-dst)
CG_VEC_VARIANT = "vec_va_variant_cg"       # VV/VX/VI/VF/WV/WX/WI/VVM/VXM/VFM
CG_VEC_NF = "vec_nfields_cg"               # Zvlsseg NFIELDS bins (1..8)
CG_VEC_SEG_X_MODE = "vec_seg_addr_mode_cross_cg"  # cross: NF × addr mode
CG_VEC_WIDE_NARROW = "vec_widening_narrowing_cg"  # widening / narrowing / quad-widening / convert
CG_VEC_CRYPTO = "vec_crypto_subext_cg"     # zvbb / zvbc / zvkn family
# vtype transitions across vsetvli emissions — sampled at sequence level
# when a vsetvli appears mid-stream (riscv_vsetvli_stress_instr_stream).
CG_VEC_SEW_TRANS = "vec_sew_transition_cg"     # prev_SEW -> new_SEW
CG_VEC_LMUL_TRANS = "vec_lmul_transition_cg"   # prev_LMUL -> new_LMUL
CG_VEC_VTYPE_TRANS = "vec_vtype_transition_cg"  # full vtype tuple transition
# vstart corner cases — sampled when riscv_vstart_corner_instr_stream emits
# `csrwi vstart, N` before a vector op.
CG_VEC_VSTART = "vec_vstart_cg"            # zero / one / small / mid / max

CG_CACHE_LINE_CROSS = "cache_line_cross_cg"   # load/store crossing 64B line
CG_PAGE_CROSS = "page_cross_cg"               # load/store crossing 4KiB page
CG_BRANCH_DIST = "branch_distance_cg"         # branch byte-offset bucket (signed)
CG_BRANCH_PATTERN = "branch_pattern_cg"       # T/N 3-gram (e.g. T_T_N)

# Value-class coverage (riscv-isac val_comb-style); rs1/rs2 sampled at
# runtime via a virtual reg-file built from spike GPR-write events.
CG_RS1_VAL_CLASS = "rs1_val_class_cg"
CG_RS2_VAL_CLASS = "rs2_val_class_cg"
CG_RD_VAL_CLASS = "rd_val_class_cg"
CG_RS_VAL_CROSS = "rs_val_class_cross_cg"

# Modern checkbox extensions (Zicond / Zicbo* / Zihint* / Zimop / Zcmop).
# opcode_cg carries the per-instruction count, but extensions are
# semantically meaningful in clusters: cbo *operation* type, prefetch
# *direction*, mop *variant* (R/RR/C). This covergroup makes the
# sub-extension story visible at a glance.
CG_MODERN_EXT = "modern_ext_cg"
# Fence pred/succ encoding patterns. Each FENCE carries a 4+4 bit
# pred/succ pair (R/W/I/O). Bins are "<pred>__<succ>" canonicalised.
# Captures the memory-ordering corner-cases hazard_cg can't see.
CG_FENCE = "fence_cg"
# LR/SC sequencing pattern — port of riscv-isac's lr_sc_pattern_cov.
# Bins: lr_only, sc_only, paired, lr_with_intervening_op,
# nested_lr (back-to-back LR), unpaired_sc.
CG_LR_SC_PATTERN = "lr_sc_pattern_cg"
# Privileged events seen at runtime (parsed from spike trace). Bins:
# satp_write, sfence_vma, mret_taken, sret_taken, ecall_taken,
# debug_entered, dret_taken. Complements priv_mode_cg by counting
# *transitions* rather than *modes*.
CG_PRIV_EVENT = "priv_event_cg"
# PMP cfg-byte composition. Sampled when boot.py emits a PMP setup
# block. Bins capture (A field × L bit × XWR combo) so we can confirm
# every meaningful cfg byte shape was exercised across a regression.
# Bin name format: "<addr_mode>_<l>_<xwr>" — e.g. "NAPOT_unlocked_RWX",
# "TOR_locked_R--", "OFF_unlocked_---" (bare/disabled region).
CG_PMP_CFG = "pmp_cfg_cg"
# Multi-hart race coverage. Sampled at sequence-level when the
# generator emits a ``LoadStoreSharedMemStream`` (or similar) on
# multiple harts; bins capture how many distinct harts referenced
# the shared region. Cross-trace correlation (same address hit by
# different harts within N cycles) is left to a coverage-v2 pass —
# this structural version still flags "test never raced" at a
# glance.
# Bins: only_one_hart, two_harts, three_to_seven_harts, all_harts.
CG_MULTI_HART_RACE = "multi_hart_race_cg"

# ---------------------------------------------------------------------------
# Sprint-1 additions — coverage-gap closure vs riscv-isac / riscvISACOV.
# ---------------------------------------------------------------------------
#
# CG_FP_FFLAGS — accrued FP exception flags (NV/DZ/OF/UF/NX) decoded from
# fcsr / fflags writes. Bin-per-flag plus aggregate "no_flags" /
# "multiple_flags". Critical for FP-corner verification (riscv-isac and
# Imperas' coverage tracks each fflag distinctly; we previously only
# tracked the FCSR write event itself via csr_value_cg).
CG_FP_FFLAGS = "fp_fflags_cg"
# Per-cause trap decode. Sampled when xCAUSE writes show up in the
# spike commit log. Bin name format:
#   "exception_<n>_<NAME>"   for sync causes (mcause MSB=0)
#   "interrupt_<n>_<NAME>"   for async causes (mcause MSB=1)
# Replaces the coarse "trap_entered" label-match in CG_EXCEPTION.
CG_TRAP_CAUSE = "trap_cause_cg"
# Operand-register relationships beyond the trivial rs1==rs2 / rd==rs1
# crosses. Bins capture special-register patterns (sp/ra/gp/zero usage
# as src or dst) and triple-equality (rd==rs1==rs2). Static, sampled
# from each emitted instruction.
CG_OP_COMB = "op_comb_cg"
# Effective-address alignment from the runtime trace (versus the static
# alignment we already track in CG_MEM_ALIGN, which uses just the
# immediate offset). Bins: align_1 / align_2 / align_4 / align_8 /
# align_16 / align_32 / align_64 — the largest power-of-two aligning
# the address.
CG_EA_ALIGN = "ea_align_cg"
# CSR-read coverage. Tracks `csrr` (csrrs rd, csr, x0) and any csrrs/csrrc
# with a writable destination. Currently csr_value_cg only sees writes;
# many CSRs are read-only or read-mostly and never trigger our existing
# CSR coverage.
CG_CSR_READ = "csr_read_cg"
# FP corner-value dataset. riscv-isac calls these sp_dataset / dp_dataset.
# Bins: pos_zero, neg_zero, pos_inf, neg_inf, qnan, snan,
#   pos_subnormal, neg_subnormal, pos_normal_min, neg_normal_min,
#   pos_normal_max, neg_normal_max, pos_one, neg_one, generic.
# Sampled from FP register writes parsed out of the spike commit log.
CG_FP_DATASET = "fp_dataset_cg"

# ---------------------------------------------------------------------------
# Sprint-2 (deep-coverage) additions — gap closure vs riscv-isac/-ctg,
# OpenHW core-v-verif coverage plans, and ARM/Imperas micro-arch
# coverage methodology.
# ---------------------------------------------------------------------------
#
# Pipeline depth — riscv-dv's hazard_cg only flags raw/war/waw/none, not
# the *distance* between producer and consumer. Modern in-order +
# OoO pipelines stall differently at distance 1 (load-use), 2-3
# (forwarding paths), 4+ (no stall). Bin per cycle distance lets a
# verification team confirm the pipeline saw every forwarding-path
# corner.
CG_HAZARD_DIST = "hazard_distance_cg"
# Load → consumer distance — sampled per LOAD producer. Bins:
# load_use_d1, load_use_d2, load_use_d3, load_use_d4_plus, load_no_use.
# Critical for verifying load-use stall + forwarding networks.
CG_LOAD_USE = "load_use_dist_cg"
# Multi-cycle producer (MUL/DIV/REM, FDIV/FSQRT, AMO) → consumer.
# Bins: mc_use_d1..d3, mc_use_d4_plus, mc_no_use, plus per-class crosses.
CG_MC_USE = "mc_producer_use_dist_cg"
# Branch-shadow — what category sits in the slot immediately after a
# taken/not-taken branch. Useful for verifying branch-misprediction
# recovery + delay-slot behavior. Bins: shadow_<category>.
CG_BRANCH_SHADOW = "branch_shadow_cg"
# Static memory address aliasing — when a base reg + offset pair appears
# multiple times in a sliding window, sample whether the second reference
# could alias (same base reg, same/different offset). Captures
# store-to-load forwarding opportunity coverage statically; complements
# the runtime EA tracker.
CG_MEM_ALIAS = "mem_alias_cg"
# Branch direction history 4-gram — extends branch_pattern_cg's 3-gram
# to capture predictor patterns missing one bit (TTTN, NNTT, ...).
CG_BRANCH_PATTERN4 = "branch_pattern4_cg"
# Branch loop / skip classification — for each branch, classify by
# direction (fwd/bwd) × outcome (taken/not_taken). bwd_taken is "loop
# closing"; fwd_taken is "if/skip"; bwd_not_taken is "loop falling
# through" (rare, useful corner); fwd_not_taken is "if not taken".
CG_BRANCH_LOOP = "branch_loop_cg"
# Return-address-stack (RAS) classification — JAL/JALR semantic call
# vs return vs computed-jump. Bins: call (jal/jalr with rd=ra/x5),
# return (jalr with rs1=ra/x5, rd=zero), tail_call (jalr with rd=zero,
# rs1!=ra), computed (jalr with rd=ra, rs1!=zero), other.
# Required for verifying RAS prediction (mismatched depth → bug).
CG_RAS = "ras_cg"
# JALR target register class — sp/ra/gp/tp/saved/temporary/argument.
# Captures the ABI-vs-microarch design choice "what indirect target
# does this code use".
CG_JALR_TARGET = "jalr_target_class_cg"
# AMO acquire/release combinations. Spec allows {none, aq, rl, aqrl};
# rvgen's randomizer currently picks one of {none, aq, rl} (mutually
# exclusive). Bin {aq_and_rl, aq_only, rl_only, neither} so a missing
# `aqrl` bin is visible — exposes a randomizer gap and lets future
# work fix it.
CG_AMO_AQRL = "amo_aqrl_cg"
# AMO operation × width — AMOADD.W vs AMOADD.D, etc. (the W/D split is
# also visible from the trailing letter of the mnemonic).
CG_AMO_OP_WIDTH = "amo_op_width_cg"
# AMO operation × aq/rl pair — captures whether each op family was
# exercised under each ordering.
CG_AMO_OP_X_AQRL = "amo_op_aqrl_cross_cg"
# FP semantic operation — collapses the 100+ FP mnemonics into a
# small semantic op class (add/sub/mul/div/sqrt/fma/cmp/cvt/sgn/
# minmax/mv/class/load/store). Lets a verif team confirm "every FP
# op family was exercised at every precision under every rounding
# mode" without enumerating mnemonic-by-mnemonic.
CG_FP_OP = "fp_op_class_cg"
# FP rounding mode × FP semantic op — cross. Bins like
# RNE__add, RTZ__div, RUP__sqrt, RDN__fma. Required by ImperasDV /
# riscvISACOV golden coverage.
CG_FP_RM_OP_CROSS = "fp_rm_op_cross_cg"
# FP precision × FP semantic op — half (Zfh) vs single vs double.
# Bins: H__add, S__add, D__add, etc.
CG_FP_PREC_OP = "fp_precision_op_cross_cg"
# Vector AVL corners — sampled when a vsetvli is observed with an
# inferable AVL. Bins: avl_zero, avl_one, avl_max_minus_one, avl_max,
# avl_eq_vlmax (special-case when AVL == VLMAX), avl_other.
CG_VEC_AVL = "vec_avl_corner_cg"
# Vector tail / mask policy — TA × MA cross (4 combinations). Tail-
# undisturbed (TU) + mask-undisturbed (MU) leave masked elements
# untouched; tail/mask-agnostic (TA/MA) may set them to 1s. Critical
# for memory-coherency under masked vector ops.
CG_VEC_TA_MA = "vec_tail_mask_policy_cg"
# Vector vsetvl flavor — vsetvl (rs2 supplies vtype) vs vsetvli (imm
# supplies vtype) vs vsetivli (5-bit imm AVL).
CG_VEC_VSETVL_FLAVOR = "vec_vsetvl_flavor_cg"
# MSTATUS field decode — runtime, sampled when MSTATUS is written.
# Bins: mie_set, mie_clear, mpie_set, mpp_M, mpp_S, mpp_U, mprv_set,
# mxr_set, sum_set, fs_initial, fs_clean, fs_dirty, vs_initial,
# vs_clean, vs_dirty. Lets us track field-level coverage of one of the
# most security-relevant CSRs.
CG_MSTATUS_FIELD = "mstatus_field_cg"
# xTVEC mode — DIRECT vs VECTORED, both for mtvec and stvec. Bins:
# mtvec_direct, mtvec_vectored, stvec_direct, stvec_vectored.
CG_XTVEC_MODE = "xtvec_mode_cg"
# Trap-delegation — runtime, sampled on medeleg/mideleg/hedeleg writes.
# Bins per delegated cause/interrupt: medeleg_<cause>, mideleg_<irq>.
CG_DELEGATION = "delegation_cg"
# HPM counter access — runtime, sampled on csrr/csrrs/csrrw of any
# mhpmcounterN / mhpmevent N CSR. Bins: counter_<N> + counter_any.
# Required by Smcntrpmf / counter-perf-mon coverage in OpenHW plans.
CG_HPM_ACCESS = "hpm_access_cg"
# MISA letter bits — runtime, decoded from MISA writes. Bins: misa_A,
# misa_C, misa_D, misa_F, misa_H, misa_I, misa_M, misa_S, misa_U,
# misa_V. Covers "did the test exercise misa-changeable extensions".
CG_MISA = "misa_cg"
# Multiplier / divider corner-values — static, derived from rs1/rs2 when
# the runtime tracker has values. Bins: div_by_zero (rs2==0 for DIV*/REM*),
# signed_overflow (rs1==INT_MIN, rs2==-1 for DIV/REM signed), no_corner.
CG_MULDIV_CORNER = "mul_div_corner_cg"
# Bitmanip semantic op — rotate / shuffle / popcount / clz / ctz / bclr
# / bset / clmul / minmax / pack / orc / rev / sext / zext.
CG_BMANIP_OP = "bitmanip_op_cg"
# Compressed-imm corner — for RVC ops with NZIMM/NZUIMM constraints,
# sample whether the imm hit the enforced-nonzero edge (small_imm,
# max_imm, mid_imm) and whether C.LUI/C.ADDI16SP fields were near
# their respective boundaries.
CG_C_IMM_CORNER = "c_imm_corner_cg"
# Nested trap — runtime, counts traps that occur with priv level
# already in trap context. Bins: nested_M_in_M, nested_S_in_S,
# nested_M_in_S, no_nesting. Detected via xCAUSE writes inside a
# trap-handler label window.
CG_NESTED_TRAP = "nested_trap_cg"
# Debug DCSR.cause — runtime, decoded from dcsr writes (when present
# in the trace). Bins per spec §A.4: cause_ebreak, cause_trigger,
# cause_haltreq, cause_step, cause_resethaltreq, cause_group.
CG_DCSR_CAUSE = "dcsr_cause_cg"
# FP source-operand class — runtime, classifies the source FP value
# class (read from the virtual FP-state we track from FP writes).
# Bins: src_nan, src_inf, src_subnormal, src_zero, src_normal.
# Sampled per-FP-op when at least one source's prior write is known.
CG_FP_SRC_CLASS = "fp_src_class_cg"
# Interrupts pending — MIP write decode. Bins: mip_<bit>_set per bit
# 3/7/11 plus aggregate any_pending.
CG_MIP_FIELD = "mip_field_cg"
# Walking-ones / walking-zeros bit coverage — riscv-isac CGF
# `walking_ones(XLEN)` and `walking_zeros(XLEN)`. For each register
# operand value observed at runtime, sample bins
# `bit_<i>_set` and `bit_<i>_clear` for i in [0, 64). Catches
# bit-slice-MUX and clamp-on-shift bugs invisible to corner-bucket
# coverage.
CG_WALKING_ONES = "walking_ones_cg"
CG_WALKING_ZEROS = "walking_zeros_cg"
# Alternating bit-pattern coverage — riscv-isac CGF `alternate(XLEN)`.
# Bins: alt_5555 (0x5555…) / alt_AAAA / alt_byte_5A / alt_byte_A5.
# Reveals checkerboard / nibble-swap data-path bugs.
CG_ALTERNATE = "alternating_pattern_cg"
# Leading / trailing ones/zeros count — for verifying clz/ctz/cpop
# value-class coverage. Bins: lead0_<n> / lead1_<n> / trail0_<n>
# / trail1_<n> for n in {0..XLEN}, capped to a coarse log2 bucket.
CG_LEAD_TRAIL = "leading_trailing_cg"
# MXR × SUM × MPRV cross — sampled at every load/store. Captures
# the "supervisor accesses user-page" corner that core-v-verif
# flags as one of the most-broken-ships configs.
CG_MXR_SUM_MPRV = "mxr_sum_mprv_cross_cg"
# FCVT-overflow / saturation corners — runtime, sampled per FCVT
# instruction with operand class. Bins: fcvt_w_inf, fcvt_w_nan,
# fcvt_w_overflow_pos, fcvt_w_overflow_neg, fcvt_l_inf, fcvt_l_nan,
# fcvt_lu_neg_zero (RISC-V spec: NaN→max+1, signed-overflow→max,
# unsigned-negative→0). Critical because every shipped RISC-V
# core has had at least one FCVT saturation bug.
CG_FCVT_CORNER = "fcvt_corner_cg"
# RVC illegal-immediate corners — directed-stream-injected; bin per
# spec-defined illegal RVC encoding (c.addi nzimm=0 reserved,
# c.addi16sp imm=0 reserved, c.lui nzimm=0 reserved, etc.).
CG_RVC_ILLEGAL = "rvc_illegal_corner_cg"
# Shamt boundary corners — slli/srli/srai/sllw/sraw/srlw + Zb*-rotates
# with shamt at {0, 1, XLEN-1, XLEN}. Catches off-by-one in shifters.
CG_SHAMT_CORNER = "shamt_corner_cg"
# H-extension virtual-instr trap — runtime, sampled when scause
# decodes to cause=22 (virtual-instruction). Bins: vi_wfi, vi_sfence,
# vi_csr, vi_other. Critical for HS/VS-mode boot verification.
CG_VIRT_INSTR_TRAP = "virtual_instr_trap_cg"
# Vector vsetvl AVL paths — riscv-isac calls these out as the
# canonical 4 paths through vsetvl{i}: rd!=x0 rs1!=x0 (normal),
# rd!=x0 rs1==x0 (set-VLMAX), rd==x0 rs1==x0 (keep-vl preserve),
# vsetivli (5-bit imm AVL).
CG_VSETVL_AVL = "vsetvl_avl_path_cg"
# Vector register-group overlap — for widening/narrowing/segment
# ops, sample whether the dest fully overlaps src (legal),
# partially overlaps (illegal except specific cases), or doesn't
# overlap. Bins: full_overlap, partial_overlap, no_overlap.
CG_VREG_OVERLAP = "vreg_overlap_cg"
# Atomic-aligned / misaligned — runtime, sampled when LR/SC/AMO is
# observed. Bins: aligned_w (.w address %4 == 0), misaligned_w
# (must trap), aligned_d, misaligned_d.
CG_ATOMIC_ALIGN = "atomic_alignment_cg"
# WFI corner — runtime, sampled whenever WFI is retired. Bins:
# wfi_M_irq_pending, wfi_M_no_irq, wfi_S_tw_set, wfi_S_tw_clear,
# wfi_U_tw_set. Captures the `mstatus.TW` trap-on-WFI path.
CG_WFI_CORNER = "wfi_corner_cg"


ALL_COVERGROUPS: tuple[str, ...] = (
    CG_OPCODE, CG_FORMAT, CG_CATEGORY, CG_GROUP,
    CG_RS1, CG_RS2, CG_RD,
    CG_IMM_SIGN, CG_HAZARD, CG_CSR,
    CG_FP_RM, CG_VTYPE, CG_VREG, CG_FPR,
    CG_FMT_X_CAT, CG_CAT_X_GRP,
    CG_MEM_ALIGN, CG_LS_WIDTH,
    CG_CAT_TRANS, CG_OP_TRANS,
    CG_BRANCH_DIR, CG_EXCEPTION, CG_PRIV_MODE,
    CG_IMM_EXT,
    CG_PC_REACH,
    CG_RS1_EQ_RS2, CG_RS1_EQ_RD,
    CG_BR_PER_MNEM, CG_VTYPE_DYN,
    CG_CSR_ACCESS, CG_LS_OFFSET, CG_STREAM, CG_CSR_VAL,
    CG_RS_VAL_CORNER, CG_BIT_ACTIVITY,
    CG_RS1_RS2_CROSS, CG_RD_RS1_CROSS,
    CG_VEC_LS_MODE, CG_VEC_EEW, CG_VEC_EEW_VS_SEW, CG_VEC_EMUL,
    CG_VEC_VM, CG_VEC_VM_X_CAT, CG_VEC_AMO_WD,
    CG_VEC_VARIANT, CG_VEC_NF, CG_VEC_SEG_X_MODE,
    CG_VEC_WIDE_NARROW, CG_VEC_CRYPTO,
    CG_VEC_SEW_TRANS, CG_VEC_LMUL_TRANS, CG_VEC_VTYPE_TRANS,
    CG_VEC_VSTART,
    CG_CACHE_LINE_CROSS, CG_PAGE_CROSS,
    CG_BRANCH_DIST, CG_BRANCH_PATTERN,
    CG_RS1_VAL_CLASS, CG_RS2_VAL_CLASS, CG_RD_VAL_CLASS, CG_RS_VAL_CROSS,
    CG_MODERN_EXT, CG_FENCE, CG_LR_SC_PATTERN, CG_PRIV_EVENT,
    CG_PMP_CFG, CG_MULTI_HART_RACE,
    CG_FP_FFLAGS, CG_TRAP_CAUSE, CG_OP_COMB, CG_EA_ALIGN,
    CG_CSR_READ, CG_FP_DATASET,
    # Sprint-2 — deep-coverage additions.
    CG_HAZARD_DIST, CG_LOAD_USE, CG_MC_USE, CG_BRANCH_SHADOW,
    CG_MEM_ALIAS, CG_BRANCH_PATTERN4, CG_BRANCH_LOOP,
    CG_RAS, CG_JALR_TARGET,
    CG_AMO_AQRL, CG_AMO_OP_WIDTH, CG_AMO_OP_X_AQRL,
    CG_FP_OP, CG_FP_RM_OP_CROSS, CG_FP_PREC_OP,
    CG_VEC_AVL, CG_VEC_TA_MA, CG_VEC_VSETVL_FLAVOR,
    CG_MSTATUS_FIELD, CG_XTVEC_MODE, CG_DELEGATION,
    CG_HPM_ACCESS, CG_MISA,
    CG_MULDIV_CORNER, CG_BMANIP_OP, CG_C_IMM_CORNER,
    CG_NESTED_TRAP, CG_DCSR_CAUSE,
    CG_FP_SRC_CLASS, CG_MIP_FIELD,
    # Sprint-2 — second wave (research-driven gaps).
    CG_WALKING_ONES, CG_WALKING_ZEROS, CG_ALTERNATE, CG_LEAD_TRAIL,
    CG_MXR_SUM_MPRV, CG_FCVT_CORNER, CG_RVC_ILLEGAL,
    CG_SHAMT_CORNER, CG_VIRT_INSTR_TRAP,
    CG_VSETVL_AVL, CG_VREG_OVERLAP, CG_ATOMIC_ALIGN,
    CG_WFI_CORNER,
)


def sample_multi_hart_race(db: CoverageDB, harts_with_shared_access: set[int]) -> None:
    """Sample CG_MULTI_HART_RACE based on how many harts hit the shared region.

    Caller passes the set of hart indices that emitted at least one
    instruction in the shared-memory stream. Bin: ``only_one_hart``
    when only one hart accessed; ``two_harts`` when two; etc.
    """
    n = len(harts_with_shared_access)
    if n == 0:
        return
    if n == 1:
        bin_name = "only_one_hart"
    elif n == 2:
        bin_name = "two_harts"
    elif n <= 7:
        bin_name = "three_to_seven_harts"
    else:
        bin_name = "all_harts"
    _bump(db, CG_MULTI_HART_RACE, bin_name)


# ---------------------------------------------------------------------------
# PMP cfg-byte sampler. Called once per boot-time PMP region — the
# generator hooks this from boot.gen_pre_enter_privileged_mode when
# enable_pmp_setup is on.
# ---------------------------------------------------------------------------


def sample_pmp_region(db: CoverageDB, region) -> None:
    """Sample one PMP region into ``CG_PMP_CFG``.

    ``region`` is an :class:`rvgen.privileged.pmp.PmpRegion` instance.
    Bin label format::

        "<addr_mode>_<lock>_<XWR>"

    where ``addr_mode`` ∈ {OFF, TOR, NA4, NAPOT}, ``lock`` ∈
    {locked, unlocked}, and ``XWR`` is a 3-character string
    formed from the X/W/R bits with '-' for cleared.
    """
    a_name = region.a.name
    lock = "locked" if region.l else "unlocked"
    xwr = ""
    xwr += "X" if region.x else "-"
    xwr += "W" if region.w else "-"
    xwr += "R" if region.r else "-"
    if xwr == "---":
        # Compress any-disabled-permission into a single bin.
        xwr = "none"
    _bump(db, CG_PMP_CFG, f"{a_name}_{lock}_{xwr}")


# ---------------------------------------------------------------------------
# Modern checkbox-extension classifier (Zicond / Zicbo* / Zihint* / Zimop /
# Zcmop). One bin per semantic operation cluster — finer-grained than
# group_cg (which groups by RV32ZICOND / RV64ZICOND etc.) but more
# digestible than opcode_cg (which has 581 individual bins).
# ---------------------------------------------------------------------------


def _modern_ext_bin(name: RiscvInstrName) -> str | None:
    """Return a semantic bin name for one of the modern checkbox extensions.

    Returns None when the instruction isn't part of any of these
    extensions — sample_instr then skips bumping CG_MODERN_EXT.
    """
    n = name.name
    if n in ("CZERO_EQZ", "CZERO_NEZ"):
        return f"zicond_{n.lower()}"
    if n in ("CBO_CLEAN", "CBO_FLUSH", "CBO_INVAL"):
        return f"zicbom_{n[4:].lower()}"
    if n == "CBO_ZERO":
        return "zicboz_zero"
    if n in ("PREFETCH_I", "PREFETCH_R", "PREFETCH_W"):
        return f"zicbop_{n[-1].lower()}"
    if n == "PAUSE":
        return "zihintpause_pause"
    if n.startswith("NTL_"):
        return f"zihintntl_{n[4:].lower()}"
    if n.startswith("MOP_R_"):
        # Cluster all 32 mop.r.N into one bin per quartile so the bin count
        # stays manageable and a missing quartile is visible immediately.
        idx = int(n.rsplit("_", 1)[1])
        if idx < 8:
            return "zimop_r_q0"
        if idx < 16:
            return "zimop_r_q1"
        if idx < 24:
            return "zimop_r_q2"
        return "zimop_r_q3"
    if n.startswith("MOP_RR_"):
        return "zimop_rr"
    if n.startswith("C_MOP_"):
        return "zcmop_any"
    return None


# ---------------------------------------------------------------------------
# Fence pred/succ classifier. RV FENCE encodes pred[3:0] || succ[3:0] as
# the 8 low bits of the I-format imm. Bits map to I/O/R/W (3..0). Useful
# canonical patterns:
#   rw_rw   — full barrier (most common, equiv to "fence rw,rw")
#   rw_w    — release-style (orders preceding ops before the next write)
#   r_rw    — acquire-style
#   io_io   — IO-only (mmio fences)
#   any     — anything we don't have a name for
# Phase-1 sampler: when the assembler emits a bare "fence" we treat it
# as rw_rw (the GCC default). When the user constructs a FENCE Instr
# with imm set explicitly the imm bits drive the classification.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sprint-1 helpers: FP fflags, trap cause, op-combination, EA alignment,
# FP corner-value dataset.
# ---------------------------------------------------------------------------


# RISC-V fcsr exception flag bit positions (priv-arch §11.2).
_FFLAG_BITS = (
    (0, "nx_set"),  # inexact
    (1, "uf_set"),  # underflow
    (2, "of_set"),  # overflow
    (3, "dz_set"),  # divide-by-zero
    (4, "nv_set"),  # invalid operation
)


def _fp_fflags_bins(value: int) -> tuple[str, ...]:
    """Return the bin names for an FFLAGS / FCSR write value.

    fflags lives in fcsr[4:0]; this function tolerates either fflags
    (5-bit) or fcsr (8-bit, frm in [7:5]) writes.

    Returns the tuple of distinct bin names — a single bit set yields a
    single-element tuple ("nv_set",); zero yields ("no_flags",); two or
    more bits set yields the per-flag bins **plus** "multiple_flags" so a
    goals file can demand both individual and aggregate coverage.
    """
    flags_bits = value & 0x1F
    if flags_bits == 0:
        return ("no_flags",)
    bins: list[str] = []
    for bit, name in _FFLAG_BITS:
        if flags_bits & (1 << bit):
            bins.append(name)
    if len(bins) > 1:
        bins.append("multiple_flags")
    return tuple(bins)


# Synchronous / asynchronous trap cause codes (priv-arch v1.13 §3.1.15).
_EXCEPTION_NAMES: dict[int, str] = {
    0: "instr_addr_misaligned",
    1: "instr_access_fault",
    2: "illegal_instruction",
    3: "breakpoint",
    4: "load_addr_misaligned",
    5: "load_access_fault",
    6: "store_amo_addr_misaligned",
    7: "store_amo_access_fault",
    8: "ecall_u",
    9: "ecall_s",
    10: "reserved_10",
    11: "ecall_m",
    12: "instr_page_fault",
    13: "load_page_fault",
    14: "reserved_14",
    15: "store_amo_page_fault",
    20: "instr_guest_page_fault",
    21: "load_guest_page_fault",
    22: "virtual_instruction",
    23: "store_amo_guest_page_fault",
}

_INTERRUPT_NAMES: dict[int, str] = {
    0: "u_software",
    1: "s_software",
    3: "m_software",
    4: "u_timer",
    5: "s_timer",
    7: "m_timer",
    8: "u_external",
    9: "s_external",
    11: "m_external",
    12: "counter_overflow",  # Sscofpmf
}


def _trap_cause_bin(cause: int, xlen: int = 64) -> str:
    """Decode a raw mcause/scause value into a covergroup bin name.

    mcause MSB == 1 → asynchronous interrupt; MSB == 0 → synchronous
    exception. Unknown codes get a generic "unknown_<n>" bin so we
    don't hide them.
    """
    sign_bit = 1 << (xlen - 1)
    if cause & sign_bit:
        code = cause & (sign_bit - 1)
        name = _INTERRUPT_NAMES.get(code, f"unknown_{code}")
        return f"interrupt_{code:02d}_{name}"
    code = cause
    name = _EXCEPTION_NAMES.get(code, f"unknown_{code}")
    return f"exception_{code:02d}_{name}"


# Static op-combination sampler — bins capture special-register usage and
# triple-equality patterns the simpler rs1_eq_rs2 / rs1_eq_rd crosses miss.
_SPECIAL_REGS = {
    RiscvReg.ZERO: "zero",
    RiscvReg.RA: "ra",
    RiscvReg.SP: "sp",
    RiscvReg.GP: "gp",
    RiscvReg.TP: "tp",
}


def _op_comb_bins(instr: Instr) -> tuple[str, ...]:
    """Return op-combination bins for a single instruction.

    Each instruction may contribute multiple bins (e.g. an instruction
    with rs1=sp and rd=ra contributes both ``rs1_is_sp`` and
    ``rd_is_ra``). The triple-equality bin is rare but very informative
    (``add x5, x5, x5`` style).
    """
    bins: list[str] = []
    if instr.has_rd and instr.has_rs1 and instr.has_rs2:
        if instr.rd == instr.rs1 == instr.rs2 and instr.rd != RiscvReg.ZERO:
            bins.append("rd_eq_rs1_eq_rs2")
    if instr.has_rs1:
        sp = _SPECIAL_REGS.get(instr.rs1)
        if sp is not None:
            bins.append(f"rs1_is_{sp}")
    if instr.has_rs2:
        sp = _SPECIAL_REGS.get(instr.rs2)
        if sp is not None:
            bins.append(f"rs2_is_{sp}")
    if instr.has_rd:
        sp = _SPECIAL_REGS.get(instr.rd)
        if sp is not None:
            bins.append(f"rd_is_{sp}")
    return tuple(bins)


def _ea_align_bin(addr: int) -> str:
    """Return the largest power-of-two aligning ``addr`` (capped at 64).

    Returns one of: align_1, align_2, align_4, align_8, align_16,
    align_32, align_64. Unaligned (addr % 2 != 0) maps to align_1.
    """
    if addr == 0:
        return "align_64"  # zero address considered max-aligned for coverage
    a = addr & 0xFFFF_FFFF_FFFF_FFFF
    # Find lowest set bit position.
    low = (a & -a).bit_length() - 1
    if low >= 6:
        return "align_64"
    return f"align_{1 << low}"


# IEEE-754 single + double FP corner classification. The bin set is
# riscv-isac sp_dataset / dp_dataset compatible.
_FP_DATASET_BINS = (
    "pos_zero", "neg_zero", "pos_inf", "neg_inf",
    "qnan", "snan",
    "pos_subnormal", "neg_subnormal",
    "pos_normal_min", "neg_normal_min",
    "pos_normal_max", "neg_normal_max",
    "pos_one", "neg_one",
    "generic",
)


def _fp_dataset_bin(value: int, width: int) -> str:
    """Classify an IEEE-754 bit-pattern of ``width`` bits (32 or 64).

    Returns one of :data:`_FP_DATASET_BINS`. width=16 (Zfh) is also
    accepted; subnormal / normal-min / normal-max thresholds are derived
    from the format.
    """
    if width == 16:
        sign_bit = 1 << 15
        exp_mask = 0x1F
        exp_shift = 10
        mant_mask = (1 << 10) - 1
        normal_max_exp = 0x1E
        normal_max_mant = (1 << 10) - 1
        one_pattern = 0x3C00
    elif width == 32:
        sign_bit = 1 << 31
        exp_mask = 0xFF
        exp_shift = 23
        mant_mask = (1 << 23) - 1
        normal_max_exp = 0xFE
        normal_max_mant = (1 << 23) - 1
        one_pattern = 0x3F800000
    elif width == 64:
        sign_bit = 1 << 63
        exp_mask = 0x7FF
        exp_shift = 52
        mant_mask = (1 << 52) - 1
        normal_max_exp = 0x7FE
        normal_max_mant = (1 << 52) - 1
        one_pattern = 0x3FF0000000000000
    else:
        return "generic"

    v = value & ((1 << width) - 1)
    sign = bool(v & sign_bit)
    exp = (v >> exp_shift) & exp_mask
    mant = v & mant_mask

    # ±0
    if exp == 0 and mant == 0:
        return "neg_zero" if sign else "pos_zero"
    # ±inf / NaN (exp = all-ones)
    if exp == exp_mask:
        if mant == 0:
            return "neg_inf" if sign else "pos_inf"
        # NaN — quiet bit is the MSB of mantissa.
        quiet_bit = 1 << (exp_shift - 1)
        return "qnan" if (mant & quiet_bit) else "snan"
    # Subnormal (exp == 0, mant != 0).
    if exp == 0:
        return "neg_subnormal" if sign else "pos_subnormal"
    # Normal min (exp == 1, mant == 0).
    if exp == 1 and mant == 0:
        return "neg_normal_min" if sign else "pos_normal_min"
    # Normal max.
    if exp == normal_max_exp and mant == normal_max_mant:
        return "neg_normal_max" if sign else "pos_normal_max"
    # ±1.
    if (v & ~sign_bit) == one_pattern:
        return "neg_one" if sign else "pos_one"
    return "generic"


def _fence_pat_bin(imm: int) -> str:
    pred = (imm >> 4) & 0xF
    succ = imm & 0xF

    def _decode(field: int) -> str:
        if field == 0xF:
            return "rwio"
        if field == 0x3:
            return "rw"
        if field == 0x1:
            return "w"
        if field == 0x2:
            return "r"
        if field == 0xC:
            return "io"
        if field == 0x8:
            return "i"
        if field == 0x4:
            return "o"
        if field == 0x0:
            return "0"
        return f"raw{field:x}"

    return f"{_decode(pred)}__{_decode(succ)}"


# ---------------------------------------------------------------------------
# Sprint-2 helpers — pipeline distance, RAS/JALR class, AMO ordering,
# FP semantic op, vector AVL/policy, M-extension corners, B-extension
# semantic op classifier, RVC immediate corner classifier.
# ---------------------------------------------------------------------------


def _hazard_dist_bin(distance: int) -> str:
    """Bin a register-dependency producer→consumer cycle distance.

    The "cycle" here is a generated-instruction count; in real silicon a
    1-cycle distance is the load-use hazard, 2-3 are typical forwarding
    paths, 4+ usually doesn't stall. The verification value is showing
    the spread.
    """
    if distance == 1:
        return "dist_1_load_use"
    if distance == 2:
        return "dist_2"
    if distance == 3:
        return "dist_3"
    if distance <= 5:
        return "dist_4_5"
    if distance <= 7:
        return "dist_6_7"
    return "dist_8_plus"


_LOAD_INSTR_NAMES = frozenset({
    RiscvInstrName.LB, RiscvInstrName.LBU, RiscvInstrName.LH,
    RiscvInstrName.LHU, RiscvInstrName.LW, RiscvInstrName.LWU,
    RiscvInstrName.LD,
    RiscvInstrName.C_LW, RiscvInstrName.C_LWSP,
    RiscvInstrName.C_LD, RiscvInstrName.C_LDSP,
    # FP loads count as multi-cycle producers in the FP-use covergroup.
    RiscvInstrName.FLW, RiscvInstrName.FLD,
})


def _is_multicycle_producer(name: RiscvInstrName) -> bool:
    """Return True for ops whose result lands ≥2 cycles after issue.

    M-extension (MUL*/DIV*/REM*), F/D-extension divide/sqrt, AMO ops
    typically take 4-32 cycles. We collapse them into one "multi-cycle"
    bucket because their consumer-distance distribution is similar from
    a verification-coverage standpoint.
    """
    n = name.name
    if n.startswith(("MUL", "DIV", "REM")):
        return True
    if n in ("FDIV_S", "FDIV_D", "FDIV_H",
             "FSQRT_S", "FSQRT_D", "FSQRT_H"):
        return True
    if n.startswith("AMO") or n.startswith(("LR_", "SC_")):
        return True
    return False


def _amo_aqrl_bin(aq: bool, rl: bool) -> str:
    if aq and rl:
        return "aq_and_rl"
    if aq:
        return "aq_only"
    if rl:
        return "rl_only"
    return "neither"


_AMO_PRINCIPAL_RE = re.compile(r"^(AMO[A-Z]+|LR|SC)_([WD])$")


def _amo_op_width_split(name: RiscvInstrName) -> tuple[str, str] | None:
    """Split an AMO/LR/SC mnemonic into (op_family, width).

    Returns ``("AMOADD", "W")`` for ``AMOADD_W``, ``("LR", "D")`` for
    ``LR_D``. Returns None for non-AMO instructions.
    """
    m = _AMO_PRINCIPAL_RE.match(name.name)
    if not m:
        return None
    return m.group(1), m.group(2)


# FP semantic-op classifier. Maps each FP mnemonic into one of a small
# set of semantic op classes — far easier to reason about coverage of
# "every FP-add covered" than enumerating per precision × per rounding
# mode × per W/L variant.
def _fp_op_class(name: RiscvInstrName) -> str | None:
    n = name.name
    if not (n.startswith("F") and n != "FENCE" and n != "FENCE_I"):
        return None
    # Specific groups first.
    if n.startswith(("FMADD", "FMSUB", "FNMADD", "FNMSUB")):
        return "fma"
    if n.startswith("FADD"):
        return "add"
    if n.startswith("FSUB"):
        return "sub"
    if n.startswith("FMUL"):
        return "mul"
    if n.startswith("FDIV"):
        return "div"
    if n.startswith("FSQRT"):
        return "sqrt"
    if n.startswith(("FMIN", "FMAX")):
        return "minmax"
    if n.startswith(("FEQ", "FLT", "FLE", "FGT", "FGE")):
        return "compare"
    if n.startswith("FCVT"):
        return "convert"
    if n.startswith("FSGN"):
        return "sign"
    if n.startswith("FCLASS"):
        return "classify"
    if n in ("FMV_W_X", "FMV_X_W", "FMV_D_X", "FMV_X_D",
             "FMV_H_X", "FMV_X_H"):
        return "move"
    if n in ("FLW", "FLD", "FLH"):
        return "load"
    if n in ("FSW", "FSD", "FSH"):
        return "store"
    return None


def _fp_precision(name: RiscvInstrName) -> str | None:
    """Return ``H``, ``S``, or ``D`` based on the destination precision.

    For FCVT_*_*, RISC-V mnemonic convention is FCVT.<dst>.<src>, so the
    second underscore segment is the destination.
    """
    n = name.name
    # FCVT_<dst>_<src> — destination is parts[1].
    if n.startswith("FCVT_"):
        parts = n.split("_")
        if len(parts) >= 2 and parts[1] in ("S", "D", "H"):
            return parts[1]
    if n.endswith("_S") or n in ("FLW", "FSW"):
        return "S"
    if n.endswith("_D") or n in ("FLD", "FSD"):
        return "D"
    if n.endswith("_H") or n in ("FLH", "FSH"):
        return "H"
    return None


# Bitmanip semantic-op classifier. Maps each B-extension mnemonic into
# one of a small set of op classes.
def _bitmanip_op_class(name: RiscvInstrName) -> str | None:
    n = name.name
    if n.startswith(("ROL", "ROR")):
        return "rotate"
    if n.startswith(("CLZ", "CLZW")):
        return "clz"
    if n.startswith(("CTZ", "CTZW")):
        return "ctz"
    if n.startswith("CPOP"):
        return "popcount"
    if n.startswith(("MAX", "MIN")):
        return "minmax"
    if n.startswith("CLMUL"):
        return "clmul"
    if n.startswith(("BCLR", "BSET", "BEXT", "BINV")):
        return "single_bit"
    if n.startswith(("ANDN", "ORN", "XNOR")):
        return "logic_neg"
    if n.startswith(("PACK", "PACKH", "PACKW")):
        return "pack"
    if n.startswith("ORC"):
        return "or_combine"
    if n in ("REV8", "BREV8", "BREV"):
        return "byte_reverse"
    if n in ("ZEXT_H", "ZEXT_W", "SEXT_B", "SEXT_H"):
        return "extend"
    if n.startswith(("SH1ADD", "SH2ADD", "SH3ADD")):
        return "shift_add"
    if n.startswith(("SLLI_UW", "ADD_UW", "ZEXT_UW")):
        return "uw"
    if n.startswith("XPERM"):
        return "xperm"
    return None


def _ras_class(name: RiscvInstrName, rs1: RiscvReg | None,
               rd: RiscvReg | None) -> str | None:
    """Classify a JAL/JALR for RAS prediction modeling.

    RISC-V ABI link reg = x1 (ra). x5 (t0) is also pushed/popped by the
    RAS per priv-arch §2.5.1 ("Standard Calling Convention"). The
    classification follows the spec rule:

    - ``call``: rd ∈ {x1, x5}, rs1 ≠ {x1, x5}.
    - ``return``: rs1 ∈ {x1, x5}, rd not in {x1, x5}.
    - ``coroutine_swap``: rs1 ∈ {x1, x5}, rd ∈ {x1, x5}, rs1 ≠ rd.
    - ``computed``: JALR with rd, rs1 outside the link-reg pair.
    - ``other`` for non-link-reg variants (e.g. tail call jal x0, tgt).
    """
    if name not in (RiscvInstrName.JAL, RiscvInstrName.JALR,
                    RiscvInstrName.C_J, getattr(RiscvInstrName, "C_JAL", None),
                    RiscvInstrName.C_JR, RiscvInstrName.C_JALR):
        return None
    link_regs = (RiscvReg.RA, RiscvReg.T0)
    rd_link = rd in link_regs if rd is not None else False
    rs1_link = rs1 in link_regs if rs1 is not None else False
    # JAL / C_JAL / C_J — only rd matters (no rs1).
    if name in (RiscvInstrName.JAL, getattr(RiscvInstrName, "C_JAL", None)):
        if rd_link:
            return "call"
        if rd == RiscvReg.ZERO:
            return "tail_call"
        return "other"
    if name == RiscvInstrName.C_J:
        return "tail_call"
    # JALR / C_JR / C_JALR — both rs1 and rd matter.
    if name == RiscvInstrName.C_JR:
        # c.jr rs1 == jalr x0, rs1, 0 — pure indirect jump.
        return "return" if rs1_link else "computed"
    if name == RiscvInstrName.C_JALR:
        # c.jalr rs1 == jalr ra, rs1, 0.
        return "computed" if not rs1_link else "coroutine_swap"
    # JALR.
    if rs1_link and rd_link and rs1 != rd:
        return "coroutine_swap"
    if rs1_link and not rd_link:
        return "return"
    if rd_link and not rs1_link:
        return "call"
    if rd == RiscvReg.ZERO and not rs1_link:
        return "tail_call"
    return "computed"


_REG_CLASS = {
    RiscvReg.ZERO: "zero",
    RiscvReg.RA: "ra",
    RiscvReg.SP: "sp",
    RiscvReg.GP: "gp",
    RiscvReg.TP: "tp",
}
for _r in (RiscvReg.T0, RiscvReg.T1, RiscvReg.T2, RiscvReg.T3,
           RiscvReg.T4, RiscvReg.T5, RiscvReg.T6):
    _REG_CLASS[_r] = "temporary"
for _r in (RiscvReg.A0, RiscvReg.A1, RiscvReg.A2, RiscvReg.A3,
           RiscvReg.A4, RiscvReg.A5, RiscvReg.A6, RiscvReg.A7):
    _REG_CLASS[_r] = "argument"
for _r in (RiscvReg.S0, RiscvReg.S1, RiscvReg.S2, RiscvReg.S3,
           RiscvReg.S4, RiscvReg.S5, RiscvReg.S6, RiscvReg.S7,
           RiscvReg.S8, RiscvReg.S9, RiscvReg.S10, RiscvReg.S11):
    _REG_CLASS[_r] = "saved"


def _muldiv_corner_bin(name: RiscvInstrName, rs1_val: int | None,
                       rs2_val: int | None, xlen: int = 64) -> str | None:
    """Static M-extension corner classifier.

    Returns one of the canonical "interesting" bins:

    - ``div_by_zero``: any DIV/REM with rs2 == 0.
    - ``signed_overflow``: signed DIV/REM with rs1 == INT_MIN, rs2 == -1.
    - ``mul_max_pair``: MUL with both operands at max signed magnitude
      (forces upper bits — useful for MULH coverage).
    - None for non-M ops or non-corner values.
    """
    n = name.name
    if not n.startswith(("MUL", "DIV", "REM")):
        return None
    if rs2_val is None:
        return None
    mask = (1 << xlen) - 1
    rs2 = rs2_val & mask
    if n.startswith(("DIV", "REM")) and rs2 == 0:
        return "div_by_zero"
    if rs1_val is not None:
        rs1 = rs1_val & mask
        # Signed-overflow corner: rs1 == 1 << (xlen-1), rs2 == -1.
        signed_min = 1 << (xlen - 1)
        signed_neg_one = mask
        if (rs1 == signed_min and rs2 == signed_neg_one
                and n.startswith(("DIV_", "DIV", "REM"))
                and not n.startswith(("DIVU", "REMU"))):
            return "signed_overflow"
        if n.startswith("MUL"):
            # Both operands at signed-max gives a result that exercises
            # all upper bits in MULH/MULHSU/MULHU.
            signed_max = signed_min - 1
            if rs1 == signed_max and rs2 == signed_max:
                return "mul_max_pair"
            if rs1 == mask and rs2 == mask:
                return "mul_neg_one_pair"
    return None


def _c_imm_corner_bin(name: RiscvInstrName, imm: int) -> str | None:
    """Classify a compressed-instruction immediate for boundary corners.

    Compressed ops have severely-constrained immediates with
    NZIMM/NZUIMM (must-be-nonzero) requirements. Bins capture whether
    we exercised the small / max / negative-max corners for each.
    """
    n = name.name
    if not n.startswith("C_"):
        return None
    if imm == 0:
        return f"{n.lower()}_zero_imm"
    a = abs(imm)
    if a == 1:
        return f"{n.lower()}_imm_one"
    # Common max-imm corners — we can't tell exact field width here
    # without knowing the C-format, but we can detect "large" imm.
    if a >= 32:
        return f"{n.lower()}_imm_large"
    return None


# ---------------------------------------------------------------------------
# Sprint-2 wave 2 — value-class abstract bins, FCVT corners, shamt
# corners, RVC illegal-imm, vector AVL/overlap, atomic alignment.
# ---------------------------------------------------------------------------


def _walking_ones_bins(value: int, xlen: int = 64) -> tuple[str, ...]:
    """Return one bin per set bit position of ``value``.

    ``value`` is interpreted as a 2's-complement integer of ``xlen`` bits.
    Bin name format: ``bit_<i>_set`` for each set bit. Returns
    ``("no_bits_set",)`` when value == 0.
    """
    mask = (1 << xlen) - 1
    v = value & mask
    if v == 0:
        return ("no_bits_set",)
    bins = []
    while v:
        b = (v & -v).bit_length() - 1
        bins.append(f"bit_{b:02d}_set")
        v &= v - 1
    return tuple(bins)


def _walking_zeros_bins(value: int, xlen: int = 64) -> tuple[str, ...]:
    """Return one bin per cleared bit position of ``value`` (within xlen)."""
    mask = (1 << xlen) - 1
    v = (~value) & mask
    if v == 0:
        return ("all_bits_set",)
    bins = []
    while v:
        b = (v & -v).bit_length() - 1
        bins.append(f"bit_{b:02d}_clear")
        v &= v - 1
    return tuple(bins)


def _alternate_bin(value: int, xlen: int = 64) -> str | None:
    """Return one of {alt_5555, alt_AAAA, alt_byte_A5, alt_byte_5A} or None.

    - ``alt_5555``: bit-alternating 0x5555… (each pair of bits is 01).
    - ``alt_AAAA``: bit-alternating 0xAAAA… (each pair is 10).
    - ``alt_byte_A5``: every byte equals 0xA5 — nibble-alternating
      within each byte. (E.g. 0xA5A5A5A5 in xlen=32.)
    - ``alt_byte_5A``: every byte equals 0x5A.

    Returns None for non-alternating values.
    """
    mask = (1 << xlen) - 1
    v = value & mask
    a55 = sum((0x55 << (8 * i)) for i in range(xlen // 8)) & mask
    aaa = sum((0xAA << (8 * i)) for i in range(xlen // 8)) & mask
    if v == a55:
        return "alt_5555"
    if v == aaa:
        return "alt_AAAA"
    bA5 = sum((0xA5 << (8 * i)) for i in range(xlen // 8)) & mask
    b5A = sum((0x5A << (8 * i)) for i in range(xlen // 8)) & mask
    if v == bA5:
        return "alt_byte_A5"
    if v == b5A:
        return "alt_byte_5A"
    return None


def _leading_trailing_bins(value: int, xlen: int = 64) -> tuple[str, ...]:
    """Compute leading-/trailing-ones/zeros buckets for ``value``.

    Bucket boundaries: 0, 1, 2, 4, 8, 16, 32, 64. The bucket label
    encodes the run-type and the largest bucket whose threshold the run
    meets — e.g. ``lead0_8`` means at least 8 leading zeros.
    """
    mask = (1 << xlen) - 1
    v = value & mask
    out = []

    def _bucket(n: int) -> int:
        for thr in (64, 32, 16, 8, 4, 2, 1):
            if n >= thr:
                return thr
        return 0

    # Leading zeros — count from MSB down to first 1.
    if v == 0:
        out.append("lead0_64")
    else:
        lz = xlen - v.bit_length()
        out.append(f"lead0_{_bucket(lz)}")
    # Trailing zeros.
    if v == 0:
        out.append("trail0_64")
    else:
        tz = (v & -v).bit_length() - 1
        out.append(f"trail0_{_bucket(tz)}")
    # Leading ones — count from MSB.
    inv = (~v) & mask
    if inv == 0:
        out.append("lead1_64")
    else:
        lo = xlen - inv.bit_length()
        if lo > 0:
            out.append(f"lead1_{_bucket(lo)}")
    # Trailing ones.
    if inv == 0:
        out.append("trail1_64")
    else:
        to = (inv & -inv).bit_length() - 1
        if to > 0:
            out.append(f"trail1_{_bucket(to)}")
    return tuple(out)


# Shift-instruction shamt-boundary classifier. Captures the corners
# that off-by-one shifters miss: shamt=0 (identity), shamt=1, shamt=
# XLEN-1 (full-shift), shamt==XLEN (UB on RV32 / legal mod XLEN on RV64).
_SHIFT_INSTR_NAMES = frozenset({
    RiscvInstrName.SLLI, RiscvInstrName.SRLI, RiscvInstrName.SRAI,
    RiscvInstrName.SLL, RiscvInstrName.SRL, RiscvInstrName.SRA,
    RiscvInstrName.SLLIW, RiscvInstrName.SRLIW, RiscvInstrName.SRAIW,
    RiscvInstrName.SLLW, RiscvInstrName.SRLW, RiscvInstrName.SRAW,
})


def _shamt_corner_bin(name: RiscvInstrName, shamt: int,
                      xlen: int = 64) -> str | None:
    """Bin a shift-instruction shamt against canonical corners.

    Returns None for non-shift ops or non-corner values.
    """
    n = name.name
    if name not in _SHIFT_INSTR_NAMES and not n.startswith(("ROL", "ROR")):
        return None
    if shamt == 0:
        return f"{n.lower()}_shamt_zero"
    if shamt == 1:
        return f"{n.lower()}_shamt_one"
    if shamt == xlen - 1:
        return f"{n.lower()}_shamt_max_minus_one"
    if shamt == xlen:
        return f"{n.lower()}_shamt_xlen"
    if shamt > xlen:
        return f"{n.lower()}_shamt_oob"
    return None


# Vector vsetvl flavor classifier.
def _vsetvl_flavor(name: RiscvInstrName, rd: RiscvReg | None,
                   rs1: RiscvReg | None) -> str | None:
    """Return ``vsetvl`` / ``vsetvli`` / ``vsetivli`` and AVL path bin.

    Returns the path bin (e.g. "vsetvli_set_vlmax") for use with
    CG_VSETVL_AVL. None if not a vset family.
    """
    n = name.name
    if n not in ("VSETVL", "VSETVLI", "VSETIVLI"):
        return None
    flavor = n.lower()
    # vsetivli always takes an immediate AVL — no rs1 path.
    if flavor == "vsetivli":
        return f"{flavor}_imm_avl"
    is_rd_zero = rd == RiscvReg.ZERO if rd is not None else False
    is_rs1_zero = rs1 == RiscvReg.ZERO if rs1 is not None else False
    if not is_rd_zero and not is_rs1_zero:
        return f"{flavor}_normal"
    if not is_rd_zero and is_rs1_zero:
        return f"{flavor}_set_vlmax"
    if is_rd_zero and is_rs1_zero:
        return f"{flavor}_keep_vl"
    return f"{flavor}_other"


# FCVT corner bin classifier — runtime helper. Inputs are (op, src_class).
def _fcvt_corner_bin(name: RiscvInstrName, src_class: str) -> str | None:
    """Sample a saturation corner for an FCVT instruction with known src.

    ``src_class`` is one of {nan, inf, normal, subnormal, zero}. Returns
    a bin like ``fcvt_w_s_nan_to_intmax`` only for spec-mandated saturation
    corners — None otherwise.
    """
    n = name.name
    if not n.startswith("FCVT_"):
        return None
    parts = n.split("_")
    if len(parts) < 3:
        return None
    dst = parts[1]
    src = parts[2]
    int_dst = dst in ("W", "WU", "L", "LU")
    if not int_dst:
        return None  # FP→FP overflows aren't in scope here
    if src_class == "nan":
        return f"{n.lower()}_nan_input"
    if src_class == "inf":
        return f"{n.lower()}_inf_input"
    return None


# Vector register-group overlap classifier — sampled when a vector op
# has both vd and at least one vs slot. Returns one of {full_overlap,
# partial_overlap, no_overlap}.
def _vreg_overlap_class(vd: int, vs: int, emul: int) -> str:
    """Classify EMUL-aware vd/vs register-group overlap.

    ``vd``, ``vs`` are vector-register indices [0..31]; ``emul`` is the
    register-group multiplier (1, 2, 4, 8) — fractional EMUL is treated
    as 1 for overlap purposes (a single register is the dest group).
    """
    g = max(emul, 1)
    vd_lo, vd_hi = vd, vd + g - 1
    vs_lo, vs_hi = vs, vs + g - 1
    if vd == vs:
        return "full_overlap"
    if vd_lo <= vs_hi and vs_lo <= vd_hi:
        return "partial_overlap"
    return "no_overlap"


def new_db() -> CoverageDB:
    """Return a freshly-initialised, empty CoverageDB."""
    return {cg: {} for cg in ALL_COVERGROUPS}


# ---------------------------------------------------------------------------
# Per-instruction sampler
# ---------------------------------------------------------------------------


def _bump(db: CoverageDB, cg: str, bin_name: str) -> None:
    bins = db.setdefault(cg, {})
    bins[bin_name] = bins.get(bin_name, 0) + 1


def _imm_sign_bin(imm: int, imm_len: int) -> str:
    # Interpret imm as signed 2's complement of imm_len bits for sign.
    if imm_len == 0:
        return "zero"
    sign_bit = 1 << (imm_len - 1)
    v = imm & ((1 << imm_len) - 1)
    if v == 0:
        return "zero"
    if v & sign_bit:
        return "neg"
    return "pos"


def _imm_range_bin(imm: int, imm_len: int) -> str:
    """Classify the immediate against the canonical value-class bins."""
    if imm_len == 0:
        return "none"
    return _value_class(imm, imm_len)


_BYTE_OPS = frozenset({
    RiscvInstrName.LB, RiscvInstrName.LBU, RiscvInstrName.SB,
})
_HALF_OPS = frozenset({
    RiscvInstrName.LH, RiscvInstrName.LHU, RiscvInstrName.SH,
})
_WORD_OPS = frozenset({
    RiscvInstrName.LW, RiscvInstrName.LWU, RiscvInstrName.SW,
})
_DWORD_OPS = frozenset({
    RiscvInstrName.LD, RiscvInstrName.SD,
})


def _load_store_width_bin(name: RiscvInstrName) -> str | None:
    if name in _BYTE_OPS:
        return "byte"
    if name in _HALF_OPS:
        return "half"
    if name in _WORD_OPS:
        return "word"
    if name in _DWORD_OPS:
        return "dword"
    return None


def _mem_align_bin(offset: int, name: RiscvInstrName) -> str | None:
    """Classify the access by its natural alignment requirement + the offset bits.

    Natural alignment for ``name`` × offset mod natural width:

    - byte ops: always aligned (``aligned``).
    - half ops: ``aligned`` iff offset%2 == 0, else ``unaligned_half``.
    - word ops: ``aligned`` iff offset%4 == 0, else ``unaligned_word``.
    - dword ops: ``aligned`` iff offset%8 == 0, else ``unaligned_dword``.
    """
    if name in _BYTE_OPS:
        return "byte_aligned"
    if name in _HALF_OPS:
        return "half_aligned" if offset % 2 == 0 else "half_unaligned"
    if name in _WORD_OPS:
        return "word_aligned" if offset % 4 == 0 else "word_unaligned"
    if name in _DWORD_OPS:
        return "dword_aligned" if offset % 8 == 0 else "dword_unaligned"
    return None


# ---------------------------------------------------------------------------
# Vector-specific samplers — only invoked from sample_instr when group==RVV.
# ---------------------------------------------------------------------------


# Mnemonic prefix → ratified Zv* sub-extension family. Used by CG_VEC_CRYPTO
# so a single bin per family captures whether the test exercises Zvbb/Zvbc/Zvkn.
_ZV_FAMILY_BY_PREFIX: tuple[tuple[tuple[str, ...], str], ...] = (
    (("VANDN", "VBREV", "VBREV8", "VREV8", "VCLZ", "VCTZ", "VCPOP",
      "VROL", "VROR", "VWSLL"), "zvbb"),
    (("VCLMUL", "VCLMULH"), "zvbc"),
    (("VAES", "VSHA2"), "zvkn"),
)


_CACHE_LINE_BYTES = 64    # standard for ARM/Intel/most RISC-V cores
_PAGE_BYTES = 4096
_ACCESS_WIDTH_BY_BIN = {"byte": 1, "half": 2, "word": 4, "dword": 8}


# The canonical set of value-class bins the rs1/rs2/rd_val_class_cg covergroups
# accept. Imported by cgf.py for the ``corners()`` abstract-bin function.
VALUE_CLASS_BINS: tuple[str, ...] = (
    "zero", "one", "all_ones", "min_signed", "max_signed",
    "walking_one", "walking_zero", "alternating", "small", "generic",
)

# Pre-computed alternating-pattern masks per XLEN — recomputing them per
# call costs measurably on long traces.
_ALT_MASKS: dict[int, tuple[int, int]] = {
    xlen: (
        sum(1 << i for i in range(0, xlen, 2)),  # 0x55..55
        sum(1 << i for i in range(1, xlen, 2)),  # 0xAA..AA
    )
    for xlen in (8, 16, 32, 64, 128)
}


def _value_class(val: int, xlen: int) -> str:
    """Classify a register value into industry-standard corner buckets.

    Mirrors what riscv-isac calls ``val_comb`` corners + the
    walking_ones / walking_zeros expansions. Returns one bin name from
    :data:`VALUE_CLASS_BINS`.
    """
    mask = (1 << xlen) - 1
    v = val & mask
    if v == 0:
        return "zero"
    if v == mask:
        return "all_ones"
    if v == 1:
        return "one"
    sign_bit = 1 << (xlen - 1)
    if v == sign_bit:
        return "min_signed"
    if v == sign_bit - 1:
        return "max_signed"
    if v & (v - 1) == 0:
        return "walking_one"
    inv = (~v) & mask
    if inv & (inv - 1) == 0:
        return "walking_zero"
    alt_a, alt_b = _ALT_MASKS.get(xlen, (0, 0))
    if v == alt_a or v == alt_b:
        return "alternating"
    sval = v - (1 << xlen) if v & sign_bit else v
    if -16 <= sval <= 16:
        return "small"
    return "generic"


_ADDR_MODE_BY_FMT: dict = {
    RiscvInstrFormat.VL_FORMAT: "UNIT_STRIDED",
    RiscvInstrFormat.VS_FORMAT: "UNIT_STRIDED",
    RiscvInstrFormat.VLS_FORMAT: "STRIDED",
    RiscvInstrFormat.VSS_FORMAT: "STRIDED",
    RiscvInstrFormat.VLX_FORMAT: "INDEXED",
    RiscvInstrFormat.VSX_FORMAT: "INDEXED",
    RiscvInstrFormat.VAMO_FORMAT: "INDEXED",
}


def _vector_family(name: RiscvInstrName) -> str | None:
    n = name.name
    for prefixes, fam in _ZV_FAMILY_BY_PREFIX:
        if any(n.startswith(p) for p in prefixes):
            return fam
    return None


def _sample_vector(db: CoverageDB, instr: Instr, vector_cfg) -> None:
    """Bump the vector-specific covergroups.

    Only called when ``instr.group == RVV`` and a ``vector_cfg`` is in scope.
    Each bump is wrapped in a try/except so an ill-formed vector pseudo
    (``vmv.v.x`` from the LS stream init) doesn't crash the sampler.
    """
    name = instr.instr_name
    cat = getattr(instr, "category", None)
    fmt = getattr(instr, "format", None)

    # Mask usage — ``vm`` is 1 (unmasked) or 0 (masked).
    vm = getattr(instr, "vm", None)
    if vm is not None:
        bin_name = "unmasked" if vm == 1 else "masked"
        _bump(db, CG_VEC_VM, bin_name)
        if cat is not None:
            _bump(db, CG_VEC_VM_X_CAT, f"{bin_name}__{cat.name}")

    # Address mode for vector loads/stores. Inferred from the format.
    if fmt is not None and fmt in _ADDR_MODE_BY_FMT:
        _bump(db, CG_VEC_LS_MODE, _ADDR_MODE_BY_FMT[fmt])

    # EEW / EMUL — set by the load/store randomizer.
    eew = getattr(instr, "eew", 0)
    emul = getattr(instr, "emul", 0)
    if eew:
        _bump(db, CG_VEC_EEW, f"EEW{eew}")
        sew = vector_cfg.vtype.vsew
        if eew == sew:
            rel = "eq"
        elif eew > sew:
            rel = "wider"
        else:
            rel = "narrower"
        _bump(db, CG_VEC_EEW_VS_SEW, f"EEW{eew}_vs_SEW{sew}_{rel}")
    if emul:
        _bump(db, CG_VEC_EMUL, f"EMUL{emul}")

    # AMO write-destination flag.
    if cat is not None and getattr(cat, "name", "") == "AMO":
        wd = getattr(instr, "wd", None)
        if wd is not None:
            _bump(db, CG_VEC_AMO_WD, "wd_set" if wd else "wd_clear")

    # va_variant — VV / VX / VI / VF / WV / WX / WI / VVM / VXM / VFM ...
    if getattr(instr, "has_va_variant", False):
        variant = getattr(instr, "va_variant", None)
        if variant is not None:
            _bump(db, CG_VEC_VARIANT, variant.name)

    # Zvlsseg NFIELDS — instr.nfields is (NF - 1) when set.
    nfields = getattr(instr, "nfields", 0)
    sub_extension = getattr(instr, "sub_extension", "")
    if sub_extension == "zvlsseg" and nfields is not None:
        nf = nfields + 1
        _bump(db, CG_VEC_NF, f"NF{nf}")
        seg_mode = _ADDR_MODE_BY_FMT.get(fmt) if fmt is not None else None
        if seg_mode is not None:
            _bump(db, CG_VEC_SEG_X_MODE, f"NF{nf}__{seg_mode}")

    # Widening / narrowing / quad-widening / convert — set by VectorInstr.
    if getattr(instr, "is_quad_widening_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "quad_widening")
    elif getattr(instr, "is_widening_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "widening")
    elif getattr(instr, "is_narrowing_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "narrowing")
    elif getattr(instr, "is_convert_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "convert")

    # Crypto family — Zvbb / Zvbc / Zvkn.
    fam = _vector_family(name)
    if fam is not None:
        _bump(db, CG_VEC_CRYPTO, fam)


_BRANCH_INSTR_NAMES = frozenset({
    RiscvInstrName.BEQ, RiscvInstrName.BNE,
    RiscvInstrName.BLT, RiscvInstrName.BGE,
    RiscvInstrName.BLTU, RiscvInstrName.BGEU,
    RiscvInstrName.C_BEQZ, RiscvInstrName.C_BNEZ,
})


def _sample_branch_distance(db: CoverageDB, instr: Instr) -> None:
    """Bin a branch's static target distance (taken delta).

    Only the byte-offset magnitude + sign is statically known; whether
    the branch is taken at runtime is sampled separately by
    ``rvgen.coverage.runtime``.

    Distance buckets are picked to align with branch-predictor design
    rules of thumb:

    - ``zero``  (offset == 0; never seen in well-formed asm)
    - ``fwd_short`` / ``bwd_short``    : |off| < 16 (within an 8-instr window)
    - ``fwd_medium`` / ``bwd_medium``  : 16 ≤ |off| < 256 (typical loop body)
    - ``fwd_long`` / ``bwd_long``      : 256 ≤ |off| < 4096 (function-scope)
    - ``fwd_huge`` / ``bwd_huge``      : ≥ 4096 (rare; needs ±2KiB fixup)
    """
    if instr.instr_name not in _BRANCH_INSTR_NAMES:
        return
    # The generator stashes the branch's resolved byte offset in
    # ``imm``. For unresolved string-label branches it'll be 0; in that
    # case we can't bin — bail.
    off = int(getattr(instr, "imm", 0))
    if off == 0:
        # Try the imm_str: branches resolved late may carry a number there.
        try:
            off = int(instr.imm_str)
        except (ValueError, AttributeError):
            return
    if off == 0:
        _bump(db, CG_BRANCH_DIST, "zero")
        return
    direction = "fwd" if off > 0 else "bwd"
    mag = abs(off)
    if mag < 16:
        bucket = "short"
    elif mag < 256:
        bucket = "medium"
    elif mag < 4096:
        bucket = "long"
    else:
        bucket = "huge"
    _bump(db, CG_BRANCH_DIST, f"{direction}_{bucket}")


def sample_instr(db: CoverageDB, instr: Instr, *, vector_cfg=None) -> None:
    """Sample one :class:`Instr` into ``db``.

    Safe to call for any registered instruction, including vector / FP /
    compressed / pseudo variants. No-ops gracefully on instructions that
    lack optional slots (e.g. the ``_LiPseudo`` emitted by directed streams).

    When ``vector_cfg`` is provided and the instruction is a vector op,
    also bumps :data:`CG_VTYPE_DYN` with a ``SEW<w>_LMUL<n>`` bin name —
    this tells the reporter what vtype was active when each vector op
    was generated.
    """
    # Opcode — use the enum name if present, fall back to the class name.
    try:
        opcode_name = instr.instr_name.name
    except AttributeError:
        return  # pseudo w/o enum — skip

    _bump(db, CG_OPCODE, opcode_name)

    try:
        _bump(db, CG_FORMAT, instr.format.name)
    except AttributeError:
        pass
    try:
        _bump(db, CG_CATEGORY, instr.category.name)
    except AttributeError:
        pass
    try:
        _bump(db, CG_GROUP, instr.group.name)
    except AttributeError:
        pass

    # Register operand sampling — only the slots the instr actually uses.
    has_rs1 = getattr(instr, "has_rs1", False)
    has_rs2 = getattr(instr, "has_rs2", False)
    has_rd = getattr(instr, "has_rd", False)
    rs1_val = getattr(instr, "rs1", None) if has_rs1 else None
    rs2_val = getattr(instr, "rs2", None) if has_rs2 else None
    rd_val = getattr(instr, "rd", None) if has_rd else None
    if isinstance(rs1_val, RiscvReg):
        _bump(db, CG_RS1, rs1_val.name)
    if isinstance(rs2_val, RiscvReg):
        _bump(db, CG_RS2, rs2_val.name)
    if isinstance(rd_val, RiscvReg):
        _bump(db, CG_RD, rd_val.name)

    # rs1==rs2: a surprisingly common pipeline-interesting case (e.g.
    # "add x5, x5, x5" doubles x5; branches on rs1==rs2 always take/
    # fall-through in a deterministic way). Only bump for R/B formats
    # where both reads are meaningful.
    if isinstance(rs1_val, RiscvReg) and isinstance(rs2_val, RiscvReg):
        _bump(db, CG_RS1_EQ_RS2, "equal" if rs1_val == rs2_val else "distinct")
        # Full rs1 × rs2 cross: ~1024 possible bins on a reg-file access.
        # Worth tracking because port conflicts / forwarding paths often
        # depend on the specific pair.
        _bump(db, CG_RS1_RS2_CROSS, f"{rs1_val.name}__{rs2_val.name}")
    if isinstance(rs1_val, RiscvReg) and isinstance(rd_val, RiscvReg):
        _bump(db, CG_RS1_EQ_RD, "equal" if rs1_val == rd_val else "distinct")
        _bump(db, CG_RD_RS1_CROSS, f"{rd_val.name}__{rs1_val.name}")

    # op_comb — special-register usage (sp/ra/gp/zero) and triple-equality.
    for bn in _op_comb_bins(instr):
        _bump(db, CG_OP_COMB, bn)

    # Immediate sign (only if the instr actually has one and it was
    # randomized — branches resolved to label refs skip here since they
    # don't carry a meaningful signed immediate).
    has_imm = getattr(instr, "has_imm", False)
    imm_len = getattr(instr, "imm_len", 0)
    if has_imm and imm_len:
        _bump(db, CG_IMM_SIGN, _imm_sign_bin(instr.imm, imm_len))
        _bump(db, CG_IMM_EXT, _imm_range_bin(instr.imm, imm_len))

    # Load/store width + memory alignment samplers (static — we know the
    # offset the emitter chose, which is what GCC will ultimately feed spike).
    width_bin = _load_store_width_bin(instr.instr_name)
    off = 0  # exposed below for offset / cache-line / page-cross sampling
    if width_bin is not None:
        _bump(db, CG_LS_WIDTH, width_bin)
        try:
            off = int(instr.imm_str) if instr.imm_str.lstrip('-').isdigit() else int(instr.imm)
        except (AttributeError, ValueError):
            off = int(getattr(instr, "imm", 0))
        align_bin = _mem_align_bin(off, instr.instr_name)
        if align_bin is not None:
            _bump(db, CG_MEM_ALIGN, align_bin)

    # CSR — CsrInstr subclasses carry a 12-bit csr addr; decode via enum.
    if isinstance(instr, CsrInstr):
        csr_addr = int(getattr(instr, "csr", 0)) & 0xFFF
        csr_name = _PRIV_REG_BY_ADDR.get(csr_addr, f"CSR_{csr_addr:03X}")
        _bump(db, CG_CSR, csr_name)
        # CSR access-type — read (CSRRS/CSRRC with rs1=x0; CSRRSI/CSRRCI with
        # imm==0), write (CSRRW / CSRRWI always; CSRRS/CSRRC when effective
        # operand is nonzero). We conservatively treat CSRRS/C as writes
        # unless we can prove rs1==x0 / imm==0. CSRRWI always writes.
        name = instr.instr_name
        write_ops = (RiscvInstrName.CSRRW, RiscvInstrName.CSRRWI)
        clearset_ops = (RiscvInstrName.CSRRS, RiscvInstrName.CSRRC,
                         RiscvInstrName.CSRRSI, RiscvInstrName.CSRRCI)
        if name in write_ops:
            access = "write"
        elif name in clearset_ops:
            # Read-only if operand is zero.
            if name in (RiscvInstrName.CSRRS, RiscvInstrName.CSRRC):
                access = "read" if getattr(instr, "rs1", None) == RiscvReg.ZERO else "write"
            else:
                access = "read" if getattr(instr, "imm", 0) == 0 else "write"
        else:
            access = "read"
        _bump(db, CG_CSR_ACCESS, f"{csr_name}__{access}")
        # Read-only covergroup — useful when csr_value_cg / csr_access_cg
        # are filtered to writes-only (e.g. when the user runs with
        # +include_write_reg restricted) but we still want to know which
        # CSRs the test actually read.
        if access == "read":
            _bump(db, CG_CSR_READ, csr_name)

    # Load/store offset magnitude + cache-line + page-cross — all use the
    # ``off`` already computed above for the alignment classifier.
    if width_bin is not None:
        if off == 0:
            off_bin = "zero"
        elif off > 0:
            off_bin = "pos_small" if off < 128 else ("pos_medium" if off < 1024 else "pos_large")
        else:
            off_bin = "neg_small" if off > -128 else ("neg_medium" if off > -1024 else "neg_large")
        _bump(db, CG_LS_OFFSET, off_bin)

        # Cache-line + page-crossing — approximated against the stream's
        # per-region base when available, else just the offset. Under-reports
        # without a base hint; never over-reports.
        access_w = _ACCESS_WIDTH_BY_BIN.get(width_bin, 1)
        base_addr = int(getattr(instr, "_stream_region_base", 0)) or 0
        eff = base_addr + off
        if access_w > 1:
            line_start = eff & ~(_CACHE_LINE_BYTES - 1)
            line_end = (eff + access_w - 1) & ~(_CACHE_LINE_BYTES - 1)
            if line_start != line_end:
                _bump(db, CG_CACHE_LINE_CROSS, f"cross_w{access_w}")
            elif (eff & (_CACHE_LINE_BYTES - 1)) >= _CACHE_LINE_BYTES - access_w:
                _bump(db, CG_CACHE_LINE_CROSS, f"near_end_w{access_w}")
            else:
                _bump(db, CG_CACHE_LINE_CROSS, f"in_line_w{access_w}")

            page_start = eff & ~(_PAGE_BYTES - 1)
            page_end = (eff + access_w - 1) & ~(_PAGE_BYTES - 1)
            if page_start != page_end:
                _bump(db, CG_PAGE_CROSS, f"cross_w{access_w}")
            else:
                _bump(db, CG_PAGE_CROSS, "in_page")

    # Directed-stream attribution: the stream's finalize() stamps a
    # "Start <stream_name>" comment on the first instr. We sample it into
    # the directed_stream covergroup so verif teams see which streams
    # actually contributed at least one instruction to the main sequence.
    comment = getattr(instr, "comment", "") or ""
    if comment.startswith("Start "):
        _bump(db, CG_STREAM, comment[len("Start "):].strip() or "unknown")

    # FP rounding mode — FloatingPointInstr carries .rm.
    rm = getattr(instr, "rm", None)
    if isinstance(rm, FRoundingMode):
        _bump(db, CG_FP_RM, rm.name)

    # FP register operands
    for slot in ("fs1", "fs2", "fs3", "fd"):
        has_slot = getattr(instr, f"has_{slot}", False)
        if not has_slot:
            continue
        reg = getattr(instr, slot, None)
        if reg is not None and hasattr(reg, "name"):
            _bump(db, CG_FPR, reg.name)

    # Vector register operands + vtype
    for slot in ("vs1", "vs2", "vs3", "vd"):
        has_slot = getattr(instr, f"has_{slot}", False)
        if not has_slot:
            continue
        reg = getattr(instr, slot, None)
        if reg is not None and hasattr(reg, "name"):
            _bump(db, CG_VREG, reg.name)

    # vtype sampling — when the caller passed a vector_cfg and this instr
    # came from the RVV group, record the active (SEW, LMUL) combination.
    if vector_cfg is not None:
        try:
            if instr.group == RiscvInstrGroup.RVV:
                sew = vector_cfg.vtype.vsew
                lmul = vector_cfg.vtype.vlmul
                frac = vector_cfg.vtype.fractional_lmul
                lmul_tag = f"MF{lmul}" if frac and lmul > 1 else f"M{lmul}"
                _bump(db, CG_VTYPE_DYN, f"SEW{sew}_{lmul_tag}")

                # Vector-specific covergroups — only meaningful for RVV ops.
                _sample_vector(db, instr, vector_cfg)
        except AttributeError:
            pass

    # Crosses
    try:
        _bump(db, CG_FMT_X_CAT, f"{instr.format.name}__{instr.category.name}")
    except AttributeError:
        pass
    try:
        _bump(db, CG_CAT_X_GRP, f"{instr.category.name}__{instr.group.name}")
    except AttributeError:
        pass

    # Microarchitectural — branch-distance bucket (static).
    _sample_branch_distance(db, instr)

    # Modern checkbox extensions — Zicond / Zicbo* / Zihint* / Zimop / Zcmop.
    # _modern_ext_bin returns None for non-modern ops, so the conditional
    # gates the bump.
    me_bin = _modern_ext_bin(instr.instr_name)
    if me_bin is not None:
        _bump(db, CG_MODERN_EXT, me_bin)

    # Fence — pred/succ encoding pattern.
    if instr.instr_name == RiscvInstrName.FENCE:
        # The convert2asm path emits a bare "fence" (defaulting to rw,rw)
        # but the underlying Instr may carry an explicit imm — sample it
        # either way. SV's pred/succ are bits 27..20 of the encoded
        # instruction, which we expose via the Instr's .imm field for
        # FENCE-format ops.
        fimm = int(getattr(instr, "imm", 0xFF))
        # Default GCC-emitted "fence" is fence rw,rw → 0xFF.
        if fimm == 0:
            fimm = 0xFF
        _bump(db, CG_FENCE, _fence_pat_bin(fimm))

    # ---- Sprint-2 static samplers ----

    # AMO ordering bits + per-op cross. AMO/LR/SC instructions carry
    # ``aq`` and ``rl`` flags from the AMO base class.
    aq = getattr(instr, "aq", None)
    rl = getattr(instr, "rl", None)
    if aq is not None and rl is not None:
        ordering = _amo_aqrl_bin(bool(aq), bool(rl))
        _bump(db, CG_AMO_AQRL, ordering)
        ow = _amo_op_width_split(instr.instr_name)
        if ow is not None:
            op_family, width = ow
            _bump(db, CG_AMO_OP_WIDTH, f"{op_family}_{width}")
            _bump(db, CG_AMO_OP_X_AQRL, f"{op_family}_{width}__{ordering}")

    # FP semantic-op classes — collapse 100+ FP mnemonics into 13 ops.
    fp_op = _fp_op_class(instr.instr_name)
    if fp_op is not None:
        _bump(db, CG_FP_OP, fp_op)
        if isinstance(rm, FRoundingMode):
            _bump(db, CG_FP_RM_OP_CROSS, f"{rm.name}__{fp_op}")
        prec = _fp_precision(instr.instr_name)
        if prec is not None:
            _bump(db, CG_FP_PREC_OP, f"{prec}__{fp_op}")

    # Bitmanip semantic op.
    bm_op = _bitmanip_op_class(instr.instr_name)
    if bm_op is not None:
        _bump(db, CG_BMANIP_OP, bm_op)

    # RAS classification (call / return / coroutine / computed / tail).
    ras = _ras_class(instr.instr_name, rs1_val if isinstance(rs1_val, RiscvReg) else None,
                     rd_val if isinstance(rd_val, RiscvReg) else None)
    if ras is not None:
        _bump(db, CG_RAS, ras)
        # JALR target class — record the ABI class of rs1.
        if instr.instr_name == RiscvInstrName.JALR and isinstance(rs1_val, RiscvReg):
            _bump(db, CG_JALR_TARGET, _REG_CLASS.get(rs1_val, "other"))

    # Compressed-imm corner.
    if has_imm and instr.instr_name.name.startswith("C_"):
        ci = _c_imm_corner_bin(instr.instr_name, int(getattr(instr, "imm", 0)))
        if ci is not None:
            _bump(db, CG_C_IMM_CORNER, ci)

    # Shamt-boundary corner (static — for shift / rotate instructions
    # whose imm is the shift amount).
    if has_imm and (instr.instr_name in _SHIFT_INSTR_NAMES
                     or instr.instr_name.name.startswith(("ROLI", "RORI"))):
        sh = int(getattr(instr, "imm", 0)) & 0x7F
        sb = _shamt_corner_bin(instr.instr_name, sh, xlen=64)
        if sb is not None:
            _bump(db, CG_SHAMT_CORNER, sb)

    # vsetvl flavor + AVL-path classifier (static — sampled when the
    # generator emits a vsetvl{,i} / vsetivli).
    if instr.instr_name.name in ("VSETVL", "VSETVLI", "VSETIVLI"):
        rs1_for = rs1_val if isinstance(rs1_val, RiscvReg) else None
        rd_for = rd_val if isinstance(rd_val, RiscvReg) else None
        vf = _vsetvl_flavor(instr.instr_name, rd_for, rs1_for)
        if vf is not None:
            _bump(db, CG_VSETVL_AVL, vf)

    # Walking-ones / -zeros / alternating-pattern coverage on the
    # static immediate field (riscv-isac CGF abstract-bin functions).
    # Sampled per-imm only for ops whose imm is a value (not a label /
    # offset). Branches and JAL/JALR carry an offset, not a value, so
    # we exclude those — but I/U-format ALU ops' imms are values worth
    # bit-position coverage.
    if has_imm and instr.instr_name in (
            RiscvInstrName.ADDI, RiscvInstrName.ANDI, RiscvInstrName.ORI,
            RiscvInstrName.XORI, RiscvInstrName.SLTI, RiscvInstrName.SLTIU,
            RiscvInstrName.LUI, RiscvInstrName.AUIPC,
            RiscvInstrName.ADDIW):
        imm_value = int(getattr(instr, "imm", 0))
        for bn in _walking_ones_bins(imm_value, xlen=32):
            _bump(db, CG_WALKING_ONES, bn)
        for bn in _walking_zeros_bins(imm_value, xlen=32):
            _bump(db, CG_WALKING_ZEROS, bn)
        ap = _alternate_bin(imm_value, xlen=32)
        if ap is not None:
            _bump(db, CG_ALTERNATE, ap)
        for bn in _leading_trailing_bins(imm_value, xlen=32):
            _bump(db, CG_LEAD_TRAIL, bn)


# ---------------------------------------------------------------------------
# Sequence sampler — hazard detection
# ---------------------------------------------------------------------------


def sample_sequence(db: CoverageDB, seq: Iterable[Instr], *, vector_cfg=None) -> None:
    """Sample every instruction in ``seq`` plus inter-instruction hazards.

    Hazard detection looks at *register* dependencies only:

    - RAW (Read-After-Write): instr N reads a register written by instr ≤ N-1.
    - WAR (Write-After-Read): instr N writes a register read by instr ≤ N-1.
    - WAW (Write-After-Write): instr N writes a register written by instr ≤ N-1.

    "≤ N-1" is a sliding window of ``HAZARD_WINDOW`` instructions — beyond
    that, the register is effectively retired for hazard-counting purposes.

    When ``vector_cfg`` is provided it's forwarded to each per-instruction
    sample so vector ops can tag :data:`CG_VTYPE_DYN` bins.
    """
    last_writer_at: dict[RiscvReg, int] = {}
    last_reader_at: dict[RiscvReg, int] = {}
    prev_category: str | None = None
    prev_opcode: str | None = None
    # LR/SC pattern tracker. State tracks the last LR seen and how many
    # non-LR/SC instructions have followed it (the "intervening" count).
    last_lr_idx: int | None = None
    intervening_since_lr: int = 0
    seen_lr: bool = False
    seen_sc: bool = False
    # Sprint-2 — multi-cycle / load producer trackers.
    # ``producer_at[reg] = (idx, kind)`` where kind ∈ {"load", "mc", "alu"}.
    producer_at: dict[RiscvReg, tuple[int, str]] = {}
    # Track which loads / multi-cycle producers have been "consumed" so we
    # can credit no-use bins at end of sequence.
    load_consumers: set[int] = set()
    mc_consumers: set[int] = set()
    load_emitted: set[int] = set()
    mc_emitted: set[int] = set()
    # Branch-shadow tracker — set when previous instr was a branch, so
    # the next iteration can credit one shadow_<category> bin.
    prev_was_branch_idx: int | None = None
    # Static memory-aliasing window: ``mem_window`` records the last few
    # (base_reg, offset, op_kind) triples; aliasing is sampled when the
    # current instr's base reg matches a recent entry.
    mem_window: deque = deque(maxlen=8)
    # vtype transition state. The boot-time vsetvli sets the initial vtype;
    # each subsequent vsetvli observed in the stream becomes a "new vtype"
    # and we sample the (prev → new) transitions.
    prev_vtype: tuple[int, int, bool] | None = None
    if vector_cfg is not None:
        prev_vtype = (
            vector_cfg.vtype.vsew,
            vector_cfg.vtype.vlmul,
            vector_cfg.vtype.fractional_lmul,
        )

    for idx, instr in enumerate(seq):
        sample_instr(db, instr, vector_cfg=vector_cfg)

        # LR/SC pattern detection. Looks at instr_name to decide; bins
        # are sampled at LR-then-SC closure or at SC-without-LR.
        try:
            iname = instr.instr_name
        except AttributeError:
            iname = None
        if iname in (RiscvInstrName.LR_W, getattr(RiscvInstrName, "LR_D", None)):
            if last_lr_idx is not None:
                _bump(db, CG_LR_SC_PATTERN, "nested_lr")
            last_lr_idx = idx
            intervening_since_lr = 0
            seen_lr = True
        elif iname in (RiscvInstrName.SC_W, getattr(RiscvInstrName, "SC_D", None)):
            if last_lr_idx is None:
                _bump(db, CG_LR_SC_PATTERN, "unpaired_sc")
            elif intervening_since_lr == 0:
                _bump(db, CG_LR_SC_PATTERN, "paired")
            else:
                _bump(db, CG_LR_SC_PATTERN, "lr_with_intervening_op")
            last_lr_idx = None
            intervening_since_lr = 0
            seen_sc = True
        elif last_lr_idx is not None:
            intervening_since_lr += 1

        # vstart-corner pseudo carries _vstart_value — bin it.
        if hasattr(instr, "_vstart_value"):
            v = int(instr._vstart_value)
            if v == 0:
                bin_name = "zero"
            elif v == 1:
                bin_name = "one"
            elif v <= 4:
                bin_name = "small"
            elif v <= 16:
                bin_name = "mid"
            else:
                bin_name = "high"
            _bump(db, CG_VEC_VSTART, bin_name)

        # vsetvli emitted by the vsetvli-stress stream carries the new
        # SEW/LMUL/fractional/TA/MA as Python attrs (not real instr_name
        # because it's a pseudo). Match by class name to keep this hook
        # local to the streams module.
        if type(instr).__name__ == "_VsetvliPseudo" and vector_cfg is not None:
            new_sew = getattr(instr, "_sew", None)
            new_lmul = getattr(instr, "_lmul", None)
            new_frac = getattr(instr, "_fractional", False)
            if new_sew and new_lmul:
                lmul_tag_new = f"MF{new_lmul}" if new_frac and new_lmul > 1 else f"M{new_lmul}"
                if prev_vtype is not None:
                    p_sew, p_lmul, p_frac = prev_vtype
                    p_lmul_tag = f"MF{p_lmul}" if p_frac and p_lmul > 1 else f"M{p_lmul}"
                    _bump(db, CG_VEC_SEW_TRANS,
                          f"SEW{p_sew}__SEW{new_sew}")
                    _bump(db, CG_VEC_LMUL_TRANS,
                          f"{p_lmul_tag}__{lmul_tag_new}")
                    _bump(db, CG_VEC_VTYPE_TRANS,
                          f"SEW{p_sew}_{p_lmul_tag}__SEW{new_sew}_{lmul_tag_new}")
                prev_vtype = (new_sew, new_lmul, bool(new_frac))

        # Category + opcode transitions — valuable for finding sequencing
        # bugs (e.g. LOAD immediately after BRANCH is a stall on some pipes).
        try:
            cur_cat = instr.category.name
            if prev_category is not None:
                _bump(db, CG_CAT_TRANS, f"{prev_category}__{cur_cat}")
            prev_category = cur_cat
        except AttributeError:
            prev_category = None
        try:
            cur_op = instr.instr_name.name
            if prev_opcode is not None:
                _bump(db, CG_OP_TRANS, f"{prev_opcode}__{cur_op}")
            prev_opcode = cur_op
        except AttributeError:
            prev_opcode = None

        # Branch-shadow — the *previous* instr was a branch, so this
        # instr's category is the branch shadow.
        if prev_was_branch_idx is not None and prev_was_branch_idx == idx - 1:
            try:
                _bump(db, CG_BRANCH_SHADOW, f"shadow_{instr.category.name}")
            except AttributeError:
                pass
            prev_was_branch_idx = None

        # Collect the regs this instr reads/writes.
        reads: set[RiscvReg] = set()
        writes: set[RiscvReg] = set()
        for slot in ("rs1", "rs2"):
            if getattr(instr, f"has_{slot}", False):
                r = getattr(instr, slot, None)
                if isinstance(r, RiscvReg):
                    reads.add(r)
        if getattr(instr, "has_rd", False):
            r = getattr(instr, "rd", None)
            if isinstance(r, RiscvReg) and r != RiscvReg.ZERO:
                writes.add(r)

        # Producer / consumer tracking for load-use & multi-cycle-use bins.
        try:
            iname2 = instr.instr_name
        except AttributeError:
            iname2 = None
        if iname2 is not None:
            # Did any of this instr's reads consume a tracked producer?
            for r in reads:
                if r in producer_at:
                    p_idx, kind = producer_at[r]
                    distance = idx - p_idx
                    if kind == "load" and distance >= 1:
                        _bump(db, CG_LOAD_USE, _hazard_dist_bin(distance)
                              .replace("dist", "load_use"))
                        load_consumers.add(p_idx)
                    elif kind == "mc" and distance >= 1:
                        _bump(db, CG_MC_USE, _hazard_dist_bin(distance)
                              .replace("dist", "mc_use"))
                        mc_consumers.add(p_idx)
            # Tag this instr if it's a producer kind.
            if iname2 in _LOAD_INSTR_NAMES:
                for r in writes:
                    producer_at[r] = (idx, "load")
                load_emitted.add(idx)
            elif _is_multicycle_producer(iname2):
                for r in writes:
                    producer_at[r] = (idx, "mc")
                mc_emitted.add(idx)
            else:
                # ALU op — refresh producer record so we don't credit
                # load-use distance against an intermediate ALU result.
                for r in writes:
                    producer_at[r] = (idx, "alu")
            # Branch-shadow flag for next iteration.
            if iname2 in _BRANCH_INSTR_NAMES or iname2 in (
                    RiscvInstrName.JAL, RiscvInstrName.JALR,
                    RiscvInstrName.C_J, getattr(RiscvInstrName, "C_JAL", None),
                    RiscvInstrName.C_JR, RiscvInstrName.C_JALR):
                prev_was_branch_idx = idx
            # Static memory-alias window — sample when the current
            # instr is a load/store with a base reg.
            width_bin_seq = _load_store_width_bin(iname2)
            if width_bin_seq is not None and getattr(instr, "has_rs1", False):
                base = getattr(instr, "rs1", None)
                if isinstance(base, RiscvReg):
                    try:
                        off_seq = int(getattr(instr, "imm", 0))
                    except (TypeError, ValueError):
                        off_seq = 0
                    op_kind = "load" if iname2 in _LOAD_INSTR_NAMES else "store"
                    # Detect alias against window.
                    aliased = False
                    for prev_base, prev_off, prev_kind in mem_window:
                        if prev_base == base:
                            if prev_off == off_seq:
                                tag = f"{prev_kind}_then_{op_kind}_same_addr"
                            else:
                                tag = f"{prev_kind}_then_{op_kind}_same_base_diff_off"
                            _bump(db, CG_MEM_ALIAS, tag)
                            aliased = True
                            break
                    if not aliased:
                        _bump(db, CG_MEM_ALIAS, "no_alias_in_window")
                    mem_window.append((base, off_seq, op_kind))

        hazard_found = False
        window_start = idx - HAZARD_WINDOW
        # RAW: one of our reads was recently written.
        for r in reads:
            if r == RiscvReg.ZERO:
                continue
            if r in last_writer_at:
                w_at = last_writer_at[r]
                if w_at >= window_start and w_at < idx:
                    _bump(db, CG_HAZARD, "raw")
                    # Distance bin (cycles between producer and consumer).
                    _bump(db, CG_HAZARD_DIST, _hazard_dist_bin(idx - w_at))
                    hazard_found = True
                    break
        # WAW: one of our writes was recently written.
        if not hazard_found:
            for r in writes:
                if r in last_writer_at:
                    w_at = last_writer_at[r]
                    if w_at >= window_start and w_at < idx:
                        _bump(db, CG_HAZARD, "waw")
                        hazard_found = True
                        break
        # WAR: one of our writes was recently read.
        if not hazard_found:
            for r in writes:
                if r in last_reader_at:
                    r_at = last_reader_at[r]
                    if r_at >= window_start and r_at < idx:
                        _bump(db, CG_HAZARD, "war")
                        hazard_found = True
                        break

        if not hazard_found:
            _bump(db, CG_HAZARD, "none")

        for r in reads:
            last_reader_at[r] = idx
        for r in writes:
            last_writer_at[r] = idx

    # End-of-sequence LR/SC closure. Sequences that contain an LR but
    # never paired it with an SC (and never reached `nested_lr`) score
    # an "lr_only" bin so the user knows the SC arm is missing.
    if last_lr_idx is not None:
        _bump(db, CG_LR_SC_PATTERN, "lr_only")
    # If the sequence had only SCs that never paired (all flagged
    # `unpaired_sc` already) the bin is preserved by the per-instr loop.

    # Credit no-use bins for unconsumed producers. Each emitted load /
    # multi-cycle producer that never had a downstream consumer reads
    # its result registers a "no_use" bin — quantifies how often the
    # generator emits dead loads / dead long-latency ops.
    for p_idx in load_emitted - load_consumers:
        _bump(db, CG_LOAD_USE, "load_no_use")
    for p_idx in mc_emitted - mc_consumers:
        _bump(db, CG_MC_USE, "mc_no_use")


HAZARD_WINDOW = 8  # sliding-window size (in instructions) for hazard counting


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge(dst: CoverageDB, src: CoverageDB) -> CoverageDB:
    """Merge ``src`` into ``dst`` by bin-wise addition (returns ``dst``).

    Missing covergroups or bins in ``dst`` are created on the fly.
    """
    for cg, bins in src.items():
        dst_bins = dst.setdefault(cg, {})
        for bn, cnt in bins.items():
            dst_bins[bn] = dst_bins.get(bn, 0) + cnt
    return dst


def clone(db: CoverageDB) -> CoverageDB:
    return copy.deepcopy(db)


# ---------------------------------------------------------------------------
# CSR address → enum-name table (module-local — avoids repeat lookups)
# ---------------------------------------------------------------------------


def _build_priv_reg_by_addr() -> dict[int, str]:
    return {int(pr): pr.name for pr in PrivilegedReg}


_PRIV_REG_BY_ADDR: dict[int, str] = _build_priv_reg_by_addr()
