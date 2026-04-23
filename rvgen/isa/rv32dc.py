"""RV32DC (compressed double-precision FP) — port of ``src/isa/rv32dc_instr.sv``.

Same shape as :mod:`rvgen.isa.rv32fc` but for double-precision
load/store (``c.fld`` / ``c.fsd`` / ``c.fldsp`` / ``c.fsdsp``). These are
valid on both RV32 and RV64 when ``D`` and ``C`` extensions are present.
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
from rvgen.isa.rv32fc import CompressedFpInstr


def _fc(name, fmt, cat):
    define_instr(name, fmt, cat, G.RV32DC, ImmType.UIMM, base=CompressedFpInstr)


_fc(N.C_FLD,   F.CL_FORMAT,  C.LOAD)
_fc(N.C_FSD,   F.CS_FORMAT,  C.STORE)
_fc(N.C_FLDSP, F.CI_FORMAT,  C.LOAD)
_fc(N.C_FSDSP, F.CSS_FORMAT, C.STORE)
