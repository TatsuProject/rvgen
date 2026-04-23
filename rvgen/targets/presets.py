"""Preset tuples for reuse across :class:`TargetCfg` definitions.

Each preset captures a canonical set of CSRs, interrupt causes, or
exception causes shared by a class of targets (M-only, U/S/M, etc.).
Targets assemble their own config by picking relevant presets in
:mod:`rvgen.targets.builtin`.

The YAML loader (:mod:`rvgen.targets.loader`) can reference these
presets by name — e.g. ``implemented_csr: MMODE_CSRS`` in the YAML
expands to :data:`MMODE_CSRS` here, avoiding hundreds of lines of
CSR enumeration in every user-authored target file.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    ExceptionCause,
    InterruptCause,
    PrivilegedReg,
)


# ---------------------------------------------------------------------------
# CSR presets
# ---------------------------------------------------------------------------


#: Machine-mode CSR set — the default for M-only cores.
MMODE_CSRS: tuple[PrivilegedReg, ...] = (
    PrivilegedReg.MVENDORID,
    PrivilegedReg.MARCHID,
    PrivilegedReg.MIMPID,
    PrivilegedReg.MHARTID,
    PrivilegedReg.MSTATUS,
    PrivilegedReg.MISA,
    PrivilegedReg.MEDELEG,
    PrivilegedReg.MIDELEG,
    PrivilegedReg.MIE,
    PrivilegedReg.MTVEC,
    PrivilegedReg.MCOUNTEREN,
    PrivilegedReg.MSCRATCH,
    PrivilegedReg.MEPC,
    PrivilegedReg.MCAUSE,
    PrivilegedReg.MTVAL,
    PrivilegedReg.MIP,
)

#: User-mode CSRs present in privileged targets.
UMODE_CSRS: tuple[PrivilegedReg, ...] = (
    PrivilegedReg.USTATUS,
    PrivilegedReg.UIE,
    PrivilegedReg.UTVEC,
    PrivilegedReg.USCRATCH,
    PrivilegedReg.UEPC,
    PrivilegedReg.UCAUSE,
    PrivilegedReg.UTVAL,
    PrivilegedReg.UIP,
)

#: Supervisor-mode CSRs present in privileged targets.
SMODE_CSRS: tuple[PrivilegedReg, ...] = (
    PrivilegedReg.SSTATUS,
    PrivilegedReg.SEDELEG,
    PrivilegedReg.SIDELEG,
    PrivilegedReg.SIE,
    PrivilegedReg.STVEC,
    PrivilegedReg.SCOUNTEREN,
    PrivilegedReg.SSCRATCH,
    PrivilegedReg.SEPC,
    PrivilegedReg.SCAUSE,
    PrivilegedReg.STVAL,
    PrivilegedReg.SIP,
    PrivilegedReg.SATP,
)


# ---------------------------------------------------------------------------
# Interrupt / exception presets
# ---------------------------------------------------------------------------


#: Minimal implemented interrupt set (M-only targets).
MMODE_INTERRUPTS: tuple[InterruptCause, ...] = (
    InterruptCause.M_SOFTWARE_INTR,
    InterruptCause.M_TIMER_INTR,
    InterruptCause.M_EXTERNAL_INTR,
)

#: Exception set for M-only targets.
MMODE_EXCEPTIONS: tuple[ExceptionCause, ...] = (
    ExceptionCause.INSTRUCTION_ACCESS_FAULT,
    ExceptionCause.ILLEGAL_INSTRUCTION,
    ExceptionCause.BREAKPOINT,
    ExceptionCause.LOAD_ADDRESS_MISALIGNED,
    ExceptionCause.LOAD_ACCESS_FAULT,
    ExceptionCause.ECALL_MMODE,
)

#: Full interrupt set for U/S/M-capable targets.
USM_INTERRUPTS: tuple[InterruptCause, ...] = (
    InterruptCause.U_SOFTWARE_INTR,
    InterruptCause.S_SOFTWARE_INTR,
    InterruptCause.M_SOFTWARE_INTR,
    InterruptCause.U_TIMER_INTR,
    InterruptCause.S_TIMER_INTR,
    InterruptCause.M_TIMER_INTR,
    InterruptCause.U_EXTERNAL_INTR,
    InterruptCause.S_EXTERNAL_INTR,
    InterruptCause.M_EXTERNAL_INTR,
)

#: Full exception set for U/S/M-capable targets.
USM_EXCEPTIONS: tuple[ExceptionCause, ...] = (
    ExceptionCause.INSTRUCTION_ADDRESS_MISALIGNED,
    ExceptionCause.INSTRUCTION_ACCESS_FAULT,
    ExceptionCause.ILLEGAL_INSTRUCTION,
    ExceptionCause.BREAKPOINT,
    ExceptionCause.LOAD_ADDRESS_MISALIGNED,
    ExceptionCause.LOAD_ACCESS_FAULT,
    ExceptionCause.STORE_AMO_ADDRESS_MISALIGNED,
    ExceptionCause.STORE_AMO_ACCESS_FAULT,
    ExceptionCause.ECALL_UMODE,
    ExceptionCause.ECALL_SMODE,
    ExceptionCause.ECALL_MMODE,
    ExceptionCause.INSTRUCTION_PAGE_FAULT,
    ExceptionCause.LOAD_PAGE_FAULT,
    ExceptionCause.STORE_AMO_PAGE_FAULT,
)


# ---------------------------------------------------------------------------
# Name-based lookup (used by :mod:`rvgen.targets.loader`)
# ---------------------------------------------------------------------------


#: Name → preset tuple mapping. Used by the YAML loader to resolve
#: references like ``implemented_csr: MMODE_CSRS``.
PRESETS: dict[str, tuple] = {
    "MMODE_CSRS": MMODE_CSRS,
    "UMODE_CSRS": UMODE_CSRS,
    "SMODE_CSRS": SMODE_CSRS,
    "MMODE_INTERRUPTS": MMODE_INTERRUPTS,
    "MMODE_EXCEPTIONS": MMODE_EXCEPTIONS,
    "USM_INTERRUPTS": USM_INTERRUPTS,
    "USM_EXCEPTIONS": USM_EXCEPTIONS,
}
