"""Regression for the directed-stream knob-leak bugs.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md, findings H3-H5):
five directed streams silently emitted instructions the user had
explicitly forbidden via ``cfg.no_*`` knobs:

  * ``riscv_loop_instr``  → emitted BNE under ``+no_branch_jump=1``
  * ``riscv_jal_instr``   → emitted JAL under ``+no_branch_jump=1``
  * ``riscv_jalr_instr``  → emitted JALR under ``+no_branch_jump=1``
  * ``riscv_hypervisor_instr`` → emitted HFENCE under ``+no_fence=1``
  * ``riscv_vstart_corner_instr_stream`` → emitted CSRWI vstart under ``+no_csr_instr=1``

The fix introduces a class-level ``BANNED_BY`` attribute on
:class:`DirectedInstrStream`. The splicer in ``asm_program_gen.py``
consults it and drops the stream entirely when any listed cfg knob is
truthy. The user's knob wins over the +directed_instr_N plusarg.

These tests pin three things:
  1. The five known-leaky streams declare the correct ``BANNED_BY``.
  2. ``is_banned_by(cfg)`` returns the knob name when set, else None.
  3. End-to-end: the splicer actually drops the stream and the
     generated ``.S`` contains zero of the forbidden mnemonics.
"""

from __future__ import annotations

import random as _rnd

import pytest

from rvgen.asm_program_gen import AsmProgramGen
from rvgen.config import make_config
from rvgen.isa.filtering import create_instr_list
from rvgen.streams import get_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.targets import get_target


# -------- (1) Declared BANNED_BY on the five known-leaky streams ----------

@pytest.mark.parametrize("stream_name,expected_banned_by", [
    ("riscv_loop_instr",                   ("no_branch_jump",)),
    ("riscv_jal_instr",                    ("no_branch_jump",)),
    ("riscv_jalr_instr",                   ("no_branch_jump",)),
    # Hypervisor stream mixes HFENCE (fence) with HLV/HSV (load/store);
    # extended to list both knobs as part of the M9 closure cycle.
    ("riscv_hypervisor_instr",             ("no_fence", "no_load_store")),
    ("riscv_vstart_corner_instr_stream",   ("no_csr_instr",)),
])
def test_known_leaky_streams_declare_banned_by(stream_name, expected_banned_by):
    cls = get_stream(stream_name)
    assert cls.BANNED_BY == expected_banned_by, (
        f"H3-H5 regression: {stream_name} BANNED_BY = {cls.BANNED_BY!r}, "
        f"expected {expected_banned_by!r}. Did someone remove the declaration?"
    )


def test_base_class_default_banned_by_is_empty():
    """The default — a stream with no class-level override is unconstrained."""
    assert DirectedInstrStream.BANNED_BY == ()


# -------- (2) is_banned_by() returns the active knob, or None -------------

def test_is_banned_by_returns_knob_when_set():
    target = get_target("rv32imc")
    cfg = make_config(target, gen_opts="+no_branch_jump=1")
    cls = get_stream("riscv_loop_instr")
    assert cls.is_banned_by(cfg) == "no_branch_jump"


def test_is_banned_by_returns_none_when_knob_clear():
    target = get_target("rv32imc")
    cfg = make_config(target, gen_opts="")
    cls = get_stream("riscv_loop_instr")
    assert cls.is_banned_by(cfg) is None


# -------- (3) End-to-end: forbidden mnemonics absent from .S --------------

def _gen_S_lines(target_name: str, gen_opts: str) -> list[str]:
    """Drive the generator end-to-end and return the emitted .S lines."""
    target = get_target(target_name)
    cfg = make_config(target, gen_opts=gen_opts)
    cfg.seed = 42
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(42))
    return gen.gen_program()


def _count_directed_blocks(lines: list[str], stream_name: str) -> int:
    """Count occurrences of ``start <stream_name>`` comments — one per atom.

    The base ``finalize()`` writes the comment as ``"Start <name>"`` but
    ``Instr.convert2asm`` lowercases the entire asm line on emit
    (rvgen/isa/base.py:322), so the .S contains ``#start <name>``.
    """
    needle = f"start {stream_name}".lower()
    return sum(1 for L in lines if needle in L.lower())


def test_e2e_no_branch_jump_drops_loop_jal_jalr_streams():
    lines = _gen_S_lines(
        "rv32imc",
        "+no_branch_jump=1 "
        "+directed_instr_1=riscv_loop_instr,5 "
        "+directed_instr_2=riscv_jal_instr,3 "
        "+directed_instr_3=riscv_jalr_instr,2",
    )
    assert _count_directed_blocks(lines, "riscv_loop_instr") == 0
    assert _count_directed_blocks(lines, "riscv_jal_instr") == 0
    assert _count_directed_blocks(lines, "riscv_jalr_instr") == 0


def test_e2e_no_csr_instr_drops_vstart_corner_stream():
    lines = _gen_S_lines(
        "rv64gcv",
        "+no_csr_instr=1 "
        "+directed_instr_1=riscv_vstart_corner_instr_stream,2",
    )
    assert _count_directed_blocks(
        lines, "riscv_vstart_corner_instr_stream",
    ) == 0
    # And no raw csrwi vstart sneaks in from the stream:
    assert not any("csrwi" in L and "vstart" in L for L in lines)


def test_e2e_no_fence_drops_hypervisor_stream():
    # Hypervisor stream needs RV64H — use rv64gch.
    lines = _gen_S_lines(
        "rv64gch",
        "+no_fence=1 +directed_instr_1=riscv_hypervisor_instr,3",
    )
    assert _count_directed_blocks(lines, "riscv_hypervisor_instr") == 0
    assert not any("hfence" in L for L in lines)


def test_e2e_streams_still_emit_when_knob_clear():
    """Sanity: with no_branch_jump CLEAR, the stream is allowed and emits."""
    lines = _gen_S_lines(
        "rv32imc",
        "+directed_instr_1=riscv_loop_instr,3",
    )
    assert _count_directed_blocks(lines, "riscv_loop_instr") >= 1
