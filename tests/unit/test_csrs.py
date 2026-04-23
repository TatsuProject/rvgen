"""Tests for rvgen.isa.csrs — CSR field layouts."""

from __future__ import annotations

import pytest

from rvgen.isa.csrs import (
    CsrField,
    get_csr_fields,
    has_csr_layout,
    privilege_level,
)
from rvgen.isa.enums import (
    PrivilegedLevel,
    PrivilegedReg,
    RegFieldAccess,
)


def _by_name(fields: list[CsrField]) -> dict[str, CsrField]:
    return {f.name: f for f in fields}


# ---------------------------------------------------------------------------
# Privilege level
# ---------------------------------------------------------------------------


def test_privilege_level_machine():
    assert privilege_level(PrivilegedReg.MSTATUS) == PrivilegedLevel.M_LEVEL
    assert privilege_level(PrivilegedReg.MEPC) == PrivilegedLevel.M_LEVEL
    assert privilege_level(PrivilegedReg.MHARTID) == PrivilegedLevel.M_LEVEL


def test_privilege_level_supervisor():
    assert privilege_level(PrivilegedReg.SSTATUS) == PrivilegedLevel.S_LEVEL
    assert privilege_level(PrivilegedReg.SATP) == PrivilegedLevel.S_LEVEL


def test_privilege_level_user():
    assert privilege_level(PrivilegedReg.USTATUS) == PrivilegedLevel.U_LEVEL
    assert privilege_level(PrivilegedReg.FFLAGS) == PrivilegedLevel.U_LEVEL
    assert privilege_level(PrivilegedReg.CYCLE) == PrivilegedLevel.U_LEVEL


# ---------------------------------------------------------------------------
# MSTATUS layout
# ---------------------------------------------------------------------------


def test_mstatus_field_order_rv32():
    fields = get_csr_fields(PrivilegedReg.MSTATUS, xlen=32)
    names = [f.name for f in fields]
    # First 19 fields are XLEN-independent.
    assert names[:19] == [
        "UIE", "SIE", "WPRI0", "MIE", "UPIE", "SPIE", "WPRI1", "MPIE",
        "SPP", "VS", "MPP", "FS", "XS", "MPRV", "SUM", "MXR", "TVM", "TW", "TSR",
    ]
    # RV32 adds an 8-bit WPRI3 and then SD.
    assert names[19:] == ["WPRI3", "SD"]
    assert fields[-2].width == 8
    assert fields[-1].name == "SD" and fields[-1].width == 1


def test_mstatus_rv64_has_uxl_sxl():
    fields = get_csr_fields(PrivilegedReg.MSTATUS, xlen=64)
    names = [f.name for f in fields]
    assert "UXL" in names
    assert "SXL" in names
    # Total width must equal XLEN.
    total_width = sum(f.width for f in fields)
    assert total_width == 64


def test_mstatus_total_width_rv32():
    total = sum(f.width for f in get_csr_fields(PrivilegedReg.MSTATUS, xlen=32))
    assert total == 32


# ---------------------------------------------------------------------------
# MISA
# ---------------------------------------------------------------------------


def test_misa_layout():
    fields = get_csr_fields(PrivilegedReg.MISA, xlen=32)
    names = [f.name for f in fields]
    assert names == ["WARL0", "WLRL", "MXL"]
    assert fields[0].width == 26
    assert fields[1].width == 32 - 28  # 4
    assert fields[2].width == 2
    assert fields[0].access == RegFieldAccess.WARL
    assert fields[2].access == RegFieldAccess.WARL


def test_misa_total_width_matches_xlen():
    assert sum(f.width for f in get_csr_fields(PrivilegedReg.MISA, xlen=32)) == 32
    assert sum(f.width for f in get_csr_fields(PrivilegedReg.MISA, xlen=64)) == 64


# ---------------------------------------------------------------------------
# SATP
# ---------------------------------------------------------------------------


def test_satp_rv32():
    fields = get_csr_fields(PrivilegedReg.SATP, xlen=32)
    assert [f.name for f in fields] == ["PPN", "ASID", "MODE"]
    assert [f.width for f in fields] == [22, 9, 1]
    assert sum(f.width for f in fields) == 32


def test_satp_rv64():
    fields = get_csr_fields(PrivilegedReg.SATP, xlen=64)
    assert [f.name for f in fields] == ["PPN", "ASID", "MODE"]
    assert [f.width for f in fields] == [44, 16, 4]
    assert sum(f.width for f in fields) == 64


# ---------------------------------------------------------------------------
# MTVEC
# ---------------------------------------------------------------------------


def test_mtvec_layout():
    fields = get_csr_fields(PrivilegedReg.MTVEC, xlen=64)
    assert [f.name for f in fields] == ["MODE", "BASE"]
    assert fields[0].width == 2
    assert fields[1].width == 62
    assert fields[0].access == RegFieldAccess.WARL
    assert fields[1].access == RegFieldAccess.WARL


# ---------------------------------------------------------------------------
# MEDELEG / MIDELEG
# ---------------------------------------------------------------------------


def test_medeleg_has_ipf_lpf_spf():
    fields = get_csr_fields(PrivilegedReg.MEDELEG, xlen=32)
    by_name = _by_name(fields)
    for required in ("IAM", "IAF", "ILGL", "BREAK", "LAM", "LAF", "SAM", "SAF",
                     "ECFU", "ECFS", "ECFM", "IPF", "LPF", "SPF"):
        assert required in by_name, f"MEDELEG missing {required}"


def test_mideleg_bit_positions():
    fields = get_csr_fields(PrivilegedReg.MIDELEG, xlen=64)
    names = [f.name for f in fields]
    # Expected order of 1-bit fields (positions 0..11), then a wide WARL3 tail.
    assert names[:12] == [
        "USIP", "SSIP", "WARL0", "MSIP", "UTIP", "STIP", "WARL1", "MTIP",
        "UEIP", "SEIP", "WARL2", "MEIP",
    ]
    assert names[-1] == "WARL3"


# ---------------------------------------------------------------------------
# PMP configuration
# ---------------------------------------------------------------------------


def test_pmpcfg0_rv32():
    fields = get_csr_fields(PrivilegedReg.PMPCFG0, xlen=32)
    names = [f.name for f in fields]
    assert names == ["PMP0CFG", "PMP1CFG", "PMP2CFG", "PMP3CFG"]
    assert sum(f.width for f in fields) == 32


def test_pmpcfg0_rv64_has_eight_entries():
    fields = get_csr_fields(PrivilegedReg.PMPCFG0, xlen=64)
    names = [f.name for f in fields]
    assert names == [f"PMP{i}CFG" for i in range(8)]
    assert sum(f.width for f in fields) == 64


def test_pmpaddr_width_rv32_vs_rv64():
    rv32 = get_csr_fields(PrivilegedReg.PMPADDR0, xlen=32)
    assert rv32 == [CsrField("ADDRESS", 32, RegFieldAccess.WARL)]

    rv64 = get_csr_fields(PrivilegedReg.PMPADDR0, xlen=64)
    assert rv64[0].name == "ADDRESS"
    assert rv64[0].width == 54
    assert rv64[1].name == "WARL"
    assert rv64[1].width == 10


# ---------------------------------------------------------------------------
# MCAUSE / SCAUSE / UCAUSE share the layout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("csr", [PrivilegedReg.MCAUSE, PrivilegedReg.SCAUSE, PrivilegedReg.UCAUSE])
def test_xcause_layout(csr):
    fields = get_csr_fields(csr, xlen=32)
    names = [f.name for f in fields]
    # SV layout: CODE (4 WLRL), WLRL (XLEN-5), INTERRUPT (1 WARL).
    # Total: 4 + (32-5) + 1 = 32.
    assert names == ["CODE", "WLRL", "INTERRUPT"]
    assert sum(f.width for f in fields) == 32


# ---------------------------------------------------------------------------
# has_csr_layout / KeyError for CSRs riscv-dv init_reg doesn't handle.
# ---------------------------------------------------------------------------


def test_has_csr_layout_for_mstatus():
    assert has_csr_layout(PrivilegedReg.MSTATUS) is True


def test_unknown_csr_raises():
    # Hypervisor CSRs are enumerated but not populated in riscv_privil_reg.
    assert has_csr_layout(PrivilegedReg.HSTATUS) is False
    with pytest.raises(KeyError):
        get_csr_fields(PrivilegedReg.HSTATUS, xlen=64)
