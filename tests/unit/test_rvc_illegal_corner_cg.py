"""Regression for the dead-covergroup bug.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md, finding H7):
``rvc_illegal_corner_cg`` was declared in ``ALL_COVERGROUPS`` and wired
into dashboard subsystem maps, but no ``_bump`` call site existed —
the covergroup shipped as a permanent empty ``{}`` in every
``coverage.json``. Any goal written for it would never close.

The fix wires the covergroup as a REGRESSION WATCHDOG: the bins
correspond to RVC reserved / HINT encodings that
``rvgen/isa/compressed.py`` constrains every random C-format
generation to avoid. The bins should therefore stay at 0 forever
under healthy generation — but if the constraint logic regresses
(someone removes the ``nzimm != 0`` guard, etc.), the watchdog
fires and goals will fail.

These tests pin:
  1. The covergroup is reachable — the sampler in collectors.py knows
     how to bump it.
  2. Under default generation (which honors the spec constraints),
     every watchdog bin stays at 0.
  3. The sampler correctly identifies a reserved encoding when handed
     a synthesized instruction (positive control).
"""

from __future__ import annotations

import random as _rnd

from rvgen.asm_program_gen import AsmProgramGen
from rvgen.config import make_config
from rvgen.coverage import sample_sequence
from rvgen.coverage.collectors import CG_RVC_ILLEGAL, new_db, sample_instr
from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import create_instr_list
from rvgen.targets import get_target


def test_sampler_is_wired_and_reachable():
    """Synthetic positive control: hand the sampler a reserved encoding
    and confirm it bumps the right bin. Catches "I forgot to add the
    bump call" silently re-introducing the bug.
    """
    db = new_db()

    # C.LUI nzimm=0 — RESERVED
    instr = get_instr(RiscvInstrName.C_LUI)
    instr.rd = RiscvReg.S0
    instr.imm = 0
    sample_instr(db, instr)
    assert db[CG_RVC_ILLEGAL].get("c_lui_nzimm_zero", 0) == 1

    # C.SLLI shamt=0 — HINT
    instr = get_instr(RiscvInstrName.C_SLLI)
    instr.rd = RiscvReg.S0
    instr.imm = 0
    sample_instr(db, instr)
    assert db[CG_RVC_ILLEGAL].get("c_slli_shamt_zero", 0) == 1

    # C.ADDI nzimm=0 — HINT
    instr = get_instr(RiscvInstrName.C_ADDI)
    instr.rd = RiscvReg.S0
    instr.imm = 0
    sample_instr(db, instr)
    assert db[CG_RVC_ILLEGAL].get("c_addi_nzimm_zero", 0) == 1


def test_default_generation_does_not_trip_watchdog():
    """End-to-end: generate a long rv32imc test, sample it, and confirm
    every watchdog bin stays at 0. Healthy generation never emits a
    reserved RVC encoding. If this test ever fails, find out which
    randomize_imm path lost its constraint.
    """
    target = get_target("rv32imc")
    cfg = make_config(target, gen_opts="+instr_cnt=1000")
    cfg.seed = 100
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(100))
    gen.gen_program()

    db = new_db()
    for seq in gen.hart_sequences:
        sample_sequence(db, seq.instr_stream.instr_list)

    watchdog_bins = db.get(CG_RVC_ILLEGAL, {})
    # Every bin should be 0 or absent.
    assert not any(v > 0 for v in watchdog_bins.values()), (
        f"H7 regression watchdog tripped: rvc_illegal_corner_cg has "
        f"non-zero bins {watchdog_bins!r}. This means the generator "
        "emitted a reserved/HINT RVC encoding. Inspect "
        "rvgen/isa/compressed.py:randomize_imm — a constraint was lost."
    )


def test_covergroup_is_no_longer_dead_in_all_covergroups():
    """The covergroup must still appear in ALL_COVERGROUPS so dashboard
    + tools can find it. This caught the original ship-with-dead-cg
    mistake — keep it caught."""
    from rvgen.coverage.collectors import ALL_COVERGROUPS
    assert CG_RVC_ILLEGAL in ALL_COVERGROUPS
