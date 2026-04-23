"""YAML loader for user-authored :class:`TargetCfg` definitions.

This is rvgen's analogue of riscv-dv's ``target/<name>/riscv_core_setting.sv``
— users can declare a new target without editing framework code by
dropping a YAML file into the user area (typically
``<user_dir>/targets/<name>.yaml``) and running with
``--target <name>``.

Schema (all fields optional except ``name``, ``xlen``, ``supported_isa``,
and ``supported_privileged_mode``):

.. code-block:: yaml

    # user/targets/my_core.yaml — example
    name: my_core
    xlen: 32
    supported_isa: [RV32I, RV32M, RV32C]
    supported_privileged_mode: [MACHINE_MODE]
    satp_mode: BARE
    support_sfence: false
    support_unaligned_load_store: true
    num_harts: 1

    # Implementation-defined CLINT memory map. Defaults are the SiFive
    # CLINT layout (Spike / QEMU virt). Override when the DUT differs.
    clint:
      base: 0x02000000
      mtime_offset: 0xBFF8
      mtimecmp_offset: 0x4000
      msip_offset: 0x0

    # GCC + spike toolchain strings.
    isa_string: rv32imc_zicsr_zifencei
    mabi: ilp32

    # Individual instructions to exclude even though their extension
    # group is in supported_isa.
    unsupported_instr: [MUL, MULH]

    # Which architectural CSRs / interrupts / exceptions the core
    # implements. Supports ``preset: <NAME>`` for the canonical sets
    # defined in :mod:`rvgen.targets.presets`.
    implemented_csr: MMODE_CSRS
    implemented_interrupt: MMODE_INTERRUPTS
    implemented_exception: MMODE_EXCEPTIONS
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rvgen.isa.enums import (
    ExceptionCause,
    InterruptCause,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvInstrName,
    SatpMode,
)
from rvgen.targets.core_setting import TargetCfg
from rvgen.targets.presets import PRESETS


def _resolve_enum_list(values: Any, enum_cls: type) -> tuple:
    """Convert a YAML list of enum names into a tuple of enum members.

    Also accepts a string that names a preset tuple from :data:`PRESETS`
    (e.g. ``implemented_csr: MMODE_CSRS``) — expanded in-place.
    """
    if values is None:
        return ()
    if isinstance(values, str):
        # Preset reference.
        if values in PRESETS:
            return tuple(PRESETS[values])
        # Single enum name as a bare string.
        return (enum_cls[values],)
    if not isinstance(values, (list, tuple)):
        raise TypeError(
            f"Expected a list of {enum_cls.__name__} names (or a preset), "
            f"got {type(values).__name__}: {values!r}"
        )
    out = []
    for v in values:
        if not isinstance(v, str):
            raise TypeError(
                f"Expected enum name (str) inside list, got "
                f"{type(v).__name__}: {v!r}"
            )
        out.append(enum_cls[v])
    return tuple(out)


def _resolve_enum_scalar(value: Any, enum_cls: type, default):
    if value is None:
        return default
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        return enum_cls[value]
    raise TypeError(
        f"Expected {enum_cls.__name__} name or None, got "
        f"{type(value).__name__}: {value!r}"
    )


def _resolve_int_list(values: Any) -> tuple:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise TypeError(f"Expected list of ints, got {type(values).__name__}")
    return tuple(int(v) for v in values)


def load_target_yaml(path: Path | str) -> TargetCfg:
    """Parse a target-config YAML file into a :class:`TargetCfg`.

    Raises
    ------
    FileNotFoundError
        If ``path`` doesn't exist.
    ValueError
        If a required field is missing or a value is malformed.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Target config not found: {p}")
    with p.open("r") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(
            f"{p}: top-level YAML must be a mapping, got {type(data).__name__}"
        )

    # --- Required fields ---
    for req in ("name", "xlen", "supported_isa", "supported_privileged_mode"):
        if req not in data:
            raise ValueError(f"{p}: missing required field {req!r}")

    # --- CLINT section (optional; defaults to SiFive layout) ---
    clint = data.get("clint") or {}
    if not isinstance(clint, dict):
        raise ValueError(f"{p}: 'clint' must be a mapping if present")

    kwargs: dict[str, Any] = {
        "name": str(data["name"]),
        "xlen": int(data["xlen"]),
        "supported_isa": _resolve_enum_list(
            data["supported_isa"], RiscvInstrGroup
        ),
        "supported_privileged_mode": _resolve_enum_list(
            data["supported_privileged_mode"], PrivilegedMode
        ),
        "satp_mode": _resolve_enum_scalar(
            data.get("satp_mode"), SatpMode, SatpMode.BARE
        ),
        "supported_interrupt_mode": _resolve_enum_list(
            data.get("supported_interrupt_mode"), MtvecMode
        ) or (MtvecMode.DIRECT, MtvecMode.VECTORED),
        "max_interrupt_vector_num": int(data.get("max_interrupt_vector_num", 16)),
        "num_harts": int(data.get("num_harts", 1)),
        "num_gpr": int(data.get("num_gpr", 32)),
        "num_float_gpr": int(data.get("num_float_gpr", 32)),
        "num_vec_gpr": int(data.get("num_vec_gpr", 32)),
        "vlen": int(data.get("vlen", 512)),
        "elen": int(data.get("elen", 32)),
        "selen": int(data.get("selen", 8)),
        "max_lmul": int(data.get("max_lmul", 8)),
        "vector_extension_enable": bool(data.get("vector_extension_enable", False)),
        "support_pmp": bool(data.get("support_pmp", False)),
        "support_epmp": bool(data.get("support_epmp", False)),
        "support_debug_mode": bool(data.get("support_debug_mode", False)),
        "support_umode_trap": bool(data.get("support_umode_trap", False)),
        "support_sfence": bool(data.get("support_sfence", False)),
        "support_unaligned_load_store":
            bool(data.get("support_unaligned_load_store", True)),
        "unsupported_instr": _resolve_enum_list(
            data.get("unsupported_instr"), RiscvInstrName
        ),
        "implemented_csr": _resolve_enum_list(
            data.get("implemented_csr"), PrivilegedReg
        ),
        "custom_csr": _resolve_int_list(data.get("custom_csr")),
        "implemented_interrupt": _resolve_enum_list(
            data.get("implemented_interrupt"), InterruptCause
        ),
        "implemented_exception": _resolve_enum_list(
            data.get("implemented_exception"), ExceptionCause
        ),
        "clint_base": int(clint.get("base", 0x02000000)),
        "msip_offset": int(clint.get("msip_offset", 0x0)),
        "mtimecmp_offset": int(clint.get("mtimecmp_offset", 0x4000)),
        "mtime_offset": int(clint.get("mtime_offset", 0xBFF8)),
        "isa_string": str(data.get("isa_string", "") or ""),
        "mabi": str(data.get("mabi", "") or ""),
    }
    return TargetCfg(**kwargs)


# ---------------------------------------------------------------------------
# User-area discovery
# ---------------------------------------------------------------------------


def discover_user_targets(user_dir: Path | str) -> dict[str, Path]:
    """Return ``{target_name: yaml_path}`` for every ``.yaml`` file under
    ``<user_dir>/targets/``. The target name comes from each YAML's
    ``name`` field so the filename and the exposed name can differ.

    Unparseable YAMLs are skipped silently — the CLI resolver will hit
    the real error when the user actually selects that target.
    """
    root = Path(user_dir) / "targets"
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.yaml")):
        try:
            with path.open("r") as fh:
                data = yaml.safe_load(fh) or {}
            name = data.get("name") if isinstance(data, dict) else None
            if isinstance(name, str) and name:
                out[name] = path
        except (yaml.YAMLError, OSError):
            continue
    return out
