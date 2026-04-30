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

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from rvgen.coverage.collectors import CoverageDB


@dataclass(frozen=True, slots=True)
class Goals:
    """Parsed coverage goals: covergroup -> {bin: required-hit-count}."""

    data: dict[str, dict[str, int]]

    def covergroup(self, name: str) -> dict[str, int]:
        return self.data.get(name, {})

    def covergroup_names(self) -> tuple[str, ...]:
        return tuple(self.data)


# ---------------------------------------------------------------------------
# Abstract bin functions — riscv-isac CGF compatibility
# ---------------------------------------------------------------------------
#
# Every abstract pattern collapses to a single canonical value-class bin
# (the ``_value_class`` codomain). SV-style bin-per-bit-position would
# produce ``width`` bins per pattern, leaving each requiring 1 hit —
# which is typically untestable. Mapping to the bin a real ISS sample
# would land in keeps goals achievable.

_ABSTRACT_FN_RE = re.compile(
    r"^(?P<name>walking_ones|walking_zeros|alternating|corners)\((?P<args>[^)]*)\)$"
)


def _resolve_abstract(spec: str) -> tuple[str, ...] | None:
    """If ``spec`` looks like ``walking_ones(32)`` etc., return the bin tuple.

    Returns ``None`` when ``spec`` isn't an abstract function call —
    callers fall back to treating it as a literal bin name.
    """
    from rvgen.coverage.collectors import VALUE_CLASS_BINS
    m = _ABSTRACT_FN_RE.match(spec.strip())
    if not m:
        return None
    expansion: dict[str, tuple[str, ...]] = {
        "walking_ones": ("walking_one",),
        "walking_zeros": ("walking_zero",),
        "alternating": ("alternating",),
        "corners": VALUE_CLASS_BINS,
    }
    return expansion.get(m.group("name"))


def _load_one(path: str | Path) -> dict[str, dict[str, int]]:
    """Parse a single goals YAML file into a normalised dict-of-dict.

    Supports two abstract-function shorthands borrowed from riscv-isac:

    .. code-block:: yaml

        # Per-bin specification (vanilla)
        rs1_val_class_cg:
          zero: 5
          all_ones: 5
          walking_one: 3

        # Whole-cg shorthand expansion: a string value invokes the
        # abstract function expander on the whole covergroup.
        rs1_val_class_cg: "corners()"        # all 10 canonical corner bins
        rd_val_class_cg:  "walking_ones(32)"

        # Per-bin abstract: a single key maps to a function string.
        rs1_val_class_cg:
          "walking_ones(32)": 3   # walking_one bin gets a goal of 3
          generic: 100
    """
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Coverage goals file {path!r} must be a YAML mapping")
    out: dict[str, dict[str, int]] = {}
    for cg, bins in raw.items():
        # Whole-cg string: expand into all bins with default goal 1.
        if isinstance(bins, str):
            expanded = _resolve_abstract(bins)
            if expanded is None:
                raise ValueError(
                    f"Covergroup {cg!r} in {path!r}: string value {bins!r} "
                    f"is not a known abstract function (corners(), "
                    f"walking_ones(N), walking_zeros(N), alternating(N))"
                )
            out[str(cg)] = {b: 1 for b in expanded}
            continue
        if not isinstance(bins, dict):
            raise ValueError(
                f"Covergroup {cg!r} in {path!r} must map to a bin-count dict; got {type(bins).__name__}"
            )
        normalised: dict[str, int] = {}
        for bn, cnt in bins.items():
            # Per-bin abstract: expand to multiple bins with the same count.
            expanded = _resolve_abstract(str(bn))
            try:
                count = int(cnt)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Bin {cg}.{bn} in {path!r} must be an integer; got {cnt!r}"
                ) from e
            if expanded is not None:
                for b in expanded:
                    normalised[b] = count
            else:
                normalised[str(bn)] = count
        out[str(cg)] = normalised
    return out


def load_goals(path: str | Path) -> Goals:
    """Load a CGF-style YAML file."""
    return Goals(data=_load_one(path))


def load_goals_layered(*paths: str | Path) -> Goals:
    """Load multiple goals files and merge them, last-writer wins per bin.

    Merge semantics (valuable for per-target / per-test overlays):

    - If file A sets ``opcode_cg.FENCE: 2`` and B sets ``opcode_cg.FENCE: 0``
      (optional), the final goal is ``0`` — bin is tracked but not required.
    - If A sets ``opcode_cg.ADD: 5`` and B omits it, the final goal stays 5.
    - New covergroups / bins introduced by later files are added to the
      final view.

    Useful layouts::

        # Base rv32imc goals + rv64gcv overlay (adds vector bins):
        --cov_goals baseline.yaml --cov_goals rv64gcv.yaml

        # Test that disables branches explicitly:
        --cov_goals baseline.yaml --cov_goals arithmetic_basic.yaml
    """
    merged: dict[str, dict[str, int]] = {}
    for p in paths:
        src = _load_one(p)
        for cg, bins in src.items():
            merged.setdefault(cg, {}).update(bins)
    return Goals(data=merged)


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
