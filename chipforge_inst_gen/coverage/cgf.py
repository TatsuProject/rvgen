"""Coverage goals (CGF-style) — loader, comparison, helpers.

The goals file is a YAML dictionary of ``covergroup_name -> {bin_name: required_hit_count}``.
A minimal example::

    opcode_cg:
      ADD: 5
      SUB: 5
      BEQ: 10
    hazard_cg:
      raw: 50
      war: 50
      waw: 50
    format_cg:
      R_FORMAT: 100
      I_FORMAT: 100

Syntax compatible-enough with riscv-isac's CGF that a future bridge can
import/export between the two; we don't claim full compatibility (we don't
model ``config``, ``val_comb``, ``abstract_comb`` — those need runtime ISS
state we don't collect yet).

Design decision: goals with ``required == 0`` are *optional* bins — they
appear in reports but don't block "goals met" status. This gives the user a
cheap way to track a metric without mandating it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from chipforge_inst_gen.coverage.collectors import CoverageDB


@dataclass(frozen=True, slots=True)
class Goals:
    """Parsed coverage goals: covergroup -> {bin: required-hit-count}."""

    data: dict[str, dict[str, int]]

    def covergroup(self, name: str) -> dict[str, int]:
        return self.data.get(name, {})

    def covergroup_names(self) -> tuple[str, ...]:
        return tuple(self.data)


def load_goals(path: str | Path) -> Goals:
    """Load a CGF-style YAML file. Ignores unknown keys silently."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Coverage goals file {path!r} must be a YAML mapping")
    # Normalise: every value must be a dict[str, int]
    out: dict[str, dict[str, int]] = {}
    for cg, bins in raw.items():
        if not isinstance(bins, dict):
            raise ValueError(
                f"Covergroup {cg!r} in {path!r} must map to a bin-count dict; got {type(bins).__name__}"
            )
        normalised: dict[str, int] = {}
        for bn, cnt in bins.items():
            try:
                normalised[str(bn)] = int(cnt)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Bin {cg}.{bn} in {path!r} must be an integer; got {cnt!r}"
                ) from e
        out[str(cg)] = normalised
    return Goals(data=out)


def missing_bins(db: CoverageDB, goals: Goals) -> dict[str, dict[str, tuple[int, int]]]:
    """Return bins whose observed count is below the required count.

    Returns a nested dict ``{covergroup: {bin: (observed, required)}}``.
    Bins with ``required == 0`` are treated as optional and never flagged.
    """
    result: dict[str, dict[str, tuple[int, int]]] = {}
    for cg, bins in goals.data.items():
        db_bins = db.get(cg, {})
        shortfall: dict[str, tuple[int, int]] = {}
        for bn, required in bins.items():
            if required <= 0:
                continue
            observed = db_bins.get(bn, 0)
            if observed < required:
                shortfall[bn] = (observed, required)
        if shortfall:
            result[cg] = shortfall
    return result


def goals_met(db: CoverageDB, goals: Goals) -> bool:
    """Return True iff every required bin (count > 0) in ``goals`` is at
    least ``required`` in ``db``.
    """
    return not missing_bins(db, goals)
