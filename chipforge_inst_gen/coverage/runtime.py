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
from pathlib import Path

from chipforge_inst_gen.coverage.collectors import (
    CG_BR_PER_MNEM,
    CG_BRANCH_DIR,
    CG_CSR_VAL,
    CG_EXCEPTION,
    CG_OPCODE,
    CG_PRIV_MODE,
    CG_RS_VAL_CORNER,
    CoverageDB,
)


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
                # GPR write-values — classify against the canonical corners.
                for wm in _GPR_WRITE_IN_COMMIT_RE.finditer(writes):
                    val = int(wm.group("val"), 16)
                    _bump(CG_RS_VAL_CORNER, _corner_bucket(val))
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

            # Branch direction — the *previous* instruction was the branch;
            # now that we see this PC, we know whether the branch was taken.
            if prev_was_branch and prev_pc is not None and prev_mnem is not None:
                expected_fall_through = prev_pc + prev_bin_bytes
                if pc == expected_fall_through:
                    _bump(CG_BRANCH_DIR, "not_taken")
                    _bump(CG_BR_PER_MNEM, f"{prev_mnem.upper()}__NT")
                else:
                    _bump(CG_BRANCH_DIR, "taken")
                    _bump(CG_BR_PER_MNEM, f"{prev_mnem.upper()}__T")
                branches += 1

            prev_pc = pc
            prev_bin_bytes = bin_bytes
            prev_mnem = mnem
            prev_was_branch = mnem in _BRANCH_MNEMS

            # Privilege-mode transition.
            if mnem in _PRIV_MNEMS:
                _bump(CG_PRIV_MODE, _PRIV_MNEMS[mnem])

    return {
        "lines_parsed": lines_parsed,
        "pc_reach_labels": label_hits,
        "branches_observed": branches,
    }
