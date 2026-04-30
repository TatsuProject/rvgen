"""PMP (Physical Memory Protection) configuration + boot CSR sequence.

Port of ``src/riscv_pmp_cfg.sv`` and the relevant parts of
``riscv_asm_program_gen.sv::gen_pmp_setup``. Phase-1 scope: single-region
permissive default + 1..16-region randomization with TOR / NAPOT / NA4
addressing modes. ePMP (mseccfg.MML/MMWP) is registered but not
exercised by default.

Encoding reference (RISC-V Privileged Spec v1.12 §3.7):

* **pmpcfg<i>** — XLEN/8 cfg bytes packed into one CSR (4 on RV32, 8 on
  RV64). Each cfg byte: ``[L:0:0:A[1:0]:X:W:R]``  where bit 7 = L
  (lock), bits 4..3 = A (OFF=0/TOR=1/NA4=2/NAPOT=3), bits 2..0 = X/W/R.

* **pmpaddr<i>** — RV32: 32 bits = addr[33:2]. RV64: 54 bits =
  addr[55:2] (top 10 bits WARL=0). NAPOT encoding: a region of size
  2^G covering ``base..base+2^G-1`` is written as
  ``(base >> 2) | ((1 << (G - 3)) - 1)`` — i.e. fill the low (G-3)
  bits with 1s then a 0 then the high bits of the base.

This module exposes:

* :class:`PmpRegion` — one cfg+addr pair.
* :class:`PmpCfg`    — collection of regions + global PMP knobs.
* :func:`gen_setup_pmp` — emit the boot CSR-write sequence.
* :func:`napot_addr`   — helper to encode a NAPOT region.

The module deliberately avoids randomization in Phase-1: callers
construct a :class:`PmpCfg` explicitly. Random-PMP support lands in a
follow-up release once the ePMP corner cases are covered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from rvgen.config import Config
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    PrivilegedReg,
    RiscvReg,
)


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


class PmpAddrMode(IntEnum):
    """SV ``pmp_addr_mode_t`` (riscv_instr_pkg.sv:1132)."""

    OFF = 0b00
    TOR = 0b01
    NA4 = 0b10
    NAPOT = 0b11


@dataclass(slots=True)
class PmpRegion:
    """One PMP region (one pmpcfg byte + one pmpaddr CSR)."""

    l: int = 0
    a: PmpAddrMode = PmpAddrMode.TOR
    x: int = 1
    w: int = 1
    r: int = 1
    # The actual address as the user thinks of it (byte address).
    # NAPOT/TOR encoding is applied by :func:`pack_addr`.
    addr: int = 0
    # Optional comment that ends up in the asm output for traceability.
    comment: str = ""

    def cfg_byte(self) -> int:
        """Return the 8-bit pmpcfg byte for this region.

        Bit layout: ``[L | 0 | 0 | A[1:0] | X | W | R]``  (L is bit 7,
        A is bits 4..3, X/W/R are bits 2..0).
        """
        return (
            (self.l & 1) << 7
            | (int(self.a) & 0b11) << 3
            | (self.x & 1) << 2
            | (self.w & 1) << 1
            | (self.r & 1)
        )

    def pack_addr(self, xlen: int) -> int:
        """Return the value to write to pmpaddr<i>.

        For OFF/NA4: ``addr >> 2``.
        For TOR:     ``addr >> 2`` (the cfg byte selects TOR; the
                     hardware compares with the previous pmpaddr).
        For NAPOT:   ``addr`` is interpreted as already-encoded by the
                     caller (use :func:`napot_addr`); we shift right 2.

        On RV64 the actual CSR is 54 bits; we mask to that width.
        """
        v = self.addr >> 2
        if xlen == 32:
            return v & 0xFFFF_FFFF
        return v & ((1 << 54) - 1)


def napot_addr(base: int, region_size_log2: int) -> int:
    """Build the byte-address used as ``addr`` for a NAPOT region.

    A NAPOT region of size ``2^G`` bytes starting at ``base`` is
    encoded into pmpaddr as ``(base >> 2) | ((1 << (G - 3)) - 1)``.
    The convention here: pass the *byte* base address and the desired
    log2 size; the helper produces the byte-aligned value to assign
    to :attr:`PmpRegion.addr` (still pre-shift). :meth:`PmpRegion.pack_addr`
    will shift right 2 at packing time.

    Spec requires ``G >= 3`` (smallest NAPOT region is 8 bytes).
    """
    if region_size_log2 < 3:
        raise ValueError("NAPOT region size must be >= 2^3 (8) bytes")
    encoded = ((base >> 2) | ((1 << (region_size_log2 - 3)) - 1))
    # Shift back left 2 so PmpRegion.pack_addr can do the final >>2.
    return encoded << 2


@dataclass(slots=True)
class PmpCfg:
    """Top-level PMP configuration.

    Defaults match SV's ``set_defaults``: 1 region, TOR mode covering
    [0, pmp_max_offset), full RWX, no lock. ePMP knobs are present but
    only emitted to mseccfg when at least one is non-zero.
    """

    pmp_num_regions: int = 1
    pmp_granularity: int = 0
    regions: list[PmpRegion] = field(default_factory=list)
    # ePMP machine-security cfg. ``rlb=1`` matches SV default and lets
    # M-mode rewrite locked entries (useful in tests that exercise
    # locked/unlocked transitions).
    mseccfg_rlb: int = 1
    mseccfg_mmwp: int = 0
    mseccfg_mml: int = 0
    # When True, gen_setup_pmp emits no asm. Used by tests that want
    # to inherit Spike's permissive default.
    suppress_setup: bool = False


def make_default_cfg(xlen: int, num_regions: int = 1) -> PmpCfg:
    """Return a PmpCfg matching SV's ``set_defaults``.

    Single-region default: NAPOT covering all of memory with full RWX.
    Multi-region default: TOR regions evenly distributed across the
    address space, each fully permissive.
    """
    if num_regions == 1:
        # All-memory NAPOT region.
        if xlen == 32:
            # Bit 31 sets means the high half of the 32-bit space is
            # included; the low (XLEN-2) ones below is the all-ones
            # NAPOT spec encoding for "cover the entire 32-bit space".
            addr = ((1 << (xlen - 2)) - 1) << 2
        else:
            # RV64: the actual CSR is 54 bits; encode an all-ones NAPOT
            # spanning the full 54-bit physical address space.
            addr = ((1 << 54) - 1) << 2
        regions = [PmpRegion(
            l=0, a=PmpAddrMode.NAPOT, x=1, w=1, r=1,
            addr=addr, comment="all-memory NAPOT permissive",
        )]
    else:
        # SV ``assign_default_addr_offset``: distribute pmp_max_offset
        # / (N - 1) across regions.
        max_offset = (1 << (xlen - 1)) - 1
        step = max_offset // max(num_regions - 1, 1)
        regions = [
            PmpRegion(
                l=0, a=PmpAddrMode.TOR, x=1, w=1, r=1,
                addr=step * i,
                comment=f"region {i}",
            )
            for i in range(num_regions)
        ]
    return PmpCfg(
        pmp_num_regions=num_regions,
        regions=regions,
    )


# ---------------------------------------------------------------------------
# Boot CSR sequence
# ---------------------------------------------------------------------------


def _pmpcfg_csr(index: int, xlen: int) -> int:
    """Return the address of the pmpcfg CSR holding region ``index``.

    Cfg-per-CSR is 4 on RV32 and 8 on RV64. CSR addresses are
    PMPCFG0..PMPCFG15 contiguous starting at 0x3A0; on RV64 only
    even-numbered slots are implemented (PMPCFG0, PMPCFG2, ...).
    """
    base = PrivilegedReg.PMPCFG0.value
    if xlen == 32:
        return base + (index // 4)
    return base + 2 * (index // 8)


def _pmpaddr_csr(index: int) -> int:
    """Return the address of pmpaddr<index>."""
    return PrivilegedReg.PMPADDR0.value + index


def gen_setup_pmp(cfg: Config, pmp: PmpCfg, scratch: RiscvReg) -> list[str]:
    """Emit the boot PMP-programming sequence.

    Sequence per CSR-aligned block of regions:

        li   xN, <packed_cfg_value>
        csrw pmpcfg<idx>, xN

    Then per region:

        li   xN, <pmpaddr_value>
        csrw pmpaddr<i>, xN

    Optionally writes mseccfg if any ePMP bit is set.

    Returns an empty list when ``pmp.suppress_setup`` is True or when
    there are zero regions configured.
    """
    if pmp.suppress_setup or not pmp.regions:
        return []

    out: list[str] = []
    xlen = cfg.target.xlen
    cfg_per_csr = xlen // 8

    # Pack pmpcfg bytes into XLEN-wide values.
    num_csrs = (len(pmp.regions) + cfg_per_csr - 1) // cfg_per_csr
    for csr_idx in range(num_csrs):
        packed = 0
        first = csr_idx * cfg_per_csr
        last = min(first + cfg_per_csr, len(pmp.regions))
        for slot, region_idx in enumerate(range(first, last)):
            packed |= pmp.regions[region_idx].cfg_byte() << (slot * 8)
        # Compute the actual CSR address: RV32 uses pmpcfg0/1/2/3,
        # RV64 uses pmpcfg0/2/4/...  -- _pmpcfg_csr already encodes that.
        csr_addr = _pmpcfg_csr(first, xlen)
        out.append(_line(f"li {scratch.abi}, 0x{packed:x}"))
        out.append(_line(f"csrw 0x{csr_addr:x}, {scratch.abi} # PMPCFG"))

    # Then write each pmpaddr.
    for i, region in enumerate(pmp.regions):
        addr_val = region.pack_addr(xlen)
        comment = f" # {region.comment}" if region.comment else ""
        out.append(_line(f"li {scratch.abi}, 0x{addr_val:x}"))
        out.append(_line(
            f"csrw 0x{_pmpaddr_csr(i):x}, {scratch.abi} # PMPADDR{i}{comment}"
        ))

    # mseccfg if any ePMP bit is asserted.
    if pmp.mseccfg_mml or pmp.mseccfg_mmwp or pmp.mseccfg_rlb != 1:
        mseccfg_val = (
            (pmp.mseccfg_mml & 1)
            | ((pmp.mseccfg_mmwp & 1) << 1)
            | ((pmp.mseccfg_rlb & 1) << 2)
        )
        if PrivilegedReg.MSECCFG in cfg.target.implemented_csr:
            out.append(_line(f"li {scratch.abi}, 0x{mseccfg_val:x}"))
            out.append(_line(
                f"csrw 0x{PrivilegedReg.MSECCFG.value:x}, {scratch.abi} # MSECCFG"
            ))

    return out
