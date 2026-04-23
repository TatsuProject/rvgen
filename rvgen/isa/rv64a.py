"""RV64A atomic-memory-operation registrations — port of ``src/isa/rv64a_instr.sv``."""

from __future__ import annotations

from rvgen.isa.amo import AmoInstr
from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr


for _name in (
    N.LR_D, N.SC_D,
    N.AMOSWAP_D, N.AMOADD_D, N.AMOAND_D, N.AMOOR_D, N.AMOXOR_D,
    N.AMOMIN_D, N.AMOMAX_D, N.AMOMINU_D, N.AMOMAXU_D,
):
    define_instr(_name, F.R_FORMAT, C.AMO, G.RV64A, base=AmoInstr)


RV64A_INSTR_NAMES = (
    N.LR_D, N.SC_D,
    N.AMOSWAP_D, N.AMOADD_D, N.AMOAND_D, N.AMOOR_D, N.AMOXOR_D,
    N.AMOMIN_D, N.AMOMAX_D, N.AMOMINU_D, N.AMOMAXU_D,
)
