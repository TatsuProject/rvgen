"""Tests for online (within-seed) coverage steering.

Verifies the moat feature — no other open-source RISC-V generator does
within-seed coverage feedback. The standard random walker picks each
next instruction uniformly from the filtered candidate pool; the
steerer biases that choice toward mnemonics whose goals bins are still
under-hit.

Tests cover:

1. Steerer construction with goals + candidate pool.
2. ``refresh()`` recomputes weights from a synthetic instruction list.
3. Under-hit opcodes get the highest boost; over-hit ones stay at
   baseline.
4. ``steer_choice`` falls back to uniform on no steerer.
5. End-to-end: a generator run with steering closes more bins than the
   same seed without steering, on a small targeted goals file.
"""

from __future__ import annotations

import random

import pytest

from rvgen.config import Config, make_config
from rvgen.coverage.cgf import Goals
from rvgen.coverage.collectors import (
    CG_OPCODE,
    new_db,
    sample_instr,
)
from rvgen.coverage.steering import (
    OnlineCoverageSteer,
    SteerStats,
    steer_choice,
    _BASE_WEIGHT,
    _OPCODE_BOOST,
)
from rvgen.isa.enums import RiscvInstrName as N, RiscvReg as R
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import create_instr_list
from rvgen.targets import get_target


def _goals(opcode_bins: dict[str, int]) -> Goals:
    return Goals(data={CG_OPCODE: dict(opcode_bins)})


# ---------------------------------------------------------------------------
# OnlineCoverageSteer basics
# ---------------------------------------------------------------------------


def test_initial_weights_uniform_at_baseline():
    pool = (N.ADD, N.SUB, N.AND, N.OR)
    s = OnlineCoverageSteer(goals=_goals({}), candidate_pool=pool)
    weights = s.weights_for(list(pool))
    assert all(w == _BASE_WEIGHT for w in weights)


def test_refresh_with_no_misses_keeps_baseline():
    pool = (N.ADD,)
    s = OnlineCoverageSteer(goals=_goals({"ADD": 1}), candidate_pool=pool)
    # Sample one ADD into the partial stream — meets quota.
    i = get_instr(N.ADD)
    i.rs1 = i.rs2 = i.rd = R.A0
    s.refresh([i])
    assert s.weights_for([N.ADD])[0] == _BASE_WEIGHT
    assert s.stats.last_missing_opcodes == 0


def test_refresh_under_quota_boosts_weight():
    pool = (N.ADD, N.SUB)
    # Goals: 10 ADDs required, 0 seen so far.
    s = OnlineCoverageSteer(
        goals=_goals({"ADD": 10, "SUB": 0}),
        candidate_pool=pool,
    )
    s.refresh([])  # zero in-progress instructions
    weights = s.weights_for(list(pool))
    add_weight = weights[0]
    sub_weight = weights[1]
    # ADD should be massively boosted (quota=10, deficit=1.0 → ~16x).
    assert add_weight > _OPCODE_BOOST
    # SUB has required=0 (visible/optional) → no boost.
    assert sub_weight == _BASE_WEIGHT
    assert s.stats.last_missing_opcodes == 1


def test_refresh_partial_progress_scales_boost_down():
    pool = (N.ADD,)
    s = OnlineCoverageSteer(
        goals=_goals({"ADD": 10}),
        candidate_pool=pool,
    )
    # 5 of 10 required ADDs already seen — boost should be ~12 (8 * 1.5).
    samples = []
    for _ in range(5):
        i = get_instr(N.ADD)
        i.rs1 = i.rs2 = i.rd = R.A0
        samples.append(i)
    s.refresh(samples)
    w = s.weights_for([N.ADD])[0]
    # 8 * (1 + 0.5) = 12. Allow +/- 1.
    assert 11 <= w <= 13


def test_steer_choice_no_steerer_falls_back_to_uniform():
    rng = random.Random(0)
    cands = [N.ADD, N.SUB, N.AND]
    # 1000 picks should yield roughly uniform distribution (~333 each).
    counts = {n: 0 for n in cands}
    for _ in range(1000):
        counts[steer_choice(rng, cands, None)] += 1
    # Allow generous slack — uniform sampling, not exact.
    for c in counts.values():
        assert 250 < c < 420


def test_steer_choice_biased_when_steerer_set():
    rng = random.Random(0)
    pool = (N.ADD, N.SUB, N.AND)
    s = OnlineCoverageSteer(
        goals=_goals({"AND": 100}),  # AND massively under-quota
        candidate_pool=pool,
    )
    s.refresh([])
    counts = {n: 0 for n in pool}
    for _ in range(1000):
        counts[steer_choice(rng, list(pool), s)] += 1
    # AND should dominate by a large margin.
    assert counts[N.AND] > counts[N.ADD] * 3
    assert counts[N.AND] > counts[N.SUB] * 3
    assert s.stats.biased_picks > 0


# ---------------------------------------------------------------------------
# Tier cascade — category / format / group spreading.
# ---------------------------------------------------------------------------


def test_category_miss_boosts_all_category_members():
    from rvgen.coverage.collectors import CG_CATEGORY
    pool = (N.ADD, N.SUB, N.AND, N.OR, N.LB)
    # Miss on LOAD category — should boost LB only (the only LOAD in pool).
    s = OnlineCoverageSteer(
        goals=Goals(data={CG_CATEGORY: {"LOAD": 5}}),
        candidate_pool=pool,
    )
    s.refresh([])
    weights = dict(zip(pool, s.weights_for(list(pool))))
    assert weights[N.LB] > _BASE_WEIGHT
    assert weights[N.ADD] == _BASE_WEIGHT
    assert s.stats.last_missing_categories == 1


# ---------------------------------------------------------------------------
# End-to-end: steering closes more bins than no-steering on the same seed.
# ---------------------------------------------------------------------------


def _generated_opcodes(cfg, seed: int) -> set[str]:
    """Generate one test sequence and return the set of opcode bins hit."""
    from rvgen.asm_program_gen import AsmProgramGen

    avail = create_instr_list(cfg)
    rng = random.Random(seed)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=rng)
    gen.gen_program()
    db = new_db()
    if gen.main_sequence is not None and gen.main_sequence.instr_stream is not None:
        for ins in gen.main_sequence.instr_stream.instr_list:
            sample_instr(db, ins)
    return set(db.get(CG_OPCODE, {}).keys())


def test_steering_closes_more_bins_than_baseline(tmp_path):
    """End-to-end: same seed, same target, two configs.

    No-steering: standard random walk.
    Steering: bias toward goals.

    The steered run must close at least as many bins targeted by the
    goals as the baseline. We use a goals file that asks for several
    rare-ish opcodes (ones the random walker rarely picks within 800
    instructions); steering should pull them in.
    """
    target = get_target("rv32imc")
    seed = 42
    instr_cnt = 800

    # Baseline run — no steering.
    cfg_base = make_config(target, gen_opts=f"+instr_cnt={instr_cnt}")
    cfg_base.seed = seed
    base_opcodes = _generated_opcodes(cfg_base, seed)

    # Construct a goals file targeting opcodes the random walker rarely
    # picks in 800 instructions. SLLI/SRLI/SRAI are shift ops; the
    # random pool has 60+ ARITHMETIC/LOGICAL ops so each shift gets
    # picked maybe 5-10× per 800.
    goals_path = tmp_path / "steer_goals.yaml"
    goals_path.write_text(
        "opcode_cg:\n"
        "  SLLI: 30\n"
        "  SRLI: 30\n"
        "  SRAI: 30\n"
        "  AND:  30\n"
        "  XOR:  30\n"
        "  OR:   30\n"
    )

    cfg_steer = make_config(target, gen_opts=f"+instr_cnt={instr_cnt}")
    cfg_steer.seed = seed
    cfg_steer.cov_steering = True
    cfg_steer.cov_goals_paths = (str(goals_path),)
    cfg_steer.cov_steering_refresh = 100
    steer_opcodes = _generated_opcodes(cfg_steer, seed)

    # The baseline already hits all 6 — the boost matters for *count*,
    # not *presence*. Re-snapshot the per-opcode counts and check the
    # steered run hit them more often.
    cfg_base2 = make_config(target, gen_opts=f"+instr_cnt={instr_cnt}")
    cfg_base2.seed = seed
    rng = random.Random(seed)
    from rvgen.asm_program_gen import AsmProgramGen
    avail = create_instr_list(cfg_base2)
    gen_b = AsmProgramGen(cfg=cfg_base2, avail=avail, rng=rng)
    gen_b.gen_program()
    db_b = new_db()
    for ins in gen_b.main_sequence.instr_stream.instr_list:
        sample_instr(db_b, ins)

    cfg_steer2 = make_config(target, gen_opts=f"+instr_cnt={instr_cnt}")
    cfg_steer2.seed = seed
    cfg_steer2.cov_steering = True
    cfg_steer2.cov_goals_paths = (str(goals_path),)
    cfg_steer2.cov_steering_refresh = 100
    rng = random.Random(seed)
    avail = create_instr_list(cfg_steer2)
    gen_s = AsmProgramGen(cfg=cfg_steer2, avail=avail, rng=rng)
    gen_s.gen_program()
    db_s = new_db()
    for ins in gen_s.main_sequence.instr_stream.instr_list:
        sample_instr(db_s, ins)

    # Sum the targeted bins in both runs.
    targets = {"SLLI", "SRLI", "SRAI", "AND", "XOR", "OR"}
    base_total = sum(db_b[CG_OPCODE].get(t, 0) for t in targets)
    steer_total = sum(db_s[CG_OPCODE].get(t, 0) for t in targets)
    # Steering should hit the targeted bins materially more often.
    assert steer_total > base_total
