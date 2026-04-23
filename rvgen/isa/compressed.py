"""Compressed (RVC) instruction base class — port of ``src/isa/riscv_compressed_instr.sv``.

Priority for Phase 1 is:

1. Correct operand / immediate rendering via :meth:`convert2asm` so GCC can
   assemble the generated ``.S``.
2. Correct ``has_*`` flags per format so downstream stream generators pick
   valid registers (x8..x15 for three-bit compressed fields).
3. Correct immediate widths + alignment shifts so ``imm_str`` reflects the
   real byte-offset value.

The 16-bit binary encoding (``convert2bin``) is not implemented in Phase 1;
it's only used by riscv-dv's illegal-instruction emission, which is itself
deferred. Calling ``convert2bin`` on a compressed instr raises
``NotImplementedError`` so we fail loudly rather than silently producing
wrong bytes.
"""

from __future__ import annotations

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    MAX_INSTR_STR_LEN,
    ImmType,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.utils import format_string


_FMT = RiscvInstrFormat
_CAT = RiscvInstrCategory
_IMM = ImmType


# Compressed 3-bit register field covers x8..x15 (S0..A5).
_COMPRESSED_REGS = (
    RiscvReg.S0, RiscvReg.S1,
    RiscvReg.A0, RiscvReg.A1, RiscvReg.A2, RiscvReg.A3, RiscvReg.A4, RiscvReg.A5,
)


class CompressedInstr(Instr):
    """Base class for every RVC instruction.

    Default constructor sets ``rs1 = rs2 = rd = S0`` to match SV's initial
    state (riscv_compressed_instr.sv:86).
    """

    __slots__ = ("imm_align",)

    def __init__(self) -> None:
        super().__init__()
        self.rs1 = RiscvReg.S0
        self.rs2 = RiscvReg.S0
        self.rd = RiscvReg.S0
        self.is_compressed = True
        # imm_align is set by set_imm_len (below).

    # -------- Immediate width + alignment --------

    def set_imm_len(self) -> None:
        """SV: ``set_imm_len`` (riscv_compressed_instr.sv:92)."""
        fmt = self.format
        name = self.instr_name
        if fmt in (_FMT.CI_FORMAT, _FMT.CSS_FORMAT):
            self.imm_len = 6
        elif fmt in (_FMT.CL_FORMAT, _FMT.CS_FORMAT):
            self.imm_len = 5
        elif fmt == _FMT.CJ_FORMAT:
            self.imm_len = 11
        elif fmt == _FMT.CB_FORMAT:
            self.imm_len = 6 if name == RiscvInstrName.C_ANDI else 7
        elif fmt == _FMT.CIW_FORMAT:
            self.imm_len = 8
        else:
            self.imm_len = 0  # CA/CR formats have no immediate.

        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF

        # Imm alignment shift (multiplies the stored value at render time).
        if name in (
            RiscvInstrName.C_SQ, RiscvInstrName.C_LQ,
            RiscvInstrName.C_LQSP, RiscvInstrName.C_SQSP,
            RiscvInstrName.C_ADDI16SP,
        ):
            self.imm_align = 4
        elif name in (
            RiscvInstrName.C_SD, RiscvInstrName.C_LD,
            RiscvInstrName.C_LDSP, RiscvInstrName.C_SDSP,
        ):
            self.imm_align = 3
        elif name in (
            RiscvInstrName.C_SW, RiscvInstrName.C_LW,
            RiscvInstrName.C_LWSP, RiscvInstrName.C_SWSP,
            RiscvInstrName.C_ADDI4SPN,
        ):
            self.imm_align = 2
        elif name == RiscvInstrName.C_LUI:
            self.imm_align = 12
        elif name in (
            RiscvInstrName.C_J, RiscvInstrName.C_JAL,
            RiscvInstrName.C_BNEZ, RiscvInstrName.C_BEQZ,
        ):
            self.imm_align = 1
        else:
            self.imm_align = 0

    def extend_imm(self) -> None:
        """Sign-extend then apply alignment shift (SV:128)."""
        if self.instr_name != RiscvInstrName.C_LUI:
            super().extend_imm()
            self.imm = (self.imm << self.imm_align) & 0xFFFFFFFF

    def randomize_imm(self, rng, xlen: int) -> None:
        """Respect NZIMM / NZUIMM 'low 6 bits != 0' and C_LUI/shift top-zero.

        SV: ``imm_val_c`` (riscv_compressed_instr.sv:44).
        """
        name = self.instr_name
        if name in (
            RiscvInstrName.C_SRAI, RiscvInstrName.C_SRLI, RiscvInstrName.C_SLLI,
        ):
            # Shamt is [1, 31] on RV32 (SV constrains top bits to 0).
            self.imm = rng.randint(1, 31)
            return
        if name == RiscvInstrName.C_LUI:
            # SV constrains imm[31:5]==0 AND imm[5:0]!=0 → effective range [1, 31].
            self.imm = rng.randint(1, 31)
            return
        if self.imm_type in (_IMM.NZIMM, _IMM.NZUIMM):
            val = rng.getrandbits(self.imm_len)
            if (val & 0x3F) == 0:
                val |= 1
            if name == RiscvInstrName.C_ADDI4SPN:
                val &= ~0b11
                if val == 0:
                    val = 0b100
            self.imm = val
            return
        self.imm = rng.getrandbits(self.imm_len) if self.imm_len else 0

    # -------- has_* flags per format --------

    def set_rand_mode(self) -> None:
        """SV: ``set_rand_mode`` (riscv_compressed_instr.sv:135)."""
        fmt = self.format
        name = self.instr_name
        # Start from defaults then selectively disable per format.
        if fmt == _FMT.CR_FORMAT:
            if self.category == _CAT.JUMP:
                self.has_rd = False
            else:
                self.has_rs1 = False
            self.has_imm = False
        elif fmt == _FMT.CSS_FORMAT:
            self.has_rs1 = False
            self.has_rd = False
        elif fmt == _FMT.CL_FORMAT:
            self.has_rs2 = False
        elif fmt == _FMT.CS_FORMAT:
            self.has_rd = False
        elif fmt == _FMT.CA_FORMAT:
            self.has_rs1 = False
            self.has_imm = False
        elif fmt in (_FMT.CI_FORMAT, _FMT.CIW_FORMAT):
            self.has_rs1 = False
            self.has_rs2 = False
        elif fmt == _FMT.CJ_FORMAT:
            self.has_rs1 = False
            self.has_rs2 = False
            self.has_rd = False
        elif fmt == _FMT.CB_FORMAT:
            if name != RiscvInstrName.C_ANDI:
                self.has_rd = False
            self.has_rs2 = False

    # -------- Assembly rendering --------

    def convert2asm(self, prefix: str = "") -> str:
        """Port of SV ``convert2asm`` (riscv_compressed_instr.sv:176)."""
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm_str = mnemonic
        name = self.instr_name

        if self.category != _CAT.SYSTEM:
            fmt = self.format
            if fmt in (_FMT.CI_FORMAT, _FMT.CIW_FORMAT):
                if name == RiscvInstrName.C_NOP:
                    asm_str = "c.nop"
                elif name == RiscvInstrName.C_ADDI16SP:
                    asm_str = f"{mnemonic}sp, {self.get_imm()}"
                elif name == RiscvInstrName.C_ADDI4SPN:
                    asm_str = f"{mnemonic}{self.rd.name}, sp, {self.get_imm()}"
                elif name in (RiscvInstrName.C_LDSP, RiscvInstrName.C_LWSP, RiscvInstrName.C_LQSP):
                    asm_str = f"{mnemonic}{self.rd.name}, {self.get_imm()}(sp)"
                else:
                    asm_str = f"{mnemonic}{self.rd.name}, {self.get_imm()}"
            elif fmt == _FMT.CL_FORMAT:
                asm_str = f"{mnemonic}{self.rd.name}, {self.get_imm()}({self.rs1.name})"
            elif fmt == _FMT.CS_FORMAT:
                if self.category == _CAT.STORE:
                    asm_str = f"{mnemonic}{self.rs2.name}, {self.get_imm()}({self.rs1.name})"
                else:
                    asm_str = f"{mnemonic}{self.rs1.name}, {self.rs2.name}"
            elif fmt == _FMT.CA_FORMAT:
                asm_str = f"{mnemonic}{self.rd.name}, {self.rs2.name}"
            elif fmt == _FMT.CB_FORMAT:
                asm_str = f"{mnemonic}{self.rs1.name}, {self.get_imm()}"
            elif fmt == _FMT.CSS_FORMAT:
                if self.category == _CAT.STORE:
                    asm_str = f"{mnemonic}{self.rs2.name}, {self.get_imm()}(sp)"
                else:
                    asm_str = f"{mnemonic}{self.rs2.name}, {self.get_imm()}"
            elif fmt == _FMT.CR_FORMAT:
                if name in (RiscvInstrName.C_JR, RiscvInstrName.C_JALR):
                    asm_str = f"{mnemonic}{self.rs1.name}"
                else:
                    asm_str = f"{mnemonic}{self.rd.name}, {self.rs2.name}"
            elif fmt == _FMT.CJ_FORMAT:
                asm_str = f"{mnemonic}{self.get_imm()}"
        else:
            if name == RiscvInstrName.C_EBREAK:
                # SV emits a "c.ebreak;c.nop;" pair to keep pc+2 aligned after
                # the handler restores MEPC+4.
                asm_str = "c.ebreak;c.nop;"

        if self.comment:
            asm_str = f"{asm_str} #{self.comment}"
        return asm_str.lower()

    # -------- Binary encoding: deferred --------

    def convert2bin(self, prefix: str = "") -> str:
        raise NotImplementedError(
            "Compressed instruction binary encoding not implemented in Phase 1. "
            "Use convert2asm + assembler (GCC) to produce bytes."
        )

    def get_opcode(self) -> int:  # not applicable — 7-bit opcode is 32-bit only
        raise NotImplementedError("Compressed instrs use 2-bit c_opcode, not 7-bit")
