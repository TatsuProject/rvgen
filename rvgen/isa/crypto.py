"""RISC-V crypto extensions (Zbkb/Zbkc/Zbkx/Zknd/Zkne/Zknh/Zksh/Zksed).

The ratified K family bundles AES, SHA-2, SM3, SM4 and some supporting
bit-manip ops. riscv-dv's SV source never implemented them (they were still
being ratified), so this is net-new functionality on top of the SV parity
baseline.

Sub-extensions
--------------

- **Zbkb** — rotation + permutation + ANDN/ORN/XNOR + ``brev8``/``zip``/``unzip``.
  Most mnemonics overlap with Zbb/Zbc/draft-B (``ror``/``rol``/``rori``/
  ``andn``/``orn``/``xnor``/``pack``/``packh``/``rev8``) which we register once
  via :mod:`rvgen.isa.bitmanip`; only ``brev8``/``zip``/``unzip``
  are new here.
- **Zbkc** — carry-less multiply. The same two instructions as Zbc, so no new
  opcodes.
- **Zbkx** — byte/nibble permutation (``xperm.b``/``xperm.n``). Already
  registered as draft-B ``XPERM_B`` / ``XPERM_N``.
- **Zkne / Zknd** — AES encrypt + decrypt. RV32 uses 32-bit operations with a
  two-bit ``bs`` (byte-select) immediate; RV64 uses 64-bit mid/final-round
  variants plus key-schedule helpers (AES64KS1I, AES64KS2, AES64IM).
- **Zknh** — SHA-2 round helpers. SHA-256 sigma/sum ops work on both widths.
  SHA-512 has an RV32 split-register variant (``sha512sig0l`` etc.) and a
  single-instruction RV64 variant (``sha512sig0``).
- **Zksh / Zksed** — SM3 and SM4.

All instruction encodings are standard R-type (``rd, rs1, rs2``) or I-type
with a ``bs`` / ``rnum`` small immediate. We render them through the base
``Instr`` class's R/I-format formatters; the AES32* ops need a custom formatter
since they append the ``bs`` immediate (``aes32esi rd, rs1, rs2, bs``).

Binary encoding (``convert2bin``) is deferred — GCC 15.1+ can assemble all of
these when the right ``_zkn`` / ``_zks`` / ``_zbk*`` extension names are on
``-march``; we'll only need manual binary when emitting illegal-instruction
tests.
"""

from __future__ import annotations

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    ImmType,
    MAX_INSTR_STR_LEN,
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_instr
from rvgen.isa.utils import format_string


# ---------------------------------------------------------------------------
# Zbkb-only ops: brev8 (I-unary), zip/unzip (I-unary, RV32 only)
# ---------------------------------------------------------------------------


class _UnaryIInstr(Instr):
    """I-format unary ops (``rd, rs1``) — no immediate, no rs2.

    Covers ``brev8``, ``zip``, ``unzip``, ``aes64im`` (Zknd), ``sha256sig0``,
    etc. set_rand_mode clears has_imm + has_rs2; convert2asm renders just the
    two-register form.
    """

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = False
        self.has_rs2 = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.rd.name}, {self.rs1.name}"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


# ---------------------------------------------------------------------------
# AES 32-bit ops: aes32esi/aes32esmi/aes32dsi/aes32dsmi — R-format w/ bs imm
# ---------------------------------------------------------------------------


class _Aes32Instr(Instr):
    """AES RV32 ops: ``mnem rd, rs1, rs2, bs`` where bs is a 2-bit immediate.

    The SV-style encoding is an I-format with imm[1:0] = bs, but the asm form
    exposes bs as a trailing operand (matching GCC's accepted syntax).
    """

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = True

    def set_imm_len(self) -> None:
        self.imm_len = 2   # bs is 2 bits
        self.imm_mask = (0xFFFFFFFF << 2) & 0xFFFFFFFF

    def randomize_imm(self, rng, xlen: int) -> None:
        self.imm = rng.randint(0, 3)

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = (
            f"{mnemonic}{self.rd.name}, {self.rs1.name}, "
            f"{self.rs2.name}, {self.imm & 0x3}"
        )
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


# ---------------------------------------------------------------------------
# AES64KS1I — I-format with rnum (4-bit)
# ---------------------------------------------------------------------------


class _Aes64Ks1iInstr(Instr):
    """``aes64ks1i rd, rs1, rnum`` — rnum is a 4-bit round constant index."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_imm = True
        self.has_rs2 = False

    def set_imm_len(self) -> None:
        self.imm_len = 4
        self.imm_mask = (0xFFFFFFFF << 4) & 0xFFFFFFFF

    def randomize_imm(self, rng, xlen: int) -> None:
        # rnum 0..10 legal per spec; use full 4-bit range and let the assembler
        # flag anything outside (we don't want the generator to over-constrain).
        self.imm = rng.randint(0, 10)

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.rd.name}, {self.rs1.name}, {self.imm & 0xF}"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


# ---------------------------------------------------------------------------
# SM4ED / SM4KS — R-format with bs (2-bit) immediate, like aes32*
# ---------------------------------------------------------------------------


class _Sm4Instr(_Aes32Instr):
    """``sm4ed rd, rs1, rs2, bs`` / ``sm4ks rd, rs1, rs2, bs``."""

    # Identical encoding shape to _Aes32Instr — inherit everything.


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def _un_i(name, group):
    """I-unary (rd, rs1) registration."""
    define_instr(name, F.I_FORMAT, C.ARITHMETIC, group, base=_UnaryIInstr)


def _rtype(name, group, cat=C.ARITHMETIC):
    """Plain R-format (rd, rs1, rs2)."""
    define_instr(name, F.R_FORMAT, cat, group)


def _aes32(name, group):
    define_instr(name, F.I_FORMAT, C.ARITHMETIC, group,
                 ImmType.UIMM, base=_Aes32Instr)


def _aes64ks1i(name, group):
    define_instr(name, F.I_FORMAT, C.ARITHMETIC, group,
                 ImmType.UIMM, base=_Aes64Ks1iInstr)


def _sm4(name, group):
    define_instr(name, F.R_FORMAT, C.ARITHMETIC, group,
                 ImmType.UIMM, base=_Sm4Instr)


# Registration convention: one mnemonic = one class = one group. The filter
# layer maps RV32ZBKB → RV64ZBKB reachability (an RV64 target lists both
# RV32ZBKB and RV64ZBKB in supported_isa so the RV32ZBKB-registered BREV8 is
# picked up). ZIP/UNZIP are RV32-only per spec — they will only be emitted
# when the target includes RV32ZBKB and XLEN=32 (enforced by an xlen guard in
# filtering).

# --- Zbkb-only new ops ---
_un_i(N.BREV8, G.RV32ZBKB)
_un_i(N.ZIP,   G.RV32ZBKB)
_un_i(N.UNZIP, G.RV32ZBKB)

# --- RV32 AES ---
_aes32(N.AES32ESI,  G.RV32ZKNE)
_aes32(N.AES32ESMI, G.RV32ZKNE)
_aes32(N.AES32DSI,  G.RV32ZKND)
_aes32(N.AES32DSMI, G.RV32ZKND)

# --- RV64 AES ---
_rtype(N.AES64ES,  G.RV64ZKNE)
_rtype(N.AES64ESM, G.RV64ZKNE)
_rtype(N.AES64DS,  G.RV64ZKND)
_rtype(N.AES64DSM, G.RV64ZKND)
_aes64ks1i(N.AES64KS1I, G.RV64ZKNE)  # shared with Zknd but registered once under Zkne
_rtype(N.AES64KS2, G.RV64ZKNE)
_un_i(N.AES64IM, G.RV64ZKND)

# --- SHA-256 sigma/sum (register under RV32ZKNH; RV64 target lists both) ---
_un_i(N.SHA256SIG0, G.RV32ZKNH)
_un_i(N.SHA256SIG1, G.RV32ZKNH)
_un_i(N.SHA256SUM0, G.RV32ZKNH)
_un_i(N.SHA256SUM1, G.RV32ZKNH)

# --- SHA-512 RV32 H/L split pair (RV32ZKNH only) ---
for name in (N.SHA512SIG0L, N.SHA512SIG0H, N.SHA512SIG1L, N.SHA512SIG1H,
             N.SHA512SUM0R, N.SHA512SUM1R):
    _rtype(name, G.RV32ZKNH)

# --- SHA-512 RV64 single-instruction (RV64ZKNH only) ---
for name in (N.SHA512SIG0, N.SHA512SIG1, N.SHA512SUM0, N.SHA512SUM1):
    _un_i(name, G.RV64ZKNH)

# --- SM3 (Zksh) ---
_un_i(N.SM3P0, G.RV32ZKSH)
_un_i(N.SM3P1, G.RV32ZKSH)

# --- SM4 (Zksed) ---
_sm4(N.SM4ED, G.RV32ZKSED)
_sm4(N.SM4KS, G.RV32ZKSED)
