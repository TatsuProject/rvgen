"""RV32C compressed registrations — port of ``src/isa/rv32c_instr.sv``."""

from __future__ import annotations

from rvgen.isa.compressed import CompressedInstr
from rvgen.isa.enums import (
    ImmType,
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr


def _c(name, fmt, cat, imm=ImmType.IMM):
    define_instr(name, fmt, cat, G.RV32C, imm, base=CompressedInstr)


_c(N.C_LW, F.CL_FORMAT, C.LOAD, ImmType.UIMM)
_c(N.C_SW, F.CS_FORMAT, C.STORE, ImmType.UIMM)
_c(N.C_LWSP, F.CI_FORMAT, C.LOAD, ImmType.UIMM)
_c(N.C_SWSP, F.CSS_FORMAT, C.STORE, ImmType.UIMM)
_c(N.C_ADDI4SPN, F.CIW_FORMAT, C.ARITHMETIC, ImmType.NZUIMM)
_c(N.C_ADDI, F.CI_FORMAT, C.ARITHMETIC, ImmType.NZIMM)
_c(N.C_ADDI16SP, F.CI_FORMAT, C.ARITHMETIC, ImmType.NZIMM)
_c(N.C_LI, F.CI_FORMAT, C.ARITHMETIC)
_c(N.C_LUI, F.CI_FORMAT, C.ARITHMETIC, ImmType.NZIMM)
_c(N.C_SUB, F.CA_FORMAT, C.ARITHMETIC)
_c(N.C_ADD, F.CR_FORMAT, C.ARITHMETIC)
_c(N.C_NOP, F.CI_FORMAT, C.ARITHMETIC)
_c(N.C_MV, F.CR_FORMAT, C.ARITHMETIC)
_c(N.C_ANDI, F.CB_FORMAT, C.LOGICAL)
_c(N.C_XOR, F.CA_FORMAT, C.LOGICAL)
_c(N.C_OR, F.CA_FORMAT, C.LOGICAL)
_c(N.C_AND, F.CA_FORMAT, C.LOGICAL)
_c(N.C_BEQZ, F.CB_FORMAT, C.BRANCH)
_c(N.C_BNEZ, F.CB_FORMAT, C.BRANCH)
_c(N.C_SRLI, F.CB_FORMAT, C.SHIFT, ImmType.NZUIMM)
_c(N.C_SRAI, F.CB_FORMAT, C.SHIFT, ImmType.NZUIMM)
_c(N.C_SLLI, F.CI_FORMAT, C.SHIFT, ImmType.NZUIMM)
_c(N.C_J, F.CJ_FORMAT, C.JUMP)
_c(N.C_JAL, F.CJ_FORMAT, C.JUMP)
_c(N.C_JR, F.CR_FORMAT, C.JUMP)
_c(N.C_JALR, F.CR_FORMAT, C.JUMP)
_c(N.C_EBREAK, F.CI_FORMAT, C.SYSTEM)


RV32C_INSTR_NAMES = (
    N.C_LW, N.C_SW, N.C_LWSP, N.C_SWSP,
    N.C_ADDI4SPN, N.C_ADDI, N.C_ADDI16SP, N.C_LI, N.C_LUI,
    N.C_SUB, N.C_ADD, N.C_NOP, N.C_MV,
    N.C_ANDI, N.C_XOR, N.C_OR, N.C_AND,
    N.C_BEQZ, N.C_BNEZ,
    N.C_SRLI, N.C_SRAI, N.C_SLLI,
    N.C_J, N.C_JAL, N.C_JR, N.C_JALR,
    N.C_EBREAK,
)
