"""Functional coverage collection for chipforge-inst-gen.

Public API:

- :class:`CoverageDB` — holds observed bin hit-counts across covergroups.
- :func:`sample_instr(db, instr)` — sample one :class:`Instr` into the DB.
- :func:`sample_sequence(db, seq)` — sample a whole emitted sequence,
  including hazard detection over adjacent instruction pairs.
- :func:`load_goals(path)` — load a CGF-style YAML file listing required
  hit counts per covergroup.
- :func:`merge(dst, src)` — merge one CoverageDB into another.
- :func:`render_report(db, goals)` — render a human-readable summary string.
- :func:`goals_met(db, goals)` — boolean: have all required counts been hit.

The model is inspired by `riscv-isac <https://riscv-isac.readthedocs.io>`_'s
CGF format (goals) but the observed-DB and bin naming are our own, chosen
to match what the Python generator can sample statically without a parsed
ISS log. Runtime-sourced bins (exception causes, PC histograms, actual
branch direction) are Phase-2 work.
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
    missing_bins,
)
from chipforge_inst_gen.coverage.report import render_report
from chipforge_inst_gen.coverage.runtime import sample_trace_file


__all__ = [
    "CoverageDB",
    "Goals",
    "goals_met",
    "load_goals",
    "merge",
    "missing_bins",
    "render_report",
    "sample_instr",
    "sample_sequence",
    "sample_trace_file",
]
