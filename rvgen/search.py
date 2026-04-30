"""Adversarial seed search — genetic-algorithm-style coverage hunting.

The existing ``--auto_regress --cov_directed`` path uses a static
perturbation table to close known-missing bins one seed at a time.
That works well for the 10-15 obvious bins each test typically misses
on seed 1. But for the long tail (rare value-class corners, deep
hazard sequences, 3-gram branch patterns) the perturbation table
runs out of moves and the regression plateaus.

This module replaces the linear seed sweep with a genetic-algorithm
search over (seed, gen_opts) tuples. It treats each candidate as a
chromosome: the seed determines the random_instr stream layout, and
the gen_opts string toggles macro-level features (no_fence, vec_fp,
directed-stream injection counts). Fitness = how many bins this
candidate closes that the rest of the population didn't.

Selection + mutation iterates the population toward seeds that close
*new* coverage relative to the running merged DB, not just any
coverage. After N generations the search returns the top-K seeds
ranked by marginal-bins-closed.

This complements two other rvgen features:

* ``--auto_regress --cov_directed``  — fast, static, deterministic.
  Use this first to pick the obvious closures.
* ``rvgen.minimize``                 — minimizes a single failing test.
  Use that *after* the GA finds a hard-to-hit corner that breaks DUT.
* ``rvgen.search`` (this module)     — slow, stochastic, exploratory.
  Use this when --auto_regress plateaus and you want to dig the
  long-tail of missing bins.

Pure Python; no numpy / scipy dependency.
"""

from __future__ import annotations

import logging
import random
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chromosome — one (seed, gen_opts) candidate.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Chromosome:
    """One search candidate.

    Attributes
    ----------
    seed : int
        Seed for the random_instr stream.
    gen_opts : str
        Plusarg string applied on top of the testlist's gen_opts.
    fitness : float
        Number of marginal bins this candidate closed (set by
        :func:`evaluate_population`). Higher is better.
    closed_bins : frozenset[tuple[str, str]]
        Set of (covergroup, bin_name) tuples this candidate hit
        that the running merged DB hadn't seen.
    """

    seed: int
    gen_opts: str = ""
    fitness: float = 0.0
    closed_bins: frozenset[tuple[str, str]] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Mutation operators — alter a chromosome's gen_opts to explore the space.
# ---------------------------------------------------------------------------


# Each operator is (toggle, value_pool) — the GA picks one, applies it.
# Operators that take values pick uniformly from the pool.
_GEN_OPT_OPERATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("+no_fence", ("0", "1")),
    ("+no_csr_instr", ("0", "1")),
    ("+no_branch_jump", ("0", "1")),
    ("+no_ebreak", ("0", "1")),
    ("+no_ecall", ("0", "1")),
    ("+no_wfi", ("0", "1")),
    ("+vec_fp", ("0", "1")),
    ("+vec_narrowing_widening", ("0", "1")),
    ("+enable_pmp_setup", ("0", "1")),
    ("+enable_zvlsseg", ("0", "1")),
)

# Directed-stream injectors — each adds one stream at a random idx
# with a random count. Mutation picks 0..2 of these per chromosome.
_STREAM_INJECTORS: tuple[str, ...] = (
    "riscv_jal_instr",
    "riscv_jalr_instr",
    "riscv_loop_instr",
    "riscv_load_store_rand_instr_stream",
    "riscv_load_store_hazard_instr_stream",
    "riscv_load_store_stress_instr_stream",
    "riscv_multi_page_load_store_instr_stream",
    "riscv_load_store_shared_mem_stream",
    "riscv_lr_sc_instr_stream",
    "riscv_amo_instr_stream",
    "riscv_int_numeric_corner_stream",
    "riscv_hazard_instr_stream",
    "riscv_vector_load_store_instr_stream",
    "riscv_vector_amo_instr_stream",
    "riscv_vector_hazard_instr_stream",
    "riscv_vsetvli_stress_instr_stream",
    "riscv_vstart_corner_instr_stream",
)


def mutate(chromo: Chromosome, rng: random.Random) -> Chromosome:
    """Return a new chromosome with one or two gen_opts changes.

    Strategy:

    1. With prob 0.5, flip a random toggle in :data:`_GEN_OPT_OPERATORS`.
    2. With prob 0.5, append a new directed-stream injection.
    3. Always nudge the seed by ±[1..7] so two mutations of the same
       parent yield distinct random-instr layouts.
    """
    new_opts = chromo.gen_opts
    # Toggle flip.
    if rng.random() < 0.5:
        plusarg, pool = rng.choice(_GEN_OPT_OPERATORS)
        new_value = rng.choice(pool)
        # Replace existing setting if present, else append.
        prefix = plusarg + "="
        parts = [p for p in new_opts.split() if not p.startswith(prefix)]
        parts.append(f"{plusarg}={new_value}")
        new_opts = " ".join(parts)
    # Stream injection.
    if rng.random() < 0.5:
        stream = rng.choice(_STREAM_INJECTORS)
        idx = rng.randint(30, 60)   # high indexes to avoid clobbering testlist's
        count = rng.randint(2, 8)
        new_opts = (new_opts + f" +directed_instr_{idx}={stream},{count}").strip()
    # Seed perturbation.
    delta = rng.randint(-7, 7) or 1
    return Chromosome(
        seed=max(1, chromo.seed + delta),
        gen_opts=new_opts,
    )


def crossover(a: Chromosome, b: Chromosome, rng: random.Random) -> Chromosome:
    """Combine two chromosomes' gen_opts and pick one parent's seed.

    For each plusarg present in either parent, pick the value from a
    randomly-chosen parent. Seed is inherited from one parent (50/50)
    perturbed by ±[1..3] so the child is distinct.
    """
    a_parts = a.gen_opts.split()
    b_parts = b.gen_opts.split()
    a_dict = _parts_to_dict(a_parts)
    b_dict = _parts_to_dict(b_parts)

    merged: dict[str, str] = {}
    keys = set(a_dict) | set(b_dict)
    for key in keys:
        if key in a_dict and key in b_dict:
            merged[key] = a_dict[key] if rng.random() < 0.5 else b_dict[key]
        elif key in a_dict:
            merged[key] = a_dict[key]
        else:
            merged[key] = b_dict[key]

    new_opts = " ".join(f"{k}={v}" for k, v in sorted(merged.items()))
    parent_seed = a.seed if rng.random() < 0.5 else b.seed
    nudge = rng.randint(-3, 3) or 1
    return Chromosome(seed=max(1, parent_seed + nudge), gen_opts=new_opts)


def _parts_to_dict(parts: list[str]) -> dict[str, str]:
    """Parse ``+a=b +c=d`` plusarg strings into a dict."""
    out: dict[str, str] = {}
    for p in parts:
        if p.startswith("+") and "=" in p:
            k, v = p.split("=", 1)
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Fitness — how many marginal bins did each candidate close?
# ---------------------------------------------------------------------------


def evaluate_population(
    population: list[Chromosome],
    eval_fn: Callable[[Chromosome], dict],
    merged_db: dict | None = None,
) -> dict:
    """Run ``eval_fn`` on each chromosome, score by marginal-bin contribution.

    ``eval_fn(chromo)`` returns a CoverageDB dict (covergroup ->
    bin -> hit count). We compute fitness as the number of (cg, bin)
    pairs the chromosome hit that aren't already in ``merged_db``.

    Returns the updated ``merged_db`` after merging in every
    chromosome's coverage. Each chromosome's ``fitness`` and
    ``closed_bins`` fields are mutated in place.
    """
    merged_db = merged_db or {}

    for chromo in population:
        per_chromo_db = eval_fn(chromo)
        # Compute marginal contribution.
        new_bins: set[tuple[str, str]] = set()
        for cg, bins in per_chromo_db.items():
            existing = merged_db.get(cg, {})
            for bin_name in bins:
                if bin_name not in existing:
                    new_bins.add((cg, bin_name))
        chromo.closed_bins = frozenset(new_bins)
        chromo.fitness = float(len(new_bins))
        # Merge into the running DB so subsequent chromosomes are
        # scored against the cumulative state.
        for cg, bins in per_chromo_db.items():
            slot = merged_db.setdefault(cg, {})
            for bin_name, count in bins.items():
                slot[bin_name] = slot.get(bin_name, 0) + count

    return merged_db


# ---------------------------------------------------------------------------
# Selection — keep the top-K, optionally with elitism.
# ---------------------------------------------------------------------------


def select(
    population: list[Chromosome],
    keep_n: int,
    rng: random.Random,
    elitism: int = 1,
) -> list[Chromosome]:
    """Return the top-``keep_n`` chromosomes.

    ``elitism`` chromosomes from the top of the fitness ranking are
    retained unconditionally. The remaining ``keep_n - elitism`` slots
    are filled by tournament selection (random pairs, fitter wins) so
    the search doesn't collapse onto a single high-fitness lineage too
    quickly.
    """
    population.sort(key=lambda c: -c.fitness)
    elites = population[:elitism]
    rest_pool = population[elitism:]
    chosen: list[Chromosome] = list(elites)

    while len(chosen) < keep_n and rest_pool:
        # Tournament size 2.
        a = rng.choice(rest_pool)
        b = rng.choice(rest_pool)
        winner = a if a.fitness >= b.fitness else b
        chosen.append(winner)
        rest_pool.remove(winner)

    return chosen


# ---------------------------------------------------------------------------
# Top-level GA driver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SearchResult:
    """Ranked-by-fitness final population + accumulated coverage."""

    top_chromosomes: list[Chromosome]
    merged_db: dict


def genetic_search(
    *,
    eval_fn: Callable[[Chromosome], dict],
    population_size: int = 8,
    generations: int = 6,
    elitism: int = 2,
    seed_base: int = 100,
    initial_gen_opts: str = "",
    on_progress: Callable[[int, list[Chromosome]], None] | None = None,
    rng: random.Random | None = None,
) -> SearchResult:
    """Run the GA. Returns top chromosomes + accumulated coverage DB.

    Each generation:
    1. Score the population with ``evaluate_population``.
    2. Optionally call ``on_progress(generation, population)``.
    3. Select the top ``population_size // 2``.
    4. Refill the population by crossover + mutation.
    """
    rng = rng or random.Random(seed_base)

    population: list[Chromosome] = [
        Chromosome(seed=seed_base + i, gen_opts=initial_gen_opts)
        for i in range(population_size)
    ]

    merged_db: dict = {}

    for gen in range(generations):
        merged_db = evaluate_population(population, eval_fn, merged_db)
        if on_progress is not None:
            on_progress(gen, sorted(population, key=lambda c: -c.fitness))

        # Stop early if no chromosome closed any new bins for two
        # generations in a row — search has plateaued.
        if all(c.fitness == 0 for c in population) and gen > 0:
            _LOG.info("genetic_search: plateau hit at generation %d", gen)
            break

        if gen == generations - 1:
            break

        # Select.
        keep = max(elitism + 1, population_size // 2)
        survivors = select(population, keep_n=keep, rng=rng, elitism=elitism)

        # Reproduce.
        new_pop: list[Chromosome] = list(survivors)
        while len(new_pop) < population_size:
            if len(survivors) >= 2 and rng.random() < 0.5:
                a = rng.choice(survivors)
                b = rng.choice(survivors)
                child = crossover(a, b, rng)
            else:
                parent = rng.choice(survivors)
                child = mutate(parent, rng)
            new_pop.append(child)

        population = new_pop

    population.sort(key=lambda c: -c.fitness)
    return SearchResult(top_chromosomes=population, merged_db=merged_db)


# ---------------------------------------------------------------------------
# Real-world eval_fn that drives the rvgen pipeline.
# ---------------------------------------------------------------------------


def make_default_eval_fn(
    *,
    target: str,
    test: str = "riscv_rand_instr_test",
    main_program_instr_cnt: int = 500,
) -> Callable[[Chromosome], dict]:
    """Return an eval_fn that runs gen-only on each chromosome.

    Each evaluation:
    1. Builds an rvgen Generator with the chromosome's seed + gen_opts.
    2. Runs generate() — produces lines + main_sequence.
    3. Samples coverage from main_sequence.
    4. Returns the per-chromosome CoverageDB.

    No gcc / spike — this keeps the search loop fast (~50ms / candidate).
    Coverage is static-only; runtime bins (priv_event, exception, etc.)
    won't move. For runtime-coverage-driven search the caller can
    write a custom eval_fn that runs spike + parses the trace.
    """
    from rvgen.api import Generator
    from rvgen.asm_program_gen import AsmProgramGen
    from rvgen.config import make_config
    from rvgen.coverage.collectors import new_db, sample_sequence
    from rvgen.isa.filtering import create_instr_list
    from rvgen.targets import get_target

    target_cfg = get_target(target)

    def _eval(chromo: Chromosome) -> dict:
        cfg = make_config(target_cfg, gen_opts=chromo.gen_opts)
        cfg.seed = chromo.seed
        cfg.main_program_instr_cnt = main_program_instr_cnt

        avail = create_instr_list(cfg)
        rng_local = random.Random(chromo.seed)
        gen = AsmProgramGen(cfg=cfg, avail=avail, rng=rng_local)
        gen.gen_program()

        db = new_db()
        if gen.main_sequence is not None and gen.main_sequence.instr_stream is not None:
            sample_sequence(
                db,
                gen.main_sequence.instr_stream.instr_list,
                vector_cfg=cfg.vector_cfg,
            )
        return db

    return _eval
