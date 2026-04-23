"""RV32M multiply/divide instructions — port of ``src/isa/rv32m_instr.sv``.

All 8 instructions are R_FORMAT, ARITHMETIC category, RV32M group.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr


define_instr(N.MUL, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.MULH, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.MULHSU, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.MULHU, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.DIV, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.DIVU, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.REM, F.R_FORMAT, C.ARITHMETIC, G.RV32M)
define_instr(N.REMU, F.R_FORMAT, C.ARITHMETIC, G.RV32M)


RV32M_INSTR_NAMES = (
    N.MUL, N.MULH, N.MULHSU, N.MULHU, N.DIV, N.DIVU, N.REM, N.REMU,
)
