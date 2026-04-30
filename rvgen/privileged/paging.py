"""SV32 / SV39 page-table generator + SATP boot wiring.

Port of ``src/riscv_page_table_entry.sv`` + ``src/riscv_page_table.sv`` +
``src/riscv_page_table_list.sv``. Phase-1 scope: identity-map the program
region, no exception injection. SV48 + Svnapot + Svpbmt land in a follow-up
release.

What this module produces
-------------------------

1. :class:`Pte` — a single page-table entry with v/u/g/a/d/xwr/rsw/ppn
   fields. ``pack(xlen, mode)`` returns the wire-encoded integer.
2. :class:`PageTable` — a 4 KiB-aligned table of ``4096/PteSize`` PTEs.
3. :class:`PageTableList` — orchestrator that builds the topology,
   produces the ``.section .h<N>_page_table`` asm block, and emits the
   "process_page_table" boot fix-up that links child tables together.
4. :func:`gen_setup_satp` — boot CSR sequence that loads SATP with the
   root-table PPN + mode and issues sfence.vma. Called by
   :mod:`rvgen.privileged.boot` when ``cfg.target.satp_mode != BARE``.

Topology (matches SV ``riscv_page_table_list#default_page_table_setting``):

* SV32 — 2 levels: 1 root + 2 leaves = 3 tables.
* SV39 — 3 levels: 1 root + 2 + 4 = 7 tables.

In each non-leaf table, PTEs[0..1] are link PTEs (pointing to a child
table) and PTEs[2..3] are super-leaf PTEs covering 4 MiB / 2 MiB / 1 GiB
of identity-mapped physical memory. Remaining PTEs are invalid.

The PPNs of leaf PTEs are statically set at generation time (identity
mapping from ``start_pa``). Link PTEs cannot be resolved statically
because the assembler picks ``page_table_<N>``'s physical address — they
are fixed up at boot via ``process_page_table`` which loads each link
PTE, OR-in's the runtime address of the child page_table_<N> label, and
stores it back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from rvgen.config import Config
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    PrivilegedMode,
    PrivilegedReg,
    PtePermission,
    RiscvReg,
    SatpMode,
)
from rvgen.isa.utils import hart_prefix


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


# ---------------------------------------------------------------------------
# PTE field widths (mirrors SV ``riscv_page_table_entry`` localparams).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PteGeometry:
    ppn0_width: int
    ppn1_width: int
    ppn2_width: int
    ppn3_width: int
    rsvd_width: int
    vpn_width: int
    vaddr_spare: int
    vaddr_width: int
    page_level: int

    @property
    def num_ppn_levels(self) -> int:
        return self.page_level


_GEOMETRY: dict[SatpMode, _PteGeometry] = {
    # SV32: PPN0=10, PPN1=12, no PPN2/PPN3, no rsvd. 2 levels.
    SatpMode.SV32: _PteGeometry(
        ppn0_width=10, ppn1_width=12, ppn2_width=0, ppn3_width=0,
        rsvd_width=0, vpn_width=10, vaddr_spare=0, vaddr_width=32,
        page_level=2,
    ),
    # SV39: PPN0=9, PPN1=9, PPN2=26, no PPN3, rsvd=10. 3 levels.
    SatpMode.SV39: _PteGeometry(
        ppn0_width=9, ppn1_width=9, ppn2_width=26, ppn3_width=0,
        rsvd_width=10, vpn_width=9, vaddr_spare=25, vaddr_width=39,
        page_level=3,
    ),
    # SV48: PPN0=9, PPN1=9, PPN2=9, PPN3=9, rsvd=10. 4 levels.
    SatpMode.SV48: _PteGeometry(
        ppn0_width=9, ppn1_width=9, ppn2_width=9, ppn3_width=9,
        rsvd_width=10, vpn_width=9, vaddr_spare=16, vaddr_width=48,
        page_level=4,
    ),
}


def _xlen_for(mode: SatpMode) -> int:
    return 32 if mode == SatpMode.SV32 else 64


# ---------------------------------------------------------------------------
# PTE data class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Pte:
    """A single page-table entry (port of ``riscv_page_table_entry``).

    Fields mirror the SV source one-for-one. ``pack(mode)`` returns the
    XLEN-wide integer with the correct bit layout per mode.

    For an identity-mapped happy-path PTE: ``v=1``, ``xwr=R_W_EXECUTE``,
    ``a=d=1`` (avoid implementation-specific page-fault traps), ``rsw=0``,
    ``rsvd=0``, with PPN bits set to map vaddr -> the same physical
    address.
    """

    v: int = 1
    xwr: PtePermission = PtePermission.R_W_EXECUTE_PAGE
    u: int = 0
    g: int = 0
    a: int = 1
    d: int = 1
    rsw: int = 0
    rsvd: int = 0
    ppn0: int = 0
    ppn1: int = 0
    ppn2: int = 0
    ppn3: int = 0

    def is_link(self) -> bool:
        """True iff this PTE points to a child page table (xwr == NEXT_LEVEL)."""
        return self.xwr == PtePermission.NEXT_LEVEL_PAGE

    def set_ppn_for_pa(self, base_pa: int, pte_index: int, level: int,
                       mode: SatpMode) -> None:
        """Assign PPN fields so this PTE maps vaddr -> base_pa + offset.

        Mirrors SV ``set_ppn`` (riscv_page_table_entry.sv:158). For an
        identity-mapped leaf PTE, ``base_pa`` is the program start
        (default 0x80000000) and ``pte_index`` is the linear index of
        the PTE across all tables at that level.
        """
        geom = _GEOMETRY[mode]
        # Slice PPN fields out of base_pa.
        self.ppn0 = (base_pa >> 12) & ((1 << geom.ppn0_width) - 1)
        self.ppn1 = (base_pa >> (12 + geom.ppn0_width)) & ((1 << geom.ppn1_width) - 1)
        if geom.ppn2_width:
            self.ppn2 = (base_pa >> (12 + geom.ppn0_width + geom.ppn1_width)) \
                        & ((1 << geom.ppn2_width) - 1)
        if geom.ppn3_width:
            self.ppn3 = (base_pa >> (12 + geom.ppn0_width + geom.ppn1_width
                                       + geom.ppn2_width)) \
                        & ((1 << geom.ppn3_width) - 1)
        # Add per-PTE increments — only the levels >= page_level get the
        # carry from pte_index, lower levels keep their slice from base_pa.
        pte_per_table = 4096 // (_xlen_for(mode) // 8)
        idx = pte_index
        for lvl in range(4):
            if lvl >= level:
                incr = idx % pte_per_table
                idx //= pte_per_table
                if lvl == 0:
                    self.ppn0 += incr
                elif lvl == 1:
                    self.ppn1 += incr
                elif lvl == 2:
                    self.ppn2 += incr
                elif lvl == 3:
                    self.ppn3 += incr

    def pack(self, mode: SatpMode) -> int:
        """Return the XLEN-wide integer encoding (riscv_page_table_entry.sv:135).

        SV32: ``{ppn1, ppn0, rsw, d, a, g, u, xwr, v}``  (32 bits)
        SV39: ``{rsvd, ppn2, ppn1, ppn0, rsw, d, a, g, u, xwr, v}``  (64 bits)
        SV48: ``{rsvd, ppn3, ppn2, ppn1, ppn0, rsw, d, a, g, u, xwr, v}``  (64)
        """
        xwr_bits = int(self.xwr) & 0b111
        low10 = (
            (self.rsw & 0b11) << 8
            | (self.d & 1) << 7
            | (self.a & 1) << 6
            | (self.g & 1) << 5
            | (self.u & 1) << 4
            | (xwr_bits) << 1
            | (self.v & 1)
        )
        geom = _GEOMETRY[mode]
        if mode == SatpMode.SV32:
            ppn = (
                (self.ppn1 & ((1 << geom.ppn1_width) - 1)) << geom.ppn0_width
                | (self.ppn0 & ((1 << geom.ppn0_width) - 1))
            )
            return (ppn << 10) | low10
        if mode == SatpMode.SV39:
            ppn = (
                (self.ppn2 & ((1 << geom.ppn2_width) - 1))
                << (geom.ppn0_width + geom.ppn1_width)
                | (self.ppn1 & ((1 << geom.ppn1_width) - 1)) << geom.ppn0_width
                | (self.ppn0 & ((1 << geom.ppn0_width) - 1))
            )
            return (
                (self.rsvd & ((1 << geom.rsvd_width) - 1))
                << (10 + geom.ppn0_width + geom.ppn1_width + geom.ppn2_width)
                | (ppn << 10)
                | low10
            )
        if mode == SatpMode.SV48:
            ppn = (
                (self.ppn3 & ((1 << geom.ppn3_width) - 1))
                << (geom.ppn0_width + geom.ppn1_width + geom.ppn2_width)
                | (self.ppn2 & ((1 << geom.ppn2_width) - 1))
                << (geom.ppn0_width + geom.ppn1_width)
                | (self.ppn1 & ((1 << geom.ppn1_width) - 1)) << geom.ppn0_width
                | (self.ppn0 & ((1 << geom.ppn0_width) - 1))
            )
            return (
                (self.rsvd & ((1 << geom.rsvd_width) - 1))
                << (10 + geom.ppn0_width + geom.ppn1_width
                    + geom.ppn2_width + geom.ppn3_width)
                | (ppn << 10)
                | low10
            )
        raise ValueError(f"Unsupported SatpMode {mode!r}")


# Pre-built canonical PTEs.
def _valid_leaf(privileged_mode: PrivilegedMode) -> Pte:
    return Pte(
        v=1, xwr=PtePermission.R_W_EXECUTE_PAGE,
        u=(1 if privileged_mode == PrivilegedMode.USER_MODE else 0),
        a=1, d=1,
    )


def _valid_link() -> Pte:
    # Per spec: link PTEs (xwr == 0b000) must have u=a=d=0.
    return Pte(v=1, xwr=PtePermission.NEXT_LEVEL_PAGE, u=0, a=0, d=0)


# ---------------------------------------------------------------------------
# Table + table-list scaffolding.
# ---------------------------------------------------------------------------


# Match SV defaults: 2 link PTEs + 2 super-leaf PTEs per non-leaf table.
LINK_PTE_PER_TABLE = 2
SUPER_LEAF_PTE_PER_TABLE = 2


@dataclass(slots=True)
class PageTable:
    table_id: int
    level: int
    ptes: list[Pte] = field(default_factory=list)


@dataclass(slots=True)
class PageTableList:
    """Topology + PTE contents for one hart's page tables.

    Construct with :func:`build_default_page_tables`. Then:

    * ``gen_data_section()`` returns the ``.section`` block to splice
      into the ``.data``-side of the program (called by
      ``rvgen.asm_program_gen``).
    * ``gen_process_page_table()`` returns the M-mode boot fix-up
      that runs once at startup to wire link PTEs to their child
      tables' runtime addresses.
    """

    mode: SatpMode
    privileged_mode: PrivilegedMode
    start_pa: int
    num_per_level: list[int]
    tables: list[PageTable]

    @property
    def xlen(self) -> int:
        return _xlen_for(self.mode)

    @property
    def page_level(self) -> int:
        return _GEOMETRY[self.mode].page_level

    @property
    def root_label(self) -> str:
        return "page_table_0"

    # -------- table layout --------

    def get_child_table_id(self, table_id: int, pte_id: int) -> int:
        """Mirror of SV ``get_child_table_id`` — child = ``i*L + j + 1``."""
        return table_id * LINK_PTE_PER_TABLE + pte_id + 1

    # -------- asm emission --------

    def gen_data_section(self, hart: int = 0, num_harts: int = 1) -> list[str]:
        """Emit the ``.section .h<N>_page_table`` block.

        Layout (mirrors SV ``riscv_data_page_gen``-style output):

            .section .h<N>_page_table,"aw",@progbits
            .align 12
            page_table_0:
            .dword 0x...
            ...

        Each table is 4 KiB (``4096/PteSize`` PTEs). Tables are
        consecutive in memory; the ``.align 12`` only applies to the
        very first table — successive tables auto-align because each is
        4 KiB long.
        """
        prefix = hart_prefix(hart, num_harts)
        word_directive = ".dword" if self.xlen == 64 else ".word"
        pte_per_table = 4096 // (self.xlen // 8)

        lines: list[str] = [
            f".section .{prefix}page_table,\"aw\",@progbits",
            ".align 12",
        ]
        for tbl in self.tables:
            lines.append(f"page_table_{tbl.table_id}:")
            for i in range(pte_per_table):
                pte = tbl.ptes[i] if i < len(tbl.ptes) else Pte(v=0, xwr=PtePermission.NEXT_LEVEL_PAGE,
                                                                 u=0, a=0, d=0)
                bits = pte.pack(self.mode)
                width = self.xlen // 4   # hex digits for one XLEN-wide word
                lines.append(_line(f"{word_directive} 0x{bits:0{width}x}"))
        return lines

    def gen_process_page_table(self, cfg: Config) -> list[str]:
        """Emit the runtime fix-up that links page tables together.

        For every link PTE in every non-leaf table, this code loads the
        current PTE, takes the runtime address of the child table via
        ``la``, shifts it right 2 bits to land in the PPN field, OR's
        it into the PTE, and stores it back. Finishes with a single
        ``sfence.vma``.

        Mirrors SV ``process_page_table`` (riscv_page_table_list.sv:437)
        but skips the kernel-page U-bit clear (we don't currently emit
        kernel sub-programs).
        """
        gpr0 = cfg.gpr[0]
        gpr1 = cfg.gpr[1]
        gpr2 = cfg.gpr[2]
        load_op = "ld" if self.xlen == 64 else "lw"
        store_op = "sd" if self.xlen == 64 else "sw"
        pte_size = self.xlen // 8

        out: list[str] = []
        for tbl in self.tables:
            if tbl.level == 0:
                # Leaf-only tables don't have link PTEs to fix up.
                continue
            out.append(_line(
                f"la {gpr1.abi}, page_table_{tbl.table_id}+2048 "
                f"# Process PT_{tbl.table_id}"
            ))
            for j, pte in enumerate(tbl.ptes[:LINK_PTE_PER_TABLE]):
                if pte.xwr != PtePermission.NEXT_LEVEL_PAGE:
                    continue
                offset = j * pte_size - 2048
                child_id = self.get_child_table_id(tbl.table_id, j)
                out.extend([
                    _line(f"{load_op} {gpr2.abi}, {offset}({gpr1.abi})"),
                    _line(
                        f"la {gpr0.abi}, page_table_{child_id} "
                        f"# Link PT_{tbl.table_id}_PTE_{j} -> PT_{child_id}"
                    ),
                    # Shift right 2 because PPN is at bit 10 in the PTE
                    # but la already gives a byte-aligned address; the
                    # 12-bit page offset is implicit (page_table_N is
                    # 4 KiB aligned), so we just need >>2 to place the
                    # remaining bits in bits 10+.
                    _line(f"srli {gpr0.abi}, {gpr0.abi}, 2"),
                    _line(f"or {gpr2.abi}, {gpr0.abi}, {gpr2.abi}"),
                    _line(f"{store_op} {gpr2.abi}, {offset}({gpr1.abi})"),
                ])
        out.append(_line("sfence.vma"))
        return out


def build_default_page_tables(
    mode: SatpMode,
    privileged_mode: PrivilegedMode,
    start_pa: int = 0x8000_0000,
) -> PageTableList:
    """Build the canonical identity-mapped topology used by riscv-dv.

    SV32: 1 + 2 = 3 tables.
    SV39: 1 + 2 + 4 = 7 tables.
    """
    if mode == SatpMode.BARE:
        raise ValueError("build_default_page_tables called with SatpMode.BARE")
    geom = _GEOMETRY[mode]
    # num_per_level[level] = LinkPtePerTable ** (PageLevel - level - 1)
    # SV39 → [4, 2, 1]: level 0 has 4 tables, level 1 has 2, level 2 has 1.
    num_per_level = [
        LINK_PTE_PER_TABLE ** (geom.page_level - lvl - 1)
        for lvl in range(geom.page_level)
    ]
    pte_per_table = 4096 // (_xlen_for(mode) // 8)

    # Build tables top-down: level page_level-1 (root) first, then
    # successive levels each with more tables. Mirrors SV ``get_level``.
    tables: list[PageTable] = []
    table_id = 0
    for level in range(geom.page_level - 1, -1, -1):
        for _ in range(num_per_level[level]):
            tables.append(PageTable(table_id=table_id, level=level, ptes=[]))
            table_id += 1

    # Populate PTEs.
    pte_index_per_level: dict[int, int] = {}
    for tbl in tables:
        ptes: list[Pte] = []
        for j in range(pte_per_table):
            if tbl.level > 0:
                if j < LINK_PTE_PER_TABLE:
                    ptes.append(_valid_link())
                elif j < LINK_PTE_PER_TABLE + SUPER_LEAF_PTE_PER_TABLE:
                    pte = _valid_leaf(privileged_mode)
                    pte.set_ppn_for_pa(
                        start_pa, pte_index_per_level.get(tbl.level, 0),
                        tbl.level, mode,
                    )
                    pte_index_per_level[tbl.level] = \
                        pte_index_per_level.get(tbl.level, 0) + 1
                    ptes.append(pte)
                else:
                    # Invalid filler.
                    ptes.append(Pte(v=0, xwr=PtePermission.NEXT_LEVEL_PAGE,
                                     u=0, a=0, d=0))
            else:
                # Level 0 — leaf-only table.
                pte = _valid_leaf(privileged_mode)
                pte.set_ppn_for_pa(
                    start_pa, pte_index_per_level.get(0, 0),
                    0, mode,
                )
                pte_index_per_level[0] = pte_index_per_level.get(0, 0) + 1
                ptes.append(pte)
        tbl.ptes = ptes

    return PageTableList(
        mode=mode, privileged_mode=privileged_mode,
        start_pa=start_pa, num_per_level=num_per_level,
        tables=tables,
    )


# ---------------------------------------------------------------------------
# SATP boot wiring (called from rvgen.privileged.boot.gen_pre_enter_*).
# ---------------------------------------------------------------------------


def _satp_mode_value(mode: SatpMode, xlen: int) -> int:
    """Return the value to OR into SATP's MODE field.

    RV32 SATP.MODE is bit 31 (1 bit, 0=Bare/1=SV32).
    RV64 SATP.MODE is bits 60..63 (4 bits, 0=Bare/8=SV39/9=SV48/...).
    """
    if xlen == 32:
        return (1 << 31) if mode == SatpMode.SV32 else 0
    return (int(mode) & 0xF) << 60


def gen_setup_satp(cfg: Config, scratch: RiscvReg) -> list[str]:
    """Emit the SATP-programming sequence at boot.

    Sequence:

        la   xN, page_table_0
        srli xN, xN, 12        ; convert byte address -> PPN
        li   xM, <mode_field>  ; mode encoded in top bits
        or   xN, xN, xM
        csrw SATP, xN
        sfence.vma

    Mirrors SV ``setup_satp`` in ``riscv_privileged_common_seq.sv``.
    """
    target = cfg.target
    if target.satp_mode == SatpMode.BARE:
        return []
    xlen = target.xlen
    mode_val = _satp_mode_value(target.satp_mode, xlen)
    out = [
        _line(f"la {scratch.abi}, page_table_0"),
        _line(f"srli {scratch.abi}, {scratch.abi}, 12"),
    ]
    if mode_val:
        # We pick gpr[3] (a stable scratch beyond gpr[0..2] which trap.py
        # tends to grab). cfg.gpr[3] is reserved by post_randomize and
        # safe to clobber here.
        mode_reg = cfg.gpr[3]
        out.append(_line(f"li {mode_reg.abi}, 0x{mode_val:x}"))
        out.append(_line(f"or {scratch.abi}, {scratch.abi}, {mode_reg.abi}"))
    out.append(_line(
        f"csrw 0x{PrivilegedReg.SATP.value:x}, {scratch.abi}"
    ))
    out.append(_line("sfence.vma"))
    return out


# ---------------------------------------------------------------------------
# Public-facing helpers used by asm_program_gen.
# ---------------------------------------------------------------------------


def is_paging_enabled(cfg: Config) -> bool:
    """True iff the target requests SATP != BARE and bare-program mode is off."""
    return (
        cfg.target is not None
        and cfg.target.satp_mode != SatpMode.BARE
        and not cfg.bare_program_mode
    )
