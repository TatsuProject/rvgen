"""Regression for the cache_conflict_cg dead-goals bug.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md, finding H6):
baseline.yaml declared ``way_pressure_5..way_pressure_8`` on
``cache_conflict_cg``, but under the stream's default geometry
(``cache_ways=4, extra_per_set=2``) total pressure tops out at 6 and
anything ``> ways`` is collapsed into ``eviction`` by the sampler — so
``way_pressure_5..8`` could never increment. Declaring them as goals
created permanently-failing regressions.

This test pins the rule: every ``cache_conflict_cg`` goal bin must be
reachable by the stream's sampler with shipped defaults.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rvgen.streams.load_store import CacheConflictInstrStream


def _goals_for(cg_name: str) -> dict[str, int]:
    p = Path(__file__).parent.parent.parent / "rvgen/coverage/goals/baseline.yaml"
    data = yaml.safe_load(p.read_text())
    return data.get(cg_name, {})


def test_cache_conflict_goals_only_contain_reachable_bins():
    goals = _goals_for("cache_conflict_cg")
    assert goals, "cache_conflict_cg missing from baseline.yaml"

    # Reachable bin namespace under default stream geometry:
    #   way_pressure_1..cache_ways  +  eviction
    # (anything > cache_ways collapses into ``eviction`` per
    #  rvgen/coverage/collectors.py:1828.)
    default_ways = CacheConflictInstrStream.__dataclass_fields__["cache_ways"].default
    reachable = {f"way_pressure_{i}" for i in range(1, default_ways + 1)}
    reachable.add("eviction")
    # set_<N> is also reachable (one per cache set) — allow any.

    unreachable_goals = {
        b for b in goals
        if not b.startswith("set_") and b not in reachable
    }
    assert not unreachable_goals, (
        f"cache_conflict_cg goals reference bins the sampler can never "
        f"produce under default cache_ways={default_ways}: "
        f"{sorted(unreachable_goals)}. "
        "Either drop the dead bins or raise the stream's default cache_ways."
    )
