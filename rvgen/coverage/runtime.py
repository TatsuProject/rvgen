"""Runtime coverage sampling from a spike ``-l`` trace log.

The ``spike -l --log=<path>`` output looks like::

    core   0: >>>>  h0_start
    core   0: 0x00000000 (0x00000297) auipc   t0, 0x0
    core   0: 0x00000004 (0x02028593) addi    a1, t0, 32
    core   0: 0x80000006 (0x00028067) jr      t0

Each non-``>>>>`` line retires one instruction; the hex binary lets us
detect compressed (16-bit) vs standard (32-bit) sizes. Labels appear on
``>>>>`` lines and tell us when execution entered a symbolic region.

We extract three runtime covergroups:

- :data:`CG_BRANCH_DIR` — every branch instruction is classified as
  ``taken`` (next PC jumped) or ``not_taken`` (PC advanced by 2/4).
- :data:`CG_PC_REACH` — the set of named labels that execution *entered*
  (each ``>>>>`` line contributes one bin).
- :data:`CG_PRIV_MODE` — any MRET / SRET / URET executed, plus the "M"
  bin for plain execution (always hit since tests start in M-mode).

Dynamic opcode sampling (what actually ran vs what we emitted) also
feeds into the existing :data:`CG_OPCODE` covergroup — a valuable cross
with static sampling: a large gap between them means we generated dead
code. We tag dynamic hits with suffix ``_dyn`` so they don't merge into
the static ones.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from rvgen.coverage.collectors import (
    CG_ALTERNATE,
    CG_ATOMIC_ALIGN,
    CG_BIT_ACTIVITY,
    CG_BR_PER_MNEM,
    CG_BRANCH_DIR,
    CG_BRANCH_LOOP,
    CG_BRANCH_PATTERN4,
    CG_CSR_VAL,
    CG_DCSR_CAUSE,
    CG_DELEGATION,
    CG_EA_ALIGN,
    CG_EXCEPTION,
    CG_FCVT_CORNER,
    CG_FP_DATASET,
    CG_FP_FFLAGS,
    CG_FP_SRC_CLASS,
    CG_HPM_ACCESS,
    CG_LEAD_TRAIL,
    CG_MIP_FIELD,
    CG_MISA,
    CG_MSTATUS_FIELD,
    CG_MXR_SUM_MPRV,
    CG_NESTED_TRAP,
    CG_OPCODE,
    CG_PRIV_EVENT,
    CG_PRIV_MODE,
    CG_RS_VAL_CORNER,
    CG_TRAP_CAUSE,
    CG_VIRT_INSTR_TRAP,
    CG_WALKING_ONES,
    CG_WALKING_ZEROS,
    CG_WFI_CORNER,
    CG_XTVEC_MODE,
    CoverageDB,
    _alternate_bin,
    _ea_align_bin,
    _fp_dataset_bin,
    _fp_fflags_bins,
    _leading_trailing_bins,
    _trap_cause_bin,
    _value_class,
    _walking_ones_bins,
    _walking_zeros_bins,
    _fp_op_class,
)
from rvgen.isa.enums import RiscvInstrName as _N


# Privileged events we sample as transitions. Each bin reflects a
# specific privileged-mode event seen at runtime — independent of the
# privilege *mode* (M/S/U) which CG_PRIV_MODE tracks. A test that
# only spent time in M but issued sfence.vma + mret bumps both
# bins here.
_PRIV_EVENT_MNEMS = {
    "mret": "mret_taken",
    "sret": "sret_taken",
    "uret": "uret_taken",
    "ecall": "ecall_taken",
    "ebreak": "ebreak_taken",
    "wfi": "wfi_taken",
    "sfence.vma": "sfence_vma",
    "dret": "dret_taken",
    # Cache-management hints (Zicbo*) — semantically privileged-adjacent
    # because they're CSR-controlled by mseccfg / hcounteren / etc.
    "cbo.clean": "cbo_clean",
    "cbo.flush": "cbo_flush",
    "cbo.inval": "cbo_inval",
    "cbo.zero": "cbo_zero",
}

# Sprint-2: MSTATUS field decode. Bits per priv-arch v1.13 §3.1.6.
def _mstatus_field_bins(value: int) -> tuple[str, ...]:
    bins: list[str] = []
    # MIE bit 3, MPIE bit 7, SIE bit 1, SPIE bit 5, UIE bit 0, UPIE bit 4.
    if value & (1 << 3):
        bins.append("mie_set")
    if value & (1 << 7):
        bins.append("mpie_set")
    if value & (1 << 1):
        bins.append("sie_set")
    if value & (1 << 5):
        bins.append("spie_set")
    # MPP[12:11], SPP[8].
    mpp = (value >> 11) & 0x3
    bins.append({0: "mpp_U", 1: "mpp_S", 3: "mpp_M"}.get(mpp, "mpp_reserved"))
    spp = (value >> 8) & 0x1
    bins.append("spp_S" if spp else "spp_U")
    if value & (1 << 17):
        bins.append("mprv_set")
    if value & (1 << 18):
        bins.append("sum_set")
    if value & (1 << 19):
        bins.append("mxr_set")
    if value & (1 << 20):
        bins.append("tvm_set")
    if value & (1 << 21):
        bins.append("tw_set")
    if value & (1 << 22):
        bins.append("tsr_set")
    # FS[14:13], VS[10:9].
    fs = (value >> 13) & 0x3
    if fs:
        bins.append({1: "fs_initial", 2: "fs_clean", 3: "fs_dirty"}.get(fs, "fs_off"))
    vs = (value >> 9) & 0x3
    if vs:
        bins.append({1: "vs_initial", 2: "vs_clean", 3: "vs_dirty"}.get(vs, "vs_off"))
    return tuple(bins) if bins else ("mstatus_zero",)


# MIP / MIE field decode. The interesting bits are 1/3/5/7/9/11 (S/M
# software/timer/external).
_MIP_BIT_NAMES = {
    1: "ssip", 3: "msip",
    5: "stip", 7: "mtip",
    9: "seip", 11: "meip",
    # Sscofpmf — counter-overflow.
    13: "lcofip",
}


def _mip_field_bins(value: int) -> tuple[str, ...]:
    bins: list[str] = []
    for bit, n in _MIP_BIT_NAMES.items():
        if value & (1 << bit):
            bins.append(f"{n}_pending")
    if not bins:
        bins.append("none_pending")
    else:
        bins.append("any_pending")
    return tuple(bins)


# MISA letter decode — bit positions are 'A'=0 .. 'Z'=25.
def _misa_letter_bins(value: int) -> tuple[str, ...]:
    """Return one bin per set extension letter."""
    bins = []
    for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if value & (1 << i):
            bins.append(f"misa_{letter}")
    return tuple(bins)


# xTVEC.MODE decode — low 2 bits select DIRECT (00) / VECTORED (01); 10/11 reserved.
def _xtvec_mode_bin(csr_name: str, value: int) -> str:
    mode = value & 0x3
    if csr_name == "MTVEC":
        return f"mtvec_{'direct' if mode == 0 else 'vectored' if mode == 1 else 'reserved'}"
    if csr_name == "STVEC":
        return f"stvec_{'direct' if mode == 0 else 'vectored' if mode == 1 else 'reserved'}"
    return f"{csr_name.lower()}_unknown"


# Trap-delegation decoder. Bit positions of medeleg/mideleg correspond
# to the cause codes in collectors._EXCEPTION_NAMES / _INTERRUPT_NAMES.
def _delegation_bins(csr_name: str, value: int) -> tuple[str, ...]:
    """Return one bin per delegated cause/interrupt bit set."""
    out = []
    if csr_name == "MEDELEG":
        from rvgen.coverage.collectors import _EXCEPTION_NAMES
        for code, ename in _EXCEPTION_NAMES.items():
            if value & (1 << code):
                out.append(f"medeleg_{ename}")
    elif csr_name == "MIDELEG":
        from rvgen.coverage.collectors import _INTERRUPT_NAMES
        for code, iname in _INTERRUPT_NAMES.items():
            if value & (1 << code):
                out.append(f"mideleg_{iname}")
    elif csr_name in ("HEDELEG", "HIDELEG"):
        out.append(f"{csr_name.lower()}_set")
    return tuple(out)


# DCSR.cause field — bits [8:6] in the DCSR register layout.
_DCSR_CAUSE_NAMES = {
    1: "ebreak",
    2: "trigger",
    3: "haltreq",
    4: "step",
    5: "resethaltreq",
    6: "group",
}


def _dcsr_cause_bin(value: int) -> str:
    cause = (value >> 6) & 0x7
    name = _DCSR_CAUSE_NAMES.get(cause, "unknown")
    return f"dcsr_cause_{cause}_{name}"


def _classify_fp_value(value: int, width: int) -> str | None:
    """Return a coarse FP-class bin name for a source-operand sample.

    Coarser than fp_dataset_bin: collapses the 15-bin dataset to 5 bins
    so the source-class covergroup highlights "did we exercise NaN /
    Inf / subnormal / zero / normal as inputs to FP ops".
    """
    bin_name = _fp_dataset_bin(value, width)
    if bin_name in ("pos_zero", "neg_zero"):
        return "src_zero"
    if bin_name in ("pos_inf", "neg_inf"):
        return "src_inf"
    if bin_name in ("qnan", "snan"):
        return "src_nan"
    if bin_name in ("pos_subnormal", "neg_subnormal"):
        return "src_subnormal"
    if bin_name == "generic":
        return "src_normal"
    if bin_name.startswith(("pos_", "neg_")):
        return "src_normal"  # min/max/one are normals
    return None


# HPM counter / event CSR addresses. mhpmcounter3..31 = 0xB03..0xB1F;
# mhpmcounter3h..31h = 0xB83..0xB9F (RV32 high halves); mhpmevent3..31 =
# 0x323..0x33F. We compress per-counter bins to keep the bin count
# manageable: counter_3..31 + event_3..31, plus aggregate any_hpm.
def _hpm_csr_bin(csr_name: str) -> str | None:
    """Return an HPM bin name if the CSR name is a perf-counter, else None."""
    if csr_name.startswith("MHPMCOUNTER"):
        return f"counter_{csr_name[len('MHPMCOUNTER'):].lstrip('0') or '0'}"
    if csr_name.startswith("MHPMEVENT"):
        return f"event_{csr_name[len('MHPMEVENT'):].lstrip('0') or '0'}"
    if csr_name in ("MCYCLE", "MCYCLEH"):
        return "mcycle"
    if csr_name in ("MINSTRET", "MINSTRETH"):
        return "minstret"
    if csr_name in ("CYCLE", "CYCLEH"):
        return "cycle_user"
    if csr_name in ("INSTRET", "INSTRETH"):
        return "instret_user"
    if csr_name in ("TIME", "TIMEH"):
        return "time"
    if csr_name == "MCOUNTINHIBIT":
        return "mcountinhibit"
    return None


# CSR addresses whose *write* bumps a priv_event bin. Keyed by the
# canonical CSR name uppercase (matches what CSR_WRITE_IN_COMMIT_RE
# extracts from spike --log-commits).
_PRIV_EVENT_CSR_WRITES = {
    "SATP": "satp_write",
    "MEDELEG": "medeleg_write",
    "MIDELEG": "mideleg_write",
    "MSTATUS": "mstatus_write",
    "STVEC": "stvec_write",
    "MTVEC": "mtvec_write",
    "PMPCFG0": "pmpcfg_write",
    "PMPCFG1": "pmpcfg_write",
    "PMPCFG2": "pmpcfg_write",
    "DCSR": "dcsr_write",
    "DPC": "dpc_write",
    "DSCRATCH0": "dscratch_write",
    "DSCRATCH1": "dscratch_write",
}


# Spike trace format (v1.1.x). Capture PC, binary encoding, mnemonic,
# and operand tail. The binary width lets us distinguish 32-bit (8 hex
# chars) from compressed (4 hex chars).
_TRACE_RE = re.compile(
    r"^core\s+\d+:\s+0x(?P<pc>[0-9a-f]+)\s+\(0x(?P<bin>[0-9a-f]+)\)\s+"
    r"(?P<mnem>\S+)(?:\s+(?P<tail>.*))?$"
)

# Label-enter line (spike's auto-symbolisation).
_LABEL_RE = re.compile(r"^core\s+\d+:\s+>>>>\s*(?P<label>\S+)\s*$")

# Commit line — emitted per-instruction when spike runs with
# ``--log-commits``. Format is ``core N: <priv> 0x<pc> (0x<bin>) <writes>``
# where <writes> can include register writes (``x5  0x...``) and CSR
# writes (``c769_misa 0x...``). Multiple writes on the same line.
_COMMIT_RE = re.compile(
    r"^core\s+\d+:\s+(?P<pri>\d)\s+0x(?P<pc>[0-9a-f]+)\s+\(0x(?P<bin>[0-9a-f]+)\)\s*(?P<writes>.*)$"
)
_CSR_WRITE_IN_COMMIT_RE = re.compile(r"c[0-9a-f]+_(?P<csr>\w+)\s+0x(?P<val>[0-9a-f]+)")
# GPR (and FPR) write inside commit: matches "x5 0x..." / "f5 0x..." with
# any-width hex value. Using the register name as a hint for covergroup
# dimensionality (only GPR writes matter for the corner-value coverage).
_GPR_WRITE_IN_COMMIT_RE = re.compile(r"\b(?P<reg>x\d+)\s+0x(?P<val>[0-9a-f]+)")
# Floating-point register writes — same shape as GPR but f0..f31. The
# value width tells us the FP precision: 4 hex chars = half (Zfh),
# 8 = single, 16 = double. NaN-boxing means a 64-bit single sits in the
# high bits as 0xFFFFFFFF — we strip that for the dataset classifier.
_FPR_WRITE_IN_COMMIT_RE = re.compile(r"\b(?P<reg>f\d+)\s+0x(?P<val>[0-9a-f]+)")
# Memory-access events (load/store) — spike --log-commits emits "mem" tokens
# with the effective address. Format examples:
#   "x12 0xdeadbeef mem 0x80001000"           (load — read EA)
#   "mem 0x80001008 0xdeadbeef"               (store — written address + data)
_MEM_EA_RE = re.compile(r"\bmem\s+0x(?P<addr>[0-9a-f]+)")

# ABI register-name → x-register mapping. Used to translate spike's
# disassembled operand tails (which use ABI names like ``a0``, ``s1``,
# ``ra``) back to the canonical x-numbers our virtual reg-file tracks.
_ABI_TO_XREG = {
    "zero": "x0", "ra": "x1", "sp": "x2", "gp": "x3", "tp": "x4",
    "t0": "x5", "t1": "x6", "t2": "x7",
    "s0": "x8", "fp": "x8", "s1": "x9",
    "a0": "x10", "a1": "x11", "a2": "x12", "a3": "x13",
    "a4": "x14", "a5": "x15", "a6": "x16", "a7": "x17",
    "s2": "x18", "s3": "x19", "s4": "x20", "s5": "x21",
    "s6": "x22", "s7": "x23", "s8": "x24", "s9": "x25",
    "s10": "x26", "s11": "x27",
    "t3": "x28", "t4": "x29", "t5": "x30", "t6": "x31",
}
# Operand-tail regex: captures up to 3 register/imm tokens (rd, rs1, rs2
# or rd, rs1, imm — the position-2 token is rs1 in either case).
_OPERAND_TOKEN_RE = re.compile(
    r"(?P<tok>-?\b(?:[a-z]\d+|zero|ra|sp|gp|tp|fp|s\d|a\d|t\d|0x[0-9a-f]+|-?\d+)\b)"
)


def _corner_bucket(val: int) -> str:
    """Classify a 64-bit value against the canonical corner set."""
    v64 = val & 0xFFFF_FFFF_FFFF_FFFF
    if v64 == 0:
        return "zero"
    if v64 == 0xFFFF_FFFF_FFFF_FFFF:
        return "all_ones_64"
    if v64 == 0xFFFF_FFFF:
        return "all_ones_32"
    if v64 == 0x8000_0000_0000_0000:
        return "min_signed_64"
    if v64 == 0x8000_0000:
        return "min_signed_32"
    if v64 == 0x7FFF_FFFF_FFFF_FFFF:
        return "max_signed_64"
    if v64 == 0x7FFF_FFFF:
        return "max_signed_32"
    if v64 <= 0xFF:
        return "small_pos"
    if v64 & 0x8000_0000_0000_0000:
        return "msb64_set"
    return "generic"

# Spike's priv-level mapping in commit lines.
_PRI_LEVEL_BIN = {"0": "U_mode", "1": "S_mode", "3": "M_mode"}


# Branch mnemonics spike emits (canonical RV names).
_BRANCH_MNEMS = frozenset({
    "beq", "bne", "blt", "bge", "bltu", "bgeu",
    # C.BEQZ / C.BNEZ compressed forms.
    "c.beqz", "c.bnez",
    # Pseudo-forms spike may disasm.
    "beqz", "bnez", "bltz", "bgez", "bgtz", "blez",
})

# Privilege-transition mnemonics.
_PRIV_MNEMS = {"mret": "M_return", "sret": "S_return", "uret": "U_return"}

# CSR-write mnemonics. For each we look at the rs1 (or imm) operand to
# classify the value being written.
_CSR_WRITE_MNEMS = frozenset({"csrw", "csrrw", "csrrwi", "csrs", "csrrs", "csrrsi", "csrc", "csrrc", "csrrci"})


def _value_bucket(val: int, xlen: int = 64) -> str:
    """Bucket a 2's-complement value into coverage bins."""
    mask = (1 << xlen) - 1
    v = val & mask
    if v == 0:
        return "zero"
    if v == mask:
        return "all_ones"
    if v & (1 << (xlen - 1)):
        return "msb_set"
    if v < 0x100:
        return "small"
    if v < 0x10000:
        return "medium"
    return "large"


# Canonical covergroup name for dynamically-observed opcodes. We reuse
# CG_OPCODE but with a "_dyn" suffix on each bin so the static and
# dynamic views don't collide.
CG_OPCODE_DYN_SUFFIX = "__dyn"


def sample_trace_file(
    db: CoverageDB,
    trace_path: Path,
    *,
    max_lines: int = 2_000_000,
    sample_handler_workload: bool = False,
) -> dict:
    """Parse ``trace_path`` and merge runtime bins into ``db``.

    Returns a small metadata dict: ``{lines_parsed, pc_reach_labels,
    branches_observed}``. If ``trace_path`` doesn't exist or is empty,
    returns zeros — silent on purpose so callers can batch many traces
    without crashing on a single missing log.
    """
    if not trace_path.exists():
        return {"lines_parsed": 0, "pc_reach_labels": 0, "branches_observed": 0}

    lines_parsed = 0
    label_hits = 0
    branches = 0
    prev_pc: int | None = None
    prev_bin_bytes: int = 0
    prev_mnem: str | None = None
    prev_was_branch: bool = False
    branch_history: deque[str] = deque(maxlen=64)
    # Virtual reg-file tracking last value written to each GPR. Lets us
    # derive rs1_val_class / rs2_val_class on the *next* instruction from
    # the value the previous writer left there. Spike's --log-commits
    # doesn't print rs1/rs2 reads — this mimics the behavior.
    gpr_state: dict[str, int] = {}
    # Sprint-2: virtual FP-register-file tracking — used to classify FP
    # source operands by IEEE-754 class on the next FP instruction.
    # Maps fpr name → (value, width_bits).
    fpr_state: dict[str, tuple[int, int]] = {}
    # Trap-region tracking — flipped True when we cross a `*trap*` /
    # `mtvec_handler` / `stvec_handler` label and back to False on
    # mret/sret. Lets nested-trap detection trigger only inside an
    # actual handler region.
    in_trap_region: bool = False
    # Branch-instruction PC tracker for "loop closure" classification.
    # When we observe a branch's outcome, we have prev_pc (the branch
    # site) and the current pc (the target if taken).
    last_branch_pc: int | None = None
    # Sprint-2: virtual MSTATUS state — tracks MXR/SUM/MPRV bits so
    # we can cross-sample with each load/store EA.
    mstatus_state: dict[str, int] = {"mxr": 0, "sum": 0, "mprv": 0, "tw": 0}

    def _bump(cg: str, bn: str) -> None:
        bins = db.setdefault(cg, {})
        bins[bn] = bins.get(bn, 0) + 1

    # Start in M-mode (every test we emit boots there).
    _bump(CG_PRIV_MODE, "M_entered")

    with trace_path.open() as f:
        for i, line in enumerate(f):
            if i >= max_lines:
                break
            lines_parsed += 1

            lbl = _LABEL_RE.match(line)
            if lbl:
                label_hits += 1
                label = lbl.group("label").rstrip(":")
                _bump("pc_reach_cg", label)
                # If the label is a known trap handler, count as exception-taken.
                if "trap" in label or "mtvec" in label or "stvec" in label:
                    _bump(CG_EXCEPTION, "trap_entered")
                    in_trap_region = True
                # Returning to one of the boot/main labels after a trap
                # implies we've left the trap-region.
                if label in ("main", "h0_start", "init", "test_done"):
                    in_trap_region = False
                continue

            # Commit-line handling first (has the same prefix but includes
            # a priv-level digit before the pc).
            cm = _COMMIT_RE.match(line)
            if cm:
                writes = cm.group("writes") or ""
                for wm in _CSR_WRITE_IN_COMMIT_RE.finditer(writes):
                    csr = wm.group("csr").upper()
                    val = int(wm.group("val"), 16)
                    _bump(CG_CSR_VAL, f"{csr}__{_value_bucket(val, 64)}")
                    # Privileged-event sampling: certain CSR writes are
                    # interesting events in their own right (SATP write
                    # = paging configured, PMPCFGn write = PMP region
                    # programmed, DCSR/DPC write = debug entry).
                    ev = _PRIV_EVENT_CSR_WRITES.get(csr)
                    if ev:
                        _bump(CG_PRIV_EVENT, ev)
                    # Sprint-1: FP fflags decode. fflags lives in fcsr[4:0];
                    # FFLAGS, FCSR, and FRM all alias the same 5 bits.
                    if csr in ("FFLAGS", "FCSR"):
                        for bn in _fp_fflags_bins(val):
                            _bump(CG_FP_FFLAGS, bn)
                    # Sprint-1: trap-cause decode. mcause/scause MSB toggles
                    # exception-vs-interrupt; the rest is a 4..6 bit cause code.
                    if csr in ("MCAUSE", "SCAUSE", "VSCAUSE"):
                        _bump(CG_TRAP_CAUSE, _trap_cause_bin(val, 64))
                        # Nested-trap detection — a trap-cause write while
                        # we're still inside a trap-handler label region.
                        if in_trap_region:
                            mode_now = "M" if csr == "MCAUSE" else "S"
                            _bump(CG_NESTED_TRAP,
                                  f"nested_{mode_now}_in_trap")
                        else:
                            _bump(CG_NESTED_TRAP, "no_nesting")
                        # Sprint-2: virtual-instruction trap (H-ext cause=22).
                        sign_bit = 1 << 63
                        if (val & ~sign_bit) == 22 and not (val & sign_bit):
                            # Look at the instruction that triggered it
                            # (prev_mnem) to bin the offending op family.
                            origin = (prev_mnem or "other").lower()
                            if origin == "wfi":
                                _bump(CG_VIRT_INSTR_TRAP, "vi_wfi")
                            elif origin.startswith("sfence"):
                                _bump(CG_VIRT_INSTR_TRAP, "vi_sfence")
                            elif origin.startswith("csr"):
                                _bump(CG_VIRT_INSTR_TRAP, "vi_csr")
                            else:
                                _bump(CG_VIRT_INSTR_TRAP, "vi_other")
                    # Sprint-2: MSTATUS field decode.
                    if csr in ("MSTATUS", "SSTATUS"):
                        for bn in _mstatus_field_bins(val):
                            _bump(CG_MSTATUS_FIELD, f"{csr}__{bn}")
                        # Track field bits for the MXR×SUM×MPRV cross.
                        mstatus_state["mxr"] = (val >> 19) & 1
                        mstatus_state["sum"] = (val >> 18) & 1
                        mstatus_state["mprv"] = (val >> 17) & 1
                        mstatus_state["tw"] = (val >> 21) & 1
                    # Sprint-2: xTVEC mode (DIRECT vs VECTORED).
                    if csr in ("MTVEC", "STVEC"):
                        _bump(CG_XTVEC_MODE, _xtvec_mode_bin(csr, val))
                    # Sprint-2: trap delegation (medeleg / mideleg / hedeleg).
                    if csr in ("MEDELEG", "MIDELEG", "HEDELEG", "HIDELEG"):
                        for bn in _delegation_bins(csr, val):
                            _bump(CG_DELEGATION, bn)
                    # Sprint-2: MIP / MIE pending bits.
                    if csr in ("MIP", "MIE", "SIP", "SIE"):
                        for bn in _mip_field_bins(val):
                            _bump(CG_MIP_FIELD, f"{csr}__{bn}")
                    # Sprint-2: MISA letter bits.
                    if csr == "MISA":
                        for bn in _misa_letter_bins(val):
                            _bump(CG_MISA, bn)
                    # Sprint-2: HPM / counter-CSR access.
                    hpm_bin = _hpm_csr_bin(csr)
                    if hpm_bin is not None:
                        _bump(CG_HPM_ACCESS, hpm_bin)
                    # Sprint-2: DCSR.cause decode.
                    if csr == "DCSR":
                        _bump(CG_DCSR_CAUSE, _dcsr_cause_bin(val))
                # GPR write-values — classify against the canonical corners,
                # and also bump the bit-activity covergroup for each set bit
                # (reveals dead bits — if bit_N_set never appears, no
                # instruction ever computed a value with bit N set).
                #
                # All bins here classify *test-workload* behavior — so we
                # skip them when we're inside a trap-handler region. The
                # handler's GPR push/pop is mandatory boot infrastructure
                # and pollutes coverage with noise the user didn't ask for.
                # ``sample_handler_workload=True`` overrides this filter.
                filter_workload = in_trap_region and not sample_handler_workload
                for wm in _GPR_WRITE_IN_COMMIT_RE.finditer(writes):
                    reg = wm.group("reg")
                    val = int(wm.group("val"), 16)
                    gpr_state[reg] = val
                    if filter_workload:
                        continue
                    _bump(CG_RS_VAL_CORNER, _corner_bucket(val))
                    _bump(CG_RD_VAL_CLASS := "rd_val_class_cg", _value_class(val, 64))
                    # Cap at 64 bits; bin name = "bit_N_set".
                    v = val & 0xFFFF_FFFF_FFFF_FFFF
                    while v:
                        bit = (v & -v).bit_length() - 1  # lowest set bit
                        _bump(CG_BIT_ACTIVITY, f"bit_{bit:02d}_set")
                        v &= v - 1
                    # Sprint-2: walking-ones/-zeros + alternating + leading/
                    # trailing run-length on every observed GPR write value.
                    # These are riscv-isac CGF abstract-bin functions.
                    for bn in _walking_ones_bins(val, xlen=64):
                        _bump(CG_WALKING_ONES, bn)
                    for bn in _walking_zeros_bins(val, xlen=64):
                        _bump(CG_WALKING_ZEROS, bn)
                    ap = _alternate_bin(val, xlen=64)
                    if ap is not None:
                        _bump(CG_ALTERNATE, ap)
                    for bn in _leading_trailing_bins(val, xlen=64):
                        _bump(CG_LEAD_TRAIL, bn)
                # Sprint-1: FPR writes feed the FP-corner-value dataset.
                # The hex width tells us precision; NaN-boxed singles in
                # 64-bit FPRs ride as 0xFFFFFFFF<32-bit-single>.
                for fm in _FPR_WRITE_IN_COMMIT_RE.finditer(writes):
                    reg = fm.group("reg")
                    raw = fm.group("val")
                    val = int(raw, 16)
                    width = 16 if len(raw) <= 4 else (32 if len(raw) <= 8 else 64)
                    if width == 64 and (val >> 32) == 0xFFFFFFFF:
                        _bump(CG_FP_DATASET,
                              _fp_dataset_bin(val & 0xFFFFFFFF, 32))
                    else:
                        _bump(CG_FP_DATASET, _fp_dataset_bin(val, width))
                    # Cache the FP value for the next FP op's source
                    # classification.
                    fpr_state[reg] = (val, width)
                # Sprint-1: effective-address alignment from "mem 0x..." tokens.
                for em in _MEM_EA_RE.finditer(writes):
                    addr = int(em.group("addr"), 16)
                    _bump(CG_EA_ALIGN, _ea_align_bin(addr))
                    # Sprint-2: MXR × SUM × MPRV cross at every memory
                    # access. Captures whether the test exercised every
                    # MMU-policy combination per access.
                    pri_now = _PRI_LEVEL_BIN.get(cm.group("pri"), "unknown")
                    cross_label = (
                        f"{pri_now}__"
                        f"mxr{int(mstatus_state.get('mxr', 0))}__"
                        f"sum{int(mstatus_state.get('sum', 0))}__"
                        f"mprv{int(mstatus_state.get('mprv', 0))}"
                    )
                    _bump(CG_MXR_SUM_MPRV, cross_label)
                    # Sprint-2: atomic alignment — when the previous
                    # mnemonic was an LR/SC/AMO and we now see the EA.
                    if prev_mnem is not None and prev_mnem.startswith(
                            ("lr.", "sc.", "amo")):
                        suffix = "w" if prev_mnem.endswith(".w") else (
                            "d" if prev_mnem.endswith(".d") else "x")
                        natural = 4 if suffix == "w" else 8 if suffix == "d" else 1
                        if natural > 1 and addr % natural != 0:
                            _bump(CG_ATOMIC_ALIGN, f"misaligned_{suffix}")
                        else:
                            _bump(CG_ATOMIC_ALIGN, f"aligned_{suffix}")
                # Sample the priv level observed on retirement.
                pri_bin = _PRI_LEVEL_BIN.get(cm.group("pri"))
                if pri_bin:
                    _bump(CG_PRIV_MODE, pri_bin)
                continue

            m = _TRACE_RE.match(line)
            if not m:
                continue

            pc_hex = m.group("pc")
            bin_hex = m.group("bin")
            mnem = m.group("mnem").lower()

            pc = int(pc_hex, 16)
            bin_bytes = 2 if len(bin_hex) == 4 else 4

            # Dynamic opcode sample (canonicalize: "c.add" and "add" are
            # distinct bins — valuable signal).
            _bump(CG_OPCODE, mnem.upper() + CG_OPCODE_DYN_SUFFIX)

            # rs1/rs2 value-class sampling — parse the operand tail. For
            # most R/I/S/B-format scalar ops, position 1 is rd and 2/3
            # are rs1/rs2 (or rs1/imm). For B-format the positions are
            # rs1/rs2/imm. We classify position-2 + position-3 tokens
            # against the virtual reg-file when they're recognisable
            # registers, sample the tracked value, and skip otherwise.
            tail = m.group("tail") or ""
            tokens = _OPERAND_TOKEN_RE.findall(tail)
            if len(tokens) >= 2:
                cls1: str | None = None
                cls2: str | None = None
                tok2 = tokens[1].lower()
                xreg2 = _ABI_TO_XREG.get(tok2, tok2 if tok2.startswith("x") else None)
                if xreg2 in gpr_state:
                    cls1 = _value_class(gpr_state[xreg2], 64)
                    _bump("rs1_val_class_cg", cls1)
                if len(tokens) >= 3:
                    tok3 = tokens[2].lower()
                    xreg3 = _ABI_TO_XREG.get(tok3, tok3 if tok3.startswith("x") else None)
                    if xreg3 in gpr_state:
                        cls2 = _value_class(gpr_state[xreg3], 64)
                        _bump("rs2_val_class_cg", cls2)
                if cls1 is not None and cls2 is not None:
                    _bump("rs_val_class_cross_cg", f"{cls1}__{cls2}")

            # Sprint-2: FP source-operand class. When an FP mnemonic
            # (other than load) appears, classify the source-FPR's last
            # known value into one of {nan, inf, subnormal, zero, normal}
            # and bin one entry per source.
            if mnem.startswith("f") and not mnem.startswith(("fence", "flw", "fld", "flh")):
                # Operand tail tokens 2..4 are typically the source FPRs.
                tail = m.group("tail") or ""
                fp_tokens = re.findall(r"\bf[ast]?\d+\b|\bf[t-z]\d+\b|\bf\d+\b",
                                       tail)
                for tok in fp_tokens[1:4]:  # skip the dest fp register
                    state = fpr_state.get(tok)
                    if state is None:
                        continue
                    fval, width = state
                    cls = _classify_fp_value(fval, width)
                    if cls is not None:
                        _bump(CG_FP_SRC_CLASS, cls)

            # Branch direction — the *previous* instruction was the branch;
            # now that we see this PC, we know whether the branch was taken.
            if prev_was_branch and prev_pc is not None and prev_mnem is not None:
                expected_fall_through = prev_pc + prev_bin_bytes
                taken = pc != expected_fall_through
                if taken:
                    _bump(CG_BRANCH_DIR, "taken")
                    _bump(CG_BR_PER_MNEM, f"{prev_mnem.upper()}__T")
                    branch_history.append("T")
                else:
                    _bump(CG_BRANCH_DIR, "not_taken")
                    _bump(CG_BR_PER_MNEM, f"{prev_mnem.upper()}__NT")
                    branch_history.append("N")
                branches += 1
                if len(branch_history) >= 3:
                    pattern = "".join(list(branch_history)[-3:])
                    _bump("branch_pattern_cg", pattern)
                # Sprint-2: 4-gram pattern.
                if len(branch_history) >= 4:
                    pat4 = "".join(list(branch_history)[-4:])
                    _bump(CG_BRANCH_PATTERN4, pat4)
                # Sprint-2: branch loop / skip classification.
                # fwd vs bwd is determined by the actual taken offset
                # (taken=True branches always have a delta != 0 from
                # fall-through PC; not-taken branches do too in spec
                # terms, but micro-arch-wise the prediction depends on
                # the *encoded* offset's direction). Use the delta if
                # taken, the encoded offset if not — but not_taken
                # branches don't reveal their offset here, so we only
                # bin direction for taken branches.
                if taken:
                    direction = "fwd" if pc > prev_pc else "bwd"
                    _bump(CG_BRANCH_LOOP, f"{direction}_taken")
                else:
                    _bump(CG_BRANCH_LOOP, "fall_through")

            prev_pc = pc
            prev_bin_bytes = bin_bytes
            prev_mnem = mnem
            prev_was_branch = mnem in _BRANCH_MNEMS

            # Privilege-mode transition.
            if mnem in _PRIV_MNEMS:
                _bump(CG_PRIV_MODE, _PRIV_MNEMS[mnem])
                # Leaving trap context — the handler is mret/sret-ing.
                in_trap_region = False

            # Sprint-2: WFI corner — sampled when the wfi mnemonic
            # is retired. Crosses with the current priv level + the TW
            # bit of MSTATUS we last saw (proxy for "did the test exercise
            # the trap-on-WFI semantic for non-M priv?").
            if mnem == "wfi":
                # We don't have priv level on plain trace lines; the
                # commit line alongside the WFI carries it. Approximate
                # by the most recent priv level seen on a commit line.
                tw_bit = mstatus_state.get("tw", 0)
                _bump(CG_WFI_CORNER, f"wfi_tw{int(tw_bit)}")

            # Privileged-event sampling — count each occurrence of mret /
            # sret / sfence / cbo.* / etc. independent of the priv mode
            # transition coverage. Captures "the test exercised feature X"
            # whether or not the trace actually changed mode.
            ev = _PRIV_EVENT_MNEMS.get(mnem)
            if ev:
                _bump(CG_PRIV_EVENT, ev)

    return {
        "lines_parsed": lines_parsed,
        "pc_reach_labels": label_hits,
        "branches_observed": branches,
    }
