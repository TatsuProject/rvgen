"""Tests for the scorecard CLI subcommand."""

from __future__ import annotations

import json

import pytest

from rvgen.coverage.tools import (
    _classify_opcode_bin,
    _subsys_for_bin,
    cmd_scorecard,
)


# ---------- bin classification ----------


@pytest.mark.parametrize("op,subsys", [
    ("ADD", "RV32I+RV64I"),
    ("LW", "RV32I+RV64I"),
    ("BEQ", "RV32I+RV64I"),
    ("MUL", "RV32M+RV64M"),
    ("DIV", "RV32M+RV64M"),
    ("FADD_S", "Floating point"),
    ("VADD_VV", "Vector"),
    ("VAES64_DM", "Crypto"),
    ("AES32ESI", "Crypto"),
    ("CZERO_EQZ", "Modern checkbox"),
    ("CBO_ZERO", "Modern checkbox"),
    ("PREFETCH_R", "Modern checkbox"),
    ("PAUSE", "Modern checkbox"),
    ("MOP_R_5", "Modern checkbox"),
    ("FENCE", "Memory ordering"),
    ("LR_W", "Atomics"),
    ("AMOSWAP_W", "Atomics"),
    ("MRET", "Privileged"),
    ("SFENCE_VMA", "Privileged"),
    ("ROL", "Bitmanip"),
    ("CLZ", "Bitmanip"),
    ("C_ADD", "Compressed"),
    ("C_MV", "Compressed"),
])
def test_classify_opcode_bin(op, subsys):
    assert _classify_opcode_bin(op) == subsys


def test_subsys_for_bin_uses_group_map_first():
    # vec_eew_cg is mapped to "Vector" regardless of bin name.
    assert _subsys_for_bin("vec_eew_cg", "EEW32") == "Vector"
    assert _subsys_for_bin("hazard_cg", "raw") == "Pipeline"
    assert _subsys_for_bin("priv_event_cg", "satp_write") == "Privileged"
    assert _subsys_for_bin("modern_ext_cg", "zicond_czero_eqz") == "Modern checkbox"


def test_subsys_for_bin_falls_back_to_opcode_classifier():
    # opcode_cg has no bucket — falls through to bin-based classification.
    assert _subsys_for_bin("opcode_cg", "ADD") == "RV32I+RV64I"
    assert _subsys_for_bin("opcode_cg", "FADD_S") == "Floating point"


def test_subsys_for_bin_strips_dyn_suffix_for_opcode():
    # Runtime sampler suffixes "_dyn" — should still classify correctly.
    assert _subsys_for_bin("opcode_cg", "ADD__dyn") == "RV32I+RV64I"


# ---------- end-to-end scorecard ----------


def test_scorecard_writes_ascii_to_stdout(tmp_path, capsys):
    db_path = tmp_path / "cov.json"
    db_path.write_text(json.dumps({
        "opcode_cg": {"ADD": 5, "MUL": 3, "FADD_S": 0},
        "modern_ext_cg": {"zicond_czero_eqz": 2},
    }))
    goals_path = tmp_path / "goals.yaml"
    goals_path.write_text(
        "opcode_cg:\n"
        "  ADD: 1\n"
        "  MUL: 1\n"
        "  FADD_S: 1\n"
        "modern_ext_cg:\n"
        "  zicond_czero_eqz: 1\n"
        "  zicond_czero_nez: 1\n"
    )

    class _Args:
        db = str(db_path)
        goals = [str(goals_path)]
        json = False

    rc = cmd_scorecard(_Args())
    out = capsys.readouterr().out
    assert "Subsystem" in out
    assert "OVERALL" in out
    # FADD_S has goal 1 but observed 0 → Floating point should show 0%.
    assert "Floating point" in out
    # Modern checkbox has goal 2 (czero_eqz, czero_nez), met only 1 → 50%.
    assert "Modern checkbox" in out


def test_scorecard_emits_json_when_flag_set(tmp_path, capsys):
    db_path = tmp_path / "cov.json"
    db_path.write_text(json.dumps({"opcode_cg": {"ADD": 5}}))
    goals_path = tmp_path / "goals.yaml"
    goals_path.write_text("opcode_cg:\n  ADD: 1\n")

    class _Args:
        db = str(db_path)
        goals = [str(goals_path)]
        json = True

    rc = cmd_scorecard(_Args())
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "scorecard" in payload
    assert any(r["subsystem"] == "RV32I+RV64I" for r in payload["scorecard"])
    rv32 = next(r for r in payload["scorecard"]
                if r["subsystem"] == "RV32I+RV64I")
    assert rv32["met"] == 1 and rv32["required"] == 1


def test_scorecard_returns_nonzero_on_low_subsystem(tmp_path):
    # 0/10 in a single subsystem must return rc=1 (CI gate).
    db_path = tmp_path / "cov.json"
    db_path.write_text(json.dumps({"opcode_cg": {}}))
    goals_path = tmp_path / "goals.yaml"
    goals_lines = ["opcode_cg:"]
    for i in range(10):
        goals_lines.append(f"  FADD_S_{i}: 1")
    goals_path.write_text("\n".join(goals_lines))

    class _Args:
        db = str(db_path)
        goals = [str(goals_path)]
        json = False

    rc = cmd_scorecard(_Args())
    assert rc == 1


def test_scorecard_returns_zero_when_above_threshold(tmp_path):
    db_path = tmp_path / "cov.json"
    db_path.write_text(json.dumps({"opcode_cg": {f"ADD_{i}": 1 for i in range(10)}}))
    goals_path = tmp_path / "goals.yaml"
    goals_lines = ["opcode_cg:"]
    for i in range(10):
        goals_lines.append(f"  ADD_{i}: 1")
    goals_path.write_text("\n".join(goals_lines))

    class _Args:
        db = str(db_path)
        goals = [str(goals_path)]
        json = False

    rc = cmd_scorecard(_Args())
    assert rc == 0
