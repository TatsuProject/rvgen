"""Tests for the Smaia / Ssaia (Advanced Interrupt Architecture) CSRs.

AIA v1.0 ratified 2023-08 adds CSRs only — no new instructions. Tests
cover:

1. New :class:`PrivilegedReg` enum values land at the spec-mandated
   addresses (priv-arch v1.13 + AIA v1.0 §1.5).
2. The ``rv64gc_aia`` target advertises every Smaia + Ssaia CSR through
   ``implemented_csr``.
3. The ``rv64gch_aia`` target also pulls in the H-ext AIA additions
   (HVIEN / HVIPRIO* / VSISELECT / ...).
4. Presets (`SMAIA_CSRS`, `SSAIA_CSRS`, `HAIA_CSRS`) round-trip via
   :data:`PRESETS` for YAML-target use.
"""

from __future__ import annotations

import pytest

from rvgen.isa.enums import PrivilegedReg
from rvgen.targets import get_target
from rvgen.targets.presets import (
    HAIA_CSRS,
    PRESETS,
    SMAIA_CSRS,
    SSAIA_CSRS,
)


# ---------------------------------------------------------------------------
# CSR enum addresses match the AIA v1.0 spec.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,addr",
    [
        # Smaia (M-mode)
        ("MISELECT", 0x350),
        ("MIREG", 0x351),
        ("MTOPEI", 0x35C),
        ("MTOPI", 0xFB0),
        ("MVIEN", 0x308),
        ("MVIP", 0x309),
        # Ssaia (S-mode)
        ("SISELECT", 0x150),
        ("SIREG", 0x151),
        ("STOPEI", 0x15C),
        ("STOPI", 0xDB0),
        # H-ext AIA additions
        ("HVIEN", 0x608),
        ("HVICTL", 0x609),
        ("HVIPRIO1", 0x646),
        ("HVIPRIO2", 0x647),
        ("VSISELECT", 0x250),
        ("VSIREG", 0x251),
        ("VSTOPEI", 0x25C),
        ("VSTOPI", 0xEB0),
    ],
)
def test_aia_csr_address(name, addr):
    assert getattr(PrivilegedReg, name) == addr


# ---------------------------------------------------------------------------
# Preset tuples (used by YAML targets).
# ---------------------------------------------------------------------------


def test_smaia_preset_contents():
    assert PrivilegedReg.MISELECT in SMAIA_CSRS
    assert PrivilegedReg.MTOPEI in SMAIA_CSRS
    assert PrivilegedReg.MVIEN in SMAIA_CSRS


def test_ssaia_preset_contents():
    assert PrivilegedReg.SISELECT in SSAIA_CSRS
    assert PrivilegedReg.STOPI in SSAIA_CSRS


def test_haia_preset_contents():
    assert PrivilegedReg.HVIEN in HAIA_CSRS
    assert PrivilegedReg.VSTOPEI in HAIA_CSRS


def test_aia_presets_in_lookup_table():
    assert PRESETS["SMAIA_CSRS"] == SMAIA_CSRS
    assert PRESETS["SSAIA_CSRS"] == SSAIA_CSRS
    assert PRESETS["HAIA_CSRS"] == HAIA_CSRS


# ---------------------------------------------------------------------------
# Target wiring.
# ---------------------------------------------------------------------------


def test_rv64gc_aia_target_csr_set():
    t = get_target("rv64gc_aia")
    for csr in SMAIA_CSRS:
        assert csr in t.implemented_csr
    for csr in SSAIA_CSRS:
        assert csr in t.implemented_csr
    # H-ext-only CSRs should NOT be present.
    for csr in HAIA_CSRS:
        assert csr not in t.implemented_csr


def test_rv64gch_aia_target_csr_set():
    t = get_target("rv64gch_aia")
    # All three preset families present.
    for csr in SMAIA_CSRS + SSAIA_CSRS + HAIA_CSRS:
        assert csr in t.implemented_csr
