"""Registry and ``DEFINE_INSTR``-style factory for instruction classes.

Every concrete instruction gets a :class:`rvgen.isa.base.Instr`
subclass via :func:`define_instr` (or :func:`define_csr_instr` for CSR ops);
the subclass is automatically added to :data:`INSTR_REGISTRY` so
:func:`get_instr` can instantiate it by name.

This mirrors riscv-dv's UVM-based approach (``riscv_instr::register`` +
``uvm_factory::create_object_by_name``) but without the UVM dependency —
just a plain dict keyed by :class:`RiscvInstrName`.
"""

from __future__ import annotations

from typing import Type

from rvgen.isa.base import Instr
from rvgen.isa.csr_ops import CsrInstr
from rvgen.isa.enums import (
    ImmType,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
)


#: Global registry: instruction name -> concrete subclass of Instr.
INSTR_REGISTRY: dict[RiscvInstrName, Type[Instr]] = {}


def _assert_not_registered(name: RiscvInstrName) -> None:
    if name in INSTR_REGISTRY:
        existing = INSTR_REGISTRY[name].__name__
        raise ValueError(
            f"Instruction {name.name} already registered as {existing}. "
            "Duplicate define_instr calls are not allowed."
        )


def _make_subclass(
    base: Type[Instr],
    instr_name: RiscvInstrName,
    fmt: RiscvInstrFormat,
    category: RiscvInstrCategory,
    group: RiscvInstrGroup,
    imm_type: ImmType,
) -> Type[Instr]:
    """Factory helper — create a concrete subclass with the class-level attrs."""
    class_name = f"riscv_{instr_name.name}_instr"
    return type(
        class_name,
        (base,),
        {
            "instr_name": instr_name,
            "format": fmt,
            "category": category,
            "group": group,
            "imm_type": imm_type,
        },
    )


def define_instr(
    instr_name: RiscvInstrName,
    fmt: RiscvInstrFormat,
    category: RiscvInstrCategory,
    group: RiscvInstrGroup,
    imm_type: ImmType = ImmType.IMM,
    *,
    base: Type[Instr] | None = None,
) -> Type[Instr]:
    """Register an instruction class (port of SV ``DEFINE_INSTR`` macro).

    Parameters
    ----------
    instr_name : RiscvInstrName
        Enum member identifying the instruction.
    fmt : RiscvInstrFormat
        Encoding format (R, I, S, B, U, J, ...).
    category : RiscvInstrCategory
        Semantic category (LOAD, STORE, ARITHMETIC, ...).
    group : RiscvInstrGroup
        Extension group (RV32I, RV32M, ...).
    imm_type : ImmType
        Immediate type (IMM, UIMM, NZIMM, NZUIMM). Defaults to IMM.
    base : Type[Instr], optional
        Base class. Defaults to :class:`Instr`. Pass :class:`CsrInstr` (via
        :func:`define_csr_instr`) for CSR operations.
    """
    _assert_not_registered(instr_name)
    cls = _make_subclass(base or Instr, instr_name, fmt, category, group, imm_type)
    INSTR_REGISTRY[instr_name] = cls
    return cls


def define_csr_instr(
    instr_name: RiscvInstrName,
    fmt: RiscvInstrFormat,
    category: RiscvInstrCategory,
    group: RiscvInstrGroup,
    imm_type: ImmType = ImmType.UIMM,
) -> Type[Instr]:
    """Register a CSR instruction (port of SV ``DEFINE_CSR_INSTR`` macro)."""
    return define_instr(instr_name, fmt, category, group, imm_type, base=CsrInstr)


def get_instr(name: RiscvInstrName) -> Instr:
    """Instantiate a concrete instruction by enum name.

    Direct port of SV ``riscv_instr::get_instr`` (riscv_instr.sv:272). Returns
    a fresh instance with default operands; caller is responsible for setting
    rs1/rs2/rd/imm/csr and invoking :meth:`Instr.post_randomize` afterwards.
    """
    try:
        cls = INSTR_REGISTRY[name]
    except KeyError as e:
        raise KeyError(
            f"Instruction {name.name} is not registered. "
            "Ensure its ISA module has been imported (e.g., `from rvgen.isa import rv32i`)."
        ) from e
    return cls()


def is_registered(name: RiscvInstrName) -> bool:
    """Return whether an instruction has been registered."""
    return name in INSTR_REGISTRY


def registered_names() -> frozenset[RiscvInstrName]:
    """Return the set of currently registered instruction names."""
    return frozenset(INSTR_REGISTRY.keys())


def clear_registry() -> None:
    """Remove all registered instructions (test-only helper)."""
    INSTR_REGISTRY.clear()
