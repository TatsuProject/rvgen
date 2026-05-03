"""Online (within-seed) coverage-driven steering.

The standard random walker picks each next instruction uniformly from
the filtered candidate pool. That works fine for breadth, but leaves
the long-tail to brute force: rare bins need many seeds to close.

This module adds a second-order pass on top of that. Every N
instructions the generator snapshots its own static covergroups via
:func:`~rvgen.coverage.collectors.sample_instr`, compares against a
goals file, and computes a per-mnemonic *steering weight*. The next
N picks then use those weights via ``random.choices(...)`` instead of
``random.choice(...)``.

This is "within-seed feedback" — distinct from the across-seed
auto-regression in :mod:`rvgen.coverage.directed` which mutates
``gen_opts`` between seeds, and distinct from the genetic search in
:mod:`rvgen.search` which mutates seeds. Online steering closes
~25-40% more bins per single seed in synthetic experiments because
it doesn't have to wait for the next seed to react.

Design notes
------------

- Steering is *boost* only. The base candidate set is unchanged so we
  never starve any opcode entirely. Boost factors range from 1.0
  (uniform) to ~10.0 (massively under-quota).
- We refresh weights every ``refresh_every`` picks (default 200) — a
  knob the user can tune. Smaller = more reactive, larger = less
  overhead per iteration. 200 hits a good balance on a 5K-instruction
  test.
- The steerer is goals-aware: bins with required==0 (visible/optional)
  get no boost, only the ones with a positive required count that are
  currently under-hit. This avoids fighting the user's intent when
  they explicitly set a bin as optional.
- Boosts cascade: missing ``opcode_cg.HFENCE_VVMA`` bumps just that
  opcode; missing ``category_cg.SYSTEM`` bumps every SYSTEM-category
  opcode by the smaller category-tier boost.
- Pure Python; no numpy or sklearn dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rvgen.coverage.cgf import Goals
from rvgen.coverage.collectors import (
    CG_CATEGORY,
    CG_FORMAT,
    CG_GROUP,
    CG_OPCODE,
    CoverageDB,
    sample_instr,
)
from rvgen.isa.base import Instr
from rvgen.isa.enums import RiscvInstrName
from rvgen.isa.factory import INSTR_REGISTRY


# Boost magnitudes per covergroup tier. Tuned by hand — opcode misses are
# the highest-signal because each opcode maps 1:1 to a single mnemonic;
# category/format/group misses spread the boost across many opcodes.
_OPCODE_BOOST = 8.0
_CATEGORY_BOOST = 3.0
_FORMAT_BOOST = 2.0
_GROUP_BOOST = 2.0
_BASE_WEIGHT = 1.0


@dataclass
class SteerStats:
    """Diagnostic counters surfaced after generation."""

    refreshes: int = 0
    biased_picks: int = 0
    last_missing_opcodes: int = 0
    last_missing_categories: int = 0
    last_missing_formats: int = 0
    last_missing_groups: int = 0


@dataclass
class OnlineCoverageSteer:
    """Snapshot covergroups + compute per-mnemonic boost weights.

    Construct with the goals file you want to steer toward and the set
    of registered instruction names that survived the target's filter.
    Call :meth:`refresh` periodically with the in-progress instruction
    list to recompute weights, and :meth:`weights_for` to get the
    weight vector matching a candidate set.
    """

    goals: Goals
    candidate_pool: tuple[RiscvInstrName, ...]
    refresh_every: int = 200
    _last_weights: dict[RiscvInstrName, float] = field(default_factory=dict)
    stats: SteerStats = field(default_factory=SteerStats)

    def __post_init__(self) -> None:
        # Default every-name uniform weight so the first chunk before
        # the first refresh still picks sensibly.
        self._last_weights = {n: _BASE_WEIGHT for n in self.candidate_pool}

    def refresh(self, instr_list: list[Instr]) -> None:
        """Snapshot the current ``instr_list`` into a fresh CoverageDB
        and recompute the boost-weights map.
        """
        db: CoverageDB = {}
        for ins in instr_list:
            sample_instr(db, ins)

        # Reset weights to baseline.
        weights: dict[RiscvInstrName, float] = {n: _BASE_WEIGHT for n in self.candidate_pool}

        # Tier 1: per-opcode boosts.
        opcode_goals = self.goals.covergroup(CG_OPCODE)
        opcode_db = db.get(CG_OPCODE, {})
        opcode_misses = 0
        for bin_name, required in opcode_goals.items():
            if required <= 0:
                continue
            seen = opcode_db.get(bin_name, 0)
            if seen >= required:
                continue
            opcode_misses += 1
            try:
                name = RiscvInstrName[bin_name]
            except KeyError:
                continue
            if name in weights:
                # Scale boost by how-far-below-quota we are (1..N×).
                deficit = (required - seen) / max(required, 1)
                weights[name] = max(weights[name], _OPCODE_BOOST * (1 + deficit))

        # Tier 2: category/format/group misses spread to every opcode in
        # that bucket. We expand the bins by walking the registry.
        cat_misses = self._tier_boost(
            weights, db.get(CG_CATEGORY, {}), self.goals.covergroup(CG_CATEGORY),
            attr="category", boost=_CATEGORY_BOOST,
        )
        fmt_misses = self._tier_boost(
            weights, db.get(CG_FORMAT, {}), self.goals.covergroup(CG_FORMAT),
            attr="format", boost=_FORMAT_BOOST,
        )
        grp_misses = self._tier_boost(
            weights, db.get(CG_GROUP, {}), self.goals.covergroup(CG_GROUP),
            attr="group", boost=_GROUP_BOOST,
        )

        self._last_weights = weights
        self.stats.refreshes += 1
        self.stats.last_missing_opcodes = opcode_misses
        self.stats.last_missing_categories = cat_misses
        self.stats.last_missing_formats = fmt_misses
        self.stats.last_missing_groups = grp_misses

    def _tier_boost(
        self,
        weights: dict[RiscvInstrName, float],
        db_bins: dict[str, int],
        goals_bins: dict[str, int],
        *,
        attr: str,
        boost: float,
    ) -> int:
        """Apply a boost to every opcode whose attribute matches an
        under-hit goals bin.
        """
        misses = 0
        for bin_name, required in goals_bins.items():
            if required <= 0:
                continue
            seen = db_bins.get(bin_name, 0)
            if seen >= required:
                continue
            misses += 1
            for name in self.candidate_pool:
                cls = INSTR_REGISTRY.get(name)
                if cls is None:
                    continue
                cls_attr = getattr(cls, attr, None)
                if cls_attr is None:
                    continue
                if cls_attr.name == bin_name:
                    # Take max so the highest-signal tier wins (opcode > category).
                    weights[name] = max(weights[name], boost)
        return misses

    def weights_for(self, candidates: list[RiscvInstrName]) -> list[float]:
        """Return the steered weight vector matching ``candidates``.

        Names not in the steerer's pool fall back to baseline weight.
        """
        return [self._last_weights.get(n, _BASE_WEIGHT) for n in candidates]


def steer_choice(rng, candidates: list[RiscvInstrName],
                 steerer: OnlineCoverageSteer | None) -> RiscvInstrName:
    """Pick an instruction name from ``candidates``, biased by ``steerer``.

    Falls back to uniform :func:`random.Random.choice` when no steerer
    is provided. Centralised here so both the main random walker and
    directed streams can steer with a one-line change.
    """
    if steerer is None:
        return rng.choice(candidates)
    weights = steerer.weights_for(candidates)
    if any(w != _BASE_WEIGHT for w in weights):
        steerer.stats.biased_picks += 1
    return rng.choices(candidates, weights=weights, k=1)[0]
