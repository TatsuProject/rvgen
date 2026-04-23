"""Tests for rvgen.isa.factory — registry and get_instr."""

from __future__ import annotations

import pytest

from rvgen.isa import rv32i
from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    ImmType,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
)
from rvgen.isa.factory import (
    define_instr,
    get_instr,
    is_registered,
    registered_names,
)


def test_all_rv32i_instructions_registered():
    for name in rv32i.RV32I_INSTR_NAMES:
        assert is_registered(name), f"{name.name} not registered"


def test_rv32i_count_matches_sv_54_entries():
    # rv32i_instr.sv has 54 DEFINE_*_INSTR calls: 41 base + NOP + 6 privileged
    # (URET/SRET/MRET/DRET/WFI/SFENCE_VMA) + 6 CSR.
    assert len(rv32i.RV32I_INSTR_NAMES) == 54


def test_get_instr_returns_fresh_instance():
    a = get_instr(RiscvInstrName.ADDI)
    b = get_instr(RiscvInstrName.ADDI)
    assert a is not b
    assert type(a) is type(b)


def test_get_instr_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        # BMATOR is declared in the name enum (draft RV64B) but not registered
        # by any ISA module — used as a sentinel "not registered" opcode.
        get_instr(RiscvInstrName.BMATOR)


def test_class_name_follows_riscv_N_instr_pattern():
    # SV factory resolves classes by the string "riscv_<NAME>_instr". We
    # match that convention so testlist `+directed_instr_N=...` strings keep
    # working (Phase 2 might reflect/lookup by name).
    add = get_instr(RiscvInstrName.ADD)
    assert type(add).__name__ == "riscv_ADD_instr"


def test_class_level_attributes_set_by_define():
    jal = get_instr(RiscvInstrName.JAL)
    assert type(jal).instr_name == RiscvInstrName.JAL
    assert type(jal).format == RiscvInstrFormat.J_FORMAT
    assert type(jal).category == RiscvInstrCategory.JUMP
    assert type(jal).group == RiscvInstrGroup.RV32I


def test_duplicate_register_raises():
    # The rv32i module has already registered ADD; a second register must fail.
    with pytest.raises(ValueError, match="already registered"):
        define_instr(
            RiscvInstrName.ADD,
            RiscvInstrFormat.R_FORMAT,
            RiscvInstrCategory.ARITHMETIC,
            RiscvInstrGroup.RV32I,
        )


def test_registered_names_contains_rv32i():
    names = registered_names()
    for name in rv32i.RV32I_INSTR_NAMES:
        assert name in names
