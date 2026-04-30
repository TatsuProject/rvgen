"""Modern checkbox RISC-V extensions ratified 2021-2024.

Bundles the small extensions that every up-to-date core advertises but that
riscv-dv's SV source predates: **Zicond**, **Zicbom**, **Zicboz**, **Zicbop**,
**Zihintpause**, **Zihintntl**, **Zimop**, **Zcmop**.

They are deliberately co-located in one module because each one only adds a
handful of mnemonics; splitting per-extension would be more boilerplate than
content. Group enums (``RV32ZICOND`` etc.) keep them filterable per-target.

Encoding notes
--------------

* **Zicond** — ``czero.eqz`` / ``czero.nez``: R-format on the OP/OP-32 opcode
  with funct7=0b0000111 + funct3=0x5/0x7. Same mnemonic on RV32 and RV64
  (XLEN-wide). Registered under both RV32ZICOND + RV64ZICOND so a target
  advertising either group picks them up.
* **Zicbom / Zicboz / Zicbop** — I-format MISC-MEM (opcode 0b0001111),
  funct3=0b010 with the ``rd`` field = 0 and the imm[11:0] field encoding
  the operation. Asm form: ``cbo.<op> (rs1)`` (no rd, no immediate).
  ``prefetch.<i|r|w>`` uses S-format encoding with rs2 = 0/1/3 and a 7-bit
  signed immediate concatenated as ``imm[11:5]<<5``. Asm form:
  ``prefetch.r imm(rs1)``.
* **Zihintpause** — encodes as ``fence w,0`` with the FM field = ``0b0001``;
  fixed encoding ``0x0100000F``. No operands.
* **Zihintntl** — HINT-encoded ``c.add`` variants. Mnemonics are
  ``ntl.p1``/``ntl.pall``/``ntl.s1``/``ntl.all``. Fixed compressed encodings
  at ``c.add x0, x{2,3,4,5}`` respectively. No operands.
* **Zimop** — 32 unary ``mop.r.N`` (I-format on opcode 0b1110011 with
  funct3=0b100 plus a sparse encoding of N) + 8 binary ``mop.rr.N``
  (R-format). The generator just emits the mnemonic + operands; the
  assembler handles the encoding.
* **Zcmop** — 8 compressed ``c.mop.N`` (N=1,3,...,15). C-format with no
  operands; encoding fixed per N.

Binary encoding (``convert2bin``) is left to the assembler — recent GCC
(14.x+) handles every mnemonic above with the right ``-march`` extension
suffix. We only need explicit encoding when building illegal-instr tests.
"""

from __future__ import annotations

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    MAX_INSTR_STR_LEN,
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr
from rvgen.isa.utils import format_string


# ---------------------------------------------------------------------------
# Zicond — czero.eqz / czero.nez (standard R-format).
# ---------------------------------------------------------------------------

# Both registered for RV32 and RV64 — the mnemonic is XLEN-agnostic so a
# target on either ISA picks them up identically.
define_instr(N.CZERO_EQZ, F.R_FORMAT, C.ARITHMETIC, G.RV32ZICOND)
define_instr(N.CZERO_NEZ, F.R_FORMAT, C.ARITHMETIC, G.RV32ZICOND)


# ---------------------------------------------------------------------------
# Zicbom / Zicboz — cbo.clean / cbo.flush / cbo.inval / cbo.zero
# Asm form: ``cbo.<op> (rs1)`` — no rd, no imm.
# ---------------------------------------------------------------------------


class _CboInstr(Instr):
    """``cbo.<op> (rs1)`` — single-operand cache-block hint."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = False
        self.has_rs2 = False
        self.has_rd = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}({self.rs1.name})"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


define_instr(N.CBO_CLEAN, F.I_FORMAT, C.SYNCH, G.RV32ZICBOM, base=_CboInstr)
define_instr(N.CBO_FLUSH, F.I_FORMAT, C.SYNCH, G.RV32ZICBOM, base=_CboInstr)
define_instr(N.CBO_INVAL, F.I_FORMAT, C.SYNCH, G.RV32ZICBOM, base=_CboInstr)
define_instr(N.CBO_ZERO,  F.I_FORMAT, C.SYNCH, G.RV32ZICBOZ, base=_CboInstr)


# ---------------------------------------------------------------------------
# Zicbop — prefetch.i / prefetch.r / prefetch.w
# Asm form: ``prefetch.<i|r|w> imm(rs1)`` with imm = 12-bit signed *aligned*
# to 32 bytes (low 5 bits ignored by the encoding).
# ---------------------------------------------------------------------------


class _PrefetchInstr(Instr):
    """``prefetch.<i|r|w> imm(rs1)`` — software prefetch hint."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = True
        self.has_rs2 = False
        self.has_rd = False

    def set_imm_len(self) -> None:
        # 12-bit signed offset, but low 5 bits are ignored by the encoding.
        # Generate any 12-bit signed value; the assembler will mask the low
        # bits on emit.
        self.imm_len = 12
        self.imm_mask = (0xFFFFFFFF << 12) & 0xFFFFFFFF

    def randomize_imm(self, rng, xlen: int) -> None:
        # Pick a 7-bit-aligned offset shifted left 5 bits, then keep it as a
        # 12-bit signed value [-2048, 2047]. Aligning here keeps the asm
        # output looking like idiomatic prefetch (offsets are 32-byte multiples).
        self.imm = (rng.randint(-64, 63) << 5) & 0xFFF

    def get_imm(self) -> str:
        # Sign-extend the 12-bit field for the asm string.
        v = self.imm & 0xFFF
        if v & 0x800:
            v -= 0x1000
        return str(v)

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.get_imm()}({self.rs1.name})"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


define_instr(N.PREFETCH_I, F.S_FORMAT, C.SYNCH, G.RV32ZICBOP, base=_PrefetchInstr)
define_instr(N.PREFETCH_R, F.S_FORMAT, C.SYNCH, G.RV32ZICBOP, base=_PrefetchInstr)
define_instr(N.PREFETCH_W, F.S_FORMAT, C.SYNCH, G.RV32ZICBOP, base=_PrefetchInstr)


# ---------------------------------------------------------------------------
# Zihintpause — pause (no operands).
# ---------------------------------------------------------------------------


class _PauseInstr(Instr):
    """``pause`` — no operands."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = False
        self.has_rs1 = False
        self.has_rs2 = False
        self.has_rd = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        asm = "pause"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm


define_instr(N.PAUSE, F.I_FORMAT, C.SYNCH, G.RV32ZIHINTPAUSE, base=_PauseInstr)


# ---------------------------------------------------------------------------
# Zihintntl — non-temporal-locality hints. No operands; mnemonics use a
# trailing label after ntl. (e.g. ``ntl.p1``).
# ---------------------------------------------------------------------------


class _NtlInstr(Instr):
    """``ntl.<p1|pall|s1|all>`` — non-temporal-locality hint."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = False
        self.has_rs1 = False
        self.has_rs2 = False
        self.has_rd = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = mnemonic.rstrip()
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


define_instr(N.NTL_P1,   F.I_FORMAT, C.SYNCH, G.RV32ZIHINTNTL, base=_NtlInstr)
define_instr(N.NTL_PALL, F.I_FORMAT, C.SYNCH, G.RV32ZIHINTNTL, base=_NtlInstr)
define_instr(N.NTL_S1,   F.I_FORMAT, C.SYNCH, G.RV32ZIHINTNTL, base=_NtlInstr)
define_instr(N.NTL_ALL,  F.I_FORMAT, C.SYNCH, G.RV32ZIHINTNTL, base=_NtlInstr)


# ---------------------------------------------------------------------------
# Zimop — mop.r.N (32 unary) + mop.rr.N (8 binary).
# Asm form: ``mop.r.N rd, rs1`` and ``mop.rr.N rd, rs1, rs2``. The N is
# baked into the mnemonic; in the enum it's encoded as ``MOP_R_<n>``.
# ---------------------------------------------------------------------------


class _MopRInstr(Instr):
    """``mop.r.N rd, rs1`` — unary may-be-op."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = False
        self.has_rs2 = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def get_instr_name(self) -> str:
        # MOP_R_5 → mop.r.5
        n = int(self.instr_name.name.rsplit("_", 1)[1])
        return f"mop.r.{n}"

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.rd.name}, {self.rs1.name}"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


class _MopRrInstr(Instr):
    """``mop.rr.N rd, rs1, rs2`` — binary may-be-op."""

    def get_instr_name(self) -> str:
        n = int(self.instr_name.name.rsplit("_", 1)[1])
        return f"mop.rr.{n}"


for _n in range(32):
    define_instr(
        getattr(N, f"MOP_R_{_n}"),
        F.I_FORMAT, C.ARITHMETIC, G.RV32ZIMOP, base=_MopRInstr,
    )
for _n in range(8):
    define_instr(
        getattr(N, f"MOP_RR_{_n}"),
        F.R_FORMAT, C.ARITHMETIC, G.RV32ZIMOP, base=_MopRrInstr,
    )


# ---------------------------------------------------------------------------
# Zcmop — c.mop.N (N ∈ {1,3,5,7,9,11,13,15}). No operands.
# ---------------------------------------------------------------------------


class _CMopInstr(Instr):
    """``c.mop.N`` — compressed may-be-op, no operands."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = False
        self.has_rs1 = False
        self.has_rs2 = False
        self.has_rd = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def get_instr_name(self) -> str:
        n = int(self.instr_name.name.rsplit("_", 1)[1])
        return f"c.mop.{n}"

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = mnemonic.rstrip()
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


for _n in (1, 3, 5, 7, 9, 11, 13, 15):
    define_instr(
        getattr(N, f"C_MOP_{_n}"),
        F.CI_FORMAT, C.ARITHMETIC, G.RV32ZCMOP, base=_CMopInstr,
    )


# Public catalog — useful for tests + documentation.
ZICOND_INSTR_NAMES = (N.CZERO_EQZ, N.CZERO_NEZ)
ZICBOM_INSTR_NAMES = (N.CBO_CLEAN, N.CBO_FLUSH, N.CBO_INVAL)
ZICBOZ_INSTR_NAMES = (N.CBO_ZERO,)
ZICBOP_INSTR_NAMES = (N.PREFETCH_I, N.PREFETCH_R, N.PREFETCH_W)
ZIHINTPAUSE_INSTR_NAMES = (N.PAUSE,)
ZIHINTNTL_INSTR_NAMES = (N.NTL_P1, N.NTL_PALL, N.NTL_S1, N.NTL_ALL)
ZIMOP_R_INSTR_NAMES = tuple(getattr(N, f"MOP_R_{i}") for i in range(32))
ZIMOP_RR_INSTR_NAMES = tuple(getattr(N, f"MOP_RR_{i}") for i in range(8))
ZCMOP_INSTR_NAMES = tuple(
    getattr(N, f"C_MOP_{i}") for i in (1, 3, 5, 7, 9, 11, 13, 15)
)
