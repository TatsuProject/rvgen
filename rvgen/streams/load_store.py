"""Scalar load/store directed streams — port of ``riscv_load_store_instr_lib.sv``.

Provides the full SV class hierarchy:

- :class:`LoadStoreBaseInstrStream` — locality-aware offset generation +
  alignment-aware width selection. Equivalent to SV's
  ``riscv_load_store_base_instr_stream``.
- :class:`LoadStoreStressInstrStream` — back-to-back loads/stores, no mix.
- :class:`LoadStoreRandInstrStream` — balanced loads/stores + random
  arithmetic mix. The "canonical" random variant.
- :class:`HazardInstrStream` — forces GPR hazards via a tight pool of
  available registers (6 regs by default) so RAW/WAR/WAW chains dominate.
- :class:`LoadStoreHazardInstrStream` — hazard via *address* reuse — each
  access has ``hazard_ratio`` chance of reusing the previous access's
  offset (creates back-to-back RAW/WAW on the same address).
- :class:`MultiPageLoadStoreInstrStream` — builds multiple independent
  load/store sub-streams (one per memory region) and interleaves them.
  Exercises TLB switching / prefetcher churn.
- :class:`MemRegionStressTest` — extends multi-page with a wider
  region-count range; differentiated only by the constraint range.
- :class:`LoadStoreRandAddrInstrStream` — like the base stream but offsets
  can be anywhere in the signed 12-bit range (may trap on spec-conformant
  cores without PMP permissive mapping; useful for exception-injection
  testing).
- :class:`LoadStoreSharedMemStream` — shared-region access (for multi-hart).

Every subclass produces a faithful atomic `instr_list` that the main
sequence ``insert_instr_stream`` then splices into the randomized stream.

Key invariant: ``rs1_reg`` is pinned via an initial ``la rs1_reg, region_N;
addi rs1_reg, rs1_reg, <base>`` pseudo, and subsequent loads/stores must
never pick ``rs1_reg`` as ``rd`` — that would clobber the base and send
the rest of the stream into random memory.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import ClassVar, Sequence

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import get_rand_instr, randomize_gpr_operands
from rvgen.sections.data_page import DEFAULT_MEM_REGIONS
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams.directed import _LaPseudo  # re-use existing pseudo


_LOCALITY_RANGES: dict[str, tuple[int, int]] = {
    "NARROW": (-16, 16),
    "HIGH": (-64, 64),
    "MEDIUM": (-256, 256),
    "SPARSE": (-2048, 2047),
}


# Unaligned address widths — always legal for LB/LBU/SB.
_BYTE_LOADS = (RiscvInstrName.LB, RiscvInstrName.LBU)
_BYTE_STORES = (RiscvInstrName.SB,)
_HALF_LOADS = (RiscvInstrName.LH, RiscvInstrName.LHU)
_HALF_STORES = (RiscvInstrName.SH,)
_WORD_LOADS = (RiscvInstrName.LW,)
_WORD_LOADS_RV64 = (RiscvInstrName.LWU,)
_WORD_STORES = (RiscvInstrName.SW,)
_DWORD_LOADS = (RiscvInstrName.LD,)
_DWORD_STORES = (RiscvInstrName.SD,)


_FP_WORD_LOAD = RiscvInstrName.FLW
_FP_WORD_STORE = RiscvInstrName.FSW
_FP_DWORD_LOAD = RiscvInstrName.FLD
_FP_DWORD_STORE = RiscvInstrName.FSD


def _width_allowed(
    addr: int, xlen: int, *, enable_unaligned: bool,
    enable_fp: bool = False, enable_fp_double: bool = False,
) -> list[RiscvInstrName]:
    """Return the list of load/store mnemonics legal at ``addr`` on a core of ``xlen``.

    Direct port of the alignment lattice in
    ``riscv_load_store_base_instr_stream::gen_load_store_instr``. When
    ``enable_fp`` is true, FLW/FSW are included at 4-byte alignment (and
    FLD/FSD at 8-byte alignment if ``enable_fp_double``) — mirrors SV's
    behavior when cfg.enable_floating_point is set.
    """
    allowed: list[RiscvInstrName] = list(_BYTE_LOADS) + list(_BYTE_STORES)
    if enable_unaligned:
        # Unaligned support — every LH/LHU/SH/LW/SW is legal; still only
        # aligned compressed / FP ops.
        allowed += list(_HALF_LOADS) + list(_HALF_STORES)
        allowed += list(_WORD_LOADS) + list(_WORD_STORES)
        if xlen >= 64:
            allowed += list(_WORD_LOADS_RV64) + list(_DWORD_LOADS) + list(_DWORD_STORES)
        if enable_fp and addr % 4 == 0:
            allowed += [_FP_WORD_LOAD, _FP_WORD_STORE]
            if enable_fp_double and addr % 8 == 0:
                allowed += [_FP_DWORD_LOAD, _FP_DWORD_STORE]
        return allowed

    if addr % 2 == 0:
        allowed += list(_HALF_LOADS) + list(_HALF_STORES)
    if addr % 4 == 0:
        allowed += list(_WORD_LOADS) + list(_WORD_STORES)
        if enable_fp:
            allowed += [_FP_WORD_LOAD, _FP_WORD_STORE]
    if xlen >= 64 and addr % 8 == 0:
        allowed += list(_WORD_LOADS_RV64) + list(_DWORD_LOADS) + list(_DWORD_STORES)
    if enable_fp_double and addr % 8 == 0:
        allowed += [_FP_DWORD_LOAD, _FP_DWORD_STORE]
    return allowed


# ---------------------------------------------------------------------------
# Base class — locality + alignment + mix
# ---------------------------------------------------------------------------


@dataclass
class LoadStoreBaseInstrStream(DirectedInstrStream):
    """Per-region locality-aware load/store stream (SV line 20)."""

    # Tuning knobs — subclasses can override class-level or per-instance.
    num_load_store: int = 0
    num_mixed_instr: int = 0
    locality: str = "MEDIUM"
    data_page_id: int = 0
    region_name: str = ""
    rs1_reg: RiscvReg | None = None
    base: int = 0
    # Additional GPRs that must not be clobbered by mixed filler ops. Used by
    # MultiPageLoadStoreInstrStream to protect SIBLING sub-streams' base regs
    # when several LS streams are interleaved — without this, one sub's random
    # `mulhu t6, ra, t4` can stomp another sub's `la t6, region_0`, leaving
    # its later stores pointing anywhere (including .text → self-modifying
    # code → livelock). SV handles this implicitly via its constraint solver;
    # we make it explicit.
    extra_locked_regs: tuple[RiscvReg, ...] = ()

    # SV default ranges (see legal_c in SV subclasses):
    _num_ld_st_range: ClassVar[tuple[int, int]] = (10, 30)
    _num_mix_range: ClassVar[tuple[int, int]] = (10, 30)

    # Populated during build (informational).
    _offsets: list[int] = field(default_factory=list)
    _addrs: list[int] = field(default_factory=list)
    # Number of prelude instructions (la + optional addi) at the head of
    # instr_list. Used by MultiPage parent to interleave bodies without
    # splitting the prelude from its stores.
    _prelude_len: int = 0

    def _pick_rs1_reg(self) -> RiscvReg:
        """Pick a GPR not in the reserved set for use as the base register."""
        reserved = set(self.cfg.reserved_regs) | {RiscvReg.ZERO, RiscvReg.GP, RiscvReg.RA}
        pool = [r for r in RiscvReg if r not in reserved]
        return self.rng.choice(pool)

    def _pick_region(self) -> tuple[int, str, int]:
        """Pick a data-page id, its region name, and its size.

        In multi-hart mode (num_of_harts > 1) the data_page_gen prefixes
        every region label with ``h<N>_`` so the sections for each hart
        stay distinct. Streams must reference the hart-prefixed label or
        the linker will fail with "undefined reference to 'region_0'".
        """
        from rvgen.isa.utils import hart_prefix
        regions = (
            self.cfg.mem_regions()
            if self.cfg is not None and hasattr(self.cfg, "mem_regions")
            else DEFAULT_MEM_REGIONS
        )
        idx = self.rng.randrange(len(regions))
        self.data_page_id = idx
        base_name = regions[idx].name
        num_harts = self.cfg.num_of_harts if self.cfg else 1
        full_name = hart_prefix(self.hart, num_harts) + base_name
        self.region_name = full_name
        return idx, full_name, regions[idx].size_in_bytes

    def _pick_base(self, region_size: int) -> int:
        """Pick an intra-region starting offset that leaves room for locality."""
        lo, hi = _LOCALITY_RANGES[self.locality]
        # Leave hi-abs(lo) headroom to keep addr in [0, size-1].
        slack = max(abs(lo), abs(hi))
        upper = max(0, region_size - slack - 8)
        lower = slack
        if lower >= upper:
            return region_size // 2
        return self.rng.randint(lower, upper)

    def _randomize_offsets(self, region_size: int) -> None:
        lo, hi = _LOCALITY_RANGES[self.locality]
        self._offsets = []
        self._addrs = []
        for _ in range(self.num_load_store):
            # Retry a few times to land an in-region addr.
            for _attempt in range(16):
                off = self.rng.randint(lo, hi)
                addr = self.base + off
                if 0 <= addr < region_size:
                    break
            else:
                off = 0
                addr = self.base
            self._offsets.append(off)
            self._addrs.append(addr)

    def _pick_offset_for_iteration(self, i: int) -> int:
        """Subclasses may override to force hazardous reuse."""
        return self._offsets[i]

    def _gen_load_store_instr(self, base_locked: set[RiscvReg]) -> list[Instr]:
        """Emit one load/store per (offset, addr) pair with width by alignment."""
        xlen = self.cfg.target.xlen
        enable_unaligned = self.cfg.enable_unaligned_load_store
        enable_fp = self.cfg.enable_floating_point
        # FP double precision requires both an RV32D-family group in the
        # target AND enable_floating_point — guard against RV32F-only cores.
        from rvgen.isa.enums import RiscvInstrGroup
        target_groups = set(self.cfg.target.supported_isa)
        has_fp_d = bool({RiscvInstrGroup.RV32D, RiscvInstrGroup.RV64D} & target_groups)
        enable_fp_double = enable_fp and has_fp_d

        # Available "value" regs — anything not locked.
        val_pool = [r for r in RiscvReg if r not in base_locked]
        out: list[Instr] = []
        fp_stores = frozenset({_FP_WORD_STORE, _FP_DWORD_STORE})
        fp_loads = frozenset({_FP_WORD_LOAD, _FP_DWORD_LOAD})
        int_stores = frozenset(_WORD_STORES + _HALF_STORES + _BYTE_STORES + _DWORD_STORES)
        for i in range(self.num_load_store):
            off = self._pick_offset_for_iteration(i)
            addr = self.base + off
            if 0 > addr or addr >= 4096:  # Keep a sensible cap
                addr = (self.base + off) % 4096
            allowed = _width_allowed(
                addr, xlen, enable_unaligned=enable_unaligned,
                enable_fp=enable_fp, enable_fp_double=enable_fp_double,
            )
            # Filter to ops actually registered for this target.
            registered = [n for n in allowed if n in self.avail.names]
            if not registered:
                registered = [n for n in allowed]
            pick = self.rng.choice(registered)
            instr = get_instr(pick)
            if pick in fp_loads or pick in fp_stores:
                # FP load/store: rs1 = base, fd/fs2 = FP reg. The FP instr
                # class is FloatingPointInstr, which uses has_fs2 for the
                # store-source FP reg and has_fd for the load-dest FP reg.
                from rvgen.isa.enums import RiscvFpr
                instr.rs1 = self.rs1_reg  # type: ignore[assignment]
                fp_pool = list(RiscvFpr)
                if pick in fp_stores:
                    instr.fs2 = self.rng.choice(fp_pool)
                else:
                    instr.fd = self.rng.choice(fp_pool)
            elif pick in int_stores:
                instr.rs1 = self.rs1_reg  # type: ignore[assignment]
                store_pool = [r for r in val_pool if r != self.rs1_reg]
                instr.rs2 = self.rng.choice(store_pool) if store_pool else RiscvReg.ZERO
            else:
                instr.rs1 = self.rs1_reg  # type: ignore[assignment]
                rd_pool = [r for r in val_pool if r not in (self.rs1_reg, RiscvReg.ZERO)]
                instr.rd = self.rng.choice(rd_pool) if rd_pool else RiscvReg.T0
            instr.imm = off & 0xFFF  # 12-bit signed — raw 12-bit bits
            instr.imm_str = str(off)
            instr.process_load_store = False
            out.append(instr)
        return out

    def _add_mixed_instr(self, base_locked: set[RiscvReg]) -> list[Instr]:
        """Emit ``num_mixed_instr`` random ARITH/LOGICAL/COMPARE/SHIFT ops."""
        if self.num_mixed_instr <= 0:
            return []
        out: list[Instr] = []
        xlen = self.cfg.target.xlen
        # Available regs for both operands and rd (must not clobber base).
        avail_regs = tuple(r for r in RiscvReg if r not in base_locked)
        for _ in range(self.num_mixed_instr):
            instr = get_rand_instr(
                self.rng,
                self.avail,
                include_category=[
                    RiscvInstrCategory.ARITHMETIC,
                    RiscvInstrCategory.LOGICAL,
                    RiscvInstrCategory.COMPARE,
                    RiscvInstrCategory.SHIFT,
                ],
            )
            # Protect both this stream's base AND any sibling streams' bases
            # (needed when interleaved in MultiPageLoadStoreInstrStream). Mixed
            # writes to a sibling's base point its later stores into random
            # memory — including .text, which silently self-modifies code.
            forbidden_rd = tuple(base_locked - {RiscvReg.ZERO})
            randomize_gpr_operands(
                instr, self.rng, self.cfg,
                avail_regs=avail_regs,
                reserved_rd=forbidden_rd,
            )
            if instr.has_imm:
                instr.randomize_imm(self.rng, xlen=xlen)
            instr.post_randomize()
            out.append(instr)
        return out

    def _emit_la_init(self) -> Instr:
        """Prepend the ``la rs1_reg, <region>`` pseudo + ``addi`` for base."""
        la = _LaPseudo()
        la.rd = self.rs1_reg  # type: ignore[assignment]
        la.imm_str = self.region_name
        la.atomic = True
        return la

    def _emit_addi_base(self) -> Instr | None:
        if self.base == 0:
            return None
        instr = get_instr(RiscvInstrName.ADDI)
        instr.rd = self.rs1_reg  # type: ignore[assignment]
        instr.rs1 = self.rs1_reg  # type: ignore[assignment]
        instr.imm = self.base & 0xFFF
        instr.imm_str = str(self.base)
        instr.post_randomize()
        instr.process_load_store = False
        return instr

    def build(self) -> None:
        # Legalize tuning knobs.
        if self.num_load_store == 0:
            lo, hi = self._num_ld_st_range
            self.num_load_store = self.rng.randint(lo, hi)
        if self.num_mixed_instr == 0:
            lo, hi = self._num_mix_range
            self.num_mixed_instr = self.rng.randint(lo, hi)
        if self.locality not in _LOCALITY_RANGES:
            self.locality = self.rng.choice(tuple(_LOCALITY_RANGES))
        if self.rs1_reg is None:
            self.rs1_reg = self._pick_rs1_reg()

        _, _, region_size = self._pick_region()
        self.base = self._pick_base(region_size)
        self._randomize_offsets(region_size)

        base_locked = (
            set(self.cfg.reserved_regs)
            | {self.rs1_reg, RiscvReg.ZERO}
            | set(self.extra_locked_regs)
        )

        load_store = self._gen_load_store_instr(base_locked)
        mixed = self._add_mixed_instr(base_locked)

        # Interleave: per SV, ``mix_instr_stream(mixed)`` sprinkles `mixed`
        # into `load_store` at random positions. We replicate.
        combined = list(load_store)
        if mixed:
            for i, m in enumerate(mixed):
                pos = self.rng.randint(0, len(combined))
                combined.insert(pos, m)

        self.instr_list = [self._emit_la_init()]
        addi = self._emit_addi_base()
        if addi is not None:
            self.instr_list.append(addi)
        # Record where the LS body starts so parent streams (MultiPage*)
        # can preserve the prelude-before-body invariant during interleave.
        self._prelude_len = len(self.instr_list)
        self.instr_list.extend(combined)


# ---------------------------------------------------------------------------
# Subclasses — tuning-knob differentiation
# ---------------------------------------------------------------------------


@dataclass
class LoadStoreStressInstrStream(LoadStoreBaseInstrStream):
    """Back-to-back loads/stores, no mix (SV:226)."""
    _num_ld_st_range: ClassVar[tuple[int, int]] = (10, 30)
    _num_mix_range: ClassVar[tuple[int, int]] = (0, 0)


@dataclass
class LoadStoreRandInstrStream(LoadStoreBaseInstrStream):
    """Balanced load/store + random mix (SV:257)."""
    _num_ld_st_range: ClassVar[tuple[int, int]] = (10, 30)
    _num_mix_range: ClassVar[tuple[int, int]] = (10, 30)


@dataclass
class HazardInstrStream(LoadStoreBaseInstrStream):
    """Forces register-reuse hazards via a tight available-reg pool (SV:270)."""

    num_of_avail_regs: int = 6

    _num_ld_st_range: ClassVar[tuple[int, int]] = (10, 30)
    _num_mix_range: ClassVar[tuple[int, int]] = (10, 30)

    def _pick_rs1_reg(self) -> RiscvReg:
        # Same logic but then constrain the value pool used by mix.
        return super()._pick_rs1_reg()

    def _add_mixed_instr(self, base_locked: set[RiscvReg]) -> list[Instr]:
        # Tightly restrict the avail_regs pool to force hazards.
        if self.num_mixed_instr <= 0:
            return []
        out: list[Instr] = []
        xlen = self.cfg.target.xlen
        pool = [r for r in RiscvReg if r not in base_locked]
        restricted = tuple(self.rng.sample(pool, min(self.num_of_avail_regs, len(pool))))
        for _ in range(self.num_mixed_instr):
            instr = get_rand_instr(
                self.rng, self.avail,
                include_category=[
                    RiscvInstrCategory.ARITHMETIC,
                    RiscvInstrCategory.LOGICAL,
                    RiscvInstrCategory.COMPARE,
                    RiscvInstrCategory.SHIFT,
                ],
            )
            randomize_gpr_operands(
                instr, self.rng, self.cfg,
                avail_regs=restricted,
                reserved_rd=[self.rs1_reg] if self.rs1_reg else (),
            )
            if instr.has_imm:
                instr.randomize_imm(self.rng, xlen=xlen)
            instr.post_randomize()
            out.append(instr)
        return out


@dataclass
class LoadStoreHazardInstrStream(LoadStoreBaseInstrStream):
    """Forces address hazards via offset reuse (SV:291)."""

    hazard_ratio: int = 50
    _num_ld_st_range: ClassVar[tuple[int, int]] = (10, 20)
    _num_mix_range: ClassVar[tuple[int, int]] = (1, 7)

    def build(self) -> None:
        self.hazard_ratio = self.rng.randint(20, 100)
        super().build()

    def _randomize_offsets(self, region_size: int) -> None:
        lo, hi = _LOCALITY_RANGES[self.locality]
        self._offsets = []
        self._addrs = []
        for i in range(self.num_load_store):
            if i > 0 and self.rng.randint(0, 99) < self.hazard_ratio:
                # Reuse prior offset — creates RAW/WAW on the same address.
                self._offsets.append(self._offsets[i - 1])
                self._addrs.append(self._addrs[i - 1])
                continue
            for _ in range(16):
                off = self.rng.randint(lo, hi)
                addr = self.base + off
                if 0 <= addr < region_size:
                    break
            else:
                off, addr = 0, self.base
            self._offsets.append(off)
            self._addrs.append(addr)


@dataclass
class MultiPageLoadStoreInstrStream(DirectedInstrStream):
    """Interleave N per-region sub-streams (SV:341).

    Produces one :class:`LoadStoreStressInstrStream` per selected region,
    then shuffles their instr_lists together so the dynamic access pattern
    churns across regions — exercises TLB / data-prefetcher logic.
    """

    num_of_instr_stream: int = 0
    _NUM_RANGE: ClassVar[tuple[int, int]] = (2, 8)

    def build(self) -> None:
        regions = (
            self.cfg.mem_regions()
            if self.cfg is not None and hasattr(self.cfg, "mem_regions")
            else DEFAULT_MEM_REGIONS
        )
        if self.num_of_instr_stream == 0:
            lo, hi = self._NUM_RANGE
            self.num_of_instr_stream = self.rng.randint(
                lo, min(hi, len(regions))
            )
        # Bound by number of regions available.
        self.num_of_instr_stream = min(self.num_of_instr_stream, len(regions))
        if self.num_of_instr_stream < 2:
            self.num_of_instr_stream = 1

        # Pick distinct regions.
        region_ids = self.rng.sample(
            range(len(regions)),
            self.num_of_instr_stream,
        )

        # Pick distinct rs1_regs for each sub-stream.
        reserved = set(self.cfg.reserved_regs) | {RiscvReg.ZERO, RiscvReg.GP, RiscvReg.RA}
        pool = [r for r in RiscvReg if r not in reserved]
        if len(pool) < self.num_of_instr_stream:
            # Degenerate: fall back to a single region.
            region_ids = region_ids[:len(pool)]
        rs1_choices = self.rng.sample(pool, len(region_ids))

        # Collect all sibling bases up-front so each sub can forbid mixed-op
        # writes to any OTHER sub's base register. Without this, interleaved
        # mixed ops from one sub can clobber another's `la rs1, region_N`
        # initialization, leaving later stores pointing anywhere (including
        # .text → self-modifying code → livelock).
        all_bases: tuple[RiscvReg, ...] = tuple(rs1_choices)

        # Track preludes and bodies separately. Each sub's prelude
        # (`la rs1, region_N` + optional `addi rs1, rs1, base`) MUST appear
        # before any LS op that uses that sub's rs1_reg — otherwise the LS
        # op reads whatever random init value landed in that register,
        # which is often a .text address → memory corruption.
        preludes: list[list[Instr]] = []
        bodies: list[list[Instr]] = []
        for rid, rs1 in zip(region_ids, rs1_choices):
            sub = LoadStoreStressInstrStream(
                cfg=self.cfg, avail=self.avail, rng=self.rng,
                stream_name="",  # no per-sub comment; parent's finalize handles it
            )
            sub.rs1_reg = rs1
            sub.data_page_id = rid
            sub._num_ld_st_range = (5, 10)  # smaller per-sub, SV's defaults
            sub.num_load_store = 0
            sub.num_mixed_instr = 0
            sub.locality = self.rng.choice(tuple(_LOCALITY_RANGES))
            # Protect sibling bases — not just this sub's own base.
            sub.extra_locked_regs = tuple(r for r in all_bases if r != rs1)
            sub.generate()  # build + finalize
            # Strip the stream's Start/End comments since we're wrapping.
            for i in sub.instr_list:
                if i.comment in ("Start ", "End "):
                    i.comment = ""
            split = sub._prelude_len
            preludes.append(sub.instr_list[:split])
            bodies.append(sub.instr_list[split:])

        if not bodies:
            self.instr_list = []
            return

        # Flatten all preludes up-front (every base register is now
        # initialized before any body op runs).
        emitted: list[Instr] = []
        for p in preludes:
            emitted.extend(p)

        # Interleave bodies: take the first as base, random-insert the rest.
        base = list(bodies[0])
        for body in bodies[1:]:
            for ins in body:
                pos = self.rng.randint(0, len(base))
                base.insert(pos, ins)
        emitted.extend(base)
        self.instr_list = emitted


@dataclass
class MemRegionStressTest(MultiPageLoadStoreInstrStream):
    """Same shape as multi-page, but wider region-count range (SV:410)."""
    _NUM_RANGE: ClassVar[tuple[int, int]] = (2, 8)


@dataclass
class LoadStoreRandAddrInstrStream(LoadStoreBaseInstrStream):
    """Random-address loads/stores (SV:428).

    Locality is forced SPARSE and the offset range uses the full signed
    12-bit imm ([-2048, 2047]). With a conformant core + PMP, some of
    these will take load/store access-fault exceptions — that's the point.
    """

    _num_ld_st_range: ClassVar[tuple[int, int]] = (10, 20)
    _num_mix_range: ClassVar[tuple[int, int]] = (5, 10)

    def build(self) -> None:
        self.locality = "SPARSE"
        super().build()


@dataclass
class LoadStoreSharedMemStream(LoadStoreStressInstrStream):
    """Shared-memory load/store stream (SV:243).

    Targets the un-prefixed ``shared_region_0`` section that every hart
    can see. In multi-hart mode this gives genuinely racy load/store
    sequences across harts; in single-hart mode it behaves identically
    to :class:`LoadStoreStressInstrStream` (with a different region
    label).

    Phase-2 work could add explicit fence-pair / LR-SC rendezvous
    primitives, but the shared region itself is enough for spike to
    exercise memory-ordering paths once the generator emits per-hart
    streams that race on the same address.
    """

    def _pick_region(self) -> tuple[int, str, int]:
        from rvgen.sections.data_page import DEFAULT_SHARED_REGIONS
        region = DEFAULT_SHARED_REGIONS[0]
        # Multi-hart: shared region carries no hart prefix, so the same
        # label resolves across all harts.
        self.data_page_id = 0
        self.region_name = region.name
        return 0, region.name, region.size_in_bytes


# ---------------------------------------------------------------------------
# Registration — replace the old aliases with dedicated classes.
# ---------------------------------------------------------------------------


register_stream("riscv_load_store_base_instr_stream", LoadStoreBaseInstrStream)
register_stream("riscv_load_store_stress_instr_stream", LoadStoreStressInstrStream)
register_stream("riscv_load_store_rand_instr_stream", LoadStoreRandInstrStream)
register_stream("riscv_hazard_instr_stream", HazardInstrStream)
register_stream("riscv_load_store_hazard_instr_stream", LoadStoreHazardInstrStream)
register_stream("riscv_multi_page_load_store_instr_stream", MultiPageLoadStoreInstrStream)
register_stream("riscv_mem_region_stress_test", MemRegionStressTest)
register_stream("riscv_load_store_rand_addr_instr_stream", LoadStoreRandAddrInstrStream)
register_stream("riscv_load_store_shared_mem_stream", LoadStoreSharedMemStream)
