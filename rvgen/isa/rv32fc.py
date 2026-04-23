"""RV32FC (compressed single-precision FP) — port of ``src/isa/rv32fc_instr.sv``.

The four compressed-FP load/store mnemonics (``c.flw`` / ``c.fsw`` / ``c.flwsp``
/ ``c.fswsp``) inherit FP operand handling from :class:`FloatingPointInstr`
but are 16-bit encodings, so they must carry ``is_compressed = True`` for
branch-target byte-offset math to stay accurate.
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
from rvgen.isa.floating_point import FloatingPointInstr


class CompressedFpInstr(FloatingPointInstr):
    """FP base tagged with ``is_compressed = True``.

    SV inheritance is ``riscv_fp_instr``; the compressed-ness is implicit from
    the format alone. We surface it explicitly so :func:`post_process_instr`
    counts these as 2 bytes when resolving branch offsets.
    """

    def __init__(self) -> None:
        super().__init__()
        self.is_compressed = True


def _fc(name, fmt, cat):
    define_instr(name, fmt, cat, G.RV32FC, ImmType.UIMM, base=CompressedFpInstr)


_fc(N.C_FLW,   F.CL_FORMAT,  C.LOAD)
_fc(N.C_FSW,   F.CS_FORMAT,  C.STORE)
_fc(N.C_FLWSP, F.CI_FORMAT,  C.LOAD)
_fc(N.C_FSWSP, F.CSS_FORMAT, C.STORE)
