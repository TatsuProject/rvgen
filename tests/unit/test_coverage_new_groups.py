"""Tests for the new covergroups: modern_ext, fence, lr_sc_pattern, priv_event.

These covergroups complement the 61 existing groups by capturing semantic
events the existing ones miss:

- modern_ext_cg: per-extension cluster (zicond / zicbom / zicboz / zicbop /
  zihintpause / zihintntl / zimop / zcmop) — finer than group_cg.
- fence_cg:       pred/succ encoding pattern (rw__rw, r__rw, io__io, ...).
- lr_sc_pattern_cg: LR/SC pairing at sequence level.
- priv_event_cg:  runtime-parsed privileged events (mret/sret/sfence/satp_write).
"""

from __future__ import annotations

import random

import pytest

from rvgen.coverage.collectors import (
    CG_FENCE,
    CG_LR_SC_PATTERN,
    CG_MODERN_EXT,
    _fence_pat_bin,
    _modern_ext_bin,
    new_db,
    sample_instr,
    sample_sequence,
)
from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.isa.factory import get_instr


# ---------- modern_ext_bin classifier ----------


@pytest.mark.parametrize("name,expected", [
    (RiscvInstrName.CZERO_EQZ, "zicond_czero_eqz"),
    (RiscvInstrName.CZERO_NEZ, "zicond_czero_nez"),
    (RiscvInstrName.CBO_CLEAN, "zicbom_clean"),
    (RiscvInstrName.CBO_FLUSH, "zicbom_flush"),
    (RiscvInstrName.CBO_INVAL, "zicbom_inval"),
    (RiscvInstrName.CBO_ZERO,  "zicboz_zero"),
    (RiscvInstrName.PREFETCH_I, "zicbop_i"),
    (RiscvInstrName.PREFETCH_R, "zicbop_r"),
    (RiscvInstrName.PREFETCH_W, "zicbop_w"),
    (RiscvInstrName.PAUSE, "zihintpause_pause"),
    (RiscvInstrName.NTL_P1, "zihintntl_p1"),
    (RiscvInstrName.NTL_PALL, "zihintntl_pall"),
    (RiscvInstrName.NTL_S1, "zihintntl_s1"),
    (RiscvInstrName.NTL_ALL, "zihintntl_all"),
    (RiscvInstrName.MOP_R_0, "zimop_r_q0"),
    (RiscvInstrName.MOP_R_15, "zimop_r_q1"),
    (RiscvInstrName.MOP_R_31, "zimop_r_q3"),
    (RiscvInstrName.MOP_RR_3, "zimop_rr"),
    (RiscvInstrName.C_MOP_15, "zcmop_any"),
])
def test_modern_ext_classifier(name, expected):
    assert _modern_ext_bin(name) == expected


def test_modern_ext_classifier_returns_none_for_non_modern():
    assert _modern_ext_bin(RiscvInstrName.ADD) is None
    assert _modern_ext_bin(RiscvInstrName.LW) is None


def test_modern_ext_quartiles_cover_all_32_mop_r():
    # Every MOP_R_<N> in [0, 31] maps to one of q0..q3.
    seen_quartiles = set()
    for i in range(32):
        bin_name = _modern_ext_bin(getattr(RiscvInstrName, f"MOP_R_{i}"))
        assert bin_name.startswith("zimop_r_q")
        seen_quartiles.add(bin_name)
    assert seen_quartiles == {f"zimop_r_q{i}" for i in range(4)}


# ---------- fence_pat_bin classifier ----------


@pytest.mark.parametrize("imm,expected", [
    (0xFF, "rwio__rwio"),    # full IO+memory barrier
    (0x33, "rw__rw"),        # GCC default "fence"
    (0x31, "rw__w"),         # release-style
    (0x23, "r__rw"),         # acquire-style
    (0xCC, "io__io"),        # IO-only
    (0x10, "w__0"),          # write before nothing (rare)
])
def test_fence_pat_classifier(imm, expected):
    assert _fence_pat_bin(imm) == expected


# ---------- modern_ext_cg sampling ----------


@pytest.fixture
def db():
    return new_db()


def test_sample_instr_bumps_modern_ext_for_zicond(db):
    inst = get_instr(RiscvInstrName.CZERO_EQZ)
    inst.set_rand_mode()
    inst.rd = RiscvReg.A0
    inst.rs1 = RiscvReg.A1
    inst.rs2 = RiscvReg.A2
    sample_instr(db, inst)
    assert db[CG_MODERN_EXT].get("zicond_czero_eqz") == 1


def test_sample_instr_bumps_modern_ext_for_cbo_zero(db):
    inst = get_instr(RiscvInstrName.CBO_ZERO)
    inst.set_rand_mode()
    inst.rs1 = RiscvReg.A1
    sample_instr(db, inst)
    assert db[CG_MODERN_EXT].get("zicboz_zero") == 1


def test_sample_instr_does_not_bump_modern_ext_for_plain_add(db):
    inst = get_instr(RiscvInstrName.ADD)
    inst.set_rand_mode()
    inst.rd = RiscvReg.A0
    inst.rs1 = RiscvReg.A1
    inst.rs2 = RiscvReg.A2
    sample_instr(db, inst)
    assert db[CG_MODERN_EXT] == {}


# ---------- fence_cg sampling ----------


def test_sample_instr_bumps_fence_cg(db):
    inst = get_instr(RiscvInstrName.FENCE)
    inst.set_rand_mode()
    # No imm set → defaults to GCC "fence rw,rw" = 0xFF.
    sample_instr(db, inst)
    # imm defaults to 0 in the dataclass, but our sampler treats imm==0
    # as the bare "fence" → falls back to rw,rw.
    assert any("rw" in k for k in db[CG_FENCE])


# ---------- lr_sc_pattern_cg sampling ----------


def _mk(name: RiscvInstrName, rng: random.Random):
    inst = get_instr(name)
    inst.set_rand_mode()
    if getattr(inst, "has_rd", False):
        inst.rd = RiscvReg.A0
    if getattr(inst, "has_rs1", False):
        inst.rs1 = RiscvReg.A1
    if getattr(inst, "has_rs2", False):
        inst.rs2 = RiscvReg.A2
    return inst


def test_lr_sc_paired(db):
    rng = random.Random(0)
    seq = [_mk(RiscvInstrName.LR_W, rng), _mk(RiscvInstrName.SC_W, rng)]
    sample_sequence(db, seq)
    assert db[CG_LR_SC_PATTERN].get("paired") == 1


def test_lr_sc_with_intervening_op(db):
    rng = random.Random(0)
    seq = [
        _mk(RiscvInstrName.LR_W, rng),
        _mk(RiscvInstrName.ADD, rng),
        _mk(RiscvInstrName.SC_W, rng),
    ]
    sample_sequence(db, seq)
    assert db[CG_LR_SC_PATTERN].get("lr_with_intervening_op") == 1


def test_lr_only(db):
    rng = random.Random(0)
    seq = [_mk(RiscvInstrName.LR_W, rng), _mk(RiscvInstrName.ADD, rng)]
    sample_sequence(db, seq)
    assert db[CG_LR_SC_PATTERN].get("lr_only") == 1


def test_unpaired_sc(db):
    rng = random.Random(0)
    seq = [_mk(RiscvInstrName.SC_W, rng)]
    sample_sequence(db, seq)
    assert db[CG_LR_SC_PATTERN].get("unpaired_sc") == 1


def test_nested_lr(db):
    rng = random.Random(0)
    seq = [_mk(RiscvInstrName.LR_W, rng), _mk(RiscvInstrName.LR_W, rng)]
    sample_sequence(db, seq)
    assert db[CG_LR_SC_PATTERN].get("nested_lr") == 1


# ---------- priv_event_cg lookup tables ----------


def test_priv_event_csr_table_covers_satp_and_pmpcfg():
    from rvgen.coverage.runtime import _PRIV_EVENT_CSR_WRITES
    assert _PRIV_EVENT_CSR_WRITES["SATP"] == "satp_write"
    assert _PRIV_EVENT_CSR_WRITES["PMPCFG0"] == "pmpcfg_write"
    assert _PRIV_EVENT_CSR_WRITES["DCSR"] == "dcsr_write"


def test_priv_event_mnem_table_covers_mret_sret_sfence():
    from rvgen.coverage.runtime import _PRIV_EVENT_MNEMS
    assert _PRIV_EVENT_MNEMS["mret"] == "mret_taken"
    assert _PRIV_EVENT_MNEMS["sret"] == "sret_taken"
    assert _PRIV_EVENT_MNEMS["sfence.vma"] == "sfence_vma"
    assert _PRIV_EVENT_MNEMS["dret"] == "dret_taken"
