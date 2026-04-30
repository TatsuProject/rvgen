"""Tests for riscv-isac CGF round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rvgen.coverage.cgf import Goals
from rvgen.coverage.cgf_isac import (
    _opcode_from_rvgen,
    _opcode_to_rvgen,
    _reg_to_rvgen,
    export_cgf,
    import_cgf,
)


# ---------- register translation ----------


@pytest.mark.parametrize("isac_label,rvgen_name", [
    ("x0", "ZERO"),
    ("x1", "RA"),
    ("x2", "SP"),
    ("x3", "GP"),
    ("x10", "A0"),
    ("x31", "T6"),
    ("zero", "ZERO"),
    ("ra", "RA"),
    ("sp", "SP"),
    ("a0", "A0"),
    ("a7", "A7"),
    ("s0", "S0"),
    ("fp", "S0"),   # alias
    ("ZERO", "ZERO"),
    ("A0", "A0"),
])
def test_reg_to_rvgen(isac_label, rvgen_name):
    assert _reg_to_rvgen(isac_label) == rvgen_name


def test_reg_to_rvgen_invalid_returns_none():
    assert _reg_to_rvgen("x99") is None
    assert _reg_to_rvgen("foo") is None


# ---------- opcode translation ----------


@pytest.mark.parametrize("isac,rvgen", [
    ("add", "ADD"),
    ("addi", "ADDI"),
    ("fadd.s", "FADD_S"),
    ("c.j", "C_J"),
    ("c.beqz", "C_BEQZ"),
    ("vfadd.vv", "VFADD_VV"),
])
def test_opcode_to_rvgen(isac, rvgen):
    assert _opcode_to_rvgen(isac) == rvgen


@pytest.mark.parametrize("rvgen,isac", [
    ("ADD", "add"),
    ("FADD_S", "fadd.s"),
    ("C_BEQZ", "c.beqz"),
    ("VFADD_VV", "vfadd.vv"),
])
def test_opcode_from_rvgen(rvgen, isac):
    assert _opcode_from_rvgen(rvgen) == isac


# ---------- import: minimal CGF ----------


def _write_cgf(tmp_path, payload):
    p = tmp_path / "in.cgf"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def test_import_mnemonics(tmp_path):
    p = _write_cgf(tmp_path, {
        "add": {"mnemonics": {"add": 0}},
        "sub": {"mnemonics": {"sub": 5}},
    })
    g = import_cgf(p)
    assert g.data["opcode_cg"]["ADD"] == 1   # 0 → upgraded to 1
    assert g.data["opcode_cg"]["SUB"] == 5


def test_import_register_fields(tmp_path):
    p = _write_cgf(tmp_path, {
        "add": {
            "mnemonics": {"add": 1},
            "rs1": {"x1": 1, "a0": 2},
            "rs2": {"x2": 1},
            "rd": {"x5": 1},
        },
    })
    g = import_cgf(p)
    assert g.data["rs1_cg"] == {"RA": 1, "A0": 2}
    assert g.data["rs2_cg"] == {"SP": 1}
    assert g.data["rd_cg"] == {"T0": 1}


def test_import_op_comb_rs1_eq_rs2(tmp_path):
    p = _write_cgf(tmp_path, {
        "add": {
            "mnemonics": {"add": 1},
            "op_comb": {
                "label_eq":   "rs1 == rs2",
                "label_neq":  "rs1 != rs2",
            },
        },
    })
    g = import_cgf(p)
    assert g.data["rs1_eq_rs2_cg"]["equal"] == 1
    assert g.data["rs1_eq_rs2_cg"]["distinct"] == 1


def test_import_op_comb_isac_label_convention(tmp_path):
    # riscv-isac sometimes encodes intent in the *label* not the value:
    # `unique_rs1_rs2: ''` means "rs1 != rs2".
    p = _write_cgf(tmp_path, {
        "add": {
            "mnemonics": {"add": 1},
            "op_comb": {
                "unique_rs1_rs2": "",
                "rs1_eq_rs2":     "",
            },
        },
    })
    g = import_cgf(p)
    assert g.data["rs1_eq_rs2_cg"]["distinct"] == 1
    assert g.data["rs1_eq_rs2_cg"]["equal"] == 1


def test_import_csr_comb(tmp_path):
    p = _write_cgf(tmp_path, {
        "csrrw": {
            "mnemonics": {"csrrw": 1},
            "csr_comb": {"mscratch": 5, "mepc": 1},
        },
    })
    g = import_cgf(p)
    assert g.data["csr_cg"]["MSCRATCH"] == 5
    assert g.data["csr_cg"]["MEPC"] == 1


def test_import_cross_comb_arrow_notation(tmp_path):
    # CGF uses `prev -> curr` to denote category transitions.
    p = _write_cgf(tmp_path, {
        "branch_then_load": {
            "cross_comb": {"BRANCH -> LOAD": ""},
        },
    })
    g = import_cgf(p)
    assert "BRANCH__LOAD" in g.data["category_transition_cg"]


# ---------- export ----------


def test_export_writes_yaml(tmp_path):
    g = Goals(data={"opcode_cg": {"ADD": 5, "SUB": 3}})
    out = tmp_path / "out.cgf"
    export_cgf(g, out)
    assert out.exists()
    parsed = yaml.safe_load(out.read_text())
    assert parsed["add"]["mnemonics"]["add"] == 5
    assert parsed["sub"]["mnemonics"]["sub"] == 3


def test_export_skips_dyn_suffix(tmp_path):
    # Runtime _dyn bins shouldn't pollute the CGF output.
    g = Goals(data={"opcode_cg": {"ADD": 5, "ADD__dyn": 100}})
    out = tmp_path / "out.cgf"
    export_cgf(g, out)
    parsed = yaml.safe_load(out.read_text())
    assert "add" in parsed
    # No second entry for the dyn variant.
    assert "add__dyn" not in parsed


def test_export_lifts_global_bins(tmp_path):
    # rs1_cg / rs2_cg / rd_cg / op_comb / csr_cg ride a __global__ entry.
    g = Goals(data={
        "opcode_cg": {"ADD": 1},
        "rs1_cg": {"A0": 5, "RA": 3},
        "rs1_eq_rs2_cg": {"equal": 1, "distinct": 5},
        "csr_cg": {"MSCRATCH": 5},
    })
    out = tmp_path / "out.cgf"
    export_cgf(g, out)
    parsed = yaml.safe_load(out.read_text())
    assert "__global__" in parsed
    assert parsed["__global__"]["rs1"] == {"a0": 5, "ra": 3}
    assert "rs1 == rs2" in parsed["__global__"]["op_comb"]
    assert "rs1 != rs2" in parsed["__global__"]["op_comb"]
    assert parsed["__global__"]["csr_comb"]["MSCRATCH"] == 5


# ---------- round-trip ----------


def test_round_trip_preserves_opcodes_and_registers(tmp_path):
    src = {
        "add": {
            "mnemonics": {"add": 5},
            "rs1": {"a0": 3},
            "rs2": {"a1": 2},
            "rd":  {"t0": 1},
        },
    }
    in_path = _write_cgf(tmp_path, src)
    g = import_cgf(in_path)
    out = tmp_path / "round_trip.cgf"
    export_cgf(g, out)
    parsed = yaml.safe_load(out.read_text())
    # Mnemonic survives.
    assert parsed["add"]["mnemonics"]["add"] == 5
    # Register fields hoisted to __global__ on export.
    assert parsed["__global__"]["rs1"]["a0"] == 3
    assert parsed["__global__"]["rs2"]["a1"] == 2
    assert parsed["__global__"]["rd"]["t0"] == 1
