"""Bit-manipulation extensions — port of ``src/isa/riscv_{b,zba,zbb,zbc,zbs}_instr.sv``.

Covers:

- ``Zba``  — address-generation adds (sh1add/sh2add/sh3add + UW variants).
- ``Zbb``  — base bitmanip (andn/orn/xnor, clz/ctz/cpop, min/max, rol/ror,
  sext/zext, rev8, orc_b).
- ``Zbc``  — carry-less multiply (clmul/clmulh/clmulr).
- ``Zbs``  — single-bit ops (bclr/bext/binv/bset + immediate forms).
- ``B``    — "remaining" draft-0.93 bitmanip (gorc, pack, grev, fsl/fsr/fsri,
  cmix/cmov, crc32*, shfl/unshfl, bcompress/bdecompress, bfp, xperm_*).

All instructions use the standard `Instr` base for R/I/S/B/U/J rendering; we
override immediate width and operand layout for the few I-format unary ops
(Zbb) and R4-format ternary ops (B) where SV diverges from the generic path.
"""

from __future__ import annotations

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    ImmType,
    MAX_INSTR_STR_LEN,
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
    RiscvReg,
)
from rvgen.isa.factory import define_instr
from rvgen.isa.utils import format_string


# ---------------------------------------------------------------------------
# Bases
# ---------------------------------------------------------------------------


class BInstr(Instr):
    """``riscv_b_instr`` — remaining draft bitmanip (R4 + I + R forms).

    Extra state: ``rs3`` / ``has_rs3`` for R4 forms (``cmix``/``cmov``/``fsl``/
    ``fsr``) and I forms (``fsri``/``fsriw``).
    """

    __slots__ = ("rs3", "has_rs3")

    def __init__(self) -> None:
        super().__init__()
        self.rs3: RiscvReg = RiscvReg.ZERO
        self.has_rs3: bool = False

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_rs3 = False
        name = self.instr_name
        fmt = self.format
        if fmt == F.R_FORMAT:
            # CRC / BMATFLIP — one-source R-format: rd, rs1.
            if name in (
                N.CRC32_B, N.CRC32_H, N.CRC32_W,
                N.CRC32C_B, N.CRC32C_H, N.CRC32C_W,
                N.CRC32_D, N.CRC32C_D,
                N.BMATFLIP,
            ):
                self.has_rs2 = False
        elif fmt == F.R4_FORMAT:
            self.has_imm = False
            self.has_rs3 = True
        elif fmt == F.I_FORMAT:
            self.has_rs2 = False
            if name in (N.FSRI, N.FSRIW):
                self.has_rs3 = True

    def set_imm_len(self) -> None:
        if self.format == F.I_FORMAT:
            if self.category in (C.SHIFT, C.LOGICAL):
                # $clog2(XLEN): 5 on RV32, 6 on RV64. Default to 5; RV64 subclasses
                # could override, but RV32B is the only declared B-group for now.
                self.imm_len = 5
            if self.instr_name in (N.SHFLI, N.UNSHFLI):
                self.imm_len = 4  # $clog2(XLEN)-1
        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        name = self.instr_name
        fmt = self.format
        if fmt == F.I_FORMAT and name in (N.FSRI, N.FSRIW):
            asm = (
                f"{mnemonic}{self.rd.name}, {self.rs1.name}, "
                f"{self.rs3.name}, {self.get_imm()}"
            )
        elif fmt == F.R_FORMAT and not self.has_rs2:
            # Unary R-format — same shape as Zbb unary ops.
            asm = f"{mnemonic}{self.rd.name}, {self.rs1.name}"
        elif fmt == F.R4_FORMAT:
            asm = (
                f"{mnemonic}{self.rd.name}, {self.rs1.name}, "
                f"{self.rs2.name}, {self.rs3.name}"
            )
        else:
            return super().convert2asm(prefix)
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


class ZbaInstr(Instr):
    """``riscv_zba_instr`` — imm_len = $clog2(XLEN) - 1 (= 4 on RV32)."""

    def set_imm_len(self) -> None:
        if self.instr_name == N.SLLI_UW:
            self.imm_len = 5   # $clog2(XLEN) for RV32
        else:
            self.imm_len = 4   # $clog2(XLEN) - 1 for RV32
        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF


class ZbbInstr(Instr):
    """``riscv_zbb_instr`` — unary I-format ops + ZEXT.H.

    Unary I-form (clz/ctz/cpop/sext_b/sext_h/rev8/orc_b): ``instr rd, rs1``
    (has_imm=0). RORI uses XLEN-wide shamt. ZEXT.H in R-format drops rs2.
    """

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        name = self.instr_name
        fmt = self.format
        if fmt == F.R_FORMAT and name == N.ZEXT_H:
            self.has_rs2 = False
        elif fmt == F.I_FORMAT and name in (
            N.CLZ, N.CLZW, N.CTZ, N.CTZW, N.CPOP, N.CPOPW,
            N.ORC_B, N.SEXT_B, N.SEXT_H, N.REV8,
        ):
            self.has_imm = False

    def set_imm_len(self) -> None:
        if self.format == F.I_FORMAT:
            if self.instr_name in (N.RORI, N.RORIW):
                self.imm_len = 5  # $clog2(XLEN) on RV32
            else:
                self.imm_len = 5
        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        fmt = self.format
        # Unary I-format (clz etc.) and R-format ZEXT.H: rd, rs1.
        if (fmt == F.I_FORMAT and not self.has_imm) or (
            fmt == F.R_FORMAT and not self.has_rs2
        ):
            asm = f"{mnemonic}{self.rd.name}, {self.rs1.name}"
            if self.comment:
                asm = f"{asm} #{self.comment}"
            return asm.lower()
        return super().convert2asm(prefix)


class ZbcInstr(Instr):
    """``riscv_zbc_instr`` — all three ops are plain R-format, no overrides."""


class ZbsInstr(Instr):
    """``riscv_zbs_instr`` — BCLRI/BEXTI/BINVI/BSETI use $clog2(XLEN) imm."""

    def set_imm_len(self) -> None:
        if self.format == F.I_FORMAT and self.instr_name in (
            N.BCLRI, N.BEXTI, N.BINVI, N.BSETI,
        ):
            self.imm_len = 5  # $clog2(XLEN) on RV32
        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def _zba(name, fmt, cat, grp=G.RV32ZBA, imm_type=ImmType.IMM):
    define_instr(name, fmt, cat, grp, imm_type, base=ZbaInstr)


def _zbb(name, fmt, cat, grp=G.RV32ZBB, imm_type=ImmType.IMM):
    define_instr(name, fmt, cat, grp, imm_type, base=ZbbInstr)


def _zbc(name, fmt, cat, grp=G.RV32ZBC, imm_type=ImmType.IMM):
    define_instr(name, fmt, cat, grp, imm_type, base=ZbcInstr)


def _zbs(name, fmt, cat, grp=G.RV32ZBS, imm_type=ImmType.IMM):
    define_instr(name, fmt, cat, grp, imm_type, base=ZbsInstr)


def _b(name, fmt, cat, grp=G.RV32B, imm_type=ImmType.IMM):
    define_instr(name, fmt, cat, grp, imm_type, base=BInstr)


# Zba (RV32).
_zba(N.SH1ADD, F.R_FORMAT, C.ARITHMETIC)
_zba(N.SH2ADD, F.R_FORMAT, C.ARITHMETIC)
_zba(N.SH3ADD, F.R_FORMAT, C.ARITHMETIC)

# Zbb (RV32).
_zbb(N.ANDN,   F.R_FORMAT, C.LOGICAL)
_zbb(N.CLZ,    F.I_FORMAT, C.ARITHMETIC)
_zbb(N.CPOP,   F.I_FORMAT, C.ARITHMETIC)
_zbb(N.CTZ,    F.I_FORMAT, C.ARITHMETIC)
_zbb(N.MAX,    F.R_FORMAT, C.ARITHMETIC)
_zbb(N.MAXU,   F.R_FORMAT, C.ARITHMETIC)
_zbb(N.MIN,    F.R_FORMAT, C.ARITHMETIC)
_zbb(N.MINU,   F.R_FORMAT, C.ARITHMETIC)
_zbb(N.ORC_B,  F.I_FORMAT, C.LOGICAL)
_zbb(N.ORN,    F.R_FORMAT, C.LOGICAL)
_zbb(N.REV8,   F.I_FORMAT, C.SHIFT)
_zbb(N.ROL,    F.R_FORMAT, C.SHIFT)
_zbb(N.ROR,    F.R_FORMAT, C.SHIFT)
_zbb(N.RORI,   F.I_FORMAT, C.SHIFT, imm_type=ImmType.UIMM)
_zbb(N.SEXT_B, F.I_FORMAT, C.ARITHMETIC)
_zbb(N.SEXT_H, F.I_FORMAT, C.ARITHMETIC)
_zbb(N.XNOR,   F.R_FORMAT, C.LOGICAL)
_zbb(N.ZEXT_H, F.R_FORMAT, C.ARITHMETIC)

# Zbc (RV32).
_zbc(N.CLMUL,  F.R_FORMAT, C.ARITHMETIC)
_zbc(N.CLMULH, F.R_FORMAT, C.ARITHMETIC)
_zbc(N.CLMULR, F.R_FORMAT, C.ARITHMETIC)

# Zbs (RV32).
_zbs(N.BCLR,  F.R_FORMAT, C.SHIFT)
_zbs(N.BCLRI, F.I_FORMAT, C.SHIFT, imm_type=ImmType.UIMM)
_zbs(N.BEXT,  F.R_FORMAT, C.SHIFT)
_zbs(N.BEXTI, F.I_FORMAT, C.SHIFT, imm_type=ImmType.UIMM)
_zbs(N.BINV,  F.R_FORMAT, C.SHIFT)
_zbs(N.BINVI, F.I_FORMAT, C.SHIFT, imm_type=ImmType.UIMM)
_zbs(N.BSET,  F.R_FORMAT, C.SHIFT)
_zbs(N.BSETI, F.I_FORMAT, C.SHIFT, imm_type=ImmType.UIMM)

# B (RV32) — remaining draft-0.93 ops.
_b(N.GORC,     F.R_FORMAT,  C.LOGICAL)
_b(N.GORCI,    F.I_FORMAT,  C.LOGICAL, imm_type=ImmType.UIMM)
_b(N.CMIX,     F.R4_FORMAT, C.LOGICAL)
_b(N.CMOV,     F.R4_FORMAT, C.LOGICAL)
_b(N.PACK,     F.R_FORMAT,  C.LOGICAL)
_b(N.PACKU,    F.R_FORMAT,  C.LOGICAL)
_b(N.PACKH,    F.R_FORMAT,  C.LOGICAL)
_b(N.XPERM_N,  F.R_FORMAT,  C.LOGICAL)
_b(N.XPERM_B,  F.R_FORMAT,  C.LOGICAL)
_b(N.XPERM_H,  F.R_FORMAT,  C.LOGICAL)
_b(N.SLO,      F.R_FORMAT,  C.SHIFT)
_b(N.SRO,      F.R_FORMAT,  C.SHIFT)
_b(N.SLOI,     F.I_FORMAT,  C.SHIFT, imm_type=ImmType.UIMM)
_b(N.SROI,     F.I_FORMAT,  C.SHIFT, imm_type=ImmType.UIMM)
_b(N.GREV,     F.R_FORMAT,  C.SHIFT)
_b(N.GREVI,    F.I_FORMAT,  C.SHIFT, imm_type=ImmType.UIMM)
_b(N.FSL,      F.R4_FORMAT, C.SHIFT)
_b(N.FSR,      F.R4_FORMAT, C.SHIFT)
_b(N.FSRI,     F.I_FORMAT,  C.SHIFT, imm_type=ImmType.UIMM)
_b(N.CRC32_B,  F.R_FORMAT,  C.ARITHMETIC)
_b(N.CRC32_H,  F.R_FORMAT,  C.ARITHMETIC)
_b(N.CRC32_W,  F.R_FORMAT,  C.ARITHMETIC)
_b(N.CRC32C_B, F.R_FORMAT,  C.ARITHMETIC)
_b(N.CRC32C_H, F.R_FORMAT,  C.ARITHMETIC)
_b(N.CRC32C_W, F.R_FORMAT,  C.ARITHMETIC)
_b(N.SHFL,     F.R_FORMAT,  C.ARITHMETIC)
_b(N.UNSHFL,   F.R_FORMAT,  C.ARITHMETIC)
_b(N.SHFLI,    F.I_FORMAT,  C.ARITHMETIC, imm_type=ImmType.UIMM)
_b(N.UNSHFLI,  F.I_FORMAT,  C.ARITHMETIC, imm_type=ImmType.UIMM)
_b(N.BCOMPRESS,   F.R_FORMAT, C.ARITHMETIC)
_b(N.BDECOMPRESS, F.R_FORMAT, C.ARITHMETIC)
_b(N.BFP,         F.R_FORMAT, C.ARITHMETIC)
