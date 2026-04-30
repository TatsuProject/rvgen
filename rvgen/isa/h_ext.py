"""H-extension — RISC-V Hypervisor (ratified 2021-11).

Adds HS-mode + Virtual-S/U-mode + two-stage address translation. This
module ports the **instruction surface** (16 mnemonics) and the new
group enum :data:`RV64H`. The H-mode CSRs (HSTATUS, HEDELEG, HIDELEG,
HIE, HCOUNTEREN, HGEIE, HTVAL, HIP, HVIP, HTINST, HGEIP, HENVCFG, HGATP,
HCONTEXT, HTIMEDELTA, VSSTATUS, VSIE, VSTVEC, VSSCRATCH, VSEPC, VSCAUSE,
VSTVAL, VSIP, VSATP) are already in :class:`PrivilegedReg`.

Two-stage translation (G-stage on top of VS-stage) and the full HS-mode
boot/trap shape ride a future release. For now: the random stream can
emit hypervisor instructions, targets advertising ``RV64H`` filter them
in, and the H-mode CSR namespace is reachable from CSR-write streams.

Mnemonic shapes
---------------

* ``hfence.vvma rs1, rs2`` and ``hfence.gvma rs1, rs2`` — TLB flushes.
  R-format-ish but emits two operands only (no ``rd``). Mirrors
  ``sfence.vma``.
* ``hlv.<b|bu|h|hu|w|wu|d> rd, (rs1)`` and ``hlvx.<hu|wu> rd, (rs1)``
  — guest-virtual loads. No immediate offset. Treated as a LOAD-category
  R-format with ``rd``/``rs1`` only.
* ``hsv.<b|h|w|d> rs2, (rs1)`` — guest-virtual stores. No ``rd``, no imm.

The encoders (``convert2bin``) defer to the assembler — modern GCC
(13.x+) handles every H-ext mnemonic with ``-march=...h``. The
generator only needs to emit the asm text.
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
# HFENCE.VVMA / HFENCE.GVMA — TLB-flush instructions.
# Asm: ``hfence.vvma rs1, rs2`` (mirrors sfence.vma).
# ---------------------------------------------------------------------------


class _HFenceInstr(Instr):
    """``hfence.<vvma|gvma> rs1, rs2`` — no rd, no imm."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_rd = False
        self.has_imm = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.rs1.name}, {self.rs2.name}"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


define_instr(N.HFENCE_VVMA, F.R_FORMAT, C.SYNCH, G.RV64H, base=_HFenceInstr)
define_instr(N.HFENCE_GVMA, F.R_FORMAT, C.SYNCH, G.RV64H, base=_HFenceInstr)


# ---------------------------------------------------------------------------
# HLV.* / HLVX.* — hypervisor virtual-mode loads.
# Asm: ``hlv.b rd, (rs1)`` (no immediate).
# ---------------------------------------------------------------------------


class _HLoadInstr(Instr):
    """``hlv[x].<size> rd, (rs1)`` — guest-virtual load, no imm, no rs2."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_rs2 = False
        self.has_imm = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.rd.name}, ({self.rs1.name})"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


for _n in (
    N.HLV_B, N.HLV_BU, N.HLV_H, N.HLV_HU, N.HLVX_HU,
    N.HLV_W, N.HLV_WU, N.HLVX_WU, N.HLV_D,
):
    define_instr(_n, F.I_FORMAT, C.LOAD, G.RV64H, base=_HLoadInstr)


# ---------------------------------------------------------------------------
# HSV.* — hypervisor virtual-mode stores.
# Asm: ``hsv.b rs2, (rs1)`` (no rd, no imm).
# ---------------------------------------------------------------------------


class _HStoreInstr(Instr):
    """``hsv.<size> rs2, (rs1)`` — guest-virtual store, no rd, no imm."""

    def set_rand_mode(self) -> None:
        super().set_rand_mode()
        self.has_rd = False
        self.has_imm = False

    def set_imm_len(self) -> None:
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm = f"{mnemonic}{self.rs2.name}, ({self.rs1.name})"
        if self.comment:
            asm = f"{asm} #{self.comment}"
        return asm.lower()


for _n in (N.HSV_B, N.HSV_H, N.HSV_W, N.HSV_D):
    define_instr(_n, F.S_FORMAT, C.STORE, G.RV64H, base=_HStoreInstr)


# Public catalog — useful for tests and target plumbing.
H_FENCE_INSTR_NAMES = (N.HFENCE_VVMA, N.HFENCE_GVMA)
H_LOAD_INSTR_NAMES = (
    N.HLV_B, N.HLV_BU, N.HLV_H, N.HLV_HU, N.HLVX_HU,
    N.HLV_W, N.HLV_WU, N.HLVX_WU, N.HLV_D,
)
H_STORE_INSTR_NAMES = (N.HSV_B, N.HSV_H, N.HSV_W, N.HSV_D)
RV64H_INSTR_NAMES = H_FENCE_INSTR_NAMES + H_LOAD_INSTR_NAMES + H_STORE_INSTR_NAMES
