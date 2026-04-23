"""RV64I instructions — port of ``src/isa/rv64i_instr.sv``.

Adds the RV64-specific word-sized ops to the RV32I base.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    ImmType,
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr


# LOAD / STORE (RV64-only widths)
define_instr(N.LWU, F.I_FORMAT, C.LOAD, G.RV64I)
define_instr(N.LD, F.I_FORMAT, C.LOAD, G.RV64I)
define_instr(N.SD, F.S_FORMAT, C.STORE, G.RV64I)

# Word-sized ALU ops (SV names with the "W" suffix)
define_instr(N.ADDIW, F.I_FORMAT, C.ARITHMETIC, G.RV64I)
define_instr(N.SLLIW, F.I_FORMAT, C.SHIFT, G.RV64I)
define_instr(N.SRLIW, F.I_FORMAT, C.SHIFT, G.RV64I)
define_instr(N.SRAIW, F.I_FORMAT, C.SHIFT, G.RV64I)
define_instr(N.ADDW, F.R_FORMAT, C.ARITHMETIC, G.RV64I)
define_instr(N.SUBW, F.R_FORMAT, C.ARITHMETIC, G.RV64I)
define_instr(N.SLLW, F.R_FORMAT, C.SHIFT, G.RV64I)
define_instr(N.SRLW, F.R_FORMAT, C.SHIFT, G.RV64I)
define_instr(N.SRAW, F.R_FORMAT, C.SHIFT, G.RV64I)


RV64I_INSTR_NAMES = (
    N.LWU, N.LD, N.SD,
    N.ADDIW, N.SLLIW, N.SRLIW, N.SRAIW,
    N.ADDW, N.SUBW, N.SLLW, N.SRLW, N.SRAW,
)
