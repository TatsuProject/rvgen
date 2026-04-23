"""Covergroup sampling — static (generator-side) only for Phase 1.

A :class:`CoverageDB` is a dict-of-dict keyed by covergroup name, then bin
name, with observed integer hit counts. This shape:

- serialises trivially to JSON / YAML,
- merges across runs by simple bin-wise addition,
- compares cleanly against a :class:`~chipforge_inst_gen.coverage.cgf.Goals`
  (also a dict-of-dict, but with required hit counts instead of observed).

The covergroups we collect:

======================  =========================================
Covergroup              Bins
======================  =========================================
``opcode_cg``           One bin per :class:`RiscvInstrName` member
``format_cg``           One bin per :class:`RiscvInstrFormat` member
``category_cg``         One bin per :class:`RiscvInstrCategory` member
``group_cg``            One bin per :class:`RiscvInstrGroup` member
``rs1_cg``              One bin per :class:`RiscvReg` member (+ none)
``rs2_cg``              One bin per :class:`RiscvReg` member (+ none)
``rd_cg``               One bin per :class:`RiscvReg` member (+ none)
``imm_sign_cg``         pos / zero / neg (only when has_imm)
``hazard_cg``           raw / war / waw / none (adjacent-instr pairs)
``csr_cg``              One bin per :class:`PrivilegedReg` name seen
``fp_rm_cg``            RNE / RTZ / RDN / RUP / RMM (FP ops only)
``vtype_cg``            ``SEW<w>_LMUL<n>`` (vector ops only)
``vreg_cg``             One bin per :class:`RiscvVreg` member (vector)
``fpr_cg``              One bin per :class:`RiscvFpr` member (FP)
``fmt_category_cross``  ``<format>__<category>`` cross
``category_group_cross`` ``<category>__<group>`` cross
======================  =========================================

Hazards are detected with a per-register "last writer" dictionary, reset
between sequences. Only direct register hazards are flagged (RAW/WAW/WAR
on rs1/rs2/rd), not memory hazards — those need a runtime trace.
"""

from __future__ import annotations

import copy
from typing import Iterable

from chipforge_inst_gen.isa.base import Instr
from chipforge_inst_gen.isa.csr_ops import CsrInstr
from chipforge_inst_gen.isa.enums import (
    FRoundingMode,
    PrivilegedReg,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)


# A CoverageDB is just ``{covergroup_name: {bin_name: int}}``. We use a
# dedicated type so annotation tooling can distinguish it from a raw dict
# without introducing a class overhead.
CoverageDB = dict[str, dict[str, int]]


# Canonical covergroup names (stable string keys for JSON / YAML output).
CG_OPCODE = "opcode_cg"
CG_FORMAT = "format_cg"
CG_CATEGORY = "category_cg"
CG_GROUP = "group_cg"
CG_RS1 = "rs1_cg"
CG_RS2 = "rs2_cg"
CG_RD = "rd_cg"
CG_IMM_SIGN = "imm_sign_cg"
CG_HAZARD = "hazard_cg"
CG_CSR = "csr_cg"
CG_FP_RM = "fp_rm_cg"
CG_VTYPE = "vtype_cg"
CG_VREG = "vreg_cg"
CG_FPR = "fpr_cg"
CG_FMT_X_CAT = "fmt_category_cross"
CG_CAT_X_GRP = "category_group_cross"


ALL_COVERGROUPS: tuple[str, ...] = (
    CG_OPCODE, CG_FORMAT, CG_CATEGORY, CG_GROUP,
    CG_RS1, CG_RS2, CG_RD,
    CG_IMM_SIGN, CG_HAZARD, CG_CSR,
    CG_FP_RM, CG_VTYPE, CG_VREG, CG_FPR,
    CG_FMT_X_CAT, CG_CAT_X_GRP,
)


def new_db() -> CoverageDB:
    """Return a freshly-initialised, empty CoverageDB."""
    return {cg: {} for cg in ALL_COVERGROUPS}


# ---------------------------------------------------------------------------
# Per-instruction sampler
# ---------------------------------------------------------------------------


def _bump(db: CoverageDB, cg: str, bin_name: str) -> None:
    bins = db.setdefault(cg, {})
    bins[bin_name] = bins.get(bin_name, 0) + 1


def _imm_sign_bin(imm: int, imm_len: int) -> str:
    # Interpret imm as signed 2's complement of imm_len bits for sign.
    if imm_len == 0:
        return "zero"
    sign_bit = 1 << (imm_len - 1)
    v = imm & ((1 << imm_len) - 1)
    if v == 0:
        return "zero"
    if v & sign_bit:
        return "neg"
    return "pos"


def sample_instr(db: CoverageDB, instr: Instr) -> None:
    """Sample one :class:`Instr` into ``db``.

    Safe to call for any registered instruction, including vector / FP /
    compressed / pseudo variants. No-ops gracefully on instructions that
    lack optional slots (e.g. the ``_LiPseudo`` emitted by directed streams).
    """
    # Opcode — use the enum name if present, fall back to the class name.
    try:
        opcode_name = instr.instr_name.name
    except AttributeError:
        return  # pseudo w/o enum — skip

    _bump(db, CG_OPCODE, opcode_name)

    try:
        _bump(db, CG_FORMAT, instr.format.name)
    except (AttributeError, Exception):
        pass
    try:
        _bump(db, CG_CATEGORY, instr.category.name)
    except (AttributeError, Exception):
        pass
    try:
        _bump(db, CG_GROUP, instr.group.name)
    except (AttributeError, Exception):
        pass

    # Register operand sampling — only the slots the instr actually uses.
    has_rs1 = getattr(instr, "has_rs1", False)
    has_rs2 = getattr(instr, "has_rs2", False)
    has_rd = getattr(instr, "has_rd", False)
    if has_rs1:
        rs1 = getattr(instr, "rs1", None)
        if isinstance(rs1, RiscvReg):
            _bump(db, CG_RS1, rs1.name)
    if has_rs2:
        rs2 = getattr(instr, "rs2", None)
        if isinstance(rs2, RiscvReg):
            _bump(db, CG_RS2, rs2.name)
    if has_rd:
        rd = getattr(instr, "rd", None)
        if isinstance(rd, RiscvReg):
            _bump(db, CG_RD, rd.name)

    # Immediate sign (only if the instr actually has one and it was
    # randomized — branches resolved to label refs skip here since they
    # don't carry a meaningful signed immediate).
    has_imm = getattr(instr, "has_imm", False)
    imm_len = getattr(instr, "imm_len", 0)
    if has_imm and imm_len:
        _bump(db, CG_IMM_SIGN, _imm_sign_bin(instr.imm, imm_len))

    # CSR — CsrInstr subclasses carry a 12-bit csr addr; decode via enum.
    if isinstance(instr, CsrInstr):
        csr_addr = int(getattr(instr, "csr", 0)) & 0xFFF
        csr_name = _PRIV_REG_BY_ADDR.get(csr_addr, f"CSR_{csr_addr:03X}")
        _bump(db, CG_CSR, csr_name)

    # FP rounding mode — FloatingPointInstr carries .rm.
    rm = getattr(instr, "rm", None)
    if isinstance(rm, FRoundingMode):
        _bump(db, CG_FP_RM, rm.name)

    # FP register operands
    for slot in ("fs1", "fs2", "fs3", "fd"):
        has_slot = getattr(instr, f"has_{slot}", False)
        if not has_slot:
            continue
        reg = getattr(instr, slot, None)
        if reg is not None and hasattr(reg, "name"):
            _bump(db, CG_FPR, reg.name)

    # Vector register operands + vtype
    for slot in ("vs1", "vs2", "vs3", "vd"):
        has_slot = getattr(instr, f"has_{slot}", False)
        if not has_slot:
            continue
        reg = getattr(instr, slot, None)
        if reg is not None and hasattr(reg, "name"):
            _bump(db, CG_VREG, reg.name)

    # vtype bin for any instr whose class advertises `allowed_va_variants`
    # (or is a vector LOAD/STORE): tag with "SEW<sew>_LMUL<lmul>" —
    # requires the caller to stamp .sampled_sew / .sampled_lmul OR we
    # consult a default config. We defer this: the stream-level sampler
    # knows the active vector_cfg and can bump vtype_cg there.

    # Crosses
    try:
        _bump(db, CG_FMT_X_CAT, f"{instr.format.name}__{instr.category.name}")
    except (AttributeError, Exception):
        pass
    try:
        _bump(db, CG_CAT_X_GRP, f"{instr.category.name}__{instr.group.name}")
    except (AttributeError, Exception):
        pass


# ---------------------------------------------------------------------------
# Sequence sampler — hazard detection
# ---------------------------------------------------------------------------


def sample_sequence(db: CoverageDB, seq: Iterable[Instr]) -> None:
    """Sample every instruction in ``seq`` plus inter-instruction hazards.

    Hazard detection looks at *register* dependencies only:

    - RAW (Read-After-Write): instr N reads a register written by instr ≤ N-1.
    - WAR (Write-After-Read): instr N writes a register read by instr ≤ N-1.
    - WAW (Write-After-Write): instr N writes a register written by instr ≤ N-1.

    "≤ N-1" is a sliding window of ``HAZARD_WINDOW`` instructions — beyond
    that, the register is effectively retired for hazard-counting purposes.
    """
    last_writer_at: dict[RiscvReg, int] = {}
    last_reader_at: dict[RiscvReg, int] = {}

    for idx, instr in enumerate(seq):
        sample_instr(db, instr)

        # Collect the regs this instr reads/writes.
        reads: set[RiscvReg] = set()
        writes: set[RiscvReg] = set()
        for slot in ("rs1", "rs2"):
            if getattr(instr, f"has_{slot}", False):
                r = getattr(instr, slot, None)
                if isinstance(r, RiscvReg):
                    reads.add(r)
        if getattr(instr, "has_rd", False):
            r = getattr(instr, "rd", None)
            if isinstance(r, RiscvReg) and r != RiscvReg.ZERO:
                writes.add(r)

        hazard_found = False
        window_start = idx - HAZARD_WINDOW
        # RAW: one of our reads was recently written.
        for r in reads:
            if r == RiscvReg.ZERO:
                continue
            if r in last_writer_at:
                w_at = last_writer_at[r]
                if w_at >= window_start and w_at < idx:
                    _bump(db, CG_HAZARD, "raw")
                    hazard_found = True
                    break
        # WAW: one of our writes was recently written.
        if not hazard_found:
            for r in writes:
                if r in last_writer_at:
                    w_at = last_writer_at[r]
                    if w_at >= window_start and w_at < idx:
                        _bump(db, CG_HAZARD, "waw")
                        hazard_found = True
                        break
        # WAR: one of our writes was recently read.
        if not hazard_found:
            for r in writes:
                if r in last_reader_at:
                    r_at = last_reader_at[r]
                    if r_at >= window_start and r_at < idx:
                        _bump(db, CG_HAZARD, "war")
                        hazard_found = True
                        break

        if not hazard_found:
            _bump(db, CG_HAZARD, "none")

        for r in reads:
            last_reader_at[r] = idx
        for r in writes:
            last_writer_at[r] = idx


HAZARD_WINDOW = 8  # sliding-window size (in instructions) for hazard counting


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge(dst: CoverageDB, src: CoverageDB) -> CoverageDB:
    """Merge ``src`` into ``dst`` by bin-wise addition (returns ``dst``).

    Missing covergroups or bins in ``dst`` are created on the fly.
    """
    for cg, bins in src.items():
        dst_bins = dst.setdefault(cg, {})
        for bn, cnt in bins.items():
            dst_bins[bn] = dst_bins.get(bn, 0) + cnt
    return dst


def clone(db: CoverageDB) -> CoverageDB:
    return copy.deepcopy(db)


# ---------------------------------------------------------------------------
# CSR address → enum-name table (module-local — avoids repeat lookups)
# ---------------------------------------------------------------------------


def _build_priv_reg_by_addr() -> dict[int, str]:
    return {int(pr): pr.name for pr in PrivilegedReg}


_PRIV_REG_BY_ADDR: dict[int, str] = _build_priv_reg_by_addr()
