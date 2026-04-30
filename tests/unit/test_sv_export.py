"""Tests for the SystemVerilog covergroup exporter."""

from __future__ import annotations

import pytest

from rvgen.coverage.cgf import Goals
from rvgen.coverage.sv_export import (
    _sv_class_name,
    _sv_identifier,
    emit_sv_covergroup,
    emit_sv_package,
    write_sv_package,
)


# ---------- identifier sanitisation ----------


@pytest.mark.parametrize("raw,expected", [
    ("opcode_cg", "opcode_cg"),
    ("rs1_cg", "rs1_cg"),
    ("opcode_transition_cg", "opcode_transition_cg"),
    ("ADD__dyn", "ADD__dyn"),
    ("ADD-FOO", "ADD_FOO"),
    ("MOP_R_15", "MOP_R_15"),
    ("walking_ones", "walking_ones"),
])
def test_sv_identifier_keeps_legal_chars(raw, expected):
    assert _sv_identifier(raw) == expected


def test_sv_identifier_replaces_dashes_dots_etc():
    assert _sv_identifier("foo.bar-baz") == "foo_bar_baz"


def test_sv_identifier_prefixes_digit_starts():
    assert _sv_identifier("123abc") == "b_123abc"


def test_sv_class_name_format():
    assert _sv_class_name("opcode_cg") == "rvgen_opcode_cg_cover"
    assert _sv_class_name("vec_eew_cg") == "rvgen_vec_eew_cg_cover"


# ---------- single covergroup emit ----------


def test_emit_sv_covergroup_includes_class_keyword():
    src = emit_sv_covergroup("opcode_cg", {"ADD": 5})
    assert "class rvgen_opcode_cg_cover;" in src
    assert "covergroup cg_opcode_cg" in src
    assert "endclass" in src


def test_emit_sv_covergroup_emits_one_bin_per_input():
    src = emit_sv_covergroup("opcode_cg", {"ADD": 5, "SUB": 3, "MUL": 1})
    # Each bin name should appear in the source.
    assert "bins ADD =" in src
    assert "bins SUB =" in src
    assert "bins MUL =" in src


def test_emit_sv_covergroup_marks_zero_count_as_optional():
    src = emit_sv_covergroup("foo_cg", {"a_bin": 5, "b_bin": 0})
    assert "// at_least = 5" in src
    assert "// optional" in src


def test_emit_sv_covergroup_sorts_bins_by_required_count_desc():
    src = emit_sv_covergroup("foo_cg", {"low": 1, "mid": 5, "high": 100})
    # high should appear before mid which should appear before low.
    high_idx = src.index("bins high =")
    mid_idx = src.index("bins mid =")
    low_idx = src.index("bins low =")
    assert high_idx < mid_idx < low_idx


def test_emit_sv_covergroup_quotes_bin_label_in_match_predicate():
    src = emit_sv_covergroup("opcode_cg", {"ADD__dyn": 3})
    # The runtime "_dyn" suffix needs to survive into the SV match string.
    assert 's == "ADD__dyn"' in src


def test_emit_sv_covergroup_sanitises_class_id_for_non_alphanum():
    # Hypothetical bin name that contains hyphens.
    src = emit_sv_covergroup("foo-cg", {"a": 1})
    assert "class rvgen_foo_cg_cover;" in src


# ---------- package emit ----------


def test_emit_sv_package_wraps_all_classes():
    goals = Goals(data={
        "opcode_cg": {"ADD": 1},
        "rs1_cg": {"A0": 2},
        "fp_rm_cg": {"RNE": 1},
    })
    src = emit_sv_package(goals)
    # Package boilerplate.
    assert "package rvgen_cov_pkg;" in src
    assert "endpackage : rvgen_cov_pkg" in src
    # All 3 classes should be included.
    assert "class rvgen_opcode_cg_cover;" in src
    assert "class rvgen_rs1_cg_cover;" in src
    assert "class rvgen_fp_rm_cg_cover;" in src


def test_emit_sv_package_supports_custom_package_name():
    goals = Goals(data={"opcode_cg": {"ADD": 1}})
    src = emit_sv_package(goals, package_name="my_team_cov_pkg")
    assert "package my_team_cov_pkg;" in src


def test_emit_sv_package_skips_empty_covergroups():
    goals = Goals(data={
        "opcode_cg": {"ADD": 1},
        "empty_cg": {},   # no bins → skip
    })
    src = emit_sv_package(goals)
    assert "rvgen_opcode_cg_cover" in src
    assert "rvgen_empty_cg_cover" not in src


# ---------- file output ----------


def test_write_sv_package_creates_file(tmp_path):
    goals = Goals(data={"opcode_cg": {"ADD": 1, "SUB": 1}})
    out = tmp_path / "subdir" / "cov.sv"
    written = write_sv_package(goals, out)
    assert written == out
    assert out.exists()
    text = out.read_text()
    assert "package rvgen_cov_pkg;" in text


def test_write_sv_package_exports_full_baseline(tmp_path):
    # End-to-end: load the shipped baseline goals and write the SV.
    from rvgen.coverage.cgf import load_goals
    g = load_goals("rvgen/coverage/goals/baseline.yaml")
    out = tmp_path / "baseline.sv"
    write_sv_package(g, out)
    text = out.read_text()
    # Should contain a covergroup for opcode_cg + at least one for rs1_cg.
    assert "rvgen_opcode_cg_cover" in text
    assert "rvgen_rs1_cg_cover" in text
    # And many lines (the baseline has hundreds of bins).
    assert text.count("\n") > 100
