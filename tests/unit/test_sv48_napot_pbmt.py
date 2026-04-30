"""Tests for Sv48 + Svnapot + Svpbmt encoding paths."""

from __future__ import annotations

import pytest

from rvgen.config import Config
from rvgen.isa.enums import PrivilegedMode, PtePermission, RiscvReg, SatpMode
from rvgen.privileged.paging import (
    Pte,
    build_default_page_tables,
    gen_setup_satp,
    is_paging_enabled,
)
from rvgen.targets.builtin import BUILTIN_TARGETS


# ---------- Sv48 satp mode bit ----------


def test_sv48_satp_mode_field_is_9():
    cfg = Config(target=BUILTIN_TARGETS["rv64gc_sv48"])
    cfg.init_privileged_mode = PrivilegedMode.SUPERVISOR_MODE
    out = gen_setup_satp(cfg, RiscvReg.T0)
    text = "\n".join(out)
    # SV48 mode = 9 → MODE bits = 9 << 60 = 0x9000_0000_0000_0000.
    assert "0x9000000000000000" in text


def test_sv48_target_has_satp_mode_sv48():
    t = BUILTIN_TARGETS["rv64gc_sv48"]
    assert t.satp_mode == SatpMode.SV48


def test_sv48_paging_enabled_with_supervisor_boot():
    cfg = Config(target=BUILTIN_TARGETS["rv64gc_sv48"])
    cfg.init_privileged_mode = PrivilegedMode.SUPERVISOR_MODE
    assert is_paging_enabled(cfg) is True


def test_sv48_paging_off_with_machine_boot():
    cfg = Config(target=BUILTIN_TARGETS["rv64gc_sv48"])
    assert is_paging_enabled(cfg) is False


# ---------- Sv48 topology + emit ----------


def test_sv48_default_table_count_15():
    pl = build_default_page_tables(SatpMode.SV48, PrivilegedMode.SUPERVISOR_MODE)
    assert len(pl.tables) == 15


def test_sv48_data_section_emits_15_tables():
    pl = build_default_page_tables(SatpMode.SV48, PrivilegedMode.SUPERVISOR_MODE)
    section = pl.gen_data_section(hart=0, num_harts=1)
    table_labels = [s for s in section if s.startswith("page_table_") and s.endswith(":")]
    assert len(table_labels) == 15


# ---------- Svnapot encoding ----------


def test_pte_napot_bit_is_bit_63():
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1, napot=1)
    bits = p.pack(SatpMode.SV39)
    assert (bits >> 63) & 1 == 1


def test_pte_napot_default_zero():
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1)
    bits = p.pack(SatpMode.SV39)
    assert (bits >> 63) & 1 == 0


def test_pte_napot_works_on_sv48():
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1, napot=1)
    bits = p.pack(SatpMode.SV48)
    assert (bits >> 63) & 1 == 1


# ---------- Svpbmt encoding ----------


def test_pte_pbmt_bits_at_61_62():
    # pbmt=1 (NC) → bit 61 set, bit 62 clear.
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1, pbmt=1)
    bits = p.pack(SatpMode.SV39)
    assert (bits >> 61) & 0b11 == 0b01


def test_pte_pbmt_io_value_2():
    # pbmt=2 (IO) → bit 62 set, bit 61 clear.
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1, pbmt=2)
    bits = p.pack(SatpMode.SV39)
    assert (bits >> 61) & 0b11 == 0b10


def test_pte_pbmt_default_zero():
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1)
    bits = p.pack(SatpMode.SV39)
    assert (bits >> 61) & 0b11 == 0


def test_pte_napot_and_pbmt_independent():
    # Both set — top 3 bits = napot(1) | pbmt(IO=2 << 61).
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x1,
            napot=1, pbmt=2)
    bits = p.pack(SatpMode.SV39)
    assert (bits >> 63) & 1 == 1
    assert (bits >> 61) & 0b11 == 0b10


def test_pte_napot_pbmt_dont_change_low_bits():
    p1 = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x80)
    p2 = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=1, ppn0=0x80,
             napot=1, pbmt=2)
    # Low 60 bits should match.
    assert (p1.pack(SatpMode.SV39) & ((1 << 60) - 1)) == \
           (p2.pack(SatpMode.SV39) & ((1 << 60) - 1))
