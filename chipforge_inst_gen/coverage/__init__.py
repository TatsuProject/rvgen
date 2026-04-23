"""Functional coverage collection for chipforge-inst-gen.

The model is inspired by `riscv-isac <https://riscv-isac.readthedocs.io>`_'s
CGF format (goals) but the observed-DB schema and bin naming are local.
Runtime bins (branch direction, exception taken, CSR/reg values) are
parsed from spike's ``-l --log-commits`` trace via
:func:`sample_trace_file`.

Quick use from Python (e.g. writing a custom verification script)::

    >>> from chipforge_inst_gen.coverage import (
    ...     sample_sequence, load_goals, render_report, goals_met,
    ... )
    >>> from chipforge_inst_gen.coverage.collectors import new_db
    >>> from chipforge_inst_gen.isa import rv32i  # noqa: F401
    >>> from chipforge_inst_gen.isa.factory import get_instr
    >>> from chipforge_inst_gen.isa.enums import RiscvInstrName, RiscvReg
    >>>
    >>> db = new_db()
    >>> instr = get_instr(RiscvInstrName.ADD)
    >>> instr.rs1 = RiscvReg.T0
    >>> instr.rs2 = RiscvReg.T1
    >>> instr.rd = RiscvReg.A0
    >>> instr.post_randomize()
    >>> sample_sequence(db, [instr])
    >>> db["opcode_cg"]["ADD"]
    1

Public API:

- :class:`CoverageDB` — dict-of-dict ``{cg: {bin: count}}``. Serialises
  to JSON trivially.
- :func:`sample_instr(db, instr, *, vector_cfg=None)` — sample one
  instruction.
- :func:`sample_sequence(db, seq, *, vector_cfg=None)` — sample a whole
  emitted sequence with hazard detection over an 8-instruction window.
- :func:`sample_trace_file(db, trace_path)` — ingest a spike ``--log-commits``
  trace for runtime bins.
- :func:`load_goals(path)` / :func:`load_goals_layered(*paths)` — load
  CGF-style YAML goals, optionally layered (last-writer wins per bin).
- :func:`merge(dst, src)` — bin-wise sum two DBs.
- :func:`missing_bins(db, goals)` — return ``{cg: {bin: (observed, required)}}``
  for every required bin below its target.
- :func:`goals_met(db, goals)` — boolean shortcut.
- :func:`render_report(db, goals=None)` — render a text report.
- :func:`compute_grade(db, goals=None)` — composite 0-100 quality grade.
"""

from __future__ import annotations

from chipforge_inst_gen.coverage.collectors import (
    CoverageDB,
    merge,
    sample_instr,
    sample_sequence,
)
from chipforge_inst_gen.coverage.cgf import (
    Goals,
    goals_met,
    load_goals,
    load_goals_layered,
    missing_bins,
)
from chipforge_inst_gen.coverage.report import compute_grade, render_report
from chipforge_inst_gen.coverage.runtime import sample_trace_file


__all__ = [
    "CoverageDB",
    "Goals",
    "compute_grade",
    "goals_met",
    "load_goals",
    "load_goals_layered",
    "merge",
    "missing_bins",
    "render_report",
    "sample_instr",
    "sample_sequence",
    "sample_trace_file",
]
