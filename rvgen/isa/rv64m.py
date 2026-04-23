"""RV64M instructions — port of ``src/isa/rv64m_instr.sv``."""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr


define_instr(N.MULW, F.R_FORMAT, C.ARITHMETIC, G.RV64M)
define_instr(N.DIVW, F.R_FORMAT, C.ARITHMETIC, G.RV64M)
define_instr(N.DIVUW, F.R_FORMAT, C.ARITHMETIC, G.RV64M)
define_instr(N.REMW, F.R_FORMAT, C.ARITHMETIC, G.RV64M)
define_instr(N.REMUW, F.R_FORMAT, C.ARITHMETIC, G.RV64M)


RV64M_INSTR_NAMES = (N.MULW, N.DIVW, N.DIVUW, N.REMW, N.REMUW)
