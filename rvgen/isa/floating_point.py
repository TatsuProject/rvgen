"""Floating-point base class — port of ``src/isa/riscv_floating_point_instr.sv``.

FP instructions add the following state over plain ``Instr``:

- ``fs1`` / ``fs2`` / ``fs3`` / ``fd`` — floating-point register operands.
- ``rm`` — per-instruction rounding mode (RNE/RTZ/RDN/RUP/RMM).
- ``use_rounding_mode_from_instr`` — whether to append ``, <rm>`` to the asm
  string (SV default: randomize; we default to True so ARITHMETIC ops get a
  rounding mode suffix matching golden output).

The operand layout per format matches SV exactly (see convert2asm below).
"""

from __future__ import annotations

import random

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    MAX_INSTR_STR_LEN,
    FRoundingMode,
    RiscvFpr,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.utils import format_string


_FMT = RiscvInstrFormat
_CAT = RiscvInstrCategory
_N = RiscvInstrName


# Names that take an integer rd from an fp input (FP→int).
_FP_TO_INT_NAMES = frozenset({
    _N.FMV_X_W, _N.FMV_X_D,
    _N.FCVT_W_S, _N.FCVT_WU_S, _N.FCVT_L_S, _N.FCVT_LU_S,
    _N.FCVT_W_D, _N.FCVT_WU_D, _N.FCVT_L_D, _N.FCVT_LU_D,
})

# Names that take an integer rs1 to an fp output (int→FP).
_INT_TO_FP_NAMES = frozenset({
    _N.FMV_W_X, _N.FMV_D_X,
    _N.FCVT_S_W, _N.FCVT_S_WU, _N.FCVT_S_L, _N.FCVT_S_LU,
    _N.FCVT_D_W, _N.FCVT_D_WU, _N.FCVT_D_L, _N.FCVT_D_LU,
})

# Names that don't take a rounding-mode suffix (per SV convert2asm guard).
_NO_RM_NAMES = frozenset({
    _N.FMIN_S, _N.FMAX_S, _N.FMIN_D, _N.FMAX_D,
    _N.FMV_W_X, _N.FMV_X_W, _N.FMV_D_X, _N.FMV_X_D,
    _N.FCLASS_S, _N.FCLASS_D,
    _N.FCVT_D_S, _N.FCVT_D_W, _N.FCVT_D_WU,
    _N.FSGNJ_S, _N.FSGNJN_S, _N.FSGNJX_S,
    _N.FSGNJ_D, _N.FSGNJN_D, _N.FSGNJX_D,
})

# Single-source R-format ops (FCLASS_S / FCLASS_D) — rd, fs1.
_FCLASS_NAMES = frozenset({_N.FCLASS_S, _N.FCLASS_D})


class FloatingPointInstr(Instr):
    """Base class for every RV32F / RV64F / RV32D / RV64D instruction."""

    __slots__ = ("fs1", "fs2", "fs3", "fd", "rm", "has_fs1", "has_fs2", "has_fs3",
                 "has_fd", "use_rounding_mode_from_instr")

    def __init__(self) -> None:
        super().__init__()
        self.fs1: RiscvFpr = RiscvFpr.FT0
        self.fs2: RiscvFpr = RiscvFpr.FT0
        self.fs3: RiscvFpr = RiscvFpr.FT0
        self.fd: RiscvFpr = RiscvFpr.FT0
        self.rm: FRoundingMode = FRoundingMode.RNE
        self.has_fs1: bool = True
        self.has_fs2: bool = True
        self.has_fs3: bool = False
        self.has_fd: bool = True
        self.use_rounding_mode_from_instr: bool = True
        self.is_floating_point = True

    # -------- set_rand_mode (per SV) --------

    def set_rand_mode(self) -> None:
        """Port of SV ``set_rand_mode`` (riscv_floating_point_instr.sv:122)."""
        # Default: disable all int operand rand.
        self.has_rs1 = False
        self.has_rs2 = False
        self.has_rd = False
        self.has_imm = False
        # Re-apply FP defaults (in case __slots__ defaults elided).
        self.has_fs1 = True
        self.has_fs2 = True
        self.has_fs3 = False
        self.has_fd = True
        name = self.instr_name
        fmt = self.format

        if fmt == _FMT.I_FORMAT:
            self.has_fs2 = False
            if self.category == _CAT.LOAD:
                self.has_imm = True
                self.has_rs1 = True
            elif name in _FP_TO_INT_NAMES:
                self.has_fd = False
                self.has_rd = True
            elif name in _INT_TO_FP_NAMES:
                self.has_rs1 = True
                self.has_fs1 = False
        elif fmt == _FMT.S_FORMAT:
            self.has_imm = True
            self.has_rs1 = True
            self.has_fs1 = False
            self.has_fs3 = False
        elif fmt == _FMT.R_FORMAT:
            if self.category == _CAT.COMPARE:
                self.has_rd = True
                self.has_fd = False
            elif name in _FCLASS_NAMES:
                self.has_rd = True
                self.has_fd = False
                self.has_fs2 = False
        elif fmt == _FMT.R4_FORMAT:
            self.has_fs3 = True
        elif fmt == _FMT.CL_FORMAT:
            self.has_imm = True
            self.has_rs1 = True
            self.has_fs1 = False
            self.has_fs2 = False
        elif fmt == _FMT.CS_FORMAT:
            self.has_imm = True
            self.has_rs1 = True
            self.has_fs1 = False
            self.has_fd = False
        elif fmt == _FMT.CSS_FORMAT:
            self.has_rs1 = False
            self.has_fd = False
        elif fmt == _FMT.CI_FORMAT:
            self.has_rs1 = False
            self.has_fs2 = False

    def set_imm_len(self) -> None:
        """SV: ``set_imm_len`` (riscv_floating_point_instr.sv:101)."""
        if self.format in (_FMT.CL_FORMAT, _FMT.CS_FORMAT):
            self.imm_len = 5
        elif self.format in (_FMT.CI_FORMAT, _FMT.CSS_FORMAT):
            self.imm_len = 6
        elif self.format in (_FMT.I_FORMAT, _FMT.S_FORMAT):
            self.imm_len = 12
        else:
            self.imm_len = 0
        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF

    def randomize_imm(self, rng, xlen: int) -> None:
        """FP instructions' imm is only used by load/store pseudos (12-bit signed)."""
        if self.imm_len:
            self.imm = rng.getrandbits(self.imm_len)

    def randomize_fpr_operands(self, rng: random.Random) -> None:
        """Pick random FP registers for the has_fs*/has_fd operands.

        Caller invokes after :func:`set_rand_mode` has set the has_* flags.
        """
        regs = list(RiscvFpr)
        if self.has_fs1:
            self.fs1 = rng.choice(regs)
        if self.has_fs2:
            self.fs2 = rng.choice(regs)
        if self.has_fs3:
            self.fs3 = rng.choice(regs)
        if self.has_fd:
            self.fd = rng.choice(regs)
        self.rm = rng.choice(list(FRoundingMode))

    # -------- Assembly output --------

    def convert2asm(self, prefix: str = "") -> str:
        """Port of SV ``convert2asm`` (riscv_floating_point_instr.sv:46)."""
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm_str = mnemonic
        name = self.instr_name
        fmt = self.format

        if fmt == _FMT.I_FORMAT:
            if self.category == _CAT.LOAD:
                asm_str = f"{mnemonic}{self.fd.name}, {self.get_imm()}({self.rs1.name})"
            elif name in _FP_TO_INT_NAMES:
                asm_str = f"{mnemonic}{self.rd.name}, {self.fs1.name}"
            elif name in _INT_TO_FP_NAMES:
                asm_str = f"{mnemonic}{self.fd.name}, {self.rs1.name}"
            else:
                asm_str = f"{mnemonic}{self.fd.name}, {self.fs1.name}"
        elif fmt == _FMT.S_FORMAT:
            asm_str = f"{mnemonic}{self.fs2.name}, {self.get_imm()}({self.rs1.name})"
        elif fmt == _FMT.R_FORMAT:
            if self.category == _CAT.COMPARE:
                asm_str = f"{mnemonic}{self.rd.name}, {self.fs1.name}, {self.fs2.name}"
            elif name in _FCLASS_NAMES:
                asm_str = f"{mnemonic}{self.rd.name}, {self.fs1.name}"
            else:
                asm_str = f"{mnemonic}{self.fd.name}, {self.fs1.name}, {self.fs2.name}"
        elif fmt == _FMT.R4_FORMAT:
            asm_str = (
                f"{mnemonic}{self.fd.name}, {self.fs1.name}, "
                f"{self.fs2.name}, {self.fs3.name}"
            )
        elif fmt == _FMT.CL_FORMAT:
            asm_str = f"{mnemonic}{self.fd.name}, {self.get_imm()}({self.rs1.name})"
        elif fmt == _FMT.CS_FORMAT:
            asm_str = f"{mnemonic}{self.fs2.name}, {self.get_imm()}({self.rs1.name})"
        elif fmt == _FMT.CSS_FORMAT:
            asm_str = f"{mnemonic}{self.fs2.name}, {self.get_imm()}(sp)"
        elif fmt == _FMT.CI_FORMAT:
            asm_str = f"{mnemonic}{self.fd.name}, {self.get_imm()}"

        # Append rounding mode for ARITHMETIC ops per SV guard.
        if (
            self.category == _CAT.ARITHMETIC
            and self.use_rounding_mode_from_instr
            and name not in _NO_RM_NAMES
        ):
            asm_str = f"{asm_str}, {self.rm.name}"

        if self.comment:
            asm_str = f"{asm_str} #{self.comment}"
        return asm_str.lower()

    # -------- Binary encoding: deferred to step 11 ----------
    # FP encoding is format-specific (FP has its own func7 table for R/R4).
    # Our generator produces .S that GCC assembles; convert2bin is only used
    # for illegal-instr emission. Raise clearly when called.

    def convert2bin(self, prefix: str = "") -> str:
        raise NotImplementedError(
            "FP instruction binary encoding not implemented in Phase 1. "
            "Use convert2asm + assembler (GCC) to produce bytes."
        )

    def get_opcode(self) -> int:
        # FP-LOAD, FP-STORE, OP-FP opcodes — return placeholders for now.
        if self.category == _CAT.LOAD:
            return 0b0000111
        if self.category == _CAT.STORE:
            return 0b0100111
        return 0b1010011

    def get_func3(self) -> int:
        # Full func3 table is large; deferred. Shouldn't be called until
        # binary encoding is fully implemented.
        raise NotImplementedError("FP get_func3 deferred")
