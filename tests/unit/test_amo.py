"""Tests for AMO base class and RV32A/RV64A encodings."""

from __future__ import annotations

from rvgen.isa import rv32a  # noqa: F401
from rvgen.isa.amo import AmoInstr
from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.isa.factory import get_instr


def test_amo_is_amo_instr_subclass():
    for name in (
        RiscvInstrName.LR_W, RiscvInstrName.SC_W, RiscvInstrName.AMOSWAP_W,
        RiscvInstrName.AMOADD_D, RiscvInstrName.AMOMAXU_D,
    ):
        assert isinstance(get_instr(name), AmoInstr)


def test_asm_amoswap_w_no_suffix():
    i = get_instr(RiscvInstrName.AMOSWAP_W)
    i.rd, i.rs1, i.rs2 = RiscvReg.A0, RiscvReg.A1, RiscvReg.A2
    assert i.convert2asm() == "amoswap.w    a0, a2, (a1)"


def test_asm_amoadd_w_aq_suffix():
    i = get_instr(RiscvInstrName.AMOADD_W)
    i.rd, i.rs1, i.rs2 = RiscvReg.A0, RiscvReg.A1, RiscvReg.A2
    i.aq = True
    assert i.convert2asm() == "amoadd.w.aq  a0, a2, (a1)"


def test_asm_lr_w_format_no_rs2():
    i = get_instr(RiscvInstrName.LR_W)
    i.rd, i.rs1 = RiscvReg.A0, RiscvReg.SP
    assert i.convert2asm() == "lr.w         a0, (sp)"


def test_asm_amoswap_d_rv64():
    i = get_instr(RiscvInstrName.AMOSWAP_D)
    i.rd, i.rs1, i.rs2 = RiscvReg.A0, RiscvReg.A1, RiscvReg.A2
    i.rl = True
    assert i.convert2asm() == "amoswap.d.rl a0, a2, (a1)"


def test_bin_amoswap_w():
    # amoswap.w a0, a2, (a1):
    # func5=00001, aq=0, rl=0, rs2=12, rs1=11, func3=010, rd=10, opc=0x2F
    # = (1<<27) | 0 | 0 | (12<<20) | (11<<15) | (2<<12) | (10<<7) | 0x2F
    # = 0x08C5A52F
    i = get_instr(RiscvInstrName.AMOSWAP_W)
    i.rd, i.rs1, i.rs2 = RiscvReg.A0, RiscvReg.A1, RiscvReg.A2
    assert i.convert2bin() == "08c5a52f"


def test_bin_lr_w_zeroes_rs2():
    # lr.w a0, (a1): rs2 encoded as 0 regardless of operand state.
    # func5=00010, rs2=0, rs1=11, func3=010, rd=10, opc=0x2F
    # = (2<<27) | (11<<15) | (2<<12) | (10<<7) | 0x2F
    # = 0x1005A52F
    i = get_instr(RiscvInstrName.LR_W)
    i.rd, i.rs1 = RiscvReg.A0, RiscvReg.A1
    assert i.convert2bin() == "1005a52f"


def test_bin_amoadd_d_with_aq():
    # amoadd.d.aq a0, a2, (a1):
    # func5=00000, aq=1, rl=0, rs2=12, rs1=11, func3=011, rd=10, opc=0x2F
    # = (0<<27) | (1<<26) | 0 | (12<<20) | (11<<15) | (3<<12) | (10<<7) | 0x2F
    # = 0x04C5B52F
    i = get_instr(RiscvInstrName.AMOADD_D)
    i.rd, i.rs1, i.rs2 = RiscvReg.A0, RiscvReg.A1, RiscvReg.A2
    i.aq = True
    assert i.convert2bin() == "04c5b52f"
