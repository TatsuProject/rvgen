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
    CG_EXCEPTION,
    CG_OPCODE,
    CG_PRIV_MODE,
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
