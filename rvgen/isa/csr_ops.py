"""CSR instruction base class — port of ``riscv_csr_instr`` (src/isa/riscv_csr_instr.sv).

CSR instructions (CSRRW, CSRRS, CSRRC, CSRRWI, CSRRSI, CSRRCI) differ from
plain R/I-format instructions in their operand layout:

  - I-format: ``<mnem> rd, 0x<csr_addr>, <imm>``
  - R-format: ``<mnem> rd, 0x<csr_addr>, <rs1>``

The ``csr`` field is a 12-bit address, not a register, and the binary
encoding packs it into bits[31:20] where a normal I/R would place
rs2/imm[11:0].
"""

from __future__ import annotations

from typing import ClassVar

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    MAX_INSTR_STR_LEN,
    PrivilegedReg,
    RiscvInstrFormat,
    RiscvInstrName,
)
from rvgen.isa.utils import format_string


_FMT = RiscvInstrFormat


# SV ``riscv_csr_instr`` overrides only three methods: set_rand_mode,
# convert2asm, get_opcode, get_func3, convert2bin.


_CSR_FUNC3: dict[RiscvInstrName, int] = {
    RiscvInstrName.CSRRW: 0b001,
    RiscvInstrName.CSRRS: 0b010,
    RiscvInstrName.CSRRC: 0b011,
    RiscvInstrName.CSRRWI: 0b101,
    RiscvInstrName.CSRRSI: 0b110,
    RiscvInstrName.CSRRCI: 0b111,
}


class CsrInstr(Instr):
    """Base class for CSRRW/CSRRS/CSRRC/CSRRWI/CSRRSI/CSRRCI."""

    # CSR instructions randomize ``write_csr``; we expose it as a plain bool
    # for Phase 1 (our generator doesn't do constraint-solving and honors
    # the write-vs-read decision upstream).
    __slots__ = ("write_csr",)

    def __init__(self) -> None:
        super().__init__()
        self.write_csr: bool = False

    def set_rand_mode(self) -> None:
        """SV: riscv_csr_instr.set_rand_mode (line 126)."""
        super().set_rand_mode()
        self.has_rs2 = False
        if self.format == _FMT.I_FORMAT:
            self.has_rs1 = False

    def get_opcode(self) -> int:
        # All CSR ops share opcode 1110011 (SYSTEM).
        return 0b1110011

    def get_func3(self) -> int:
        try:
            return _CSR_FUNC3[self.instr_name]
        except KeyError:
            return super().get_func3()

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        fmt = self.format
        if fmt == _FMT.I_FORMAT:
            asm_str = f"{mnemonic}{self.rd.name}, 0x{self.csr:x}, {self.get_imm()}"
        elif fmt == _FMT.R_FORMAT:
            asm_str = f"{mnemonic}{self.rd.name}, 0x{self.csr:x}, {self.rs1.name}"
        else:
            raise ValueError(
                f"Unsupported format {fmt.name} for CSR instruction {self.instr_name.name}"
            )
        if self.comment:
            asm_str = f"{asm_str} #{self.comment}"
        return asm_str.lower()

    def convert2bin(self, prefix: str = "") -> str:
        opcode = self.get_opcode()
        func3 = self.get_func3()
        csr = self.csr & 0xFFF
        rd = int(self.rd) & 0x1F
        if self.format == _FMT.I_FORMAT:
            imm5 = self.imm & 0x1F
            word = (csr << 20) | (imm5 << 15) | (func3 << 12) | (rd << 7) | opcode
        elif self.format == _FMT.R_FORMAT:
            rs1 = int(self.rs1) & 0x1F
            word = (csr << 20) | (rs1 << 15) | (func3 << 12) | (rd << 7) | opcode
        else:
            raise ValueError(
                f"Unsupported format {self.format.name} for CSR instruction"
            )
        return f"{prefix}{word & 0xFFFFFFFF:08x}"
