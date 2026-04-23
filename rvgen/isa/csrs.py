"""Per-CSR field descriptor table, ported from riscv_privil_reg.sv.

For each CSR in :class:`~rvgen.isa.enums.PrivilegedReg` the
``riscv-dv`` ``init_reg`` method builds a sequence of
``(name, bit_width, reg_field_access_t)`` triples that describes its layout.
We reproduce that here as :func:`get_csr_fields`.

The field list depends on XLEN (e.g., MSTATUS gains UXL/SXL on RV64) so each
descriptor is computed lazily given the target's XLEN.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rvgen.isa.enums import (
    PrivilegedLevel,
    PrivilegedReg,
    RegFieldAccess,
)


@dataclass(frozen=True, slots=True)
class CsrField:
    """A single field in a CSR layout.

    ``width`` is an int; the caller is responsible for resolving XLEN before
    constructing the field. Fields are listed LSB-first (riscv-dv's
    ``add_field`` call order appends from LSB).
    """

    name: str
    width: int
    access: RegFieldAccess


#: Privilege level derived from CSR address bits [9:8].
#:
#: riscv-dv's ``riscv_privil_reg.init_reg`` sets ``privil_level`` explicitly.
#: Values always match what the architectural privilege bits imply, so we
#: compute from the address — this also catches any CSR not enumerated in
#: ``init_reg`` (hypervisor, virtual-supervisor, debug, …).


def privilege_level(csr: PrivilegedReg) -> PrivilegedLevel:
    """Return the privilege level encoded in the CSR's address (bits [9:8])."""
    bits = (csr.value >> 8) & 0x3
    if bits == 0b00:
        return PrivilegedLevel.U_LEVEL
    if bits == 0b01:
        return PrivilegedLevel.S_LEVEL
    if bits == 0b11:
        return PrivilegedLevel.M_LEVEL
    # bits == 0b10 → hypervisor / virtual. Treat as S_LEVEL for non-machine
    # access checks; riscv-dv doesn't currently exercise this path.
    return PrivilegedLevel.S_LEVEL


# Private helper: a field list builder signature — given XLEN returns a list
# of CsrField. We register one per CSR that riscv_privil_reg.init_reg covers.
_FieldsBuilder = Callable[[int], list[CsrField]]


# -- Helpers for building field lists -------------------------------------


def _warl(name: str, width: int) -> CsrField:
    return CsrField(name, width, RegFieldAccess.WARL)


def _wlrl(name: str, width: int) -> CsrField:
    return CsrField(name, width, RegFieldAccess.WLRL)


def _wpri(name: str, width: int) -> CsrField:
    return CsrField(name, width, RegFieldAccess.WPRI)


def _mstatus(xlen: int) -> list[CsrField]:
    f = [
        _warl("UIE", 1),
        _warl("SIE", 1),
        _wpri("WPRI0", 1),
        _warl("MIE", 1),
        _warl("UPIE", 1),
        _warl("SPIE", 1),
        _wpri("WPRI1", 1),
        _warl("MPIE", 1),
        _wlrl("SPP", 1),
        _warl("VS", 2),
        _wlrl("MPP", 2),
        _warl("FS", 2),
        _warl("XS", 2),
        _warl("MPRV", 1),
        _warl("SUM", 1),
        _warl("MXR", 1),
        _warl("TVM", 1),
        _warl("TW", 1),
        _warl("TSR", 1),
    ]
    if xlen == 32:
        f.append(_wpri("WPRI3", 8))
    else:
        f += [
            _wpri("WPRI3", 9),
            _warl("UXL", 2),
            _warl("SXL", 2),
            _wpri("WPRI4", xlen - 37),
        ]
    f.append(_warl("SD", 1))
    return f


def _sstatus(xlen: int) -> list[CsrField]:
    f = [
        _warl("UIE", 1),
        _warl("SIE", 1),
        _wpri("WPRI0", 2),
        _warl("UPIE", 1),
        _warl("SPIE", 1),
        _wpri("WPRI1", 2),
        _wlrl("SPP", 1),
        _wpri("WPRI2", 4),
        _warl("FS", 2),
        _warl("XS", 2),
        _wpri("WPRI3", 1),
        _warl("SUM", 1),
        _warl("MXR", 1),
    ]
    if xlen == 32:
        f.append(_wpri("WPRI4", 11))
    else:
        f += [
            _wpri("WPRI4", 12),
            _warl("UXL", 2),
            _wpri("WPRI4", xlen - 35),
        ]
    f.append(_warl("SD", 1))
    return f


def _ustatus(xlen: int) -> list[CsrField]:
    return [
        _warl("UIE", 1),
        _wpri("WPRI0", 3),
        _warl("UPIE", 1),
        _wpri("WPRI1", xlen - 5),
    ]


def _xtvec(xlen: int) -> list[CsrField]:
    return [_warl("MODE", 2), _warl("BASE", xlen - 2)]


def _xtvec_s_u(xlen: int) -> list[CsrField]:
    # STVEC/UTVEC declare BASE as WLRL.
    return [_warl("MODE", 2), _wlrl("BASE", xlen - 2)]


def _medeleg(xlen: int) -> list[CsrField]:
    return [
        _warl("IAM", 1), _warl("IAF", 1), _warl("ILGL", 1), _warl("BREAK", 1),
        _warl("LAM", 1), _warl("LAF", 1), _warl("SAM", 1), _warl("SAF", 1),
        _warl("ECFU", 1), _warl("ECFS", 1), _warl("WARL0", 1), _warl("ECFM", 1),
        _warl("IPF", 1), _warl("LPF", 1), _warl("WARL1", 1), _warl("SPF", 1),
        _warl("WARL2", xlen - 16),
    ]


def _sedeleg(xlen: int) -> list[CsrField]:
    return [
        _warl("IAM", 1), _warl("IAF", 1), _warl("II", 1), _wpri("WPRI0", 1),
        _warl("LAM", 1), _warl("LAF", 1), _warl("SAM", 1), _warl("SAF", 1),
        _warl("ECFU", 1), _wpri("WPRI1", 1), _warl("WARL0", 1), _wpri("WPRI2", 1),
        _warl("IPF", 1), _warl("LPF", 1), _warl("WARL1", 1), _warl("SPF", 1),
        _warl("WARL2", xlen - 16),
    ]


def _mideleg(xlen: int) -> list[CsrField]:
    return [
        _warl("USIP", 1), _warl("SSIP", 1), _warl("WARL0", 1), _warl("MSIP", 1),
        _warl("UTIP", 1), _warl("STIP", 1), _warl("WARL1", 1), _warl("MTIP", 1),
        _warl("UEIP", 1), _warl("SEIP", 1), _warl("WARL2", 1), _warl("MEIP", 1),
        _warl("WARL3", xlen - 12),
    ]


def _sideleg(xlen: int) -> list[CsrField]:
    return [
        _warl("USIP", 1), _warl("SSIP", 1), _warl("WARL0", 1), _wpri("WPRI0", 1),
        _warl("UTIP", 1), _warl("STIP", 1), _warl("WARL1", 1), _wpri("WPRI1", 1),
        _warl("UEIP", 1), _warl("SEIP", 1), _warl("WARL2", 1), _wpri("WPRI2", 1),
        _warl("WARL3", xlen - 12),
    ]


def _mip(xlen: int) -> list[CsrField]:
    return [
        _warl("USIP", 1), _warl("SSIP", 1), _wpri("WPRI0", 1), _warl("MSIP", 1),
        _warl("UTIP", 1), _warl("STIP", 1), _wpri("WPRI1", 1), _warl("MTIP", 1),
        _warl("UEIP", 1), _warl("SEIP", 1), _wpri("WPRI2", 1), _warl("MEIP", 1),
        _wpri("WPRI3", xlen - 12),
    ]


def _mie(xlen: int) -> list[CsrField]:
    return [
        _warl("USIE", 1), _warl("SSIE", 1), _wpri("WPRI0", 1), _warl("MSIE", 1),
        _warl("UTIE", 1), _warl("STIE", 1), _wpri("WPRI1", 1), _warl("MTIE", 1),
        _warl("UEIE", 1), _warl("SEIE", 1), _wpri("WPRI2", 1), _warl("MEIE", 1),
        _wpri("WPRI3", xlen - 12),
    ]


def _sip(xlen: int) -> list[CsrField]:
    return [
        _warl("USIP", 1), _warl("SSIP", 1), _wpri("WPRI0", 2),
        _warl("UTIP", 1), _warl("STIP", 1), _wpri("WPRI1", 2),
        _warl("UEIP", 1), _warl("SEIP", 1), _wpri("WPRI2", 2),
        _wpri("WPRI3", xlen - 12),
    ]


def _sie(xlen: int) -> list[CsrField]:
    return [
        _warl("USIE", 1), _warl("SSIE", 1), _wpri("WPRI0", 2),
        _warl("UTIE", 1), _warl("STIE", 1), _wpri("WPRI1", 2),
        _warl("UEIE", 1), _warl("SEIE", 1),
        _wpri("WPRI2", xlen - 10),
    ]


def _uie(xlen: int) -> list[CsrField]:
    return [
        _warl("USIE", 1), _wpri("WPRI0", 3),
        _warl("UTIE", 1), _wpri("WPRI1", 3),
        _warl("UEIE", 1),
        _wpri("WPRI2", xlen - 9),
    ]


def _uip(xlen: int) -> list[CsrField]:
    return [
        _warl("USIP", 1), _wpri("WPRI0", 3),
        _warl("UTIP", 1), _wpri("WPRI1", 3),
        _warl("UEIP", 1),
        _wpri("WPRI2", xlen - 9),
    ]


def _counteren(xlen: int) -> list[CsrField]:
    f = [_warl("CY", 1), _warl("TM", 1), _warl("IR", 1)]
    f += [_warl(f"HPM{i}", 1) for i in range(3, 32)]
    if xlen == 64:
        f.append(_wpri("WPRI", 32))
    return f


def _xcause(xlen: int) -> list[CsrField]:
    return [
        _wlrl("CODE", 4),
        _wlrl("WLRL", xlen - 5),
        _warl("INTERRUPT", 1),
    ]


def _satp(xlen: int) -> list[CsrField]:
    if xlen == 32:
        return [_warl("PPN", 22), _warl("ASID", 9), _warl("MODE", 1)]
    return [_warl("PPN", 44), _warl("ASID", 16), _warl("MODE", 4)]


def _misa(xlen: int) -> list[CsrField]:
    return [_warl("WARL0", 26), _wlrl("WLRL", xlen - 28), _warl("MXL", 2)]


def _mvendorid(xlen: int) -> list[CsrField]:
    return [_wpri("OFFSET", 7), _wpri("BANK", xlen - 7)]


def _mseccfg(xlen: int) -> list[CsrField]:
    del xlen
    return [_warl("MML", 1), _warl("MMWP", 1), _warl("RLB", 1)]


def _pmpcfg_lo(xlen: int, start_idx: int, extra_on_rv64: bool) -> list[CsrField]:
    """PMPCFG layout. ``start_idx`` selects the four consecutive regions; on
    RV64 we additionally pack the next four.
    """
    f = [_warl(f"PMP{start_idx + i}CFG", 8) for i in range(4)]
    if extra_on_rv64 and xlen == 64:
        f += [_warl(f"PMP{start_idx + 4 + i}CFG", 8) for i in range(4)]
    return f


def _pmpaddr(xlen: int) -> list[CsrField]:
    if xlen == 64:
        return [_warl("ADDRESS", 54), _warl("WARL", 10)]
    return [_warl("ADDRESS", 32)]


def _whole_xlen(name: str, access: RegFieldAccess = RegFieldAccess.WARL) -> _FieldsBuilder:
    """Helper: a CSR that is one field spanning the full XLEN."""

    def build(xlen: int) -> list[CsrField]:
        return [CsrField(name, xlen, access)]

    return build


def _whole_64_wpri(name: str) -> _FieldsBuilder:
    def build(xlen: int) -> list[CsrField]:
        del xlen
        return [CsrField(name, 64, RegFieldAccess.WPRI)]

    return build


def _whole_32_wpri(name: str) -> _FieldsBuilder:
    def build(xlen: int) -> list[CsrField]:
        del xlen
        return [CsrField(name, 32, RegFieldAccess.WPRI)]

    return build


# ---------------------------------------------------------------------------
# Dispatch table: PrivilegedReg -> builder
# ---------------------------------------------------------------------------


_BUILDERS: dict[PrivilegedReg, _FieldsBuilder] = {
    # Machine-mode
    PrivilegedReg.MISA: _misa,
    PrivilegedReg.MVENDORID: _mvendorid,
    PrivilegedReg.MARCHID: _whole_xlen("ARCHITECTURE_ID", RegFieldAccess.WPRI),
    PrivilegedReg.MIMPID: _whole_xlen("IMPLEMENTATION", RegFieldAccess.WPRI),
    PrivilegedReg.MHARTID: _whole_xlen("HART_ID", RegFieldAccess.WPRI),
    PrivilegedReg.MSTATUS: _mstatus,
    PrivilegedReg.MTVEC: _xtvec,
    PrivilegedReg.MEDELEG: _medeleg,
    PrivilegedReg.MIDELEG: _mideleg,
    PrivilegedReg.MIP: _mip,
    PrivilegedReg.MIE: _mie,
    PrivilegedReg.MCYCLE: _whole_64_wpri("MCYCLE"),
    PrivilegedReg.MINSTRET: _whole_64_wpri("MINSTRET"),
    PrivilegedReg.MCYCLEH: _whole_32_wpri("MCYCLEH"),
    PrivilegedReg.MINSTRETH: _whole_32_wpri("MINSTRETH"),
    PrivilegedReg.MCOUNTEREN: _counteren,
    PrivilegedReg.MSCRATCH: _whole_xlen("MSCRATCH"),
    PrivilegedReg.MEPC: _whole_xlen("BASE"),
    PrivilegedReg.MCAUSE: _xcause,
    PrivilegedReg.MTVAL: _whole_xlen("VALUE"),
    PrivilegedReg.MSECCFG: _mseccfg,
    # PMPCFG: RV32 has CFG0..3 (and optionally 1/3 as even-numbered extensions);
    # RV64 packs 8 per CSR and skips the odd-indexed ones.
    PrivilegedReg.PMPCFG0: lambda x: _pmpcfg_lo(x, 0, extra_on_rv64=True),
    PrivilegedReg.PMPCFG1: lambda x: _pmpcfg_lo(x, 4, extra_on_rv64=False) if x == 32 else _pmpcfg_lo(x, 4, extra_on_rv64=False),
    PrivilegedReg.PMPCFG2: lambda x: _pmpcfg_lo(x, 8, extra_on_rv64=True),
    PrivilegedReg.PMPCFG3: lambda x: _pmpcfg_lo(x, 12, extra_on_rv64=False) if x == 32 else [],
    # Supervisor-mode
    PrivilegedReg.SSTATUS: _sstatus,
    PrivilegedReg.STVEC: _xtvec_s_u,
    PrivilegedReg.SEDELEG: _sedeleg,
    PrivilegedReg.SIDELEG: _sideleg,
    PrivilegedReg.SIP: _sip,
    PrivilegedReg.SIE: _sie,
    PrivilegedReg.SCOUNTEREN: _counteren,
    PrivilegedReg.SSCRATCH: _whole_xlen("SSCRATCH"),
    PrivilegedReg.SEPC: _whole_xlen("BASE"),
    PrivilegedReg.SCAUSE: _xcause,
    PrivilegedReg.STVAL: _whole_xlen("VALUE"),
    PrivilegedReg.SATP: _satp,
    # User-mode
    PrivilegedReg.USTATUS: _ustatus,
    PrivilegedReg.UTVEC: _xtvec_s_u,
    PrivilegedReg.UIE: _uie,
    PrivilegedReg.UIP: _uip,
    PrivilegedReg.USCRATCH: _whole_xlen("MSCRATCH"),  # SV uses "MSCRATCH" here — mirrored faithfully
    PrivilegedReg.UEPC: _whole_xlen("BASE"),
    PrivilegedReg.UCAUSE: _xcause,
    PrivilegedReg.UTVAL: _whole_xlen("VALUE"),
}


# Populate PMPADDR0..PMPADDR15 with identical layout (SV uses a case-range).
for _pmp in (
    PrivilegedReg.PMPADDR0, PrivilegedReg.PMPADDR1, PrivilegedReg.PMPADDR2, PrivilegedReg.PMPADDR3,
    PrivilegedReg.PMPADDR4, PrivilegedReg.PMPADDR5, PrivilegedReg.PMPADDR6, PrivilegedReg.PMPADDR7,
    PrivilegedReg.PMPADDR8, PrivilegedReg.PMPADDR9, PrivilegedReg.PMPADDR10, PrivilegedReg.PMPADDR11,
    PrivilegedReg.PMPADDR12, PrivilegedReg.PMPADDR13, PrivilegedReg.PMPADDR14, PrivilegedReg.PMPADDR15,
):
    _BUILDERS[_pmp] = _pmpaddr


# Populate MHPMCOUNTER3..MHPMCOUNTER31 and MHPMEVENT3..MHPMEVENT31 with
# "one XLEN-wide WARL field named after the register".
def _mhpm_field_builder(csr_name: str) -> _FieldsBuilder:
    def build(xlen: int) -> list[CsrField]:
        return [CsrField(csr_name, xlen, RegFieldAccess.WARL)]

    return build


for _csr in PrivilegedReg:
    name = _csr.name
    if name.startswith("MHPMCOUNTER") and not name.endswith("H"):
        _BUILDERS[_csr] = _mhpm_field_builder(name)
    elif name.startswith("MHPMCOUNTER") and name.endswith("H"):
        _BUILDERS[_csr] = _whole_32_wpri(name) if False else _mhpm_field_builder(name)
    elif name.startswith("MHPMEVENT"):
        _BUILDERS[_csr] = _mhpm_field_builder(name)


def get_csr_fields(csr: PrivilegedReg, xlen: int) -> list[CsrField]:
    """Return the ordered field layout of ``csr`` for the given XLEN.

    Raises ``KeyError`` if the CSR has no registered layout yet. The set of
    CSRs riscv-dv's ``init_reg`` actually knows about is a subset of the full
    ``privileged_reg_t`` enum; unknown CSRs fall through to a ``uvm_fatal`` in
    the SV source.
    """
    return _BUILDERS[csr](xlen)


def has_csr_layout(csr: PrivilegedReg) -> bool:
    """Whether a field layout is registered for ``csr``."""
    return csr in _BUILDERS
