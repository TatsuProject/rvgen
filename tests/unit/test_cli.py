"""End-to-end CLI smoke test.

The Phase 1 step 3 done-criterion is:
    python -m rvgen --target rv32imc --test riscv_arithmetic_basic_test
        --iterations 2 --steps gen
must emit two ``.S`` files (content can be crude for now).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rvgen.cli import main


_RISCV_DV_ROOT = Path.home() / "Desktop" / "verif_env_tatsu" / "riscv-dv"


pytestmark = pytest.mark.skipif(
    not _RISCV_DV_ROOT.exists(),
    reason="riscv-dv root not available at " + str(_RISCV_DV_ROOT),
)


def test_cli_fixed_seed_generates_single_s_file(tmp_path):
    # --seed is incompatible with --iterations > 1 (run.py semantics).
    # Don't pass --iterations; default to 1 from the testlist, fixed seed.
    rc = main([
        "--target", "rv32imc",
        "--test", "riscv_arithmetic_basic_test",
        "--steps", "gen",
        "--output", str(tmp_path),
        "--riscv_dv_root", str(_RISCV_DV_ROOT),
        "--seed", "42",
    ])
    assert rc == 0
    s_files = list((tmp_path / "asm_test").glob("*.S"))
    assert len(s_files) == 1


def test_cli_fixed_seed_rejects_iterations_gt_1(tmp_path):
    # Explicitly pass iterations=2 with --seed: must fail.
    rc = main([
        "--target", "rv32imc",
        "--test", "riscv_arithmetic_basic_test",
        "--iterations", "2",
        "--steps", "gen",
        "--output", str(tmp_path),
        "--riscv_dv_root", str(_RISCV_DV_ROOT),
        "--seed", "42",
    ])
    assert rc == 1


def test_cli_two_iterations_without_fixed_seed(tmp_path):
    rc = main([
        "--target", "rv32imc",
        "--test", "riscv_arithmetic_basic_test",
        "--iterations", "2",
        "--steps", "gen",
        "--output", str(tmp_path),
        "--riscv_dv_root", str(_RISCV_DV_ROOT),
        "--start_seed", "100",
    ])
    assert rc == 0
    s_files = sorted((tmp_path / "asm_test").glob("*.S"))
    assert len(s_files) == 2
    assert s_files[0].name == "riscv_arithmetic_basic_test_0.S"
    assert s_files[1].name == "riscv_arithmetic_basic_test_1.S"


def test_cli_writes_seed_yaml(tmp_path):
    rc = main([
        "--target", "rv32imc",
        "--test", "riscv_arithmetic_basic_test",
        "--iterations", "2",
        "--steps", "gen",
        "--output", str(tmp_path),
        "--riscv_dv_root", str(_RISCV_DV_ROOT),
        "--start_seed", "7",
    ])
    assert rc == 0
    seed_yaml = tmp_path / "seed.yaml"
    assert seed_yaml.exists()
    import yaml
    data = yaml.safe_load(seed_yaml.read_text())
    assert data["riscv_arithmetic_basic_test_0"] == 7
    assert data["riscv_arithmetic_basic_test_1"] == 8


def test_cli_rejects_unknown_target(tmp_path):
    with pytest.raises(SystemExit):
        main([
            "--target", "rv_fictional",
            "--output", str(tmp_path),
        ])


def test_cli_no_matching_test_returns_nonzero(tmp_path):
    rc = main([
        "--target", "rv32imc",
        "--test", "nonexistent_test",
        "--output", str(tmp_path),
        "--riscv_dv_root", str(_RISCV_DV_ROOT),
    ])
    assert rc == 1
