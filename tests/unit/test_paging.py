"""Tests for rvgen.privileged.paging — PTE packing + topology + asm emit."""

from __future__ import annotations

import pytest

from rvgen.config import Config
from rvgen.isa.enums import PrivilegedMode, PtePermission, RiscvReg, SatpMode
from rvgen.privileged.paging import (
    LINK_PTE_PER_TABLE,
    SUPER_LEAF_PTE_PER_TABLE,
    PageTable,
    PageTableList,
    Pte,
    build_default_page_tables,
    gen_setup_satp,
    is_paging_enabled,
)
from rvgen.targets.builtin import BUILTIN_TARGETS


# ---------- PTE packing ----------


def test_pte_packs_basic_leaf_sv39():
    # Valid leaf with rwx + a + d, ppn0 = 0x80000 (i.e. PA = 0x80000_000).
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, u=0, a=1, d=1, ppn0=0x80000)
    bits = p.pack(SatpMode.SV39)
    # Low 10 bits: rsw=0|d=1<<7|a=1<<6|g=0|u=0|xwr=7<<1|v=1 = 0xCF
    # → low byte: ((0)<<8)|(1<<7)|(1<<6)|(0<<5)|(0<<4)|(7<<1)|(1) = 0xCF
    assert (bits & 0x3FF) == 0xCF
    # ppn0 lives in bits 10..18 (9 bits wide for SV39).
    assert ((bits >> 10) & 0x1FF) == 0x80000 & 0x1FF


def test_pte_packs_link_sv39_xwr_zero():
    p = Pte(v=1, xwr=PtePermission.NEXT_LEVEL_PAGE, u=0, a=0, d=0)
    bits = p.pack(SatpMode.SV39)
    # Low 10 bits: just v=1 set; xwr/a/d/u all 0.
    assert (bits & 0x3FF) == 0x1


def test_pte_packs_invalid():
    p = Pte(v=0, xwr=PtePermission.NEXT_LEVEL_PAGE, u=0, a=0, d=0)
    bits = p.pack(SatpMode.SV39)
    assert bits == 0


def test_pte_packs_sv32_layout():
    # SV32 PTE: 32 bits, ppn1=12, ppn0=10.
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, u=0, a=1, d=1,
            ppn0=0x100, ppn1=0x80)
    bits = p.pack(SatpMode.SV32)
    # Layout: {ppn1[11:0], ppn0[9:0], rsw[1:0], d, a, g, u, xwr[2:0], v}
    # ppn0 in bits 10..19, ppn1 in bits 20..31.
    assert ((bits >> 10) & 0x3FF) == 0x100
    assert ((bits >> 20) & 0xFFF) == 0x80
    assert (bits & 1) == 1   # v
    # XLEN=32: must fit in 32 bits.
    assert bits < (1 << 32)


def test_pte_packs_a_d_and_xwr_consistent():
    # Verify the bit positions of a (bit 6) and d (bit 7).
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=1, d=0)
    assert (p.pack(SatpMode.SV39) >> 6) & 1 == 1   # a
    assert (p.pack(SatpMode.SV39) >> 7) & 1 == 0   # d
    p = Pte(v=1, xwr=PtePermission.R_W_EXECUTE_PAGE, a=0, d=1)
    assert (p.pack(SatpMode.SV39) >> 6) & 1 == 0
    assert (p.pack(SatpMode.SV39) >> 7) & 1 == 1


# ---------- Topology ----------


def test_sv32_has_3_tables():
    pl = build_default_page_tables(SatpMode.SV32, PrivilegedMode.USER_MODE)
    assert len(pl.tables) == 3
    # 1 root (level 1) + 2 leaves (level 0).
    levels = [t.level for t in pl.tables]
    assert levels == [1, 0, 0]


def test_sv39_has_7_tables_with_correct_levels():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    assert len(pl.tables) == 7
    levels = [t.level for t in pl.tables]
    assert levels == [2, 1, 1, 0, 0, 0, 0]


def test_sv48_has_15_tables():
    pl = build_default_page_tables(SatpMode.SV48, PrivilegedMode.USER_MODE)
    assert len(pl.tables) == 15
    levels = [t.level for t in pl.tables]
    assert levels == [3, 2, 2, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0]


def test_table_pte_count_is_4kib():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    # SV39 → XLEN 64 → 4096/8 = 512 PTEs/table.
    assert all(len(t.ptes) == 512 for t in pl.tables)


def test_root_link_ptes_are_link_type():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    root = pl.tables[0]
    for j in range(LINK_PTE_PER_TABLE):
        assert root.ptes[j].is_link()


def test_root_super_leaves_have_xwr_rwx():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    root = pl.tables[0]
    for j in range(LINK_PTE_PER_TABLE,
                   LINK_PTE_PER_TABLE + SUPER_LEAF_PTE_PER_TABLE):
        assert root.ptes[j].xwr == PtePermission.R_W_EXECUTE_PAGE


def test_user_mode_leaves_have_u_bit_set():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    leaf_table = next(t for t in pl.tables if t.level == 0)
    for pte in leaf_table.ptes:
        assert pte.u == 1


def test_supervisor_mode_leaves_have_u_bit_clear():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.SUPERVISOR_MODE)
    leaf_table = next(t for t in pl.tables if t.level == 0)
    for pte in leaf_table.ptes:
        assert pte.u == 0


def test_get_child_table_id_matches_sv():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    # SV: child = parent_id * 2 + j + 1
    assert pl.get_child_table_id(0, 0) == 1
    assert pl.get_child_table_id(0, 1) == 2
    assert pl.get_child_table_id(1, 0) == 3
    assert pl.get_child_table_id(2, 1) == 6


# ---------- Asm emission ----------


def test_data_section_has_align_12_and_root_label():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    section = pl.gen_data_section(hart=0, num_harts=1)
    assert any(".section .page_table" in s for s in section)
    assert any(s.strip() == ".align 12" for s in section)
    assert any(s == "page_table_0:" for s in section)


def test_data_section_uses_dword_for_xlen64():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    section = pl.gen_data_section(hart=0, num_harts=1)
    # Find a data line.
    data = next(s for s in section if ".dword" in s or ".word" in s)
    assert ".dword" in data


def test_data_section_uses_word_for_sv32():
    pl = build_default_page_tables(SatpMode.SV32, PrivilegedMode.USER_MODE)
    section = pl.gen_data_section(hart=0, num_harts=1)
    data = next(s for s in section if ".dword" in s or ".word" in s)
    assert ".word" in data
    assert ".dword" not in data


def test_data_section_emits_one_pte_per_4kib():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    section = pl.gen_data_section(hart=0, num_harts=1)
    pte_lines = [s for s in section if ".dword" in s]
    # 7 tables × 512 PTEs = 3584 PTEs total.
    assert len(pte_lines) == 7 * 512


def test_data_section_hart_prefixes_when_multi_hart():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    section = pl.gen_data_section(hart=2, num_harts=4)
    assert any(".section .h2_page_table" in s for s in section)


# ---------- Boot SATP setup ----------


@pytest.fixture
def sv39_cfg():
    return Config(target=BUILTIN_TARGETS["rv64gc"])


def test_setup_satp_emits_la_and_csrw(sv39_cfg):
    out = gen_setup_satp(sv39_cfg, RiscvReg.T0)
    text = "\n".join(out)
    assert "la t0, page_table_0" in text
    assert "srli t0, t0, 12" in text
    assert "csrw 0x180, t0" in text   # SATP CSR address
    assert "sfence.vma" in text


def test_setup_satp_includes_mode_bits_for_sv39(sv39_cfg):
    out = gen_setup_satp(sv39_cfg, RiscvReg.T0)
    text = "\n".join(out)
    # SV39 in RV64: MODE = 8 << 60 = 0x8000000000000000
    assert "0x8000000000000000" in text


def test_setup_satp_returns_empty_for_bare():
    cfg = Config(target=BUILTIN_TARGETS["rv32imc"])  # BARE
    out = gen_setup_satp(cfg, RiscvReg.T0)
    assert out == []


# ---------- Integration ----------


def test_is_paging_enabled_true_for_sv39():
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    assert is_paging_enabled(cfg) is True


def test_is_paging_enabled_false_for_bare():
    cfg = Config(target=BUILTIN_TARGETS["rv32imc"])
    assert is_paging_enabled(cfg) is False


def test_is_paging_enabled_false_when_bare_program_mode():
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    cfg.bare_program_mode = True
    assert is_paging_enabled(cfg) is False


# ---------- Process page table (link fix-up) ----------


def test_process_page_table_only_visits_non_leaf_tables():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    out = pl.gen_process_page_table(cfg)
    text = "\n".join(out)
    # Tables 0, 1, 2 are non-leaf — should appear. Leaf-only tables (3-6) should not.
    assert "page_table_0+2048" in text
    assert "page_table_1+2048" in text
    assert "page_table_2+2048" in text
    assert "page_table_3+2048" not in text
    assert "page_table_4+2048" not in text


def test_process_page_table_links_root_to_correct_children():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    out = pl.gen_process_page_table(cfg)
    text = "\n".join(out)
    # Root (id=0) has link PTEs at j=0, j=1 -> children 1, 2.
    assert "Link PT_0_PTE_0 -> PT_1" in text
    assert "Link PT_0_PTE_1 -> PT_2" in text


def test_process_page_table_ends_with_sfence():
    pl = build_default_page_tables(SatpMode.SV39, PrivilegedMode.USER_MODE)
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    out = pl.gen_process_page_table(cfg)
    assert "sfence.vma" in out[-1]
