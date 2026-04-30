"""Tests for the genetic-algorithm seed search."""

from __future__ import annotations

import random

import pytest

from rvgen.search import (
    Chromosome,
    SearchResult,
    crossover,
    evaluate_population,
    genetic_search,
    make_default_eval_fn,
    mutate,
    select,
)


# ---------- Chromosome dataclass ----------


def test_chromosome_default_fitness_is_zero():
    c = Chromosome(seed=1)
    assert c.fitness == 0.0
    assert c.closed_bins == frozenset()


def test_chromosome_explicit_gen_opts():
    c = Chromosome(seed=42, gen_opts="+no_fence=1")
    assert c.gen_opts == "+no_fence=1"


# ---------- mutate ----------


def test_mutate_changes_seed():
    rng = random.Random(0)
    parent = Chromosome(seed=100)
    child = mutate(parent, rng)
    # Seed should be perturbed.
    assert child.seed != parent.seed


def test_mutate_keeps_seed_positive():
    rng = random.Random(1)
    parent = Chromosome(seed=1)
    for _ in range(20):
        child = mutate(parent, rng)
        assert child.seed >= 1


def test_mutate_can_introduce_directed_stream():
    rng = random.Random(2)
    parent = Chromosome(seed=100, gen_opts="")
    # Run mutate many times — at least once should add a stream.
    has_stream = False
    for _ in range(20):
        child = mutate(parent, rng)
        if "+directed_instr_" in child.gen_opts:
            has_stream = True
            break
    assert has_stream


# ---------- crossover ----------


def test_crossover_picks_one_parents_seed_perturbed():
    rng = random.Random(0)
    a = Chromosome(seed=100, gen_opts="+no_fence=1")
    b = Chromosome(seed=200, gen_opts="+vec_fp=0")
    c = crossover(a, b, rng)
    # Seed is parent_seed ± 3.
    assert abs(c.seed - 100) <= 3 or abs(c.seed - 200) <= 3


def test_crossover_merges_plusargs_from_both_parents():
    rng = random.Random(0)
    a = Chromosome(seed=100, gen_opts="+no_fence=1")
    b = Chromosome(seed=200, gen_opts="+vec_fp=0")
    c = crossover(a, b, rng)
    # Both plusargs should be present (one from each parent).
    assert "+no_fence=1" in c.gen_opts
    assert "+vec_fp=0" in c.gen_opts


def test_crossover_picks_one_value_when_both_parents_set_same_arg():
    rng = random.Random(0)
    a = Chromosome(seed=100, gen_opts="+no_fence=1")
    b = Chromosome(seed=200, gen_opts="+no_fence=0")
    c = crossover(a, b, rng)
    # Only one no_fence setting in the result.
    assert c.gen_opts.count("+no_fence=") == 1


# ---------- evaluate_population ----------


def test_evaluate_population_assigns_marginal_fitness():
    pop = [Chromosome(seed=i) for i in range(3)]

    # Each candidate sees the same 3-bin DB. Only the *first* gets all
    # 3 marked as new; the rest see merged bins as duplicates.
    def fake_eval(chromo):
        return {"opcode_cg": {"ADD": 1, "SUB": 1, "MUL": 1}}

    merged = evaluate_population(pop, fake_eval, merged_db={})

    assert pop[0].fitness == 3
    # Subsequent chromosomes contribute nothing new.
    assert pop[1].fitness == 0
    assert pop[2].fitness == 0


def test_evaluate_population_credits_unique_bins():
    pop = [Chromosome(seed=1), Chromosome(seed=2), Chromosome(seed=3)]
    pre_canned = [
        {"opcode_cg": {"ADD": 1}},
        {"opcode_cg": {"SUB": 1}},
        {"opcode_cg": {"ADD": 1, "MUL": 1}},
    ]
    iter_canned = iter(pre_canned)

    def fake_eval(chromo):
        return next(iter_canned)

    evaluate_population(pop, fake_eval, merged_db={})

    assert pop[0].fitness == 1   # ADD is new
    assert pop[1].fitness == 1   # SUB is new
    assert pop[2].fitness == 1   # MUL is new (ADD already merged)


# ---------- select ----------


def test_select_keeps_elites_unconditionally():
    pop = [
        Chromosome(seed=1, fitness=10),
        Chromosome(seed=2, fitness=5),
        Chromosome(seed=3, fitness=1),
    ]
    rng = random.Random(0)
    chosen = select(pop, keep_n=2, rng=rng, elitism=1)
    # Top fitness must survive.
    assert chosen[0].fitness == 10


def test_select_returns_keep_n_chromosomes():
    pop = [Chromosome(seed=i, fitness=float(i)) for i in range(8)]
    rng = random.Random(0)
    chosen = select(pop, keep_n=4, rng=rng, elitism=2)
    assert len(chosen) == 4


# ---------- genetic_search ----------


def test_genetic_search_runs_generations(tmp_path):
    """Basic GA loop with a synthetic eval_fn — every chromosome
    contributes a unique bin so fitness should be positive somewhere."""

    def fake_eval(chromo):
        # Each unique seed contributes one new bin.
        return {"opcode_cg": {f"BIN_{chromo.seed}": 1}}

    result = genetic_search(
        eval_fn=fake_eval,
        population_size=4,
        generations=3,
        seed_base=10,
        rng=random.Random(0),
    )

    assert isinstance(result, SearchResult)
    # First-generation chromosomes always close at least one bin
    # (their unique seed → unique bin).
    assert any(c.fitness > 0 for c in result.top_chromosomes)


def test_genetic_search_calls_progress_callback():
    progress_calls = []

    def fake_eval(chromo):
        return {"opcode_cg": {f"BIN_{chromo.seed}": 1}}

    def on_progress(gen, pop):
        progress_calls.append((gen, len(pop)))

    genetic_search(
        eval_fn=fake_eval,
        population_size=3,
        generations=2,
        seed_base=10,
        on_progress=on_progress,
        rng=random.Random(0),
    )
    # One callback per generation.
    assert len(progress_calls) == 2
    assert progress_calls[0][0] == 0
    assert progress_calls[1][0] == 1


def test_genetic_search_plateau_early_exit():
    # Fake eval always returns empty → fitness stays 0 → search bails.
    def fake_eval(chromo):
        return {}

    progress_count = {"n": 0}

    def on_progress(gen, pop):
        progress_count["n"] += 1

    genetic_search(
        eval_fn=fake_eval,
        population_size=4,
        generations=10,
        seed_base=10,
        on_progress=on_progress,
        rng=random.Random(0),
    )
    # Should bail well before 10 generations because nothing is new.
    assert progress_count["n"] < 5


def test_genetic_search_returns_top_sorted_by_fitness():
    pre_canned = iter([
        {"a": {"x": 1}},
        {"b": {"x": 1}},
        {"c": {"x": 1}},
        {"d": {"x": 1}},
    ])

    def fake_eval(chromo):
        return next(pre_canned, {})

    result = genetic_search(
        eval_fn=fake_eval,
        population_size=4,
        generations=1,
        seed_base=10,
        rng=random.Random(0),
    )
    fitnesses = [c.fitness for c in result.top_chromosomes]
    assert fitnesses == sorted(fitnesses, reverse=True)


# ---------- end-to-end with default eval_fn ----------


def test_default_eval_fn_returns_coverage_db():
    fn = make_default_eval_fn(target="rv32imc", main_program_instr_cnt=200)
    db = fn(Chromosome(seed=42))
    # Every covergroup name from the rvgen ALL_COVERGROUPS list should be
    # present (possibly with empty bins).
    from rvgen.coverage.collectors import ALL_COVERGROUPS
    assert set(db.keys()) >= set(ALL_COVERGROUPS)


def test_default_eval_fn_produces_more_bins_with_richer_target():
    fn_imc = make_default_eval_fn(target="rv32imc", main_program_instr_cnt=200)
    fn_imafdc = make_default_eval_fn(target="rv32imafdc", main_program_instr_cnt=200)

    bins_imc = sum(len(b) for b in fn_imc(Chromosome(seed=42)).values())
    bins_imafdc = sum(len(b) for b in fn_imafdc(Chromosome(seed=42)).values())

    # Richer target should hit more bins (FP, more registers, etc.).
    assert bins_imafdc >= bins_imc
