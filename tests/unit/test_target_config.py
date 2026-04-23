"""Tests for the YAML target-config loader + user-area discovery."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rvgen.isa.enums import (
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvInstrName,
    SatpMode,
)
from rvgen.targets import (
    BUILTIN_TARGETS,
    TargetCfg,
    get_target,
    load_target_yaml,
    resolve_user_dir,
    set_user_dir,
    target_names,
)
from rvgen.targets.loader import discover_user_targets


# ---------- TargetCfg defaults ----------


def test_builtin_target_has_clint_defaults():
    t = get_target("rv32imc")
    assert t.clint_base == 0x02000000
    assert t.mtimecmp_offset == 0x4000
    assert t.mtime_offset == 0xBFF8
    assert t.msip_offset == 0x0


def test_builtin_target_isa_string_defaults_empty():
    # Built-in targets leave isa_string/mabi empty — CLI falls back
    # to the _TARGET_ISA_MABI table. YAML targets should set these.
    t = get_target("rv32imc")
    assert t.isa_string == ""
    assert t.mabi == ""


# ---------- YAML loader — happy path ----------


def _write_yaml(tmp_path: Path, name: str, contents: dict) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(contents))
    return path


def test_load_minimal_target_yaml(tmp_path):
    path = _write_yaml(tmp_path, "my_core", {
        "name": "my_core",
        "xlen": 32,
        "supported_isa": ["RV32I", "RV32M", "RV32C"],
        "supported_privileged_mode": ["MACHINE_MODE"],
    })
    t = load_target_yaml(path)
    assert t.name == "my_core"
    assert t.xlen == 32
    assert t.supported_isa == (
        RiscvInstrGroup.RV32I, RiscvInstrGroup.RV32M, RiscvInstrGroup.RV32C,
    )
    assert t.supported_privileged_mode == (PrivilegedMode.MACHINE_MODE,)
    # Defaults filled in.
    assert t.satp_mode == SatpMode.BARE
    assert t.clint_base == 0x02000000


def test_load_target_yaml_with_custom_clint(tmp_path):
    path = _write_yaml(tmp_path, "weird_soc", {
        "name": "weird_soc",
        "xlen": 64,
        "supported_isa": ["RV64I", "RV64M"],
        "supported_privileged_mode": ["MACHINE_MODE"],
        "clint": {
            "base": 0x80000000,
            "mtime_offset": 0x4000,
            "mtimecmp_offset": 0x8000,
            "msip_offset": 0x1000,
        },
    })
    t = load_target_yaml(path)
    assert t.clint_base == 0x80000000
    assert t.mtime_offset == 0x4000
    assert t.mtimecmp_offset == 0x8000
    assert t.msip_offset == 0x1000


def test_load_target_yaml_preset_expansion(tmp_path):
    # implemented_csr: MMODE_CSRS should expand to the preset tuple.
    from rvgen.targets.presets import MMODE_CSRS
    path = _write_yaml(tmp_path, "preset_core", {
        "name": "preset_core",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
        "implemented_csr": "MMODE_CSRS",
    })
    t = load_target_yaml(path)
    assert t.implemented_csr == MMODE_CSRS


def test_load_target_yaml_unsupported_instr(tmp_path):
    path = _write_yaml(tmp_path, "no_mul_hi", {
        "name": "no_mul_hi",
        "xlen": 32,
        "supported_isa": ["RV32I", "RV32M"],
        "supported_privileged_mode": ["MACHINE_MODE"],
        "unsupported_instr": ["MUL", "MULH", "MULHSU", "MULHU"],
    })
    t = load_target_yaml(path)
    assert t.unsupported_instr == (
        RiscvInstrName.MUL, RiscvInstrName.MULH,
        RiscvInstrName.MULHSU, RiscvInstrName.MULHU,
    )


def test_load_target_yaml_isa_mabi(tmp_path):
    path = _write_yaml(tmp_path, "toolchain_core", {
        "name": "toolchain_core",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
        "isa_string": "rv32i_zicsr_zifencei",
        "mabi": "ilp32",
    })
    t = load_target_yaml(path)
    assert t.isa_string == "rv32i_zicsr_zifencei"
    assert t.mabi == "ilp32"


def test_load_target_yaml_satp_mode_parses(tmp_path):
    path = _write_yaml(tmp_path, "sv39_core", {
        "name": "sv39_core",
        "xlen": 64,
        "supported_isa": ["RV64I"],
        "supported_privileged_mode": ["MACHINE_MODE", "SUPERVISOR_MODE", "USER_MODE"],
        "satp_mode": "SV39",
    })
    t = load_target_yaml(path)
    assert t.satp_mode == SatpMode.SV39


# ---------- YAML loader — error paths ----------


def test_load_target_yaml_missing_file():
    with pytest.raises(FileNotFoundError):
        load_target_yaml(Path("/nonexistent/path.yaml"))


def test_load_target_yaml_missing_required_field(tmp_path):
    path = _write_yaml(tmp_path, "broken", {"name": "broken", "xlen": 32})
    with pytest.raises(ValueError, match="missing required field"):
        load_target_yaml(path)


def test_load_target_yaml_unknown_enum_name(tmp_path):
    path = _write_yaml(tmp_path, "bad_isa", {
        "name": "bad_isa",
        "xlen": 32,
        "supported_isa": ["NONSENSE_GROUP"],
        "supported_privileged_mode": ["MACHINE_MODE"],
    })
    with pytest.raises(KeyError):
        load_target_yaml(path)


def test_load_target_yaml_non_mapping_top_level(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- this: is\n- a: list\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_target_yaml(path)


# ---------- User-area discovery ----------


def test_discover_user_targets_empty_dir(tmp_path):
    # No targets/ subdirectory → empty mapping, no crash.
    assert discover_user_targets(tmp_path) == {}


def test_discover_user_targets_single_yaml(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_yaml(tmp_path / "targets", "my_core", {
        "name": "my_core",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
    })
    found = discover_user_targets(tmp_path)
    assert "my_core" in found
    assert found["my_core"].name == "my_core.yaml"


def test_discover_user_targets_name_from_yaml_not_filename(tmp_path):
    # Filename and the YAML's `name` field may differ — the key is
    # the name field.
    (tmp_path / "targets").mkdir()
    _write_yaml(tmp_path / "targets", "any_filename", {
        "name": "different_name",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
    })
    found = discover_user_targets(tmp_path)
    assert "different_name" in found
    assert "any_filename" not in found


def test_discover_user_targets_skips_unparseable(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "broken.yaml").write_text(": : :")  # bad YAML
    (tmp_path / "targets" / "good.yaml").write_text(yaml.safe_dump({
        "name": "good",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
    }))
    found = discover_user_targets(tmp_path)
    assert "good" in found
    # Broken file is silently skipped — the CLI will surface a real
    # error later if the user actually selects that target.


# ---------- get_target dispatch (builtin + user area) ----------


@pytest.fixture
def tmp_user_dir(tmp_path):
    """Isolate these tests from any ambient user area."""
    prior = resolve_user_dir()
    (tmp_path / "targets").mkdir()
    set_user_dir(tmp_path)
    yield tmp_path
    set_user_dir(prior)


def test_get_target_finds_builtin(tmp_user_dir):
    # Built-in path still works even with a user dir configured.
    t = get_target("rv32imc")
    assert t.name == "rv32imc"
    assert t is BUILTIN_TARGETS["rv32imc"]


def test_get_target_finds_user_yaml(tmp_user_dir):
    _write_yaml(tmp_user_dir / "targets", "my_custom_core", {
        "name": "my_custom_core",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
        "clint": {"base": 0x40000000},
    })
    t = get_target("my_custom_core")
    assert t.name == "my_custom_core"
    assert t.clint_base == 0x40000000


def test_get_target_raises_for_unknown(tmp_user_dir):
    with pytest.raises(KeyError, match="Unknown target"):
        get_target("definitely_not_a_target")


def test_target_names_includes_user_yaml(tmp_user_dir):
    _write_yaml(tmp_user_dir / "targets", "xyz_core", {
        "name": "xyz_core",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
    })
    names = target_names()
    assert "xyz_core" in names
    assert "rv32imc" in names  # built-ins still present


def test_builtin_wins_on_name_collision(tmp_user_dir):
    # A user-area YAML with the same name as a built-in gets shadowed.
    # Built-in wins so users can't accidentally break the framework.
    _write_yaml(tmp_user_dir / "targets", "rv32imc", {
        "name": "rv32imc",
        "xlen": 32,
        "supported_isa": ["RV32I"],
        "supported_privileged_mode": ["MACHINE_MODE"],
        "clint": {"base": 0xDEADBEEF},
    })
    t = get_target("rv32imc")
    # Built-in CLINT base, not 0xDEADBEEF.
    assert t.clint_base == 0x02000000
