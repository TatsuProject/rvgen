"""RV32A atomic-memory-operation registrations — port of ``src/isa/rv32a_instr.sv``.

All 11 RV32A instructions are R_FORMAT, category=AMO (marked as ``LOAD`` on
LR_W and ``STORE`` on SC_W per SV), group=RV32A. We use the AMO category
uniformly since riscv-dv treats them as a separate family anyway.
"""

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
    N.LR_W, N.SC_W,
    N.AMOSWAP_W, N.AMOADD_W, N.AMOAND_W, N.AMOOR_W, N.AMOXOR_W,
    N.AMOMIN_W, N.AMOMAX_W, N.AMOMINU_W, N.AMOMAXU_W,
):
    define_instr(_name, F.R_FORMAT, C.AMO, G.RV32A, base=AmoInstr)


RV32A_INSTR_NAMES = (
    N.LR_W, N.SC_W,
    N.AMOSWAP_W, N.AMOADD_W, N.AMOAND_W, N.AMOOR_W, N.AMOXOR_W,
    N.AMOMIN_W, N.AMOMAX_W, N.AMOMINU_W, N.AMOMAXU_W,
)
