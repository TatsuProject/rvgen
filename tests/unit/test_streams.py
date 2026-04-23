"""Smoke tests for the directed-stream registry and each stream's output shape."""

from __future__ import annotations

import random

from rvgen.config import make_config
from rvgen.isa.enums import RiscvInstrCategory, RiscvInstrName, RiscvReg
from rvgen.isa.filtering import create_instr_list
from rvgen.streams import STREAM_REGISTRY, get_stream
from rvgen.targets import get_target


def _ctx(target="rv32imc"):
    cfg = make_config(get_target(target))
    avail = create_instr_list(cfg)
    return cfg, avail, random.Random(42)


def test_streams_registered():
    for required in (
        "riscv_int_numeric_corner_stream",
        "riscv_jal_instr",
        "riscv_loop_instr",
        "riscv_load_store_rand_instr_stream",
        "riscv_lr_sc_instr_stream",
        "riscv_amo_instr_stream",
    ):
        assert required in STREAM_REGISTRY, f"{required!r} missing"


def test_int_numeric_corner_stream_produces_lis_then_arith():
    cfg, avail, rng = _ctx()
    cls = get_stream("riscv_int_numeric_corner_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test")
    stream.generate()
    # First 10 should be LI pseudo (or similar init ops).
    from rvgen.streams.directed import _LiPseudo
    assert sum(1 for i in stream.instr_list if isinstance(i, _LiPseudo)) >= 5
    # Plus some arithmetic/logical/compare/shift body.
    body_count = sum(
        1 for i in stream.instr_list
        if not isinstance(i, _LiPseudo) and i.category in (
            RiscvInstrCategory.ARITHMETIC, RiscvInstrCategory.LOGICAL,
            RiscvInstrCategory.COMPARE, RiscvInstrCategory.SHIFT,
        )
    )
    assert body_count >= 15


def test_jal_instr_chain_atomic():
    cfg, avail, rng = _ctx()
    cls = get_stream("riscv_jal_instr")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_jal", num_of_jump_instr=15)
    stream.generate()
    # Layout: jump_start + N jal body + end-sentinel ADDI (2 extra instrs).
    assert len(stream.instr_list) == 15 + 2
    # All JALs except the trailing sentinel.
    jal_count = sum(1 for i in stream.instr_list if i.instr_name == RiscvInstrName.JAL)
    assert jal_count == 15 + 1  # body JALs + jump_start
    assert stream.instr_list[-1].instr_name == RiscvInstrName.ADDI
    # All atomic (DirectedInstrStream tags them on generate()).
    assert all(i.atomic for i in stream.instr_list)
    # All have unique labels.
    labels = [i.label for i in stream.instr_list]
    assert len(set(labels)) == 15 + 2


def test_loop_instr_has_counter_and_branch():
    cfg, avail, rng = _ctx()
    cls = get_stream("riscv_loop_instr")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_loop",
                 label="test_loop_0", num_of_instr_in_loop=5)
    stream.generate()
    # First instr initializes counter (addi rd, zero, N).
    first = stream.instr_list[0]
    assert first.instr_name == RiscvInstrName.ADDI
    assert first.rs1 == RiscvReg.ZERO
    # Last instr is BNE with symbol-based target.
    last = stream.instr_list[-1]
    assert last.instr_name == RiscvInstrName.BNE
    assert last.branch_assigned is True
    assert last.imm_str.endswith("_target")


def test_lr_sc_stream_has_la_lr_sc():
    cfg, avail, rng = _ctx("rv32ia")
    cls = get_stream("riscv_lr_sc_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_lrsc")
    stream.generate()
    names = [i.instr_name for i in stream.instr_list]
    assert RiscvInstrName.LR_W in names
    assert RiscvInstrName.SC_W in names


def test_amo_stream_contains_amos():
    cfg, avail, rng = _ctx("rv32ia")
    cls = get_stream("riscv_amo_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_amo", num_amo=5)
    stream.generate()
    from rvgen.isa.amo import AmoInstr
    amo_count = sum(1 for i in stream.instr_list if isinstance(i, AmoInstr))
    assert amo_count == 5
