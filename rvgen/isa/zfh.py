"""Zfh — scalar half-precision (16-bit) floating-point extension.

Ratified 2022. Adds 25 new mnemonics on RV32 (FLH, FSH, FADD.H, ...,
FCVT.D.H) plus 4 RV64-only conversions (FCVT.L.H, FCVT.LU.H,
FCVT.H.L, FCVT.H.LU). The encoding shape mirrors the RV32F/RV64F/RV32D
ops one-for-one — only the funct7 byte differs at the encoding level
and the mnemonic suffix differs at the asm level. Our generator
produces ``.S`` text and lets GCC handle the actual encoding, so all
we need to do is register the names against the existing
:class:`~rvgen.isa.floating_point.FloatingPointInstr` base.

The single piece of real glue is keeping
:mod:`rvgen.isa.floating_point` aware of which Zfh names are FP↔int
moves vs FP→int conversions vs sign-manipulation (no-rounding-mode)
ops. Those name sets live in ``floating_point.py``.

Targets that advertise the new ``RV32ZFH`` / ``RV64ZFH`` group enums
will pick the instructions up via the standard ``filter_by_target_isa``
pipeline; existing FP-handling streams (LoadStoreBaseInstrStream,
floating-point arithmetic test, etc.) work unchanged because the new
mnemonics use the same FP-register / FP-rm machinery.

GCC support: ``-march=rv32ifh_zicsr`` / ``rv64ifh_zicsr`` accepts every
Zfh mnemonic on GCC 14.x+. We add the ``_zfh`` shorthand to target
``isa_string`` strings.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr
from rvgen.isa.floating_point import FloatingPointInstr


def _zfh32(name: N, fmt: F, cat: C) -> None:
    define_instr(name, fmt, cat, G.RV32ZFH, base=FloatingPointInstr)


# ---------------------------------------------------------------------------
# RV32ZFH — base 25 mnemonics.
# ---------------------------------------------------------------------------

# Loads / stores (12-bit signed offset; same I/S-format shape as FLW/FSW).
_zfh32(N.FLH, F.I_FORMAT, C.LOAD)
_zfh32(N.FSH, F.S_FORMAT, C.STORE)

# 4-operand FMA — R4-format (fd = ±(fs1 × fs2) ± fs3).
_zfh32(N.FMADD_H,  F.R4_FORMAT, C.ARITHMETIC)
_zfh32(N.FMSUB_H,  F.R4_FORMAT, C.ARITHMETIC)
_zfh32(N.FNMSUB_H, F.R4_FORMAT, C.ARITHMETIC)
_zfh32(N.FNMADD_H, F.R4_FORMAT, C.ARITHMETIC)

# Arithmetic (R-format with fd, fs1, fs2 + rounding-mode suffix).
_zfh32(N.FADD_H, F.R_FORMAT, C.ARITHMETIC)
_zfh32(N.FSUB_H, F.R_FORMAT, C.ARITHMETIC)
_zfh32(N.FMUL_H, F.R_FORMAT, C.ARITHMETIC)
_zfh32(N.FDIV_H, F.R_FORMAT, C.ARITHMETIC)

# Sqrt is I-format-shaped (single source); but emits as fsqrt.h fd, fs1, rm
# — FloatingPointInstr.set_rand_mode already handles that via FCLASS-style
# detection, but FSQRT specifically needs has_fs2=False. The base class
# I_FORMAT branch takes the "else" path for non-LOAD/non-FCVT names which
# already does fd = fs1 only (no fs2), matching FSQRT.
_zfh32(N.FSQRT_H, F.I_FORMAT, C.ARITHMETIC)

# Sign manipulation — no rounding mode (NO_RM_NAMES).
_zfh32(N.FSGNJ_H,  F.R_FORMAT, C.ARITHMETIC)
_zfh32(N.FSGNJN_H, F.R_FORMAT, C.ARITHMETIC)
_zfh32(N.FSGNJX_H, F.R_FORMAT, C.ARITHMETIC)

# Min / max — no rounding mode.
_zfh32(N.FMIN_H, F.R_FORMAT, C.ARITHMETIC)
_zfh32(N.FMAX_H, F.R_FORMAT, C.ARITHMETIC)

# FP → int conversions (32-bit). I-format with fd = rd, fs1 = fp source.
_zfh32(N.FCVT_W_H,  F.I_FORMAT, C.ARITHMETIC)
_zfh32(N.FCVT_WU_H, F.I_FORMAT, C.ARITHMETIC)

# Bit move — no rounding mode (raw bit shuffle of low 16 bits).
_zfh32(N.FMV_X_H, F.I_FORMAT, C.ARITHMETIC)

# Compare — emits ``feq.h rd, fs1, fs2`` etc. R-format COMPARE.
_zfh32(N.FEQ_H, F.R_FORMAT, C.COMPARE)
_zfh32(N.FLT_H, F.R_FORMAT, C.COMPARE)
_zfh32(N.FLE_H, F.R_FORMAT, C.COMPARE)

# Classification — single-source R-format (FCLASS_NAMES).
_zfh32(N.FCLASS_H, F.R_FORMAT, C.ARITHMETIC)

# Int → FP conversions (32-bit).
_zfh32(N.FCVT_H_W,  F.I_FORMAT, C.ARITHMETIC)
_zfh32(N.FCVT_H_WU, F.I_FORMAT, C.ARITHMETIC)
_zfh32(N.FMV_H_X,   F.I_FORMAT, C.ARITHMETIC)

# Cross-precision conversions (single↔half, double↔half).
# FCVT.S.H widens half→single losslessly (no rm in NO_RM_NAMES).
# FCVT.H.S narrows single→half (rm needed).
# FCVT.D.H widens half→double; FCVT.H.D narrows double→half.
_zfh32(N.FCVT_S_H, F.I_FORMAT, C.ARITHMETIC)
_zfh32(N.FCVT_H_S, F.I_FORMAT, C.ARITHMETIC)
_zfh32(N.FCVT_D_H, F.I_FORMAT, C.ARITHMETIC)
_zfh32(N.FCVT_H_D, F.I_FORMAT, C.ARITHMETIC)


# ---------------------------------------------------------------------------
# RV64ZFH — 4 additional 64-bit-int <-> half conversions.
# ---------------------------------------------------------------------------

for _n in (N.FCVT_L_H, N.FCVT_LU_H, N.FCVT_H_L, N.FCVT_H_LU):
    define_instr(_n, F.I_FORMAT, C.ARITHMETIC, G.RV64ZFH,
                 base=FloatingPointInstr)


# Public catalog — useful for tests + targets.
RV32ZFH_INSTR_NAMES = (
    N.FLH, N.FSH,
    N.FMADD_H, N.FMSUB_H, N.FNMSUB_H, N.FNMADD_H,
    N.FADD_H, N.FSUB_H, N.FMUL_H, N.FDIV_H, N.FSQRT_H,
    N.FSGNJ_H, N.FSGNJN_H, N.FSGNJX_H,
    N.FMIN_H, N.FMAX_H,
    N.FCVT_W_H, N.FCVT_WU_H, N.FMV_X_H,
    N.FEQ_H, N.FLT_H, N.FLE_H,
    N.FCLASS_H,
    N.FCVT_H_W, N.FCVT_H_WU, N.FMV_H_X,
    N.FCVT_S_H, N.FCVT_H_S,
    N.FCVT_D_H, N.FCVT_H_D,
)

RV64ZFH_INSTR_NAMES = (
    N.FCVT_L_H, N.FCVT_LU_H, N.FCVT_H_L, N.FCVT_H_LU,
)
