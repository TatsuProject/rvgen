"""RV64C compressed registrations — port of ``src/isa/rv64c_instr.sv``."""

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
    define_instr(name, fmt, cat, G.RV64C, imm, base=CompressedInstr)


_c(N.C_ADDIW, F.CI_FORMAT, C.ARITHMETIC)
_c(N.C_SUBW, F.CA_FORMAT, C.ARITHMETIC)
_c(N.C_ADDW, F.CA_FORMAT, C.ARITHMETIC)
_c(N.C_LD, F.CL_FORMAT, C.LOAD, ImmType.UIMM)
_c(N.C_SD, F.CS_FORMAT, C.STORE, ImmType.UIMM)
_c(N.C_LDSP, F.CI_FORMAT, C.LOAD, ImmType.UIMM)
_c(N.C_SDSP, F.CSS_FORMAT, C.STORE, ImmType.UIMM)


RV64C_INSTR_NAMES = (
    N.C_ADDIW, N.C_SUBW, N.C_ADDW,
    N.C_LD, N.C_SD, N.C_LDSP, N.C_SDSP,
)
