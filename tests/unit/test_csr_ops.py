"""Tests for rvgen.isa.csr_ops — CSR instruction subclass."""

from __future__ import annotations

from rvgen.isa import rv32i  # noqa: F401
from rvgen.isa.csr_ops import CsrInstr
from rvgen.isa.enums import (
    PrivilegedReg,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import get_instr


def test_csrrw_is_instance_of_csr_instr():
    i = get_instr(RiscvInstrName.CSRRW)
    assert isinstance(i, CsrInstr)


def test_csrrwi_set_rand_mode_disables_rs1():
    # I_FORMAT CSR has_rs1 should be False (the uimm[4:0] field replaces rs1).
    i = get_instr(RiscvInstrName.CSRRWI)
    assert i.has_rs1 is False
    assert i.has_rs2 is False
    assert i.has_imm is True


def test_csrrw_r_format_has_rs1():
    i = get_instr(RiscvInstrName.CSRRW)
    assert i.has_rs1 is True
    assert i.has_rs2 is False  # CSR instr never uses rs2


def test_asm_csrrw_r_format():
    # csrrw a0, mstatus, a1 → "csrrw a0, 0x300, a1"
    i = get_instr(RiscvInstrName.CSRRW)
    i.rd = RiscvReg.A0
    i.rs1 = RiscvReg.A1
    i.csr = PrivilegedReg.MSTATUS.value
    i.post_randomize()
    assert i.convert2asm() == "csrrw        a0, 0x300, a1"


def test_asm_csrrs_r_format():
    i = get_instr(RiscvInstrName.CSRRS)
    i.rd = RiscvReg.A0
    i.rs1 = RiscvReg.ZERO
    i.csr = PrivilegedReg.MIE.value
    i.post_randomize()
    assert i.convert2asm() == "csrrs        a0, 0x304, zero"


def test_asm_csrrwi_i_format():
    # csrrwi a0, mstatus, 7 → "csrrwi a0, 0x300, 7"
    i = get_instr(RiscvInstrName.CSRRWI)
    i.rd = RiscvReg.A0
    i.csr = PrivilegedReg.MSTATUS.value
    i.imm = 7
    i.post_randomize()
    assert i.convert2asm() == "csrrwi       a0, 0x300, 7"


def test_asm_csrrci_i_format():
    i = get_instr(RiscvInstrName.CSRRCI)
    i.rd = RiscvReg.T0
    i.csr = PrivilegedReg.MEPC.value
    i.imm = 0
    i.post_randomize()
    assert i.convert2asm() == "csrrci       t0, 0x341, 0"


def test_bin_csrrw():
    # csrrw a0, mstatus(0x300), a1 → {csr[11:0], rs1, func3=1, rd, opc=0x73}
    # = 0x30059573
    i = get_instr(RiscvInstrName.CSRRW)
    i.rd = RiscvReg.A0
    i.rs1 = RiscvReg.A1
    i.csr = 0x300
    i.post_randomize()
    assert i.convert2bin() == "30059573"


def test_bin_csrrwi():
    # csrrwi a0, mstatus, 7 → {csr=0x300, uimm5=7, func3=5, rd=10, opc=0x73}
    # = 0x3003D573
    i = get_instr(RiscvInstrName.CSRRWI)
    i.rd = RiscvReg.A0
    i.csr = 0x300
    i.imm = 7
    i.post_randomize()
    assert i.convert2bin() == "3003d573"


def test_csr_instr_get_func3_lookup():
    m = {
        RiscvInstrName.CSRRW: 0b001,
        RiscvInstrName.CSRRS: 0b010,
        RiscvInstrName.CSRRC: 0b011,
        RiscvInstrName.CSRRWI: 0b101,
        RiscvInstrName.CSRRSI: 0b110,
        RiscvInstrName.CSRRCI: 0b111,
    }
    for name, expected in m.items():
        i = get_instr(name)
        assert i.get_func3() == expected, f"{name.name} func3 mismatch"


def test_csr_instr_uses_system_opcode():
    for name in (
        RiscvInstrName.CSRRW, RiscvInstrName.CSRRS, RiscvInstrName.CSRRC,
        RiscvInstrName.CSRRWI, RiscvInstrName.CSRRSI, RiscvInstrName.CSRRCI,
    ):
        assert get_instr(name).get_opcode() == 0b1110011
