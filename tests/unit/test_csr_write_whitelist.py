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
    # Build the stream directly (no CLI) so we don't depend on a
    # riscv-dv testlist resolution path that CI runners don't have.
    import random

    from rvgen.config import make_config
    from rvgen.isa.csr_ops import CsrInstr
    from rvgen.isa.enums import (
        PrivilegedReg,
        RiscvInstrName as N,
        RiscvReg as R,
    )
    from rvgen.isa.filtering import create_instr_list
    from rvgen.stream import RandInstrStream

    cfg = make_config(
        get_target("rv32imc"),
        gen_opts="+include_write_reg=MSCRATCH,MTVEC,MEPC",
    )
    avail = create_instr_list(cfg)
    rng = random.Random(0)
    stream = RandInstrStream(
        cfg=cfg, avail=avail, instr_cnt=4000,
        avail_regs=(R.A0, R.A1, R.A2, R.A3, R.A4, R.A5),
    )
    stream.gen_instr(rng, no_branch=True, no_load_store=True)

    write_ops = (N.CSRRW, N.CSRRWI)
    setclr_ops = (N.CSRRS, N.CSRRC, N.CSRRSI, N.CSRRCI)
    seen_writable = set()
    for ins in stream.instr_list:
        if not isinstance(ins, CsrInstr):
            continue
        if ins.instr_name in write_ops:
            seen_writable.add(ins.csr)
        elif ins.instr_name in setclr_ops:
            # set/clear with non-zero rs1 / imm == effective write.
            non_zero = (
                getattr(ins, "rs1", R.ZERO) != R.ZERO
                if ins.instr_name in (N.CSRRS, N.CSRRC)
                else getattr(ins, "imm", 0) != 0
            )
            if non_zero:
                seen_writable.add(ins.csr)

    # The widened whitelist must let at least one of MTVEC / MEPC through.
    assert (PrivilegedReg.MTVEC.value in seen_writable
            or PrivilegedReg.MEPC.value in seen_writable), (
        "Widened whitelist should produce csrr* writes targeting MTVEC or MEPC"
    )
    # USTATUS / SATP / etc must NOT appear (rv32imc doesn't implement them).
    assert PrivilegedReg.USTATUS.value not in seen_writable
    assert PrivilegedReg.SATP.value not in seen_writable
