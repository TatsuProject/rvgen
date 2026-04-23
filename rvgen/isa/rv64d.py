"""RV64D registrations — port of ``src/isa/rv64d_instr.sv``."""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr
from rvgen.isa.floating_point import FloatingPointInstr


for _n in (N.FMV_X_D, N.FMV_D_X, N.FCVT_L_D, N.FCVT_LU_D, N.FCVT_D_L, N.FCVT_D_LU):
    define_instr(_n, F.I_FORMAT, C.ARITHMETIC, G.RV64D, base=FloatingPointInstr)
