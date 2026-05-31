"""Regression for the multi-hart coverage attribution bug.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md, finding H1):
``AsmProgramGen.gen_program()`` runs a per-hart loop and assigns
``self.main_sequence = InstrSequence(...)`` inside the loop body. The
attribute is overwritten on every iteration, so after the loop only the
LAST hart's sequence is reachable. The cov step in ``rvgen/cli.py`` then
samples only that one — multi-hart targets silently drop N-1 harts'
worth of coverage bins.

These tests pin the fix:
  * ``AsmProgramGen`` exposes ``hart_sequences`` — a list of one entry per
    hart, in hart order.
  * For ``num_of_harts > 1``, every hart's sequence is non-empty.
  * The combined coverage across all hart_sequences strictly exceeds the
    coverage of any single hart's sequence (so the cov step has more
    bins to sample than it did before the fix).
"""

from __future__ import annotations

import random as _rnd

from rvgen.asm_program_gen import AsmProgramGen
from rvgen.config import make_config
from rvgen.coverage import sample_sequence
from rvgen.coverage.collectors import CG_OPCODE, new_db
from rvgen.isa.filtering import create_instr_list
from rvgen.targets import get_target


def _build_gen(num_harts: int) -> AsmProgramGen:
    target = get_target("rv32imc")
    cfg = make_config(target, gen_opts="+instr_cnt=200")
    cfg.num_of_harts = num_harts
    cfg.seed = 100
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(100))
    gen.gen_program()
    return gen


def test_hart_sequences_has_one_entry_per_hart():
    gen = _build_gen(num_harts=3)
    assert len(gen.hart_sequences) == 3
    # Sequences are in hart order (sampler iterates this).
    for seq in gen.hart_sequences:
        assert seq is not None
        assert seq.instr_stream is not None
        assert len(seq.instr_stream.instr_list) > 0


def test_main_sequence_still_points_to_last_hart_for_back_compat():
    gen = _build_gen(num_harts=3)
    # Existing callers that only knew about main_sequence get the
    # last hart's sequence — same behavior as before the fix.
    assert gen.main_sequence is gen.hart_sequences[-1]


def test_combined_hart_coverage_exceeds_single_hart_coverage():
    """The whole point of the fix: sampling every hart_sequence yields
    strictly more opcode_cg total samples than sampling just one hart's.

    Before the fix the cov step in cli.py only saw gen.main_sequence —
    i.e. the last hart. With 3 harts of ~200 instructions each, the
    combined total should be ~3× the single-hart total.
    """
    gen = _build_gen(num_harts=3)

    # Sampling only the last hart (the pre-fix behavior):
    single_db = new_db()
    sample_sequence(
        single_db, gen.hart_sequences[-1].instr_stream.instr_list,
    )
    single_total = sum(single_db[CG_OPCODE].values())

    # Sampling every hart (the post-fix behavior the cov step now does):
    combined_db = new_db()
    for seq in gen.hart_sequences:
        sample_sequence(combined_db, seq.instr_stream.instr_list)
    combined_total = sum(combined_db[CG_OPCODE].values())

    assert single_total > 0
    # With N harts we expect roughly N× more samples. Use 1.5× as a
    # robust floor (tolerates small per-hart count variations).
    assert combined_total > single_total * 1.5, (
        f"multi-hart cov attribution regression: combined_total={combined_total} "
        f"vs single_total={single_total} (expected >1.5× because 3 harts)"
    )


def test_single_hart_target_still_has_one_entry():
    """num_of_harts=1 — hart_sequences has exactly one entry and it's
    the same object as main_sequence."""
    gen = _build_gen(num_harts=1)
    assert len(gen.hart_sequences) == 1
    assert gen.hart_sequences[0] is gen.main_sequence
