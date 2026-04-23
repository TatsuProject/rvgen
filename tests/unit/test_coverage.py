"""Unit tests for the functional coverage module."""

from __future__ import annotations

from pathlib import Path

import pytest

from chipforge_inst_gen.coverage import (
    CoverageDB,
    goals_met,
    load_goals,
    merge,
    missing_bins,
    render_report,
    sample_instr,
    sample_sequence,
)
from chipforge_inst_gen.coverage.collectors import (
    ALL_COVERGROUPS,
    CG_CATEGORY,
    CG_FMT_X_CAT,
    CG_FORMAT,
    CG_GROUP,
    CG_HAZARD,
    CG_IMM_SIGN,
    CG_OPCODE,
    CG_RD,
    CG_RS1,
    CG_RS2,
    new_db,
)
from chipforge_inst_gen.isa import rv32i  # noqa: F401  (register ops)
from chipforge_inst_gen.isa.enums import RiscvInstrName, RiscvReg
from chipforge_inst_gen.isa.factory import get_instr


# ---------------------------------------------------------------------------
# new_db + merge
# ---------------------------------------------------------------------------


def test_new_db_has_every_covergroup():
    db = new_db()
    assert set(db.keys()) == set(ALL_COVERGROUPS)
    assert all(isinstance(b, dict) for b in db.values())


def test_merge_adds_bin_counts():
    a: CoverageDB = new_db()
    a[CG_OPCODE] = {"ADD": 3, "SUB": 1}
    b: CoverageDB = new_db()
    b[CG_OPCODE] = {"ADD": 5, "JAL": 2}
    merge(a, b)
    assert a[CG_OPCODE] == {"ADD": 8, "SUB": 1, "JAL": 2}


def test_merge_creates_missing_covergroups():
    dst: CoverageDB = {"opcode_cg": {"ADD": 1}}
    src: CoverageDB = {"custom_cg": {"bin1": 2}}
    merge(dst, src)
    assert dst["custom_cg"] == {"bin1": 2}
    assert dst["opcode_cg"] == {"ADD": 1}


# ---------------------------------------------------------------------------
# sample_instr
# ---------------------------------------------------------------------------


def test_sample_add_instr_bumps_expected_bins():
    db = new_db()
    instr = get_instr(RiscvInstrName.ADD)
    instr.rs1 = RiscvReg.T0
    instr.rs2 = RiscvReg.T1
    instr.rd = RiscvReg.A0
    instr.post_randomize()
    sample_instr(db, instr)

    assert db[CG_OPCODE]["ADD"] == 1
    assert db[CG_FORMAT]["R_FORMAT"] == 1
    assert db[CG_CATEGORY]["ARITHMETIC"] == 1
    assert db[CG_GROUP]["RV32I"] == 1
    assert db[CG_RS1]["T0"] == 1
    assert db[CG_RS2]["T1"] == 1
    assert db[CG_RD]["A0"] == 1
    assert db[CG_FMT_X_CAT]["R_FORMAT__ARITHMETIC"] == 1


def test_sample_addi_imm_sign_positive():
    db = new_db()
    instr = get_instr(RiscvInstrName.ADDI)
    instr.rs1 = RiscvReg.ZERO
    instr.rd = RiscvReg.A0
    instr.imm = 5
    instr.imm_len = 12
    instr.post_randomize()
    sample_instr(db, instr)
    assert db[CG_IMM_SIGN]["pos"] == 1


def test_sample_addi_imm_sign_zero():
    db = new_db()
    instr = get_instr(RiscvInstrName.ADDI)
    instr.rs1 = RiscvReg.ZERO
    instr.rd = RiscvReg.A0
    instr.imm = 0
    instr.imm_len = 12
    instr.post_randomize()
    sample_instr(db, instr)
    assert db[CG_IMM_SIGN]["zero"] == 1


def test_sample_addi_imm_sign_negative():
    db = new_db()
    instr = get_instr(RiscvInstrName.ADDI)
    instr.rs1 = RiscvReg.ZERO
    instr.rd = RiscvReg.A0
    # 12-bit signed: 0x800 has the sign bit set => negative
    instr.imm = 0x800
    instr.imm_len = 12
    instr.post_randomize()
    sample_instr(db, instr)
    assert db[CG_IMM_SIGN]["neg"] == 1


def test_sample_csr_instr():
    from chipforge_inst_gen.isa.enums import PrivilegedReg
    db = new_db()
    instr = get_instr(RiscvInstrName.CSRRW)
    instr.rs1 = RiscvReg.T0
    instr.rd = RiscvReg.A0
    instr.csr = int(PrivilegedReg.MSCRATCH)
    sample_instr(db, instr)
    assert db["csr_cg"]["MSCRATCH"] == 1


# ---------------------------------------------------------------------------
# sample_sequence — hazard detection
# ---------------------------------------------------------------------------


def _make(name: RiscvInstrName, *, rd=None, rs1=None, rs2=None):
    i = get_instr(name)
    if rd is not None:
        i.rd = rd
    if rs1 is not None:
        i.rs1 = rs1
    if rs2 is not None:
        i.rs2 = rs2
    i.post_randomize()
    return i


def test_hazard_raw_detected():
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.T0, rs1=RiscvReg.A0, rs2=RiscvReg.A1),
        _make(RiscvInstrName.ADD, rd=RiscvReg.T1, rs1=RiscvReg.T0, rs2=RiscvReg.A2),  # RAW on T0
    ]
    sample_sequence(db, seq)
    assert db[CG_HAZARD].get("raw", 0) == 1


def test_hazard_waw_detected():
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.T0, rs1=RiscvReg.A0, rs2=RiscvReg.A1),
        _make(RiscvInstrName.SUB, rd=RiscvReg.T0, rs1=RiscvReg.A2, rs2=RiscvReg.A3),  # WAW on T0
    ]
    sample_sequence(db, seq)
    assert db[CG_HAZARD].get("waw", 0) == 1


def test_hazard_war_detected():
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.T1, rs1=RiscvReg.T0, rs2=RiscvReg.A1),  # reads T0
        _make(RiscvInstrName.SUB, rd=RiscvReg.T0, rs1=RiscvReg.A2, rs2=RiscvReg.A3),  # WAR on T0
    ]
    sample_sequence(db, seq)
    assert db[CG_HAZARD].get("war", 0) == 1


def test_hazard_zero_not_counted_as_raw():
    # x0 is a special register; reads of x0 after a "write to x0" shouldn't
    # flag RAW (writes to x0 are no-ops per the spec).
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.ZERO, rs1=RiscvReg.A0, rs2=RiscvReg.A1),
        _make(RiscvInstrName.ADD, rd=RiscvReg.T0, rs1=RiscvReg.ZERO, rs2=RiscvReg.A2),
    ]
    sample_sequence(db, seq)
    assert db[CG_HAZARD].get("raw", 0) == 0
    # First instr has no predecessor, second has an rs1=ZERO read that
    # shouldn't be hazardous — both bump "none".
    assert db[CG_HAZARD].get("none", 0) == 2


def test_hazard_none_when_independent():
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.T0, rs1=RiscvReg.A0, rs2=RiscvReg.A1),
        _make(RiscvInstrName.ADD, rd=RiscvReg.T1, rs1=RiscvReg.A2, rs2=RiscvReg.A3),
        _make(RiscvInstrName.ADD, rd=RiscvReg.T2, rs1=RiscvReg.A4, rs2=RiscvReg.A5),
    ]
    sample_sequence(db, seq)
    assert db[CG_HAZARD].get("none", 0) == 3
    assert db[CG_HAZARD].get("raw", 0) == 0


# ---------------------------------------------------------------------------
# Goals loading + comparison
# ---------------------------------------------------------------------------


def test_load_goals_basic(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text(
        "opcode_cg:\n  ADD: 5\n  SUB: 3\n"
        "hazard_cg:\n  raw: 10\n"
    )
    goals = load_goals(p)
    assert goals.covergroup("opcode_cg") == {"ADD": 5, "SUB": 3}
    assert goals.covergroup("hazard_cg") == {"raw": 10}
    assert goals.covergroup("unknown_cg") == {}


def test_load_goals_baseline_file_parses():
    # Ensure the shipped baseline goals file is well-formed.
    p = Path(__file__).parent.parent.parent / "chipforge_inst_gen" / "coverage" / "goals" / "baseline.yaml"
    goals = load_goals(p)
    assert "opcode_cg" in goals.covergroup_names()
    assert goals.covergroup("opcode_cg")["ADD"] > 0


def test_goals_met_true_when_all_required_hit(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  ADD: 2\n")
    goals = load_goals(p)
    db = new_db()
    db[CG_OPCODE] = {"ADD": 5}
    assert goals_met(db, goals)


def test_goals_met_false_when_required_not_hit(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  ADD: 5\n")
    goals = load_goals(p)
    db = new_db()
    db[CG_OPCODE] = {"ADD": 2}
    assert not goals_met(db, goals)


def test_optional_bin_required_zero_not_blocking(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  ADD: 0\n")
    goals = load_goals(p)
    db = new_db()  # empty
    assert goals_met(db, goals)


def test_missing_bins_reports_shortfalls(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text(
        "opcode_cg:\n  ADD: 5\n  SUB: 5\n  ORI: 0\n"
    )
    goals = load_goals(p)
    db = new_db()
    db[CG_OPCODE] = {"ADD": 2, "SUB": 10}
    miss = missing_bins(db, goals)
    assert miss == {"opcode_cg": {"ADD": (2, 5)}}


# ---------------------------------------------------------------------------
# render_report smoke
# ---------------------------------------------------------------------------


def test_render_report_contains_each_covergroup():
    db = new_db()
    db[CG_OPCODE] = {"ADD": 5}
    report = render_report(db)
    for cg in ALL_COVERGROUPS:
        assert f"[{cg}]" in report


def test_render_report_with_goals_flags_missing(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  ADD: 10\n  SUB: 5\n")
    goals = load_goals(p)
    db = new_db()
    db[CG_OPCODE] = {"ADD": 3}
    report = render_report(db, goals)
    assert "MISSING" in report
    assert "ALL GOALS MET" not in report
    # Both bins should show up as missing (SUB not observed at all).
    assert "ADD" in report
    assert "SUB" in report


def test_render_report_goals_met_banner(tmp_path: Path):
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  ADD: 1\n")
    goals = load_goals(p)
    db = new_db()
    db[CG_OPCODE] = {"ADD": 5}
    report = render_report(db, goals)
    assert "ALL GOALS MET" in report
