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
    CG_BIT_ACTIVITY,
    CG_BR_PER_MNEM,
    CG_BRANCH_DIR,
    CG_CSR_VAL,
    CG_EXCEPTION,
    CG_OPCODE,
    CG_PRIV_EVENT,
    CG_PRIV_MODE,
    CG_RS_VAL_CORNER,
    CoverageDB,
    _value_class,
)


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


def sample_trace_file(db: CoverageDB, trace_path: Path, *, max_lines: int = 2_000_000) -> dict:
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
                # GPR write-values — classify against the canonical corners,
                # and also bump the bit-activity covergroup for each set bit
                # (reveals dead bits — if bit_N_set never appears, no
                # instruction ever computed a value with bit N set).
                for wm in _GPR_WRITE_IN_COMMIT_RE.finditer(writes):
                    reg = wm.group("reg")
                    val = int(wm.group("val"), 16)
                    _bump(CG_RS_VAL_CORNER, _corner_bucket(val))
                    _bump("rd_val_class_cg", _value_class(val, 64))
                    gpr_state[reg] = val
                    # Cap at 64 bits; bin name = "bit_N_set".
                    v = val & 0xFFFF_FFFF_FFFF_FFFF
                    while v:
                        bit = (v & -v).bit_length() - 1  # lowest set bit
                        _bump(CG_BIT_ACTIVITY, f"bit_{bit:02d}_set")
                        v &= v - 1
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

            # Branch direction — the *previous* instruction was the branch;
            # now that we see this PC, we know whether the branch was taken.
            if prev_was_branch and prev_pc is not None and prev_mnem is not None:
                expected_fall_through = prev_pc + prev_bin_bytes
                if pc == expected_fall_through:
                    _bump(CG_BRANCH_DIR, "not_taken")
                    _bump(CG_BR_PER_MNEM, f"{prev_mnem.upper()}__NT")
                    branch_history.append("N")
                else:
                    _bump(CG_BRANCH_DIR, "taken")
                    _bump(CG_BR_PER_MNEM, f"{prev_mnem.upper()}__T")
                    branch_history.append("T")
                branches += 1
                if len(branch_history) >= 3:
                    pattern = "".join(list(branch_history)[-3:])
                    _bump("branch_pattern_cg", pattern)

            prev_pc = pc
            prev_bin_bytes = bin_bytes
            prev_mnem = mnem
            prev_was_branch = mnem in _BRANCH_MNEMS

            # Privilege-mode transition.
            if mnem in _PRIV_MNEMS:
                _bump(CG_PRIV_MODE, _PRIV_MNEMS[mnem])

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
