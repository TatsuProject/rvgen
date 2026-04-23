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
# --- added in the coverage-improvement wave ---
CG_MEM_ALIGN = "mem_align_cg"          # per load/store: byte_aligned/half/word/dword/unaligned
CG_LS_WIDTH = "load_store_width_cg"    # byte/half/word/dword (sign vs zero ext)
CG_CAT_TRANS = "category_transition_cg"  # prev_category -> current_category
CG_OP_TRANS = "opcode_transition_cg"   # adjacent-instr opcode transitions (top-N coverage)
CG_BRANCH_DIR = "branch_direction_cg"  # runtime: taken / not-taken (populated from ISS log)
CG_EXCEPTION = "exception_cg"          # runtime: mcause exception values
CG_PRIV_MODE = "privilege_mode_cg"     # runtime: M/S/U mode observed
CG_REG_VAL_SIGN = "rs_val_sign_cg"     # rs-value sign class (pos/neg/zero) on the fly
CG_IMM_EXT = "imm_range_cg"            # walking-ones / walking-zeros / corner classes
CG_PC_REACH = "pc_reach_cg"            # runtime: unique labels entered
# --- more-covergroups wave ---
CG_RS1_EQ_RS2 = "rs1_eq_rs2_cg"        # R-format: rs1==rs2 (same-reg path)
CG_RS1_EQ_RD = "rs1_eq_rd_cg"          # rd==rs1 (in-place op)
CG_BR_PER_MNEM = "branch_taken_per_mnem_cg"  # cross: branch mnemonic × taken/not_taken (runtime)
CG_VTYPE_DYN = "vtype_dyn_cg"          # (SEW, LMUL) pair observed when sampling a vector op
CG_CSR_ACCESS = "csr_access_cg"        # cross: CSR name × read/write access type
CG_LS_OFFSET = "load_store_offset_cg"  # offset magnitude bins for load/store ops
CG_STREAM = "directed_stream_cg"       # which directed stream contributed instrs
CG_CSR_VAL = "csr_value_cg"            # runtime: CSR × value-bucket (parsed from spike trace)
CG_RS_VAL_CORNER = "rs_val_corner_cg"  # runtime: GPR write-value corner class
CG_BIT_ACTIVITY = "bit_activity_cg"    # runtime: per-bit GPR-write activity (bit_N_toggled)
CG_RS1_RS2_CROSS = "rs1_rs2_cross_cg"  # explicit rs1 × rs2 cross (for C-extension port-pair coverage)
CG_RD_RS1_CROSS = "rd_rs1_cross_cg"    # rd × rs1 cross (in-place op pattern)


ALL_COVERGROUPS: tuple[str, ...] = (
    CG_OPCODE, CG_FORMAT, CG_CATEGORY, CG_GROUP,
    CG_RS1, CG_RS2, CG_RD,
    CG_IMM_SIGN, CG_HAZARD, CG_CSR,
    CG_FP_RM, CG_VTYPE, CG_VREG, CG_FPR,
    CG_FMT_X_CAT, CG_CAT_X_GRP,
    CG_MEM_ALIGN, CG_LS_WIDTH,
    CG_CAT_TRANS, CG_OP_TRANS,
    CG_BRANCH_DIR, CG_EXCEPTION, CG_PRIV_MODE,
    CG_IMM_EXT,
    CG_PC_REACH,
    CG_RS1_EQ_RS2, CG_RS1_EQ_RD,
    CG_BR_PER_MNEM, CG_VTYPE_DYN,
    CG_CSR_ACCESS, CG_LS_OFFSET, CG_STREAM, CG_CSR_VAL,
    CG_RS_VAL_CORNER, CG_BIT_ACTIVITY,
    CG_RS1_RS2_CROSS, CG_RD_RS1_CROSS,
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


def _imm_range_bin(imm: int, imm_len: int) -> str:
    """Classify the immediate by its bit-pattern: walking ones, walking zeros, extremes."""
    if imm_len == 0:
        return "none"
    mask = (1 << imm_len) - 1
    v = imm & mask
    if v == 0:
        return "zero"
    if v == mask:
        return "all_ones"
    # Walking-one: exactly one bit set.
    if v & (v - 1) == 0:
        return "walking_one"
    # Walking-zero: exactly one bit cleared.
    if (~v & mask) & ((~v & mask) - 1) == 0:
        return "walking_zero"
    # Min-signed / max-signed corners.
    sign_bit = 1 << (imm_len - 1)
    if v == sign_bit:
        return "min_signed"
    if v == sign_bit - 1:
        return "max_signed"
    return "generic"


_BYTE_OPS = frozenset({
    RiscvInstrName.LB, RiscvInstrName.LBU, RiscvInstrName.SB,
})
_HALF_OPS = frozenset({
    RiscvInstrName.LH, RiscvInstrName.LHU, RiscvInstrName.SH,
})
_WORD_OPS = frozenset({
    RiscvInstrName.LW, RiscvInstrName.LWU, RiscvInstrName.SW,
})
_DWORD_OPS = frozenset({
    RiscvInstrName.LD, RiscvInstrName.SD,
})


def _load_store_width_bin(name: RiscvInstrName) -> str | None:
    if name in _BYTE_OPS:
        return "byte"
    if name in _HALF_OPS:
        return "half"
    if name in _WORD_OPS:
        return "word"
    if name in _DWORD_OPS:
        return "dword"
    return None


def _mem_align_bin(offset: int, name: RiscvInstrName) -> str | None:
    """Classify the access by its natural alignment requirement + the offset bits.

    Natural alignment for ``name`` × offset mod natural width:

    - byte ops: always aligned (``aligned``).
    - half ops: ``aligned`` iff offset%2 == 0, else ``unaligned_half``.
    - word ops: ``aligned`` iff offset%4 == 0, else ``unaligned_word``.
    - dword ops: ``aligned`` iff offset%8 == 0, else ``unaligned_dword``.
    """
    if name in _BYTE_OPS:
        return "byte_aligned"
    if name in _HALF_OPS:
        return "half_aligned" if offset % 2 == 0 else "half_unaligned"
    if name in _WORD_OPS:
        return "word_aligned" if offset % 4 == 0 else "word_unaligned"
    if name in _DWORD_OPS:
        return "dword_aligned" if offset % 8 == 0 else "dword_unaligned"
    return None


def sample_instr(db: CoverageDB, instr: Instr, *, vector_cfg=None) -> None:
    """Sample one :class:`Instr` into ``db``.

    Safe to call for any registered instruction, including vector / FP /
    compressed / pseudo variants. No-ops gracefully on instructions that
    lack optional slots (e.g. the ``_LiPseudo`` emitted by directed streams).

    When ``vector_cfg`` is provided and the instruction is a vector op,
    also bumps :data:`CG_VTYPE_DYN` with a ``SEW<w>_LMUL<n>`` bin name —
    this tells the reporter what vtype was active when each vector op
    was generated.
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
    rs1_val = getattr(instr, "rs1", None) if has_rs1 else None
    rs2_val = getattr(instr, "rs2", None) if has_rs2 else None
    rd_val = getattr(instr, "rd", None) if has_rd else None
    if isinstance(rs1_val, RiscvReg):
        _bump(db, CG_RS1, rs1_val.name)
    if isinstance(rs2_val, RiscvReg):
        _bump(db, CG_RS2, rs2_val.name)
    if isinstance(rd_val, RiscvReg):
        _bump(db, CG_RD, rd_val.name)

    # rs1==rs2: a surprisingly common pipeline-interesting case (e.g.
    # "add x5, x5, x5" doubles x5; branches on rs1==rs2 always take/
    # fall-through in a deterministic way). Only bump for R/B formats
    # where both reads are meaningful.
    if isinstance(rs1_val, RiscvReg) and isinstance(rs2_val, RiscvReg):
        _bump(db, CG_RS1_EQ_RS2, "equal" if rs1_val == rs2_val else "distinct")
        # Full rs1 × rs2 cross: ~1024 possible bins on a reg-file access.
        # Worth tracking because port conflicts / forwarding paths often
        # depend on the specific pair.
        _bump(db, CG_RS1_RS2_CROSS, f"{rs1_val.name}__{rs2_val.name}")
    if isinstance(rs1_val, RiscvReg) and isinstance(rd_val, RiscvReg):
        _bump(db, CG_RS1_EQ_RD, "equal" if rs1_val == rd_val else "distinct")
        _bump(db, CG_RD_RS1_CROSS, f"{rd_val.name}__{rs1_val.name}")

    # Immediate sign (only if the instr actually has one and it was
    # randomized — branches resolved to label refs skip here since they
    # don't carry a meaningful signed immediate).
    has_imm = getattr(instr, "has_imm", False)
    imm_len = getattr(instr, "imm_len", 0)
    if has_imm and imm_len:
        _bump(db, CG_IMM_SIGN, _imm_sign_bin(instr.imm, imm_len))
        _bump(db, CG_IMM_EXT, _imm_range_bin(instr.imm, imm_len))

    # Load/store width + memory alignment samplers (static — we know the
    # offset the emitter chose, which is what GCC will ultimately feed spike).
    width_bin = _load_store_width_bin(instr.instr_name)
    if width_bin is not None:
        _bump(db, CG_LS_WIDTH, width_bin)
        # Use the signed offset if available (the emitter stashes it in
        # imm_str as a decimal number for load/stores). Fall back to
        # instr.imm interpreted per-format.
        off = 0
        try:
            off = int(instr.imm_str) if instr.imm_str.lstrip('-').isdigit() else int(instr.imm)
        except Exception:  # noqa: BLE001
            off = int(getattr(instr, "imm", 0))
        align_bin = _mem_align_bin(off, instr.instr_name)
        if align_bin is not None:
            _bump(db, CG_MEM_ALIGN, align_bin)

    # CSR — CsrInstr subclasses carry a 12-bit csr addr; decode via enum.
    if isinstance(instr, CsrInstr):
        csr_addr = int(getattr(instr, "csr", 0)) & 0xFFF
        csr_name = _PRIV_REG_BY_ADDR.get(csr_addr, f"CSR_{csr_addr:03X}")
        _bump(db, CG_CSR, csr_name)
        # CSR access-type — read (CSRRS/CSRRC with rs1=x0; CSRRSI/CSRRCI with
        # imm==0), write (CSRRW / CSRRWI always; CSRRS/CSRRC when effective
        # operand is nonzero). We conservatively treat CSRRS/C as writes
        # unless we can prove rs1==x0 / imm==0. CSRRWI always writes.
        name = instr.instr_name
        write_ops = (RiscvInstrName.CSRRW, RiscvInstrName.CSRRWI)
        clearset_ops = (RiscvInstrName.CSRRS, RiscvInstrName.CSRRC,
                         RiscvInstrName.CSRRSI, RiscvInstrName.CSRRCI)
        if name in write_ops:
            access = "write"
        elif name in clearset_ops:
            # Read-only if operand is zero.
            if name in (RiscvInstrName.CSRRS, RiscvInstrName.CSRRC):
                access = "read" if getattr(instr, "rs1", None) == RiscvReg.ZERO else "write"
            else:
                access = "read" if getattr(instr, "imm", 0) == 0 else "write"
        else:
            access = "read"
        _bump(db, CG_CSR_ACCESS, f"{csr_name}__{access}")

    # Load/store offset magnitude — split by sign + magnitude so verif teams
    # can see whether we've exercised all the offset-field corner cases.
    if width_bin is not None:  # we already computed this above; reuse
        off = 0
        try:
            off = int(instr.imm_str) if instr.imm_str.lstrip('-').isdigit() else int(getattr(instr, "imm", 0))
        except Exception:  # noqa: BLE001
            off = int(getattr(instr, "imm", 0))
        if off == 0:
            off_bin = "zero"
        elif off > 0:
            off_bin = "pos_small" if off < 128 else ("pos_medium" if off < 1024 else "pos_large")
        else:
            off_bin = "neg_small" if off > -128 else ("neg_medium" if off > -1024 else "neg_large")
        _bump(db, CG_LS_OFFSET, off_bin)

    # Directed-stream attribution: the stream's finalize() stamps a
    # "Start <stream_name>" comment on the first instr. We sample it into
    # the directed_stream covergroup so verif teams see which streams
    # actually contributed at least one instruction to the main sequence.
    comment = getattr(instr, "comment", "") or ""
    if comment.startswith("Start "):
        _bump(db, CG_STREAM, comment[len("Start "):].strip() or "unknown")

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

    # vtype sampling — when the caller passed a vector_cfg and this instr
    # came from the RVV group, record the active (SEW, LMUL) combination.
    if vector_cfg is not None:
        try:
            if instr.group == RiscvInstrGroup.RVV:
                sew = vector_cfg.vtype.vsew
                lmul = vector_cfg.vtype.vlmul
                frac = vector_cfg.vtype.fractional_lmul
                lmul_tag = f"MF{lmul}" if frac and lmul > 1 else f"M{lmul}"
                _bump(db, CG_VTYPE_DYN, f"SEW{sew}_{lmul_tag}")
        except AttributeError:
            pass

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


def sample_sequence(db: CoverageDB, seq: Iterable[Instr], *, vector_cfg=None) -> None:
    """Sample every instruction in ``seq`` plus inter-instruction hazards.

    Hazard detection looks at *register* dependencies only:

    - RAW (Read-After-Write): instr N reads a register written by instr ≤ N-1.
    - WAR (Write-After-Read): instr N writes a register read by instr ≤ N-1.
    - WAW (Write-After-Write): instr N writes a register written by instr ≤ N-1.

    "≤ N-1" is a sliding window of ``HAZARD_WINDOW`` instructions — beyond
    that, the register is effectively retired for hazard-counting purposes.

    When ``vector_cfg`` is provided it's forwarded to each per-instruction
    sample so vector ops can tag :data:`CG_VTYPE_DYN` bins.
    """
    last_writer_at: dict[RiscvReg, int] = {}
    last_reader_at: dict[RiscvReg, int] = {}
    prev_category: str | None = None
    prev_opcode: str | None = None

    for idx, instr in enumerate(seq):
        sample_instr(db, instr, vector_cfg=vector_cfg)

        # Category + opcode transitions — valuable for finding sequencing
        # bugs (e.g. LOAD immediately after BRANCH is a stall on some pipes).
        try:
            cur_cat = instr.category.name
            if prev_category is not None:
                _bump(db, CG_CAT_TRANS, f"{prev_category}__{cur_cat}")
            prev_category = cur_cat
        except (AttributeError, Exception):
            prev_category = None
        try:
            cur_op = instr.instr_name.name
            if prev_opcode is not None:
                _bump(db, CG_OP_TRANS, f"{prev_opcode}__{cur_op}")
            prev_opcode = cur_op
        except (AttributeError, Exception):
            prev_opcode = None

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
