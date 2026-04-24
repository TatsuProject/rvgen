"""Tests for rvgen.targets."""

from __future__ import annotations

import pytest

from rvgen.isa.enums import (
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    SatpMode,
)
from rvgen.targets import BUILTIN_TARGETS, TargetCfg, get_target, target_names


def test_all_expected_targets_present():
    # riscv-dv's target/ tree + local extensions:
    #  * rv32ui              — bare no-CSR core.
    #  * rv32imc_zkn         — chipforge MCU ISA (RV32IMC + ratified Zkn crypto).
    #  * rv32imc_zkn_zks     — full ratified K-family including SM3/SM4.
    #  * rv64imc_zkn         — RV64 M-mode + AES64/SHA-512.
    #  * coralnpu            — Google Coral NPU v2 (Zve32x embedded vector).
    #  * rv32imc_zve32x      — baseline Zve32x reference.
    #  * rv32imfc_zve32f     — Zve32f (embedded + FP32 vector).
    #  * rv64imc_zve64x      — Zve64x (embedded + SEW=64 integer).
    #  * rv64imafdc_zve64d   — Zve64d (full embedded + FP64 vector).
    expected = {
        "rv32i", "rv32ia", "rv32iac", "rv32ic", "rv32if", "rv32im",
        "rv32imac", "rv32imafdc", "rv32imc", "rv32imcb", "rv32imc_sv32",
        "rv32ui", "rv32imc_zkn", "rv32imc_zkn_zks", "rv32imckf",
        "rv64imc_zkn",
        "rv64gc", "rv64gcv", "rv64imafdc", "rv64imc", "rv64imcb",
        "ml", "multi_harts",
        "coralnpu", "rv32imc_zve32x", "rv32imfc_zve32f",
        "rv64imc_zve64x", "rv64imafdc_zve64d",
    }
    # Compare against BUILTIN_TARGETS rather than target_names() so the
    # test is insensitive to whichever user-area targets happen to be
    # discoverable at test time (e.g. user/targets/chipforge-mcu.yaml).
    assert set(BUILTIN_TARGETS) == expected


def test_rv32i_config():
    t = get_target("rv32i")
    assert t.xlen == 32
    assert t.supported_isa == (RiscvInstrGroup.RV32I,)
    assert t.supported_privileged_mode == (PrivilegedMode.MACHINE_MODE,)
    assert t.satp_mode == SatpMode.BARE
    assert t.num_harts == 1


def test_rv32imc_csrs_mmode_only():
    t = get_target("rv32imc")
    assert PrivilegedReg.MSTATUS in t.implemented_csr
    assert PrivilegedReg.MTVEC in t.implemented_csr
    # No supervisor CSRs.
    assert PrivilegedReg.SSTATUS not in t.implemented_csr
    assert PrivilegedReg.SATP not in t.implemented_csr


def test_rv64gc_privileged():
    t = get_target("rv64gc")
    assert t.xlen == 64
    assert t.satp_mode == SatpMode.SV39
    assert t.supported_privileged_mode == (
        PrivilegedMode.USER_MODE,
        PrivilegedMode.SUPERVISOR_MODE,
        PrivilegedMode.MACHINE_MODE,
    )
    assert PrivilegedReg.SSTATUS in t.implemented_csr
    assert PrivilegedReg.SATP in t.implemented_csr
    assert PrivilegedReg.USTATUS in t.implemented_csr
    assert t.support_sfence is True


def test_rv64gcv_has_vector():
    t = get_target("rv64gcv")
    assert RiscvInstrGroup.RVV in t.supported_isa
    assert t.vector_extension_enable is True
    assert t.vlen == 512
    assert t.elen == 32


def test_rv32imc_sv32_has_umode():
    t = get_target("rv32imc_sv32")
    assert t.satp_mode == SatpMode.SV32
    assert PrivilegedMode.USER_MODE in t.supported_privileged_mode
    assert PrivilegedReg.USTATUS in t.implemented_csr


def test_multi_harts_is_two_harts():
    assert get_target("multi_harts").num_harts == 2


def test_unknown_target_raises():
    with pytest.raises(KeyError, match="Unknown target"):
        get_target("rv_fictional")


def test_targetcfg_is_frozen():
    t = get_target("rv32i")
    with pytest.raises(AttributeError):
        t.xlen = 64  # type: ignore[misc]


def test_rv32im_marks_high_mul_unsupported():
    t = get_target("rv32im")
    from rvgen.isa.enums import RiscvInstrName as N
    # riscv-dv's rv32im excludes the high-multiply variants.
    for name in (N.MUL, N.MULH, N.MULHSU, N.MULHU):
        assert name in t.unsupported_instr
