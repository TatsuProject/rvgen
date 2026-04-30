"""Covergroup sampling — static (generator-side) only for Phase 1.

A :class:`CoverageDB` is a dict-of-dict keyed by covergroup name, then bin
name, with observed integer hit counts. This shape:

- serialises trivially to JSON / YAML,
- merges across runs by simple bin-wise addition,
- compares cleanly against a :class:`~rvgen.coverage.cgf.Goals`
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

from rvgen.isa.base import Instr
from rvgen.isa.csr_ops import CsrInstr
from rvgen.isa.enums import (
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
CG_VEC_LS_MODE = "vec_ls_addr_mode_cg"     # UNIT_STRIDED / STRIDED / INDEXED for vector LS
CG_VEC_EEW = "vec_eew_cg"                  # EEW chosen by vector loads/stores (8/16/32/64)
CG_VEC_EEW_VS_SEW = "vec_eew_vs_sew_cg"    # cross: EEW vs current SEW (eq/wider/narrower)
CG_VEC_EMUL = "vec_emul_cg"                # vd alignment / EMUL value used
CG_VEC_VM = "vec_vm_cg"                    # masked vs unmasked vector op
CG_VEC_VM_X_CAT = "vec_vm_category_cross_cg"  # cross: vm × category
CG_VEC_AMO_WD = "vec_amo_wd_cg"            # AMO wd flag (write-dst)
CG_VEC_VARIANT = "vec_va_variant_cg"       # VV/VX/VI/VF/WV/WX/WI/VVM/VXM/VFM
CG_VEC_NF = "vec_nfields_cg"               # Zvlsseg NFIELDS bins (1..8)
CG_VEC_SEG_X_MODE = "vec_seg_addr_mode_cross_cg"  # cross: NF × addr mode
CG_VEC_WIDE_NARROW = "vec_widening_narrowing_cg"  # widening / narrowing / quad-widening / convert
CG_VEC_CRYPTO = "vec_crypto_subext_cg"     # zvbb / zvbc / zvkn family
# vtype transitions across vsetvli emissions — sampled at sequence level
# when a vsetvli appears mid-stream (riscv_vsetvli_stress_instr_stream).
CG_VEC_SEW_TRANS = "vec_sew_transition_cg"     # prev_SEW -> new_SEW
CG_VEC_LMUL_TRANS = "vec_lmul_transition_cg"   # prev_LMUL -> new_LMUL
CG_VEC_VTYPE_TRANS = "vec_vtype_transition_cg"  # full vtype tuple transition
# vstart corner cases — sampled when riscv_vstart_corner_instr_stream emits
# `csrwi vstart, N` before a vector op.
CG_VEC_VSTART = "vec_vstart_cg"            # zero / one / small / mid / max

CG_CACHE_LINE_CROSS = "cache_line_cross_cg"   # load/store crossing 64B line
CG_PAGE_CROSS = "page_cross_cg"               # load/store crossing 4KiB page
CG_BRANCH_DIST = "branch_distance_cg"         # branch byte-offset bucket (signed)
CG_BRANCH_PATTERN = "branch_pattern_cg"       # T/N 3-gram (e.g. T_T_N)

# Value-class coverage (riscv-isac val_comb-style); rs1/rs2 sampled at
# runtime via a virtual reg-file built from spike GPR-write events.
CG_RS1_VAL_CLASS = "rs1_val_class_cg"
CG_RS2_VAL_CLASS = "rs2_val_class_cg"
CG_RD_VAL_CLASS = "rd_val_class_cg"
CG_RS_VAL_CROSS = "rs_val_class_cross_cg"


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
    CG_VEC_LS_MODE, CG_VEC_EEW, CG_VEC_EEW_VS_SEW, CG_VEC_EMUL,
    CG_VEC_VM, CG_VEC_VM_X_CAT, CG_VEC_AMO_WD,
    CG_VEC_VARIANT, CG_VEC_NF, CG_VEC_SEG_X_MODE,
    CG_VEC_WIDE_NARROW, CG_VEC_CRYPTO,
    CG_VEC_SEW_TRANS, CG_VEC_LMUL_TRANS, CG_VEC_VTYPE_TRANS,
    CG_VEC_VSTART,
    CG_CACHE_LINE_CROSS, CG_PAGE_CROSS,
    CG_BRANCH_DIST, CG_BRANCH_PATTERN,
    CG_RS1_VAL_CLASS, CG_RS2_VAL_CLASS, CG_RD_VAL_CLASS, CG_RS_VAL_CROSS,
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
    """Classify the immediate against the canonical value-class bins."""
    if imm_len == 0:
        return "none"
    return _value_class(imm, imm_len)


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


# ---------------------------------------------------------------------------
# Vector-specific samplers — only invoked from sample_instr when group==RVV.
# ---------------------------------------------------------------------------


# Mnemonic prefix → ratified Zv* sub-extension family. Used by CG_VEC_CRYPTO
# so a single bin per family captures whether the test exercises Zvbb/Zvbc/Zvkn.
_ZV_FAMILY_BY_PREFIX: tuple[tuple[tuple[str, ...], str], ...] = (
    (("VANDN", "VBREV", "VBREV8", "VREV8", "VCLZ", "VCTZ", "VCPOP",
      "VROL", "VROR", "VWSLL"), "zvbb"),
    (("VCLMUL", "VCLMULH"), "zvbc"),
    (("VAES", "VSHA2"), "zvkn"),
)


_CACHE_LINE_BYTES = 64    # standard for ARM/Intel/most RISC-V cores
_PAGE_BYTES = 4096
_ACCESS_WIDTH_BY_BIN = {"byte": 1, "half": 2, "word": 4, "dword": 8}


# The canonical set of value-class bins the rs1/rs2/rd_val_class_cg covergroups
# accept. Imported by cgf.py for the ``corners()`` abstract-bin function.
VALUE_CLASS_BINS: tuple[str, ...] = (
    "zero", "one", "all_ones", "min_signed", "max_signed",
    "walking_one", "walking_zero", "alternating", "small", "generic",
)

# Pre-computed alternating-pattern masks per XLEN — recomputing them per
# call costs measurably on long traces.
_ALT_MASKS: dict[int, tuple[int, int]] = {
    xlen: (
        sum(1 << i for i in range(0, xlen, 2)),  # 0x55..55
        sum(1 << i for i in range(1, xlen, 2)),  # 0xAA..AA
    )
    for xlen in (8, 16, 32, 64, 128)
}


def _value_class(val: int, xlen: int) -> str:
    """Classify a register value into industry-standard corner buckets.

    Mirrors what riscv-isac calls ``val_comb`` corners + the
    walking_ones / walking_zeros expansions. Returns one bin name from
    :data:`VALUE_CLASS_BINS`.
    """
    mask = (1 << xlen) - 1
    v = val & mask
    if v == 0:
        return "zero"
    if v == mask:
        return "all_ones"
    if v == 1:
        return "one"
    sign_bit = 1 << (xlen - 1)
    if v == sign_bit:
        return "min_signed"
    if v == sign_bit - 1:
        return "max_signed"
    if v & (v - 1) == 0:
        return "walking_one"
    inv = (~v) & mask
    if inv & (inv - 1) == 0:
        return "walking_zero"
    alt_a, alt_b = _ALT_MASKS.get(xlen, (0, 0))
    if v == alt_a or v == alt_b:
        return "alternating"
    sval = v - (1 << xlen) if v & sign_bit else v
    if -16 <= sval <= 16:
        return "small"
    return "generic"


_ADDR_MODE_BY_FMT: dict = {
    RiscvInstrFormat.VL_FORMAT: "UNIT_STRIDED",
    RiscvInstrFormat.VS_FORMAT: "UNIT_STRIDED",
    RiscvInstrFormat.VLS_FORMAT: "STRIDED",
    RiscvInstrFormat.VSS_FORMAT: "STRIDED",
    RiscvInstrFormat.VLX_FORMAT: "INDEXED",
    RiscvInstrFormat.VSX_FORMAT: "INDEXED",
    RiscvInstrFormat.VAMO_FORMAT: "INDEXED",
}


def _vector_family(name: RiscvInstrName) -> str | None:
    n = name.name
    for prefixes, fam in _ZV_FAMILY_BY_PREFIX:
        if any(n.startswith(p) for p in prefixes):
            return fam
    return None


def _sample_vector(db: CoverageDB, instr: Instr, vector_cfg) -> None:
    """Bump the vector-specific covergroups.

    Only called when ``instr.group == RVV`` and a ``vector_cfg`` is in scope.
    Each bump is wrapped in a try/except so an ill-formed vector pseudo
    (``vmv.v.x`` from the LS stream init) doesn't crash the sampler.
    """
    name = instr.instr_name
    cat = getattr(instr, "category", None)
    fmt = getattr(instr, "format", None)

    # Mask usage — ``vm`` is 1 (unmasked) or 0 (masked).
    vm = getattr(instr, "vm", None)
    if vm is not None:
        bin_name = "unmasked" if vm == 1 else "masked"
        _bump(db, CG_VEC_VM, bin_name)
        if cat is not None:
            _bump(db, CG_VEC_VM_X_CAT, f"{bin_name}__{cat.name}")

    # Address mode for vector loads/stores. Inferred from the format.
    if fmt is not None and fmt in _ADDR_MODE_BY_FMT:
        _bump(db, CG_VEC_LS_MODE, _ADDR_MODE_BY_FMT[fmt])

    # EEW / EMUL — set by the load/store randomizer.
    eew = getattr(instr, "eew", 0)
    emul = getattr(instr, "emul", 0)
    if eew:
        _bump(db, CG_VEC_EEW, f"EEW{eew}")
        sew = vector_cfg.vtype.vsew
        if eew == sew:
            rel = "eq"
        elif eew > sew:
            rel = "wider"
        else:
            rel = "narrower"
        _bump(db, CG_VEC_EEW_VS_SEW, f"EEW{eew}_vs_SEW{sew}_{rel}")
    if emul:
        _bump(db, CG_VEC_EMUL, f"EMUL{emul}")

    # AMO write-destination flag.
    if cat is not None and getattr(cat, "name", "") == "AMO":
        wd = getattr(instr, "wd", None)
        if wd is not None:
            _bump(db, CG_VEC_AMO_WD, "wd_set" if wd else "wd_clear")

    # va_variant — VV / VX / VI / VF / WV / WX / WI / VVM / VXM / VFM ...
    if getattr(instr, "has_va_variant", False):
        variant = getattr(instr, "va_variant", None)
        if variant is not None:
            _bump(db, CG_VEC_VARIANT, variant.name)

    # Zvlsseg NFIELDS — instr.nfields is (NF - 1) when set.
    nfields = getattr(instr, "nfields", 0)
    sub_extension = getattr(instr, "sub_extension", "")
    if sub_extension == "zvlsseg" and nfields is not None:
        nf = nfields + 1
        _bump(db, CG_VEC_NF, f"NF{nf}")
        seg_mode = _ADDR_MODE_BY_FMT.get(fmt) if fmt is not None else None
        if seg_mode is not None:
            _bump(db, CG_VEC_SEG_X_MODE, f"NF{nf}__{seg_mode}")

    # Widening / narrowing / quad-widening / convert — set by VectorInstr.
    if getattr(instr, "is_quad_widening_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "quad_widening")
    elif getattr(instr, "is_widening_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "widening")
    elif getattr(instr, "is_narrowing_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "narrowing")
    elif getattr(instr, "is_convert_instr", False):
        _bump(db, CG_VEC_WIDE_NARROW, "convert")

    # Crypto family — Zvbb / Zvbc / Zvkn.
    fam = _vector_family(name)
    if fam is not None:
        _bump(db, CG_VEC_CRYPTO, fam)


_BRANCH_INSTR_NAMES = frozenset({
    RiscvInstrName.BEQ, RiscvInstrName.BNE,
    RiscvInstrName.BLT, RiscvInstrName.BGE,
    RiscvInstrName.BLTU, RiscvInstrName.BGEU,
    RiscvInstrName.C_BEQZ, RiscvInstrName.C_BNEZ,
})


def _sample_branch_distance(db: CoverageDB, instr: Instr) -> None:
    """Bin a branch's static target distance (taken delta).

    Only the byte-offset magnitude + sign is statically known; whether
    the branch is taken at runtime is sampled separately by
    ``rvgen.coverage.runtime``.

    Distance buckets are picked to align with branch-predictor design
    rules of thumb:

    - ``zero``  (offset == 0; never seen in well-formed asm)
    - ``fwd_short`` / ``bwd_short``    : |off| < 16 (within an 8-instr window)
    - ``fwd_medium`` / ``bwd_medium``  : 16 ≤ |off| < 256 (typical loop body)
    - ``fwd_long`` / ``bwd_long``      : 256 ≤ |off| < 4096 (function-scope)
    - ``fwd_huge`` / ``bwd_huge``      : ≥ 4096 (rare; needs ±2KiB fixup)
    """
    if instr.instr_name not in _BRANCH_INSTR_NAMES:
        return
    # The generator stashes the branch's resolved byte offset in
    # ``imm``. For unresolved string-label branches it'll be 0; in that
    # case we can't bin — bail.
    off = int(getattr(instr, "imm", 0))
    if off == 0:
        # Try the imm_str: branches resolved late may carry a number there.
        try:
            off = int(instr.imm_str)
        except (ValueError, AttributeError):
            return
    if off == 0:
        _bump(db, CG_BRANCH_DIST, "zero")
        return
    direction = "fwd" if off > 0 else "bwd"
    mag = abs(off)
    if mag < 16:
        bucket = "short"
    elif mag < 256:
        bucket = "medium"
    elif mag < 4096:
        bucket = "long"
    else:
        bucket = "huge"
    _bump(db, CG_BRANCH_DIST, f"{direction}_{bucket}")


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
    except AttributeError:
        pass
    try:
        _bump(db, CG_CATEGORY, instr.category.name)
    except AttributeError:
        pass
    try:
        _bump(db, CG_GROUP, instr.group.name)
    except AttributeError:
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
    off = 0  # exposed below for offset / cache-line / page-cross sampling
    if width_bin is not None:
        _bump(db, CG_LS_WIDTH, width_bin)
        try:
            off = int(instr.imm_str) if instr.imm_str.lstrip('-').isdigit() else int(instr.imm)
        except (AttributeError, ValueError):
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

    # Load/store offset magnitude + cache-line + page-cross — all use the
    # ``off`` already computed above for the alignment classifier.
    if width_bin is not None:
        if off == 0:
            off_bin = "zero"
        elif off > 0:
            off_bin = "pos_small" if off < 128 else ("pos_medium" if off < 1024 else "pos_large")
        else:
            off_bin = "neg_small" if off > -128 else ("neg_medium" if off > -1024 else "neg_large")
        _bump(db, CG_LS_OFFSET, off_bin)

        # Cache-line + page-crossing — approximated against the stream's
        # per-region base when available, else just the offset. Under-reports
        # without a base hint; never over-reports.
        access_w = _ACCESS_WIDTH_BY_BIN.get(width_bin, 1)
        base_addr = int(getattr(instr, "_stream_region_base", 0)) or 0
        eff = base_addr + off
        if access_w > 1:
            line_start = eff & ~(_CACHE_LINE_BYTES - 1)
            line_end = (eff + access_w - 1) & ~(_CACHE_LINE_BYTES - 1)
            if line_start != line_end:
                _bump(db, CG_CACHE_LINE_CROSS, f"cross_w{access_w}")
            elif (eff & (_CACHE_LINE_BYTES - 1)) >= _CACHE_LINE_BYTES - access_w:
                _bump(db, CG_CACHE_LINE_CROSS, f"near_end_w{access_w}")
            else:
                _bump(db, CG_CACHE_LINE_CROSS, f"in_line_w{access_w}")

            page_start = eff & ~(_PAGE_BYTES - 1)
            page_end = (eff + access_w - 1) & ~(_PAGE_BYTES - 1)
            if page_start != page_end:
                _bump(db, CG_PAGE_CROSS, f"cross_w{access_w}")
            else:
                _bump(db, CG_PAGE_CROSS, "in_page")

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

                # Vector-specific covergroups — only meaningful for RVV ops.
                _sample_vector(db, instr, vector_cfg)
        except AttributeError:
            pass

    # Crosses
    try:
        _bump(db, CG_FMT_X_CAT, f"{instr.format.name}__{instr.category.name}")
    except AttributeError:
        pass
    try:
        _bump(db, CG_CAT_X_GRP, f"{instr.category.name}__{instr.group.name}")
    except AttributeError:
        pass

    # Microarchitectural — branch-distance bucket (static).
    _sample_branch_distance(db, instr)


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
    # vtype transition state. The boot-time vsetvli sets the initial vtype;
    # each subsequent vsetvli observed in the stream becomes a "new vtype"
    # and we sample the (prev → new) transitions.
    prev_vtype: tuple[int, int, bool] | None = None
    if vector_cfg is not None:
        prev_vtype = (
            vector_cfg.vtype.vsew,
            vector_cfg.vtype.vlmul,
            vector_cfg.vtype.fractional_lmul,
        )

    for idx, instr in enumerate(seq):
        sample_instr(db, instr, vector_cfg=vector_cfg)

        # vstart-corner pseudo carries _vstart_value — bin it.
        if hasattr(instr, "_vstart_value"):
            v = int(instr._vstart_value)
            if v == 0:
                bin_name = "zero"
            elif v == 1:
                bin_name = "one"
            elif v <= 4:
                bin_name = "small"
            elif v <= 16:
                bin_name = "mid"
            else:
                bin_name = "high"
            _bump(db, CG_VEC_VSTART, bin_name)

        # vsetvli emitted by the vsetvli-stress stream carries the new
        # SEW/LMUL/fractional/TA/MA as Python attrs (not real instr_name
        # because it's a pseudo). Match by class name to keep this hook
        # local to the streams module.
        if type(instr).__name__ == "_VsetvliPseudo" and vector_cfg is not None:
            new_sew = getattr(instr, "_sew", None)
            new_lmul = getattr(instr, "_lmul", None)
            new_frac = getattr(instr, "_fractional", False)
            if new_sew and new_lmul:
                lmul_tag_new = f"MF{new_lmul}" if new_frac and new_lmul > 1 else f"M{new_lmul}"
                if prev_vtype is not None:
                    p_sew, p_lmul, p_frac = prev_vtype
                    p_lmul_tag = f"MF{p_lmul}" if p_frac and p_lmul > 1 else f"M{p_lmul}"
                    _bump(db, CG_VEC_SEW_TRANS,
                          f"SEW{p_sew}__SEW{new_sew}")
                    _bump(db, CG_VEC_LMUL_TRANS,
                          f"{p_lmul_tag}__{lmul_tag_new}")
                    _bump(db, CG_VEC_VTYPE_TRANS,
                          f"SEW{p_sew}_{p_lmul_tag}__SEW{new_sew}_{lmul_tag_new}")
                prev_vtype = (new_sew, new_lmul, bool(new_frac))

        # Category + opcode transitions — valuable for finding sequencing
        # bugs (e.g. LOAD immediately after BRANCH is a stall on some pipes).
        try:
            cur_cat = instr.category.name
            if prev_category is not None:
                _bump(db, CG_CAT_TRANS, f"{prev_category}__{cur_cat}")
            prev_category = cur_cat
        except AttributeError:
            prev_category = None
        try:
            cur_op = instr.instr_name.name
            if prev_opcode is not None:
                _bump(db, CG_OP_TRANS, f"{prev_opcode}__{cur_op}")
            prev_opcode = cur_op
        except AttributeError:
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
