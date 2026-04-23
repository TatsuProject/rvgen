"""RV32D registrations — port of ``src/isa/rv32d_instr.sv``."""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr
from rvgen.isa.floating_point import FloatingPointInstr


def _fp(name, fmt, cat):
    define_instr(name, fmt, cat, G.RV32D, base=FloatingPointInstr)


_fp(N.FLD, F.I_FORMAT, C.LOAD)
_fp(N.FSD, F.S_FORMAT, C.STORE)
_fp(N.FMADD_D, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FMSUB_D, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FNMSUB_D, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FNMADD_D, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FADD_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSUB_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FMUL_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FDIV_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSQRT_D, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FSGNJ_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSGNJN_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSGNJX_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FMIN_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FMAX_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_S_D, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_D_S, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FEQ_D, F.R_FORMAT, C.COMPARE)
_fp(N.FLT_D, F.R_FORMAT, C.COMPARE)
_fp(N.FLE_D, F.R_FORMAT, C.COMPARE)
_fp(N.FCLASS_D, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_W_D, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_WU_D, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_D_W, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_D_WU, F.I_FORMAT, C.ARITHMETIC)
