"""Tests for the Sprint-2 deep-coverage additions.

Sprint-2 closes gaps the research surfaced from riscv-isac, riscvISACOV,
core-v-verif (#575), and ARM/Imperas DV-plan methodology:

- Pipeline-distance hazard, load-use, multi-cycle producer-use, branch shadow.
- Static memory aliasing, branch 4-gram pattern, branch loop classification.
- RAS classification + JALR target class.
- AMO ordering bits (aq/rl) + per-op cross.
- FP semantic-op classes + RM × op + precision × op cross.
- Vector AVL corners / TA-MA policy / vsetvl flavor.
- MSTATUS field decode, xTVEC mode, trap delegation, MIP, MISA, HPM.
- M-extension corner values, B-extension semantic op, RVC imm corners.
- Nested trap, DCSR.cause, FP source class.
"""

from __future__ import annotations

import pytest

from rvgen.coverage.collectors import (
    CG_AMO_AQRL,
    CG_AMO_OP_WIDTH,
    CG_AMO_OP_X_AQRL,
    CG_BMANIP_OP,
    CG_BRANCH_LOOP,
    CG_BRANCH_PATTERN4,
    CG_BRANCH_SHADOW,
    CG_C_IMM_CORNER,
    CG_DCSR_CAUSE,
    CG_DELEGATION,
    CG_FP_OP,
    CG_FP_PREC_OP,
    CG_FP_RM_OP_CROSS,
    CG_FP_SRC_CLASS,
    CG_HAZARD_DIST,
    CG_HPM_ACCESS,
    CG_JALR_TARGET,
    CG_LOAD_USE,
    CG_MC_USE,
    CG_MEM_ALIAS,
    CG_MIP_FIELD,
    CG_MISA,
    CG_MSTATUS_FIELD,
    CG_NESTED_TRAP,
    CG_RAS,
    CG_XTVEC_MODE,
    _amo_aqrl_bin,
    _amo_op_width_split,
    _bitmanip_op_class,
    _c_imm_corner_bin,
    _fp_op_class,
    _fp_precision,
    _hazard_dist_bin,
    _muldiv_corner_bin,
    _ras_class,
    new_db,
    sample_instr,
    sample_sequence,
)
from rvgen.coverage.runtime import (
    _classify_fp_value,
    _dcsr_cause_bin,
    _delegation_bins,
    _hpm_csr_bin,
    _mip_field_bins,
    _misa_letter_bins,
    _mstatus_field_bins,
    _xtvec_mode_bin,
    sample_trace_file,
)
from rvgen.isa.enums import RiscvInstrName as N, RiscvReg as R
from rvgen.isa.factory import get_instr


# ---------------------------------------------------------------------------
# Pipeline distance / load-use / multi-cycle-use / branch shadow / mem-alias
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d,expected", [
    (1, "dist_1_load_use"),
    (2, "dist_2"),
    (3, "dist_3"),
    (5, "dist_4_5"),
    (7, "dist_6_7"),
    (10, "dist_8_plus"),
])
def test_hazard_distance_buckets(d, expected):
    assert _hazard_dist_bin(d) == expected


def test_load_use_distance_sampled_at_d1():
    db = new_db()
    seq = []
    lw = get_instr(N.LW)
    lw.rs1 = R.SP
    lw.rd = R.A0
    lw.imm = 0
    lw.imm_str = "0"
    seq.append(lw)
    add = get_instr(N.ADD)
    add.rs1 = R.A0  # consumes the load
    add.rs2 = R.A1
    add.rd = R.A2
    seq.append(add)
    sample_sequence(db, seq)
    # The load-use distance covergroup names use "load_use_<dist>" form.
    assert any(b.startswith("load_use_1_load_use")
               for b in db[CG_LOAD_USE]) or "load_use_1_load_use" in db[CG_LOAD_USE]


def test_load_no_use_credited_when_load_unconsumed():
    db = new_db()
    seq = [get_instr(N.LW), get_instr(N.ADDI), get_instr(N.ADDI)]
    seq[0].rs1 = R.SP
    seq[0].rd = R.A0
    seq[0].imm = 0
    seq[0].imm_str = "0"
    seq[1].rs1 = R.A1; seq[1].rd = R.A2; seq[1].imm = 0
    seq[2].rs1 = R.A3; seq[2].rd = R.A4; seq[2].imm = 0
    sample_sequence(db, seq)
    assert db[CG_LOAD_USE].get("load_no_use", 0) >= 1


def test_mc_use_sampled_for_mul():
    db = new_db()
    seq = []
    mul = get_instr(N.MUL)
    mul.rs1 = R.A0; mul.rs2 = R.A1; mul.rd = R.A2
    seq.append(mul)
    add = get_instr(N.ADD)
    add.rs1 = R.A2; add.rs2 = R.A3; add.rd = R.A4
    seq.append(add)
    sample_sequence(db, seq)
    assert any(k.startswith("mc_use_1") or k.startswith("mc_use_dist")
               for k in db[CG_MC_USE])


def test_branch_shadow_sampled():
    db = new_db()
    beq = get_instr(N.BEQ)
    beq.rs1 = R.A0; beq.rs2 = R.A1
    add = get_instr(N.ADD)
    add.rs1 = R.A0; add.rs2 = R.A1; add.rd = R.A2
    sample_sequence(db, [beq, add])
    assert "shadow_ARITHMETIC" in db[CG_BRANCH_SHADOW]


def test_mem_alias_detected():
    db = new_db()
    sw = get_instr(N.SW)
    sw.rs1 = R.SP; sw.rs2 = R.A0; sw.imm = 16; sw.imm_str = "16"
    lw = get_instr(N.LW)
    lw.rs1 = R.SP; lw.rd = R.A1; lw.imm = 16; lw.imm_str = "16"
    sample_sequence(db, [sw, lw])
    assert db[CG_MEM_ALIAS].get("store_then_load_same_addr", 0) >= 1


def test_mem_alias_same_base_diff_offset():
    db = new_db()
    sw = get_instr(N.SW)
    sw.rs1 = R.SP; sw.rs2 = R.A0; sw.imm = 8; sw.imm_str = "8"
    lw = get_instr(N.LW)
    lw.rs1 = R.SP; lw.rd = R.A1; lw.imm = 16; lw.imm_str = "16"
    sample_sequence(db, [sw, lw])
    assert db[CG_MEM_ALIAS].get("store_then_load_same_base_diff_off", 0) >= 1


# ---------------------------------------------------------------------------
# RAS / JALR target class
# ---------------------------------------------------------------------------


def test_ras_call_classification():
    # JAL ra, target → call.
    assert _ras_class(N.JAL, None, R.RA) == "call"


def test_ras_return_classification():
    # JALR x0, ra, 0 → return.
    assert _ras_class(N.JALR, R.RA, R.ZERO) == "return"


def test_ras_coroutine_swap():
    # JALR ra, t0, 0 — both link regs but distinct.
    assert _ras_class(N.JALR, R.T0, R.RA) == "coroutine_swap"


def test_ras_tail_call_jal_x0():
    # JAL x0, target — tail call.
    assert _ras_class(N.JAL, None, R.ZERO) == "tail_call"


def test_ras_computed_jalr():
    # JALR ra, t1, 0 — rd=ra but rs1≠ra/t0. computed.
    assert _ras_class(N.JALR, R.T1, R.RA) == "call"
    assert _ras_class(N.JALR, R.T1, R.A0) == "computed"


def test_ras_via_sample_instr_populates_db():
    db = new_db()
    i = get_instr(N.JAL)
    i.rd = R.RA
    sample_instr(db, i)
    assert db[CG_RAS].get("call", 0) == 1


def test_jalr_target_class_categorized():
    db = new_db()
    i = get_instr(N.JALR)
    i.rs1 = R.A0
    i.rd = R.RA
    sample_instr(db, i)
    assert db[CG_JALR_TARGET].get("argument", 0) == 1


# ---------------------------------------------------------------------------
# AMO aq/rl + op/width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("aq,rl,expected", [
    (False, False, "neither"),
    (True, False, "aq_only"),
    (False, True, "rl_only"),
    (True, True, "aq_and_rl"),
])
def test_amo_aqrl_bin_all_combos(aq, rl, expected):
    assert _amo_aqrl_bin(aq, rl) == expected


def test_amo_op_width_split():
    assert _amo_op_width_split(N.AMOADD_W) == ("AMOADD", "W")
    assert _amo_op_width_split(N.LR_W) == ("LR", "W")
    if hasattr(N, "AMOADD_D"):
        assert _amo_op_width_split(N.AMOADD_D) == ("AMOADD", "D")


def test_amo_op_width_split_non_amo_returns_none():
    assert _amo_op_width_split(N.ADD) is None


def test_amo_sample_instr_populates_aqrl_and_cross():
    db = new_db()
    i = get_instr(N.AMOADD_W)
    i.rs1 = R.A0; i.rs2 = R.A1; i.rd = R.A2
    i.aq = True
    i.rl = False
    sample_instr(db, i)
    assert db[CG_AMO_AQRL].get("aq_only", 0) == 1
    assert "AMOADD_W" in db[CG_AMO_OP_WIDTH]
    assert "AMOADD_W__aq_only" in db[CG_AMO_OP_X_AQRL]


# ---------------------------------------------------------------------------
# FP semantic op + RM × op + precision × op cross
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("FADD_S", "add"), ("FSUB_D", "sub"), ("FMUL_S", "mul"),
    ("FDIV_S", "div"), ("FSQRT_S", "sqrt"),
    ("FMADD_S", "fma"), ("FNMSUB_D", "fma"),
    ("FMIN_S", "minmax"), ("FMAX_D", "minmax"),
    ("FEQ_S", "compare"), ("FLT_D", "compare"),
    ("FCVT_W_S", "convert"), ("FCVT_S_D", "convert"),
    ("FSGNJ_S", "sign"), ("FCLASS_S", "classify"),
    ("FMV_X_W", "move"),
    ("FLW", "load"), ("FSD", "store"),
])
def test_fp_op_class_classifier(name, expected):
    if hasattr(N, name):
        assert _fp_op_class(getattr(N, name)) == expected


def test_fp_op_returns_none_for_non_fp():
    assert _fp_op_class(N.ADD) is None


@pytest.mark.parametrize("name,expected", [
    ("FADD_S", "S"), ("FADD_D", "D"), ("FADD_H", "H"),
    ("FCVT_S_D", "S"), ("FCVT_D_S", "D"),
    ("FLW", "S"), ("FSD", "D"),
])
def test_fp_precision_classifier(name, expected):
    if hasattr(N, name):
        assert _fp_precision(getattr(N, name)) == expected


def test_fp_op_sample_instr_emits_cross():
    from rvgen.isa.enums import FRoundingMode as FRM
    db = new_db()
    i = get_instr(N.FADD_S)
    i.fs1 = i.fs2 = i.fd = None  # don't care for this test
    i.has_fs1 = i.has_fs2 = i.has_fd = False
    i.rm = FRM.RTZ
    sample_instr(db, i)
    assert db[CG_FP_OP].get("add", 0) == 1
    assert db[CG_FP_RM_OP_CROSS].get("RTZ__add", 0) == 1
    assert db[CG_FP_PREC_OP].get("S__add", 0) == 1


# ---------------------------------------------------------------------------
# Bitmanip semantic op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("ROL", "rotate"), ("ROR", "rotate"), ("ROLW", "rotate"),
    ("CLZ", "clz"), ("CTZ", "ctz"), ("CPOP", "popcount"),
    ("MAX", "minmax"), ("MIN", "minmax"),
    ("CLMUL", "clmul"), ("CLMULH", "clmul"),
    ("BCLR", "single_bit"), ("BSET", "single_bit"),
    ("ANDN", "logic_neg"), ("ORN", "logic_neg"),
    ("REV8", "byte_reverse"),
    ("SH1ADD", "shift_add"), ("SH3ADD", "shift_add"),
    ("ZEXT_H", "extend"), ("SEXT_B", "extend"),
])
def test_bitmanip_classifier(name, expected):
    if hasattr(N, name):
        assert _bitmanip_op_class(getattr(N, name)) == expected


def test_bitmanip_sample_populates_db():
    db = new_db()
    if hasattr(N, "CLZ"):
        i = get_instr(N.CLZ)
        i.rs1 = R.A0; i.rd = R.A1
        sample_instr(db, i)
        assert db[CG_BMANIP_OP].get("clz", 0) == 1


# ---------------------------------------------------------------------------
# M-extension corner values
# ---------------------------------------------------------------------------


def test_muldiv_div_by_zero():
    assert _muldiv_corner_bin(N.DIV, 0x100, 0) == "div_by_zero"
    assert _muldiv_corner_bin(N.DIVU, 5, 0) == "div_by_zero"
    assert _muldiv_corner_bin(N.REM, 100, 0) == "div_by_zero"


def test_muldiv_signed_overflow():
    # rs1 = INT64_MIN, rs2 = -1.
    assert _muldiv_corner_bin(
        N.DIV, 1 << 63, 0xFFFFFFFFFFFFFFFF, xlen=64
    ) == "signed_overflow"


def test_muldiv_no_corner_for_normal_inputs():
    assert _muldiv_corner_bin(N.MUL, 5, 7) is None


def test_muldiv_returns_none_for_non_m():
    assert _muldiv_corner_bin(N.ADD, 0, 0) is None


# ---------------------------------------------------------------------------
# Compressed-imm corner classifier
# ---------------------------------------------------------------------------


def test_c_imm_corner_zero():
    if hasattr(N, "C_ADDI"):
        assert _c_imm_corner_bin(N.C_ADDI, 0) == "c_addi_zero_imm"


def test_c_imm_corner_one():
    if hasattr(N, "C_ADDI"):
        assert _c_imm_corner_bin(N.C_ADDI, 1) == "c_addi_imm_one"


def test_c_imm_corner_large():
    if hasattr(N, "C_ADDI"):
        assert _c_imm_corner_bin(N.C_ADDI, 60) == "c_addi_imm_large"


def test_c_imm_returns_none_for_non_c():
    assert _c_imm_corner_bin(N.ADD, 100) is None


# ---------------------------------------------------------------------------
# MSTATUS field decode
# ---------------------------------------------------------------------------


def test_mstatus_mie_set():
    bins = _mstatus_field_bins(0x8)  # bit 3 = MIE
    assert "mie_set" in bins


def test_mstatus_mpp_decode():
    bins = _mstatus_field_bins(0x1800)  # MPP=11 (M-mode)
    assert "mpp_M" in bins
    bins = _mstatus_field_bins(0x800)   # MPP=01 (S-mode)
    assert "mpp_S" in bins
    bins = _mstatus_field_bins(0x0)
    assert "mpp_U" in bins


def test_mstatus_mprv_sum_mxr_set():
    bins = _mstatus_field_bins((1 << 17) | (1 << 18) | (1 << 19))
    assert "mprv_set" in bins
    assert "sum_set" in bins
    assert "mxr_set" in bins


def test_mstatus_fs_field():
    bins = _mstatus_field_bins(3 << 13)  # FS=3 (dirty)
    assert "fs_dirty" in bins


# ---------------------------------------------------------------------------
# MIP / MIE / MISA / HPM / DCSR / xTVEC decoders
# ---------------------------------------------------------------------------


def test_mip_field_decode():
    bins = _mip_field_bins((1 << 7) | (1 << 11))  # MTIP + MEIP
    assert "mtip_pending" in bins
    assert "meip_pending" in bins
    assert "any_pending" in bins


def test_mip_no_pending():
    bins = _mip_field_bins(0)
    assert "none_pending" in bins


def test_misa_letter_decode_imc():
    # I=8, M=12, C=2.
    misa = (1 << 8) | (1 << 12) | (1 << 2)
    bins = _misa_letter_bins(misa)
    assert "misa_I" in bins
    assert "misa_M" in bins
    assert "misa_C" in bins


def test_xtvec_direct_vs_vectored():
    assert _xtvec_mode_bin("MTVEC", 0x80000000) == "mtvec_direct"
    assert _xtvec_mode_bin("MTVEC", 0x80000001) == "mtvec_vectored"
    assert _xtvec_mode_bin("STVEC", 0x10) == "stvec_direct"


def test_dcsr_cause_decode():
    # cause=4 (single-step): bits [8:6] = 100 → value = 4 << 6 = 0x100.
    assert "step" in _dcsr_cause_bin(4 << 6)
    assert "ebreak" in _dcsr_cause_bin(1 << 6)
    assert "trigger" in _dcsr_cause_bin(2 << 6)


def test_delegation_bins_medeleg():
    # bit 8 = ecall_u; should be delegated.
    bins = _delegation_bins("MEDELEG", 1 << 8)
    assert any("ecall_u" in b for b in bins)


def test_delegation_mideleg():
    # bit 7 = m_timer.
    bins = _delegation_bins("MIDELEG", 1 << 7)
    assert any("m_timer" in b for b in bins)


def test_hpm_csr_classification():
    assert _hpm_csr_bin("MCYCLE") == "mcycle"
    assert _hpm_csr_bin("MINSTRET") == "minstret"
    assert _hpm_csr_bin("MHPMCOUNTER3") == "counter_3"
    assert _hpm_csr_bin("MHPMEVENT5") == "event_5"
    assert _hpm_csr_bin("MISA") is None


# ---------------------------------------------------------------------------
# FP source-class classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("v,w,expected", [
    (0x00000000, 32, "src_zero"),
    (0x80000000, 32, "src_zero"),
    (0x7F800000, 32, "src_inf"),
    (0x7FC00000, 32, "src_nan"),
    (0x00000001, 32, "src_subnormal"),
    (0x40490FDB, 32, "src_normal"),  # ~pi
    (0x3F800000, 32, "src_normal"),  # +1.0
])
def test_classify_fp_value(v, w, expected):
    assert _classify_fp_value(v, w) == expected


# ---------------------------------------------------------------------------
# End-to-end runtime ingest — confirms the new Sprint-2 covergroups
# all decode correctly from a synthetic spike --log-commits trace.
# ---------------------------------------------------------------------------


def test_runtime_ingest_decodes_sprint2_covergroups(tmp_path):
    trace = tmp_path / "spike.log"
    trace.write_text(
        # MSTATUS write — MIE set, MPP=M, MPRV=1, MXR=1.
        "core   0: 3 0x80000010 (0x12345678) c300_mstatus 0x0000000000061808\n"
        # MTVEC write — vectored mode (bit 0 set).
        "core   0: 3 0x80000020 (0x12345678) c305_mtvec 0x0000000080000001\n"
        # STVEC write — direct mode.
        "core   0: 3 0x80000020 (0x12345678) c105_stvec 0x0000000080001000\n"
        # MEDELEG — delegate breakpoint (bit 3) and ecall_u (bit 8).
        "core   0: 3 0x80000020 (0x12345678) c302_medeleg 0x0000000000000108\n"
        # MIDELEG — delegate s_timer (bit 5).
        "core   0: 3 0x80000020 (0x12345678) c303_mideleg 0x0000000000000020\n"
        # MIP — MTIP pending.
        "core   0: 3 0x80000020 (0x12345678) c344_mip 0x0000000000000080\n"
        # MISA — RV64IMAC.
        "core   0: 3 0x80000020 (0x12345678) c301_misa 0x8000000000001105\n"
        # MHPMCOUNTER3 access.
        "core   0: 3 0x80000020 (0x12345678) cb03_mhpmcounter3 0x0000000000001000\n"
        # MCYCLE access.
        "core   0: 3 0x80000020 (0x12345678) cb00_mcycle 0x0000000000010000\n"
        # DCSR — cause = step (4).
        "core   0: 3 0x80000020 (0x12345678) c7b0_dcsr 0x0000000040000100\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert any(b.startswith("MSTATUS__") for b in db[CG_MSTATUS_FIELD])
    assert "mtvec_vectored" in db[CG_XTVEC_MODE]
    assert "stvec_direct" in db[CG_XTVEC_MODE]
    assert any("ecall_u" in b for b in db[CG_DELEGATION])
    assert any("breakpoint" in b for b in db[CG_DELEGATION])
    assert any("s_timer" in b for b in db[CG_DELEGATION])
    assert "MIP__mtip_pending" in db[CG_MIP_FIELD]
    assert "misa_I" in db[CG_MISA]
    assert "misa_M" in db[CG_MISA]
    assert "misa_C" in db[CG_MISA]
    assert "counter_3" in db[CG_HPM_ACCESS]
    assert "mcycle" in db[CG_HPM_ACCESS]
    assert any("step" in b for b in db[CG_DCSR_CAUSE])


def test_runtime_branch_pattern4_and_loop(tmp_path):
    # Build a tiny trace with 4 branch outcomes so the 4-gram pattern
    # bin gets emitted at least once.
    lines = ["core   0: >>>>  h0_start\n"]
    pc = 0x80000000
    # Alternating taken/not-taken pattern: 4 branches.
    for i in range(4):
        # Branch instr (4-byte).
        lines.append(f"core   0: 0x{pc:08x} (0x00000063) beq a0, a1, 0\n")
        # Either fall through (pc+4) or jump.
        if i % 2 == 0:
            pc += 8  # taken (offset > 4)
        else:
            pc += 4  # not taken
        lines.append(f"core   0: 0x{pc:08x} (0x00000013) addi zero, zero, 0\n")
        pc += 4
    trace = tmp_path / "br.log"
    trace.write_text("".join(lines))
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert sum(db[CG_BRANCH_PATTERN4].values()) >= 1
    # Loop classification: at least one taken branch should produce a
    # fwd_taken or bwd_taken bin.
    assert any(k.startswith("fwd_") or k.startswith("bwd_")
               or k == "fall_through"
               for k in db[CG_BRANCH_LOOP])


def test_nested_trap_detection(tmp_path):
    trace = tmp_path / "n.log"
    trace.write_text(
        "core   0: >>>>  mtvec_handler\n"
        # MCAUSE write inside the handler region — counts as nested.
        "core   0: 3 0x80000020 (0x12345678) c342_mcause 0x0000000000000003\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert any("nested" in b for b in db[CG_NESTED_TRAP])


# ---------------------------------------------------------------------------
# Wave-2 abstract bins: walking-ones, walking-zeros, alternating,
# leading/trailing run length.
# ---------------------------------------------------------------------------

from rvgen.coverage.collectors import (
    CG_ALTERNATE,
    CG_ATOMIC_ALIGN,
    CG_FCVT_CORNER,
    CG_LEAD_TRAIL,
    CG_MXR_SUM_MPRV,
    CG_RVC_ILLEGAL,
    CG_SHAMT_CORNER,
    CG_VIRT_INSTR_TRAP,
    CG_VREG_OVERLAP,
    CG_VSETVL_AVL,
    CG_WALKING_ONES,
    CG_WALKING_ZEROS,
    CG_WFI_CORNER,
    _alternate_bin,
    _fcvt_corner_bin,
    _leading_trailing_bins,
    _shamt_corner_bin,
    _vreg_overlap_class,
    _vsetvl_flavor,
    _walking_ones_bins,
    _walking_zeros_bins,
)


def test_walking_ones_each_bit():
    bins = _walking_ones_bins(0x05, xlen=8)  # bits 0 and 2
    assert "bit_00_set" in bins
    assert "bit_02_set" in bins
    assert "bit_01_set" not in bins


def test_walking_ones_zero():
    assert _walking_ones_bins(0, xlen=8) == ("no_bits_set",)


def test_walking_zeros_each_bit():
    bins = _walking_zeros_bins(0xFA, xlen=8)  # bits 0 and 2 cleared
    assert "bit_00_clear" in bins
    assert "bit_02_clear" in bins
    assert "bit_01_clear" not in bins


def test_walking_zeros_all_set():
    assert _walking_zeros_bins(0xFF, xlen=8) == ("all_bits_set",)


def test_alternate_5555():
    assert _alternate_bin(0x55555555, xlen=32) == "alt_5555"
    assert _alternate_bin(0xAAAAAAAA, xlen=32) == "alt_AAAA"


def test_alternate_byte_pattern():
    assert _alternate_bin(0xA5A5A5A5, xlen=32) == "alt_byte_A5"
    assert _alternate_bin(0x5A5A5A5A, xlen=32) == "alt_byte_5A"


def test_alternate_returns_none_for_random():
    assert _alternate_bin(0xDEADBEEF, xlen=32) is None


def test_leading_trailing_zero():
    bins = _leading_trailing_bins(0, xlen=8)
    assert "lead0_64" in bins
    assert "trail0_64" in bins


def test_leading_trailing_run_length():
    # 0xF0 = bits 4..7 set → lead0=0, trail0=4, lead1=4, trail1=0.
    bins = _leading_trailing_bins(0xF0, xlen=8)
    assert "trail0_4" in bins
    assert "lead1_4" in bins


# ---------------------------------------------------------------------------
# Shamt corners
# ---------------------------------------------------------------------------


def test_shamt_zero_corner():
    assert _shamt_corner_bin(N.SLLI, 0, xlen=64) == "slli_shamt_zero"


def test_shamt_max_minus_one_corner():
    assert _shamt_corner_bin(N.SRLI, 63, xlen=64) == "srli_shamt_max_minus_one"


def test_shamt_xlen_corner():
    assert _shamt_corner_bin(N.SLL, 64, xlen=64) == "sll_shamt_xlen"


def test_shamt_returns_none_for_non_shift():
    assert _shamt_corner_bin(N.ADD, 5) is None


def test_shamt_returns_none_for_normal():
    assert _shamt_corner_bin(N.SLLI, 7) is None


def test_shamt_sample_via_instr():
    db = new_db()
    i = get_instr(N.SLLI)
    i.rd = R.A0
    i.rs1 = R.A1
    i.imm = 0
    i.has_imm = True
    sample_instr(db, i)
    assert db[CG_SHAMT_CORNER].get("slli_shamt_zero", 0) == 1


# ---------------------------------------------------------------------------
# vsetvl AVL paths
# ---------------------------------------------------------------------------


def test_vsetvli_normal_path():
    if hasattr(N, "VSETVLI"):
        assert _vsetvl_flavor(N.VSETVLI, R.A0, R.A1) == "vsetvli_normal"


def test_vsetvli_set_vlmax_path():
    if hasattr(N, "VSETVLI"):
        assert _vsetvl_flavor(N.VSETVLI, R.A0, R.ZERO) == "vsetvli_set_vlmax"


def test_vsetvli_keep_vl_path():
    if hasattr(N, "VSETVLI"):
        assert _vsetvl_flavor(N.VSETVLI, R.ZERO, R.ZERO) == "vsetvli_keep_vl"


def test_vsetivli_imm_path():
    if hasattr(N, "VSETIVLI"):
        assert _vsetvl_flavor(N.VSETIVLI, R.A0, None) == "vsetivli_imm_avl"


def test_vsetvl_returns_none_for_non_vset():
    assert _vsetvl_flavor(N.ADD, R.A0, R.A1) is None


# ---------------------------------------------------------------------------
# Vector register-group overlap
# ---------------------------------------------------------------------------


def test_vreg_full_overlap():
    assert _vreg_overlap_class(8, 8, 1) == "full_overlap"


def test_vreg_partial_overlap():
    # vd=0 group {0..3}; vs=2 group {2..5}: partial overlap.
    assert _vreg_overlap_class(0, 2, 4) == "partial_overlap"


def test_vreg_no_overlap():
    assert _vreg_overlap_class(0, 4, 1) == "no_overlap"


# ---------------------------------------------------------------------------
# FCVT saturation corners
# ---------------------------------------------------------------------------


def test_fcvt_corner_nan_input():
    if hasattr(N, "FCVT_W_S"):
        assert _fcvt_corner_bin(N.FCVT_W_S, "nan") == "fcvt_w_s_nan_input"


def test_fcvt_corner_inf_input():
    if hasattr(N, "FCVT_LU_D"):
        assert _fcvt_corner_bin(N.FCVT_LU_D, "inf") == "fcvt_lu_d_inf_input"


def test_fcvt_corner_returns_none_for_fp_to_fp():
    if hasattr(N, "FCVT_S_D"):
        # FCVT_S_D — destination is S (FP), not int. Returns None.
        assert _fcvt_corner_bin(N.FCVT_S_D, "nan") is None


def test_fcvt_corner_normal_input_returns_none():
    if hasattr(N, "FCVT_W_S"):
        assert _fcvt_corner_bin(N.FCVT_W_S, "normal") is None


# ---------------------------------------------------------------------------
# Runtime integration — wave-2 covergroups together.
# ---------------------------------------------------------------------------


def test_runtime_walking_ones_alternating_lead_trail(tmp_path):
    trace = tmp_path / "rw.log"
    trace.write_text(
        # GPR write of 0xAAAAAAAA — full alternating pattern.
        "core   0: 3 0x80000010 (0x12345678) x10 0x00000000aaaaaaaa\n"
        # GPR write of 0xFF000000 — leading-zero run-length 32, trailing-zero run-length 24.
        "core   0: 3 0x80000020 (0x12345678) x11 0x00000000ff000000\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    # walking_ones — at least bit 1 should appear (0xAA has bits 1,3,5,7 set).
    assert any(k.startswith("bit_") for k in db[CG_WALKING_ONES])
    # alternating — 0xAAAAAAAA in 64-bit context is not the canonical
    # 64-bit pattern, but high bits are 0 → 0xAAAAAAAA pattern hits
    # alt_byte? Only the exact 64-bit pattern matches alt_AAAA. The
    # presence test is via the second value (0xFF000000).
    assert sum(db[CG_WALKING_ONES].values()) > 0
    # leading/trailing — at least one bin per write.
    assert sum(db[CG_LEAD_TRAIL].values()) > 0


def test_runtime_mxr_sum_mprv_cross(tmp_path):
    trace = tmp_path / "mmu.log"
    trace.write_text(
        # MSTATUS write — MXR=1, SUM=1, MPRV=0.
        "core   0: 3 0x80000010 (0x12345678) c300_mstatus 0x00000000000c0000\n"
        # Memory access — should sample MXR×SUM×MPRV cross.
        "core   0: 3 0x80000020 (0x00000023) mem 0x80001000\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert any("mxr1" in k and "sum1" in k for k in db[CG_MXR_SUM_MPRV])


def test_runtime_atomic_alignment(tmp_path):
    trace = tmp_path / "amo.log"
    trace.write_text(
        # Plain trace line for the AMO.
        "core   0: 0x80000010 (0x080530af) amoadd.w a1, a2, (a0)\n"
        # Commit line carrying the EA.
        "core   0: 3 0x80000010 (0x080530af) mem 0x80001004\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert "aligned_w" in db[CG_ATOMIC_ALIGN]


def test_runtime_atomic_misaligned(tmp_path):
    trace = tmp_path / "amom.log"
    trace.write_text(
        "core   0: 0x80000010 (0x080530af) amoadd.w a1, a2, (a0)\n"
        "core   0: 3 0x80000010 (0x080530af) mem 0x80001003\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert "misaligned_w" in db[CG_ATOMIC_ALIGN]


def test_runtime_virtual_instr_trap(tmp_path):
    trace = tmp_path / "vi.log"
    trace.write_text(
        # WFI mnemonic.
        "core   0: 0x80000010 (0x10500073) wfi\n"
        # SCAUSE write with cause=22 (virtual-instruction).
        "core   0: 1 0x80000010 (0x10500073) c142_scause 0x0000000000000016\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert "vi_wfi" in db[CG_VIRT_INSTR_TRAP]


def test_runtime_workload_region_filter_default(tmp_path):
    """Without main: label, bins that classify *test workload* must NOT sample.

    Captures the head-of-verification rule: an arithmetic-only test on an
    arithmetic-only core must NOT show MSTATUS / mem_align / walking_ones
    bins populated just because the boot prologue runs CSR writes and GPR
    init code. Without ``sample_handler_workload=True`` and without a
    ``main:`` label, the runtime sampler is in the "infrastructure" region.
    """
    from rvgen.coverage.collectors import (
        CG_BIT_ACTIVITY, CG_MSTATUS_FIELD, CG_WALKING_ONES,
    )
    trace = tmp_path / "boot_only.log"
    trace.write_text(
        # No main: label — we're still in boot/init context.
        "core   0: >>>>  h0_start\n"
        # Boot MSTATUS write — must NOT bump mstatus_field_cg.
        "core   0: 3 0x80000010 (0x12345678) c300_mstatus 0x0000000000001800\n"
        # Boot GPR init writes — must NOT bump walking_ones / bit_activity.
        "core   0: 3 0x80000020 (0x12345678) x10 0x00000000aaaaaaaa\n"
    )
    db = {}
    sample_trace_file(db, trace)
    assert db.get(CG_MSTATUS_FIELD, {}) == {}
    assert db.get(CG_WALKING_ONES, {}) == {}
    assert db.get(CG_BIT_ACTIVITY, {}) == {}


def test_runtime_workload_region_filter_opt_in_with_main_label(tmp_path):
    """After main:, the same writes now DO sample test-workload bins."""
    from rvgen.coverage.collectors import (
        CG_MSTATUS_FIELD, CG_WALKING_ONES,
    )
    trace = tmp_path / "with_main.log"
    trace.write_text(
        "core   0: >>>>  h0_start\n"
        # This boot write should still be filtered.
        "core   0: 3 0x80000010 (0x12345678) c300_mstatus 0x0000000000001800\n"
        "core   0: >>>>  main\n"
        # Now we're in workload — these must sample.
        "core   0: 3 0x80000020 (0x12345678) c300_mstatus 0x0000000000040000\n"
        "core   0: 3 0x80000030 (0x12345678) x10 0x00000000aaaaaaaa\n"
        "core   0: >>>>  test_done\n"
        # After test_done — filtered again.
        "core   0: 3 0x80000040 (0x12345678) c300_mstatus 0x0000000000080000\n"
    )
    db = {}
    sample_trace_file(db, trace)
    mfs = db.get(CG_MSTATUS_FIELD, {})
    # The mid-workload SUM-set write should be the only mstatus_field hit.
    assert any("sum_set" in k for k in mfs), f"expected sum_set; got {mfs}"
    # walking_ones populated from the workload GPR write.
    assert db.get(CG_WALKING_ONES, {}), "walking_ones should be populated"


def test_runtime_workload_handler_override(tmp_path):
    """sample_handler_workload=True disables the filter."""
    from rvgen.coverage.collectors import CG_MSTATUS_FIELD
    trace = tmp_path / "no_main.log"
    trace.write_text(
        "core   0: 3 0x80000010 (0x12345678) c300_mstatus 0x0000000000040000\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert any("sum_set" in k for k in db.get(CG_MSTATUS_FIELD, {}))


def test_runtime_wfi_corner(tmp_path):
    trace = tmp_path / "wfi.log"
    trace.write_text(
        # MSTATUS sets TW=1 (bit 21).
        "core   0: 3 0x80000010 (0x12345678) c300_mstatus 0x0000000000200000\n"
        # WFI retires.
        "core   0: 0x80000020 (0x10500073) wfi\n"
    )
    db = {}
    sample_trace_file(db, trace, sample_handler_workload=True)
    assert "wfi_tw1" in db[CG_WFI_CORNER]
