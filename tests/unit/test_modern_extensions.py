"""Tests for modern checkbox extensions (Zicond/Zicbom/Zicboz/Zicbop/
Zihintpause/Zihintntl/Zimop/Zcmop) — see :mod:`rvgen.isa.modern`.

For each extension we check:

1. Every mnemonic registers in ``INSTR_REGISTRY``.
2. Each instance emits the expected GCC-accepted asm form.
3. The group enum is wired so a target advertising the group picks the
   instructions up via ``filter_by_target_isa``.
"""

from __future__ import annotations

import random

import pytest

from rvgen.isa.enums import (
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import INSTR_REGISTRY, get_instr, is_registered
from rvgen.isa.modern import (
    ZCMOP_INSTR_NAMES,
    ZICBOM_INSTR_NAMES,
    ZICBOP_INSTR_NAMES,
    ZICBOZ_INSTR_NAMES,
    ZICOND_INSTR_NAMES,
    ZIHINTNTL_INSTR_NAMES,
    ZIHINTPAUSE_INSTR_NAMES,
    ZIMOP_R_INSTR_NAMES,
    ZIMOP_RR_INSTR_NAMES,
)


@pytest.fixture
def rng():
    return random.Random(42)


def _ready(inst, rd=RiscvReg.A0, rs1=RiscvReg.A1, rs2=RiscvReg.A2, rng=None):
    inst.set_rand_mode()
    inst.rd = rd
    inst.rs1 = rs1
    inst.rs2 = rs2
    if rng is not None and hasattr(inst, "randomize_imm"):
        inst.randomize_imm(rng, 32)
    return inst


# ---------- registration ----------


def test_zicond_registered():
    for n in ZICOND_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZICOND


def test_zicbom_registered():
    for n in ZICBOM_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZICBOM


def test_zicboz_registered():
    for n in ZICBOZ_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZICBOZ


def test_zicbop_registered():
    for n in ZICBOP_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZICBOP


def test_zihintpause_registered():
    for n in ZIHINTPAUSE_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZIHINTPAUSE


def test_zihintntl_registered():
    for n in ZIHINTNTL_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZIHINTNTL


def test_zimop_unary_count_is_32():
    assert len(ZIMOP_R_INSTR_NAMES) == 32
    for n in ZIMOP_R_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZIMOP


def test_zimop_binary_count_is_8():
    assert len(ZIMOP_RR_INSTR_NAMES) == 8
    for n in ZIMOP_RR_INSTR_NAMES:
        assert is_registered(n)


def test_zcmop_count_is_8_with_odd_indexes():
    assert len(ZCMOP_INSTR_NAMES) == 8
    for n in ZCMOP_INSTR_NAMES:
        assert is_registered(n)
        assert INSTR_REGISTRY[n].group == RiscvInstrGroup.RV32ZCMOP
    # Odd-only indexes per spec (1, 3, 5, 7, 9, 11, 13, 15).
    nums = sorted(int(n.name.rsplit("_", 1)[1]) for n in ZCMOP_INSTR_NAMES)
    assert nums == [1, 3, 5, 7, 9, 11, 13, 15]


# ---------- asm emission ----------


def test_czero_eqz_asm():
    asm = _ready(get_instr(RiscvInstrName.CZERO_EQZ)).convert2asm()
    assert asm.split() == ["czero.eqz", "a0,", "a1,", "a2"]


def test_czero_nez_asm():
    asm = _ready(get_instr(RiscvInstrName.CZERO_NEZ)).convert2asm()
    assert asm.split() == ["czero.nez", "a0,", "a1,", "a2"]


def test_cbo_zero_asm_has_no_rd_or_imm():
    asm = _ready(get_instr(RiscvInstrName.CBO_ZERO)).convert2asm()
    # Form: "cbo.zero (rs1)" with no rd, no rs2, no offset.
    assert "(a1)" in asm
    assert "a0" not in asm  # rd suppressed
    assert "a2" not in asm  # rs2 suppressed
    assert asm.startswith("cbo.zero")


def test_prefetch_w_asm_has_offset(rng):
    asm = _ready(get_instr(RiscvInstrName.PREFETCH_W), rng=rng).convert2asm()
    # "prefetch.w <offset>(a1)"
    assert asm.startswith("prefetch.w")
    assert asm.endswith("(a1)")


def test_prefetch_imm_is_32_byte_aligned(rng):
    # Spec ignores low 5 bits; we generate offsets that are multiples of 32.
    inst = _ready(get_instr(RiscvInstrName.PREFETCH_R), rng=rng)
    # imm field is the raw 12-bit storage. Sign-extend per get_imm.
    raw = inst.imm & 0xFFF
    if raw & 0x800:
        raw -= 0x1000
    assert raw % 32 == 0


def test_pause_asm_is_bare_mnemonic():
    asm = _ready(get_instr(RiscvInstrName.PAUSE)).convert2asm()
    assert asm.strip() == "pause"


def test_ntl_p1_asm_is_bare_mnemonic():
    asm = _ready(get_instr(RiscvInstrName.NTL_P1)).convert2asm()
    assert asm.strip() == "ntl.p1"


def test_ntl_all_asm_is_bare_mnemonic():
    asm = _ready(get_instr(RiscvInstrName.NTL_ALL)).convert2asm()
    assert asm.strip() == "ntl.all"


def test_mop_r_5_asm_two_operand():
    asm = _ready(get_instr(RiscvInstrName.MOP_R_5)).convert2asm()
    # "mop.r.5 a0, a1"
    assert asm.startswith("mop.r.5")
    assert "a0," in asm and "a1" in asm
    # rs2 must not appear.
    assert "a2" not in asm


def test_mop_rr_3_asm_three_operand():
    asm = _ready(get_instr(RiscvInstrName.MOP_RR_3)).convert2asm()
    assert asm.startswith("mop.rr.3")
    assert "a0," in asm and "a1," in asm and "a2" in asm


def test_c_mop_15_asm_is_bare_mnemonic():
    asm = _ready(get_instr(RiscvInstrName.C_MOP_15)).convert2asm()
    assert asm.strip() == "c.mop.15"


# ---------- group filter integration ----------


def test_modern_groups_distinct_from_existing():
    # Each new group enum must have a unique integer value; collisions would
    # break filter_by_target_isa.
    new_groups = (
        RiscvInstrGroup.RV32ZICOND, RiscvInstrGroup.RV64ZICOND,
        RiscvInstrGroup.RV32ZICBOM, RiscvInstrGroup.RV32ZICBOZ,
        RiscvInstrGroup.RV32ZICBOP,
        RiscvInstrGroup.RV32ZIHINTPAUSE, RiscvInstrGroup.RV32ZIHINTNTL,
        RiscvInstrGroup.RV32ZIMOP, RiscvInstrGroup.RV64ZIMOP,
        RiscvInstrGroup.RV32ZCMOP,
    )
    assert len({g.value for g in new_groups}) == len(new_groups)
