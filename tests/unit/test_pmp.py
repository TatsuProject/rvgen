"""Tests for rvgen.privileged.pmp — PMP cfg packing + asm emit."""

from __future__ import annotations

import pytest

from rvgen.config import Config
from rvgen.isa.enums import PrivilegedReg, RiscvReg
from rvgen.privileged.pmp import (
    PmpAddrMode,
    PmpCfg,
    PmpRegion,
    gen_setup_pmp,
    make_default_cfg,
    napot_addr,
)
from rvgen.targets.builtin import BUILTIN_TARGETS


# ---------- PmpRegion.cfg_byte ----------


def test_cfg_byte_all_zero_when_off_no_perms():
    r = PmpRegion(l=0, a=PmpAddrMode.OFF, x=0, w=0, r=0)
    assert r.cfg_byte() == 0


def test_cfg_byte_l_bit_at_position_7():
    r = PmpRegion(l=1, a=PmpAddrMode.OFF, x=0, w=0, r=0)
    assert r.cfg_byte() == 0x80


def test_cfg_byte_a_field_at_bits_3_4():
    r = PmpRegion(l=0, a=PmpAddrMode.NAPOT, x=0, w=0, r=0)
    assert r.cfg_byte() == 0x18   # 0b00011000
    r = PmpRegion(l=0, a=PmpAddrMode.TOR, x=0, w=0, r=0)
    assert r.cfg_byte() == 0x08   # 0b00001000
    r = PmpRegion(l=0, a=PmpAddrMode.NA4, x=0, w=0, r=0)
    assert r.cfg_byte() == 0x10   # 0b00010000


def test_cfg_byte_xwr_at_bits_0_1_2():
    r = PmpRegion(l=0, a=PmpAddrMode.OFF, x=1, w=1, r=1)
    assert r.cfg_byte() & 0x07 == 0x07
    r = PmpRegion(l=0, a=PmpAddrMode.OFF, x=0, w=0, r=1)
    assert r.cfg_byte() & 0x07 == 0x01
    r = PmpRegion(l=0, a=PmpAddrMode.OFF, x=1, w=0, r=0)
    assert r.cfg_byte() & 0x07 == 0x04


def test_cfg_byte_napot_rwx_unlocked_is_0x1f():
    r = PmpRegion(l=0, a=PmpAddrMode.NAPOT, x=1, w=1, r=1)
    assert r.cfg_byte() == 0x1F


# ---------- pack_addr ----------


def test_pack_addr_shifts_right_2():
    r = PmpRegion(addr=0x80000000)
    assert r.pack_addr(xlen=32) == 0x20000000
    assert r.pack_addr(xlen=64) == 0x20000000


def test_pack_addr_rv32_masks_to_32_bits():
    r = PmpRegion(addr=(1 << 35) - 1)
    assert r.pack_addr(xlen=32) == 0xFFFFFFFF


def test_pack_addr_rv64_masks_to_54_bits():
    r = PmpRegion(addr=(1 << 60) - 1)
    assert r.pack_addr(xlen=64) <= ((1 << 54) - 1)


# ---------- napot_addr helper ----------


def test_napot_4kib_at_0x80000000():
    encoded = napot_addr(0x80000000, 12)
    # 4 KiB region: low 9 bits of pmpaddr should be all-ones.
    assert (encoded >> 2) & 0x1FF == 0x1FF
    # Bit 9 should be 0 (the bit just above the all-ones pattern).
    assert (encoded >> 2) & (1 << 9) == 0
    # The base 0x80000000 should still be visible in the high bits.
    assert (encoded >> 2) >> 10 << 10 << 2 == 0x80000000


def test_napot_minimum_size_is_8_bytes():
    napot_addr(0x1000, 3)   # works
    with pytest.raises(ValueError):
        napot_addr(0x1000, 2)   # 4 bytes — invalid for NAPOT


# ---------- make_default_cfg ----------


def test_default_1_region_is_napot_full_rwx():
    pmp = make_default_cfg(xlen=64, num_regions=1)
    assert len(pmp.regions) == 1
    assert pmp.regions[0].a == PmpAddrMode.NAPOT
    assert pmp.regions[0].cfg_byte() == 0x1F


def test_default_n_regions_is_tor():
    pmp = make_default_cfg(xlen=64, num_regions=4)
    assert len(pmp.regions) == 4
    for r in pmp.regions:
        assert r.a == PmpAddrMode.TOR
        assert r.cfg_byte() & 0x07 == 0x07   # full RWX


# ---------- gen_setup_pmp ----------


@pytest.fixture
def rv64_cfg():
    return Config(target=BUILTIN_TARGETS["rv64gc"])


def test_setup_emits_pmpcfg_then_pmpaddr(rv64_cfg):
    pmp = make_default_cfg(xlen=64, num_regions=2)
    out = gen_setup_pmp(rv64_cfg, pmp, RiscvReg.T0)
    text = "\n".join(out)
    # PMPCFG0 (0x3a0) before PMPADDR0 (0x3b0).
    cfg_idx = text.index("0x3a0")
    addr_idx = text.index("0x3b0")
    assert cfg_idx < addr_idx


def test_setup_packs_two_cfg_bytes_into_one_csr_on_rv64(rv64_cfg):
    # 2 regions, both fully permissive TOR (cfg_byte = 0x0F).
    pmp = make_default_cfg(xlen=64, num_regions=2)
    out = gen_setup_pmp(rv64_cfg, pmp, RiscvReg.T0)
    text = "\n".join(out)
    # Packed value: 0x0F | (0x0F << 8) = 0x0F0F.
    assert "0xf0f" in text


def test_setup_returns_empty_when_suppressed():
    pmp = PmpCfg(suppress_setup=True)
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    assert gen_setup_pmp(cfg, pmp, RiscvReg.T0) == []


def test_setup_returns_empty_with_zero_regions():
    pmp = PmpCfg(pmp_num_regions=0, regions=[])
    cfg = Config(target=BUILTIN_TARGETS["rv64gc"])
    assert gen_setup_pmp(cfg, pmp, RiscvReg.T0) == []


def test_setup_emits_one_csrw_per_region(rv64_cfg):
    pmp = make_default_cfg(xlen=64, num_regions=4)
    out = gen_setup_pmp(rv64_cfg, pmp, RiscvReg.T0)
    addr_writes = [s for s in out if "PMPADDR" in s]
    assert len(addr_writes) == 4


def test_setup_uses_correct_csr_addresses_on_rv32():
    cfg = Config(target=BUILTIN_TARGETS["rv32imc"])
    pmp = make_default_cfg(xlen=32, num_regions=8)
    out = gen_setup_pmp(cfg, pmp, RiscvReg.T0)
    text = "\n".join(out)
    # RV32: 4 regions per pmpcfg → expect pmpcfg0 (0x3a0) + pmpcfg1 (0x3a1).
    assert "0x3a0" in text
    assert "0x3a1" in text


def test_setup_uses_correct_csr_addresses_on_rv64(rv64_cfg):
    pmp = make_default_cfg(xlen=64, num_regions=10)
    out = gen_setup_pmp(rv64_cfg, pmp, RiscvReg.T0)
    text = "\n".join(out)
    # RV64: 8 regions per pmpcfg, evens only → pmpcfg0 (0x3a0) + pmpcfg2 (0x3a2).
    assert "0x3a0" in text
    assert "0x3a2" in text
    # No pmpcfg1 (0x3a1).
    assert "0x3a1" not in text
