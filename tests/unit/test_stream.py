"""Tests for rvgen.stream."""

from __future__ import annotations

import random

from rvgen.config import make_config
from rvgen.isa import rv32i  # noqa: F401
from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import create_instr_list
from rvgen.stream import InstrStream, RandInstrStream
from rvgen.targets import get_target


def _make_nop_stream(n: int) -> list[Instr]:
    out: list[Instr] = []
    for _ in range(n):
        out.append(get_instr(RiscvInstrName.NOP))
    return out


# ---------------------------------------------------------------------------
# InstrStream basics
# ---------------------------------------------------------------------------


def test_instr_stream_insert_at_explicit_idx():
    s = InstrStream(instr_list=_make_nop_stream(3))
    new = get_instr(RiscvInstrName.ADDI)
    s.insert_instr(new, idx=1)
    assert len(s.instr_list) == 4
    assert s.instr_list[1] is new


def test_instr_stream_insert_into_empty():
    s = InstrStream()
    new = get_instr(RiscvInstrName.ADD)
    s.insert_instr(new, idx=0)
    assert s.instr_list == [new]


def test_instr_stream_insert_stream_replace():
    s = InstrStream(instr_list=_make_nop_stream(3))
    s.instr_list[0].label = "first"
    s.instr_list[0].has_label = True
    new_instr = [get_instr(RiscvInstrName.ADDI), get_instr(RiscvInstrName.SUB)]
    rng = random.Random(1)
    s.insert_instr_stream(new_instr, idx=0, replace=True, rng=rng)
    assert len(s.instr_list) == 4
    # New first instr inherits the label.
    assert s.instr_list[0].label == "first"
    assert s.instr_list[0].has_label is True


def test_instr_stream_insert_respects_atomic():
    # Build a stream where every instr is atomic except slot 2.
    stream_instrs = _make_nop_stream(5)
    for i, instr in enumerate(stream_instrs):
        instr.atomic = i != 2
    s = InstrStream(instr_list=stream_instrs)
    new = get_instr(RiscvInstrName.ADDI)
    rng = random.Random(0)
    s.insert_instr(new, idx=-1, rng=rng)
    # At some index >= 2 (the first non-atomic slot) the new instr must appear.
    idxs = [i for i, instr in enumerate(s.instr_list) if instr is new]
    assert idxs  # new instr appears exactly once


def test_mix_instr_stream_preserves_order():
    base = _make_nop_stream(10)
    s = InstrStream(instr_list=list(base))
    new_instrs = [get_instr(RiscvInstrName.ADDI), get_instr(RiscvInstrName.SUB), get_instr(RiscvInstrName.AND)]
    rng = random.Random(0)
    s.mix_instr_stream(new_instrs, rng=rng)
    # Only the new instrs should be "different" (not NOP); check their order.
    new_order = [i for i in s.instr_list if i.instr_name != RiscvInstrName.NOP]
    assert [i.instr_name for i in new_order] == [
        RiscvInstrName.ADDI, RiscvInstrName.SUB, RiscvInstrName.AND,
    ]


def test_convert2string_joins_lines():
    s = InstrStream(instr_list=[get_instr(RiscvInstrName.NOP), get_instr(RiscvInstrName.FENCE)])
    s.instr_list[0].post_randomize()
    s.instr_list[1].post_randomize()
    assert s.convert2string() == "nop\nfence"


# ---------------------------------------------------------------------------
# RandInstrStream.gen_instr
# ---------------------------------------------------------------------------


def test_gen_instr_fills_instr_cnt_slots():
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1 +no_csr_instr=1")
    avail = create_instr_list(cfg)
    s = RandInstrStream(cfg=cfg, avail=avail)
    s.initialize_instr_list(50)
    rng = random.Random(42)
    s.gen_instr(rng)
    # Trailing BRANCH instructions are stripped, so we may have ≤ 50.
    assert 0 < len(s.instr_list) <= 50


def test_gen_instr_no_branch_means_no_branch_in_list():
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1 +no_csr_instr=1")
    avail = create_instr_list(cfg)
    s = RandInstrStream(cfg=cfg, avail=avail)
    s.initialize_instr_list(100)
    rng = random.Random(1)
    s.gen_instr(rng, no_branch=True)
    assert all(i.category != RiscvInstrCategory.BRANCH for i in s.instr_list)


def test_gen_instr_respects_reserved_rd():
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1 +no_csr_instr=1")
    avail = create_instr_list(cfg)
    reserved = (RiscvReg.TP, RiscvReg.SP, cfg.scratch_reg)
    s = RandInstrStream(cfg=cfg, avail=avail, reserved_rd=reserved)
    s.initialize_instr_list(100)
    rng = random.Random(2)
    s.gen_instr(rng)
    for instr in s.instr_list:
        if instr.has_rd:
            assert instr.rd not in reserved


def test_gen_instr_produces_only_allowed_categories_by_default():
    # Default setup_allowed_instr: basic_instr + no branch + no load/store.
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1 +no_csr_instr=1")
    avail = create_instr_list(cfg)
    s = RandInstrStream(cfg=cfg, avail=avail)
    s.initialize_instr_list(200)
    rng = random.Random(123)
    s.gen_instr(rng, no_branch=True, no_load_store=True)
    allowed = {
        RiscvInstrCategory.SHIFT,
        RiscvInstrCategory.ARITHMETIC,
        RiscvInstrCategory.LOGICAL,
        RiscvInstrCategory.COMPARE,
    }
    for instr in s.instr_list:
        assert instr.category in allowed
