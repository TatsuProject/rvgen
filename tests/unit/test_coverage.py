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


# ---------------------------------------------------------------------------
# New covergroups (mem_align, ls_width, transitions, imm_range)
# ---------------------------------------------------------------------------


def test_mem_align_word_aligned_bin():
    from chipforge_inst_gen.coverage.collectors import CG_MEM_ALIGN, CG_LS_WIDTH
    db = new_db()
    instr = get_instr(RiscvInstrName.LW)
    instr.rs1 = RiscvReg.T0
    instr.rd = RiscvReg.A0
    instr.imm_str = "8"  # 8 is word-aligned
    instr.imm = 8
    sample_instr(db, instr)
    assert db[CG_MEM_ALIGN].get("word_aligned", 0) == 1
    assert db[CG_LS_WIDTH].get("word", 0) == 1


def test_mem_align_half_unaligned_bin():
    from chipforge_inst_gen.coverage.collectors import CG_MEM_ALIGN
    db = new_db()
    instr = get_instr(RiscvInstrName.LH)
    instr.rs1 = RiscvReg.T0
    instr.rd = RiscvReg.A0
    instr.imm_str = "3"  # 3 is not even
    instr.imm = 3
    sample_instr(db, instr)
    assert db[CG_MEM_ALIGN].get("half_unaligned", 0) == 1


def test_category_transition_sampled():
    from chipforge_inst_gen.coverage.collectors import CG_CAT_TRANS
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.T0, rs1=RiscvReg.A0, rs2=RiscvReg.A1),
        _make(RiscvInstrName.AND, rd=RiscvReg.T1, rs1=RiscvReg.A2, rs2=RiscvReg.A3),
        _make(RiscvInstrName.SLT, rd=RiscvReg.T2, rs1=RiscvReg.A4, rs2=RiscvReg.A5),
    ]
    sample_sequence(db, seq)
    # ADD → AND (ARITH → LOGICAL), AND → SLT (LOGICAL → COMPARE)
    assert db[CG_CAT_TRANS].get("ARITHMETIC__LOGICAL", 0) == 1
    assert db[CG_CAT_TRANS].get("LOGICAL__COMPARE", 0) == 1


def test_opcode_transition_sampled():
    from chipforge_inst_gen.coverage.collectors import CG_OP_TRANS
    db = new_db()
    seq = [
        _make(RiscvInstrName.ADD, rd=RiscvReg.T0, rs1=RiscvReg.A0, rs2=RiscvReg.A1),
        _make(RiscvInstrName.SUB, rd=RiscvReg.T1, rs1=RiscvReg.A2, rs2=RiscvReg.A3),
    ]
    sample_sequence(db, seq)
    assert db[CG_OP_TRANS].get("ADD__SUB", 0) == 1


def test_imm_range_walking_one():
    from chipforge_inst_gen.coverage.collectors import CG_IMM_EXT
    db = new_db()
    instr = get_instr(RiscvInstrName.ADDI)
    instr.rs1 = RiscvReg.ZERO
    instr.rd = RiscvReg.A0
    instr.imm = 1 << 5  # bit 5 set only → walking-one
    instr.imm_len = 12
    instr.post_randomize()
    sample_instr(db, instr)
    assert db[CG_IMM_EXT].get("walking_one", 0) >= 1


def test_imm_range_zero_and_all_ones():
    from chipforge_inst_gen.coverage.collectors import CG_IMM_EXT
    db = new_db()
    i0 = get_instr(RiscvInstrName.ADDI)
    i0.rs1 = RiscvReg.ZERO
    i0.rd = RiscvReg.A0
    i0.imm = 0
    i0.imm_len = 12
    i0.post_randomize()
    sample_instr(db, i0)
    i1 = get_instr(RiscvInstrName.ORI)
    i1.rs1 = RiscvReg.ZERO
    i1.rd = RiscvReg.A1
    i1.imm = 0xFFF
    i1.imm_len = 12
    i1.post_randomize()
    sample_instr(db, i1)
    assert db[CG_IMM_EXT].get("zero", 0) >= 1
    assert db[CG_IMM_EXT].get("all_ones", 0) >= 1


# ---------------------------------------------------------------------------
# Runtime coverage (spike trace parser)
# ---------------------------------------------------------------------------


def test_runtime_trace_parse_branch_direction(tmp_path: Path):
    from chipforge_inst_gen.coverage import sample_trace_file
    from chipforge_inst_gen.coverage.collectors import CG_BRANCH_DIR

    # Tiny synthetic spike trace: 2 branches, one taken, one not-taken.
    trace = tmp_path / "tiny.trace"
    trace.write_text(
        "core   0: 0x80000000 (0x00108093) addi    ra, ra, 1\n"
        "core   0: 0x80000004 (0x00108463) beq     ra, ra, pc + 8\n"
        "core   0: 0x8000000c (0x00000013) addi    x0, x0, 0\n"   # jumped to +8 → taken
        "core   0: 0x80000010 (0x00109463) bne     ra, ra, pc + 8\n"
        "core   0: 0x80000014 (0x00000013) addi    x0, x0, 0\n"   # fell through +4 → not_taken
    )
    db = new_db()
    sample_trace_file(db, trace)
    assert db[CG_BRANCH_DIR].get("taken", 0) == 1
    assert db[CG_BRANCH_DIR].get("not_taken", 0) == 1


def test_runtime_trace_parse_pc_reach(tmp_path: Path):
    from chipforge_inst_gen.coverage import sample_trace_file
    from chipforge_inst_gen.coverage.collectors import CG_PC_REACH, CG_EXCEPTION

    trace = tmp_path / "tiny.trace"
    trace.write_text(
        "core   0: >>>>  init\n"
        "core   0: 0x80000000 (0x00000013) addi    x0, x0, 0\n"
        "core   0: >>>>  mtvec_handler\n"
        "core   0: 0x80000100 (0x30200073) mret\n"
    )
    db = new_db()
    sample_trace_file(db, trace)
    assert db[CG_PC_REACH].get("init", 0) == 1
    assert db[CG_PC_REACH].get("mtvec_handler", 0) == 1
    # Exception covergroup bumped because mtvec is a trap label.
    assert db[CG_EXCEPTION].get("trap_entered", 0) == 1


def test_runtime_trace_privilege_mret(tmp_path: Path):
    from chipforge_inst_gen.coverage import sample_trace_file
    from chipforge_inst_gen.coverage.collectors import CG_PRIV_MODE

    trace = tmp_path / "tiny.trace"
    trace.write_text(
        "core   0: 0x80000000 (0x30200073) mret\n"
    )
    db = new_db()
    sample_trace_file(db, trace)
    # M_entered always bumped at start + M_return for the mret.
    assert db[CG_PRIV_MODE].get("M_return", 0) == 1
    assert db[CG_PRIV_MODE].get("M_entered", 0) == 1


def test_runtime_trace_missing_file_silent(tmp_path: Path):
    from chipforge_inst_gen.coverage import sample_trace_file

    db = new_db()
    # Non-existent path — should not raise, return zeros.
    meta = sample_trace_file(db, tmp_path / "does_not_exist.trace")
    assert meta == {"lines_parsed": 0, "pc_reach_labels": 0, "branches_observed": 0}


# ---------------------------------------------------------------------------
# Coverage tools CLI — diff / merge / attribute / export
# ---------------------------------------------------------------------------


def _dump(path: Path, db):
    import json
    path.write_text(json.dumps(db, indent=2, sort_keys=True))


def test_tools_merge_combines_bins(tmp_path: Path):
    from chipforge_inst_gen.coverage.tools import cmd_merge
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _dump(a, {"opcode_cg": {"ADD": 3, "SUB": 1}})
    _dump(b, {"opcode_cg": {"ADD": 2, "JAL": 5}})
    out = tmp_path / "out.json"
    import argparse
    ns = argparse.Namespace(inputs=[str(a), str(b)], output=str(out))
    assert cmd_merge(ns) == 0
    import json as _j
    merged = _j.loads(out.read_text())
    assert merged["opcode_cg"] == {"ADD": 5, "SUB": 1, "JAL": 5}


def test_tools_diff_reports_delta(tmp_path: Path):
    from chipforge_inst_gen.coverage.tools import _compute_diff
    a = {"opcode_cg": {"ADD": 3, "SUB": 1}}
    b = {"opcode_cg": {"ADD": 5, "JAL": 7}}
    diff = _compute_diff(a, b)
    assert diff == {"opcode_cg": {"ADD": 2, "JAL": 7, "SUB": -1}}


def test_tools_attribute_first_closer(tmp_path: Path):
    from chipforge_inst_gen.coverage.tools import cmd_attribute
    g = tmp_path / "g.yaml"
    g.write_text("opcode_cg:\n  ADD: 3\n  SUB: 2\n")
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    # a closes ADD but not SUB; b closes both.
    _dump(a, {"opcode_cg": {"ADD": 5, "SUB": 1}})
    _dump(b, {"opcode_cg": {"ADD": 1, "SUB": 3}})
    import argparse, io, contextlib
    ns = argparse.Namespace(inputs=[str(a), str(b)], goals=str(g))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_attribute(ns)
    assert rc == 0  # all bins closed by the end
    out = buf.getvalue()
    assert "2/2 required bins closed" in out


def test_tools_export_csv(tmp_path: Path):
    from chipforge_inst_gen.coverage.tools import cmd_export
    a = tmp_path / "a.json"
    _dump(a, {"opcode_cg": {"ADD": 3}})
    out = tmp_path / "o.csv"
    import argparse
    ns = argparse.Namespace(input=str(a), csv=str(out), html=None, goals=None)
    assert cmd_export(ns) == 0
    txt = out.read_text()
    assert "covergroup,bin,hit_count" in txt
    assert "opcode_cg,ADD,3" in txt


def test_tools_export_html(tmp_path: Path):
    from chipforge_inst_gen.coverage.tools import cmd_export
    a = tmp_path / "a.json"
    _dump(a, {"opcode_cg": {"ADD": 3}})
    out = tmp_path / "o.html"
    import argparse
    ns = argparse.Namespace(input=str(a), csv=None, html=str(out), goals=None)
    assert cmd_export(ns) == 0
    txt = out.read_text()
    assert "<html>" in txt
    assert "opcode_cg" in txt
    assert ">3<" in txt  # the hit count renders somewhere


# ---------------------------------------------------------------------------
# Coverage-directed perturbation
# ---------------------------------------------------------------------------


def test_directed_drops_no_fence_when_fence_missing(tmp_path: Path):
    from chipforge_inst_gen.coverage.directed import directed_gen_opts
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  FENCE: 5\n")
    goals = load_goals(p)
    db = new_db()  # FENCE=0 — missing
    new_opts, reasons = directed_gen_opts("+no_fence=1 +instr_cnt=100", db, goals)
    assert "+no_fence=1" not in new_opts
    assert "+no_fence=0" in new_opts
    assert any("no_fence" in r for r in reasons)


def test_directed_injects_stream_when_load_byte_missing(tmp_path: Path):
    from chipforge_inst_gen.coverage.directed import directed_gen_opts
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  LB: 3\n")
    goals = load_goals(p)
    db = new_db()
    new_opts, reasons = directed_gen_opts("+instr_cnt=100", db, goals)
    assert "riscv_load_store_rand_instr_stream" in new_opts
    assert any("LB missing" in r for r in reasons)


def test_directed_no_change_when_goals_met(tmp_path: Path):
    from chipforge_inst_gen.coverage.directed import directed_gen_opts
    p = tmp_path / "g.yaml"
    p.write_text("opcode_cg:\n  ADD: 3\n")
    goals = load_goals(p)
    db = new_db()
    db[CG_OPCODE] = {"ADD": 10}
    new_opts, reasons = directed_gen_opts("+instr_cnt=100 +no_fence=1", db, goals)
    assert new_opts == "+instr_cnt=100 +no_fence=1"
    assert reasons == []


def test_jalr_instr_stream_registered():
    from chipforge_inst_gen.streams import get_stream
    cls = get_stream("riscv_jalr_instr")
    assert cls.__name__ == "JalrInstr"
