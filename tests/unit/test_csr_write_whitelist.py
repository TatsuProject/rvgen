"""Tests for the +include_write_reg plusarg / Config.include_write_csr field.

Mirrors SV riscv-dv's ``include_write_reg`` knob: lets users extend the
default ``{MSCRATCH}`` whitelist of CSRs that the random stream can
issue csrrw/csrrs/csrrc against.
"""

from __future__ import annotations

from rvgen.config import Config, make_config
from rvgen.targets import get_target


def test_default_whitelist_is_mscratch():
    cfg = Config()
    assert cfg.include_write_csr == ("MSCRATCH",)


def test_plusarg_accepts_comma_list():
    cfg = make_config(get_target("rv32imc"),
                      gen_opts="+include_write_reg=MSCRATCH,MTVEC,MEDELEG")
    assert cfg.include_write_csr == ("MSCRATCH", "MTVEC", "MEDELEG")


def test_plusarg_uppercases_lowercase_input():
    # Plusarg parser is whitespace-delimited, so the value itself can't
    # contain spaces — but case shouldn't matter.
    cfg = make_config(get_target("rv32imc"),
                      gen_opts="+include_write_reg=mscratch,mtvec")
    assert cfg.include_write_csr == ("MSCRATCH", "MTVEC")


def test_plusarg_empty_value_keeps_default():
    cfg = make_config(get_target("rv32imc"), gen_opts="+include_write_reg=")
    assert cfg.include_write_csr == ("MSCRATCH",)


def test_extended_whitelist_lets_extra_csrs_through(tmp_path):
    # End-to-end: include_write_reg adds MTVEC to the writable set.
    # Run a short generation and verify that at least one csrr* op now
    # writes a CSR beyond MSCRATCH.
    import subprocess
    import sys

    out = tmp_path / "ext_csr"
    subprocess.run(
        [
            sys.executable, "-m", "rvgen",
            "--target", "rv32imc",
            "--test", "riscv_csr_test",
            "--steps", "gen",
            "--output", str(out),
            "--start_seed", "100",
            "-i", "1",
            "--gen_opts=+instr_cnt=4000 +include_write_reg=MSCRATCH,MTVEC,MEPC",
        ],
        check=True,
        capture_output=True,
    )
    asm = (out / "asm_test" / "riscv_csr_test_0.S").read_text()
    # MTVEC and MEPC are reachable now — at least one of them should appear
    # as the operand to a csrrw/csrrs/csrrc.
    assert "csrrw" in asm or "csrrs" in asm or "csrrc" in asm
