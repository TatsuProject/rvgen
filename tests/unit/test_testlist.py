"""Tests for rvgen.testlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from rvgen.testlist import TestEntry, load_testlist


# Fixture: real riscv-dv testlists.
_RISCV_DV_ROOT = Path.home() / "Desktop" / "verif_env_tatsu" / "riscv-dv"


pytestmark = pytest.mark.skipif(
    not _RISCV_DV_ROOT.exists(),
    reason="riscv-dv root not available at " + str(_RISCV_DV_ROOT),
)


def test_load_base_testlist():
    base = _RISCV_DV_ROOT / "yaml" / "base_testlist.yaml"
    entries = load_testlist(base, riscv_dv_root=_RISCV_DV_ROOT)
    names = {e.test for e in entries}
    # All 14 core tests from research/01 must appear.
    for required in (
        "riscv_arithmetic_basic_test",
        "riscv_rand_instr_test",
        "riscv_jump_stress_test",
        "riscv_loop_test",
        "riscv_rand_jump_test",
        "riscv_mmu_stress_test",
        "riscv_no_fence_test",
        "riscv_illegal_instr_test",
        "riscv_ebreak_test",
        "riscv_ebreak_debug_mode_test",
        "riscv_full_interrupt_test",
        "riscv_unaligned_load_store_test",
        "riscv_amo_test",
    ):
        assert required in names


def test_rv32imc_imports_base():
    path = _RISCV_DV_ROOT / "target" / "rv32imc" / "testlist.yaml"
    entries = load_testlist(path, riscv_dv_root=_RISCV_DV_ROOT)
    names = {e.test for e in entries}
    # Both the target-specific (riscv_non_compressed_instr_test, ...) and the
    # imported base entries (riscv_arithmetic_basic_test) must appear.
    assert "riscv_non_compressed_instr_test" in names
    assert "riscv_arithmetic_basic_test" in names


def test_filter_by_name():
    path = _RISCV_DV_ROOT / "target" / "rv32imc" / "testlist.yaml"
    entries = load_testlist(
        path,
        riscv_dv_root=_RISCV_DV_ROOT,
        test_filter="riscv_arithmetic_basic_test",
    )
    assert {e.test for e in entries} == {"riscv_arithmetic_basic_test"}


def test_iteration_override():
    path = _RISCV_DV_ROOT / "target" / "rv32imc" / "testlist.yaml"
    entries = load_testlist(
        path,
        riscv_dv_root=_RISCV_DV_ROOT,
        test_filter="riscv_arithmetic_basic_test",
        iteration_override=5,
    )
    assert all(e.iterations == 5 for e in entries)


def test_gen_opts_preserved():
    path = _RISCV_DV_ROOT / "target" / "rv32imc" / "testlist.yaml"
    entries = load_testlist(
        path,
        riscv_dv_root=_RISCV_DV_ROOT,
        test_filter="riscv_arithmetic_basic_test",
    )
    assert entries
    assert "+instr_cnt=5000" in entries[0].gen_opts
    assert "+boot_mode=m" in entries[0].gen_opts


def test_missing_testlist_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_testlist(
            tmp_path / "does_not_exist.yaml",
            riscv_dv_root=_RISCV_DV_ROOT,
        )


def test_zero_iterations_filtered(tmp_path):
    # Entries with iterations == 0 should be dropped.
    yml = tmp_path / "t.yaml"
    yml.write_text(
        "- test: foo\n"
        "  iterations: 0\n"
        "- test: bar\n"
        "  iterations: 1\n"
    )
    entries = load_testlist(yml, riscv_dv_root=_RISCV_DV_ROOT)
    assert {e.test for e in entries} == {"bar"}
