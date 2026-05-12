"""Tests for the packaged-testlist fallback.

These run unconditionally — they don't require an external riscv-dv
clone (that's the whole point of the fallback). Confirms:

1. ``rvgen/testlists/base_testlist.yaml`` ships inside the package.
2. ``_infer_testlist_path`` falls through to it when neither user-area
   nor riscv-dv has a matching file.
3. The CLI emits a clear error when ``--testlist`` is given but the
   path doesn't exist (no raw FileNotFoundError stack trace).
4. The CLI works end-to-end on the packaged testlist without any
   ``--testlist`` / ``--riscv_dv_root`` flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rvgen.cli import _infer_testlist_path, main


def test_packaged_base_testlist_exists():
    """The package wheel must include base_testlist.yaml."""
    p = Path(__file__).parent.parent.parent / "rvgen" / "testlists" / "base_testlist.yaml"
    assert p.exists(), f"Missing packaged baseline: {p}"


def test_infer_testlist_falls_through_to_packaged(tmp_path):
    """When neither user-area nor riscv-dv has the file, use the package builtin."""
    fake_root = tmp_path / "no-riscv-dv-here"
    resolved = _infer_testlist_path("rv32imc", fake_root)
    assert resolved.name == "base_testlist.yaml"
    assert "rvgen" in str(resolved) and "testlists" in str(resolved)
    assert resolved.exists()


def test_cli_bad_testlist_path_returns_nonzero_with_hint(tmp_path, caplog):
    """A bogus --testlist path produces a clear error, not a stack trace."""
    rc = main([
        "--target", "rv32imc",
        "--test", "riscv_arithmetic_basic_test",
        "--steps", "gen",
        "--output", str(tmp_path),
        "--testlist", "/path/to/nowhere/testlist.yaml",
    ])
    assert rc == 1
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "Testlist YAML not found" in msgs
    # The hint should reference the packaged baseline.
    assert "base_testlist.yaml" in msgs or "omit the flag" in msgs


def test_cli_works_without_external_riscv_dv(tmp_path):
    """End-to-end gen with no --testlist + no riscv-dv on disk.

    Uses --riscv_dv_root pointing at a directory we know doesn't exist
    to simulate a fresh ``pip install rvgen`` machine.
    """
    rc = main([
        "--target", "rv32imc",
        "--test", "riscv_arithmetic_basic_test",
        "--steps", "gen",
        "--output", str(tmp_path),
        "--seed", "42",
        "--riscv_dv_root", str(tmp_path / "no-such-dir"),
    ])
    assert rc == 0
    s_files = list((tmp_path / "asm_test").glob("*.S"))
    assert len(s_files) == 1
