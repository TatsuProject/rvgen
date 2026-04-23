"""RV32F registrations — port of ``src/isa/rv32f_instr.sv``."""

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
    define_instr(name, fmt, cat, G.RV32F, base=FloatingPointInstr)


_fp(N.FLW, F.I_FORMAT, C.LOAD)
_fp(N.FSW, F.S_FORMAT, C.STORE)
_fp(N.FMADD_S, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FMSUB_S, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FNMSUB_S, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FNMADD_S, F.R4_FORMAT, C.ARITHMETIC)
_fp(N.FADD_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSUB_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FMUL_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FDIV_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSQRT_S, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FSGNJ_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSGNJN_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FSGNJX_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FMIN_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FMAX_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_W_S, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_WU_S, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FMV_X_W, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FEQ_S, F.R_FORMAT, C.COMPARE)
_fp(N.FLT_S, F.R_FORMAT, C.COMPARE)
_fp(N.FLE_S, F.R_FORMAT, C.COMPARE)
_fp(N.FCLASS_S, F.R_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_S_W, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FCVT_S_WU, F.I_FORMAT, C.ARITHMETIC)
_fp(N.FMV_W_X, F.I_FORMAT, C.ARITHMETIC)
