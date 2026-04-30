"""Tests for Zfh — scalar half-precision FP extension."""

from __future__ import annotations

import random

import pytest

from rvgen.isa.enums import (
    RiscvFpr,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import INSTR_REGISTRY, get_instr, is_registered
from rvgen.isa.zfh import RV32ZFH_INSTR_NAMES, RV64ZFH_INSTR_NAMES


# ---------- registration ----------


def test_rv32zfh_count_matches_spec():
    # Spec: 25 mnemonics in RV32 base Zfh + 5 cross-precision conversions.
    assert len(RV32ZFH_INSTR_NAMES) == 30


def test_rv64zfh_adds_4_new_conversions():
    assert len(RV64ZFH_INSTR_NAMES) == 4
    # All four are 64-bit int <-> half-precision conversions.
    expected = {
        RiscvInstrName.FCVT_L_H, RiscvInstrName.FCVT_LU_H,
        RiscvInstrName.FCVT_H_L, RiscvInstrName.FCVT_H_LU,
    }
    assert set(RV64ZFH_INSTR_NAMES) == expected


def test_every_rv32zfh_op_is_registered():
    for n in RV32ZFH_INSTR_NAMES:
        assert is_registered(n), f"{n.name} not registered"
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZFH


def test_every_rv64zfh_op_is_registered():
    for n in RV64ZFH_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV64ZFH


# ---------- formats ----------


def test_flh_is_i_format_load():
    cls = INSTR_REGISTRY[RiscvInstrName.FLH]
    assert cls.format == RiscvInstrFormat.I_FORMAT
    assert cls.category == RiscvInstrCategory.LOAD


def test_fsh_is_s_format_store():
    cls = INSTR_REGISTRY[RiscvInstrName.FSH]
    assert cls.format == RiscvInstrFormat.S_FORMAT
    assert cls.category == RiscvInstrCategory.STORE


def test_fma_h_ops_are_r4_format():
    for n in (RiscvInstrName.FMADD_H, RiscvInstrName.FMSUB_H,
              RiscvInstrName.FNMSUB_H, RiscvInstrName.FNMADD_H):
        cls = INSTR_REGISTRY[n]
        assert cls.format == RiscvInstrFormat.R4_FORMAT


def test_fadd_fsub_fmul_fdiv_h_are_r_format():
    for n in (RiscvInstrName.FADD_H, RiscvInstrName.FSUB_H,
              RiscvInstrName.FMUL_H, RiscvInstrName.FDIV_H):
        cls = INSTR_REGISTRY[n]
        assert cls.format == RiscvInstrFormat.R_FORMAT


def test_compare_ops_are_r_format_compare_category():
    for n in (RiscvInstrName.FEQ_H, RiscvInstrName.FLT_H, RiscvInstrName.FLE_H):
        cls = INSTR_REGISTRY[n]
        assert cls.format == RiscvInstrFormat.R_FORMAT
        assert cls.category == RiscvInstrCategory.COMPARE


# ---------- asm output ----------


def _ready(inst, fd=RiscvFpr.FA0, fs1=RiscvFpr.FA1, fs2=RiscvFpr.FA2,
           fs3=RiscvFpr.FA3, rd=RiscvReg.A0, rs1=RiscvReg.A1):
    inst.set_rand_mode()
    inst.fd = fd
    inst.fs1 = fs1
    inst.fs2 = fs2
    inst.fs3 = fs3
    inst.rd = rd
    inst.rs1 = rs1
    return inst


def test_fadd_h_asm_three_fp_operands_with_rm():
    inst = _ready(get_instr(RiscvInstrName.FADD_H))
    asm = inst.convert2asm()
    # "fadd.h fa0, fa1, fa2, rne"
    assert asm.startswith("fadd.h")
    assert ", fa0, fa1, fa2, rne" in asm or "fa0, fa1, fa2, rne" in asm


def test_fmadd_h_asm_four_fp_operands_with_rm():
    inst = _ready(get_instr(RiscvInstrName.FMADD_H))
    asm = inst.convert2asm()
    assert asm.startswith("fmadd.h")
    assert "fa0, fa1, fa2, fa3" in asm
    assert "rne" in asm  # default rm


def test_fmin_h_asm_has_no_rm_suffix():
    inst = _ready(get_instr(RiscvInstrName.FMIN_H))
    asm = inst.convert2asm()
    assert asm.startswith("fmin.h")
    # No rounding-mode token at end.
    assert not asm.rstrip().endswith(("rne", "rtz", "rdn", "rup", "rmm"))


def test_fsgnj_h_asm_has_no_rm_suffix():
    inst = _ready(get_instr(RiscvInstrName.FSGNJ_H))
    asm = inst.convert2asm()
    assert asm.startswith("fsgnj.h")
    assert not asm.rstrip().endswith(("rne", "rtz", "rdn", "rup", "rmm"))


def test_fclass_h_asm_is_rd_fs1_only():
    inst = _ready(get_instr(RiscvInstrName.FCLASS_H))
    asm = inst.convert2asm()
    assert asm.startswith("fclass.h")
    assert "a0, fa1" in asm
    # No fs2 or rounding mode.
    assert "fa2" not in asm


def test_fmv_x_h_asm_is_int_dst_fp_src():
    inst = _ready(get_instr(RiscvInstrName.FMV_X_H))
    asm = inst.convert2asm()
    assert asm.startswith("fmv.x.h")
    assert "a0, fa1" in asm


def test_fmv_h_x_asm_is_fp_dst_int_src():
    inst = _ready(get_instr(RiscvInstrName.FMV_H_X))
    asm = inst.convert2asm()
    assert asm.startswith("fmv.h.x")
    assert "fa0, a1" in asm


def test_fcvt_w_h_asm_takes_rm():
    inst = _ready(get_instr(RiscvInstrName.FCVT_W_H))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.w.h")
    assert "rne" in asm  # default rm
    assert "a0, fa1" in asm


def test_fcvt_s_h_asm_no_rm_widen_to_single():
    # half→single is exact; no rounding-mode suffix.
    inst = _ready(get_instr(RiscvInstrName.FCVT_S_H))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.s.h")
    assert not asm.rstrip().endswith(("rne", "rtz", "rdn", "rup", "rmm"))


def test_fcvt_d_h_asm_no_rm_widen_to_double():
    # half→double is exact; no rm.
    inst = _ready(get_instr(RiscvInstrName.FCVT_D_H))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.d.h")
    assert not asm.rstrip().endswith(("rne", "rtz", "rdn", "rup", "rmm"))


def test_fcvt_h_s_asm_takes_rm_narrow_from_single():
    # single→half rounds; rm required.
    inst = _ready(get_instr(RiscvInstrName.FCVT_H_S))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.h.s")
    assert "rne" in asm


def test_fcvt_h_d_asm_takes_rm_narrow_from_double():
    inst = _ready(get_instr(RiscvInstrName.FCVT_H_D))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.h.d")
    assert "rne" in asm


def test_rv64_fcvt_l_h_asm():
    inst = _ready(get_instr(RiscvInstrName.FCVT_L_H))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.l.h")
    assert "rne" in asm


def test_rv64_fcvt_h_lu_asm():
    inst = _ready(get_instr(RiscvInstrName.FCVT_H_LU))
    asm = inst.convert2asm()
    assert asm.startswith("fcvt.h.lu")
    assert "fa0, a1" in asm
    assert "rne" in asm


# ---------- target integration ----------


def test_rv32imafdc_zfh_target_advertises_zfh():
    from rvgen.targets.builtin import BUILTIN_TARGETS
    t = BUILTIN_TARGETS["rv32imafdc_zfh"]
    assert RiscvInstrGroup.RV32ZFH in t.supported_isa
    # Sanity — still has scalar F + D.
    assert RiscvInstrGroup.RV32F in t.supported_isa
    assert RiscvInstrGroup.RV32D in t.supported_isa


def test_rv64imafdc_zfh_target_has_both_zfh_groups():
    from rvgen.targets.builtin import BUILTIN_TARGETS
    t = BUILTIN_TARGETS["rv64imafdc_zfh"]
    assert RiscvInstrGroup.RV32ZFH in t.supported_isa
    assert RiscvInstrGroup.RV64ZFH in t.supported_isa


def test_rv32imafdc_zfh_isa_string_includes_zfh():
    from rvgen.targets.builtin import BUILTIN_TARGETS
    t = BUILTIN_TARGETS["rv32imafdc_zfh"]
    assert "_zfh" in t.isa_string


def test_zfh_filtering_picks_up_zfh_ops():
    from rvgen.config import Config
    from rvgen.isa.filtering import create_instr_list
    from rvgen.targets.builtin import BUILTIN_TARGETS
    cfg = Config(target=BUILTIN_TARGETS["rv32imafdc_zfh"])
    inv = create_instr_list(cfg)
    # Spot-check a handful are filtered in.
    assert RiscvInstrName.FADD_H in inv.names
    assert RiscvInstrName.FCVT_W_H in inv.names
    assert RiscvInstrName.FMV_H_X in inv.names
    # RV64-only ops should NOT be in an rv32 target.
    assert RiscvInstrName.FCVT_L_H not in inv.names
