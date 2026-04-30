"""Tests for the H-extension (hypervisor) instruction surface.

The H-ext port covers the 15 instructions ratified 2021-11. We verify:

1. Every mnemonic registers in ``INSTR_REGISTRY``.
2. The asm output matches the expected guest-virtual-load/store form.
3. The ``rv64gch`` target advertises the new ``RV64H`` group enum and
   pulls every H-ext mnemonic through ``filter_by_target_isa``.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName as N,
    RiscvReg as R,
)
from rvgen.isa.factory import INSTR_REGISTRY, get_instr
from rvgen.isa.filtering import create_instr_list
from rvgen.config import Config
from rvgen.isa.h_ext import (
    H_FENCE_INSTR_NAMES,
    H_LOAD_INSTR_NAMES,
    H_STORE_INSTR_NAMES,
    RV64H_INSTR_NAMES,
)
from rvgen.targets import get_target


def _ready(inst, *, rd=R.A0, rs1=R.A1, rs2=R.A2):
    inst.set_rand_mode()
    inst.rd = rd
    inst.rs1 = rs1
    inst.rs2 = rs2
    return inst


def test_all_h_ext_names_registered():
    for name in RV64H_INSTR_NAMES:
        assert name in INSTR_REGISTRY, f"{name.name} missing from INSTR_REGISTRY"


def test_h_ext_group_assigned():
    for name in RV64H_INSTR_NAMES:
        assert INSTR_REGISTRY[name].group == RiscvInstrGroup.RV64H


def test_hfence_emits_two_operands_no_rd():
    i = _ready(get_instr(N.HFENCE_VVMA), rs1=R.A0, rs2=R.A1)
    asm = i.convert2asm()
    assert "hfence.vvma" in asm
    assert "a0, a1" in asm
    # No rd column.
    assert "a2" not in asm

    i = _ready(get_instr(N.HFENCE_GVMA), rs1=R.T0, rs2=R.T1)
    assert "hfence.gvma  t0, t1" in i.convert2asm()


def test_hload_emits_rd_paren_rs1():
    i = _ready(get_instr(N.HLV_B), rd=R.T0, rs1=R.A1)
    asm = i.convert2asm()
    assert "hlv.b" in asm
    assert "t0, (a1)" in asm
    # No rs2.
    assert "a2" not in asm


def test_hloadx_emits_rd_paren_rs1():
    i = _ready(get_instr(N.HLVX_HU), rd=R.T1, rs1=R.A2)
    assert "hlvx.hu      t1, (a2)" in i.convert2asm()
    i = _ready(get_instr(N.HLVX_WU), rd=R.T2, rs1=R.A3)
    assert "hlvx.wu      t2, (a3)" in i.convert2asm()


def test_hstore_emits_rs2_paren_rs1_no_rd():
    i = _ready(get_instr(N.HSV_W), rs1=R.A0, rs2=R.T0)
    asm = i.convert2asm()
    assert "hsv.w" in asm
    assert "t0, (a0)" in asm
    # No rd column.
    assert "a2" not in asm


def test_h_load_categories_are_load():
    for name in H_LOAD_INSTR_NAMES:
        assert INSTR_REGISTRY[name].category == RiscvInstrCategory.LOAD


def test_h_store_categories_are_store():
    for name in H_STORE_INSTR_NAMES:
        assert INSTR_REGISTRY[name].category == RiscvInstrCategory.STORE


def test_h_fence_format_is_r():
    for name in H_FENCE_INSTR_NAMES:
        assert INSTR_REGISTRY[name].format == RiscvInstrFormat.R_FORMAT
        assert INSTR_REGISTRY[name].category == RiscvInstrCategory.SYNCH


def test_rv64gch_target_advertises_rv64h_group():
    t = get_target("rv64gch")
    assert RiscvInstrGroup.RV64H in t.supported_isa
    assert t.xlen == 64


def test_rv64gch_filter_picks_up_h_instr():
    cfg = Config(target=get_target("rv64gch"))
    pool = set(create_instr_list(cfg).names)
    assert N.HFENCE_VVMA in pool
    assert N.HLV_B in pool
    assert N.HLVX_WU in pool
    assert N.HSV_D in pool


def test_other_targets_do_not_emit_h_instr():
    # rv64gc has no H — none of the H-ext mnemonics should be in the pool.
    cfg = Config(target=get_target("rv64gc"))
    pool = set(create_instr_list(cfg).names)
    for name in RV64H_INSTR_NAMES:
        assert name not in pool, f"{name.name} leaked into rv64gc pool"
