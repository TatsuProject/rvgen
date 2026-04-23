"""Tests for rvgen.seeding."""

from __future__ import annotations

import pytest

from rvgen.seeding import SeedGen


def test_fixed_seed_returns_same_value():
    g = SeedGen(fixed_seed=1234)
    assert g.get("riscv_arith_0", 0) == 1234
    # Raises on iteration > 0 (run.py semantics: --seed implies iterations=1).
    with pytest.raises(ValueError, match="incompatible with --iterations"):
        g.get("riscv_arith_1", 1)


def test_start_seed_increments():
    g = SeedGen(start_seed=1000)
    assert g.get("a", 0) == 1000
    assert g.get("a", 3) == 1003


def test_random_default_is_nonempty_and_31bit():
    g = SeedGen()
    s = g.get("a", 0)
    assert 0 <= s < (1 << 31)


def test_random_varies_across_calls():
    g = SeedGen()
    seeds = {g.get("a", i) for i in range(10)}
    # 10 random 31-bit draws should almost certainly all differ.
    assert len(seeds) == 10


def test_mutually_exclusive_config():
    with pytest.raises(ValueError):
        SeedGen(fixed_seed=1, start_seed=2)
    with pytest.raises(ValueError):
        SeedGen(start_seed=1, rerun_seeds={"x": 1})


def test_rerun_from_yaml(tmp_path):
    yml = tmp_path / "seed.yaml"
    yml.write_text("riscv_arith_0: 42\nriscv_arith_1: 43\n")
    g = SeedGen.from_yaml(yml)
    assert g.get("riscv_arith_0") == 42
    assert g.get("riscv_arith_1") == 43


def test_rerun_miss_raises(tmp_path):
    yml = tmp_path / "seed.yaml"
    yml.write_text("riscv_arith_0: 42\n")
    g = SeedGen.from_yaml(yml)
    with pytest.raises(KeyError):
        g.get("riscv_arith_1")


def test_dump_roundtrips(tmp_path):
    g = SeedGen(start_seed=100)
    observed = {"t_0": g.get("t_0", 0), "t_1": g.get("t_1", 1)}
    out = tmp_path / "seed.yaml"
    g.dump(out, observed)
    # Load the file back and check the content.
    g2 = SeedGen.from_yaml(out)
    assert g2.get("t_0") == 100
    assert g2.get("t_1") == 101
