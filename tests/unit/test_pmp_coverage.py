"""Tests for the PMP cfg-byte covergroup sampler."""

from __future__ import annotations

from rvgen.coverage.collectors import CG_PMP_CFG, new_db, sample_pmp_region
from rvgen.privileged.pmp import PmpAddrMode, PmpRegion


def test_sample_napot_unlocked_rwx():
    db = new_db()
    region = PmpRegion(l=0, a=PmpAddrMode.NAPOT, x=1, w=1, r=1)
    sample_pmp_region(db, region)
    assert db[CG_PMP_CFG] == {"NAPOT_unlocked_XWR": 1}


def test_sample_tor_locked_read_only():
    db = new_db()
    region = PmpRegion(l=1, a=PmpAddrMode.TOR, x=0, w=0, r=1)
    sample_pmp_region(db, region)
    assert db[CG_PMP_CFG] == {"TOR_locked_--R": 1}


def test_sample_off_compresses_to_none_xwr():
    db = new_db()
    region = PmpRegion(l=0, a=PmpAddrMode.OFF, x=0, w=0, r=0)
    sample_pmp_region(db, region)
    assert db[CG_PMP_CFG] == {"OFF_unlocked_none": 1}


def test_sample_na4_locked_x_only():
    db = new_db()
    region = PmpRegion(l=1, a=PmpAddrMode.NA4, x=1, w=0, r=0)
    sample_pmp_region(db, region)
    assert db[CG_PMP_CFG] == {"NA4_locked_X--": 1}


def test_sample_multiple_regions_accumulates():
    db = new_db()
    sample_pmp_region(db, PmpRegion(l=0, a=PmpAddrMode.NAPOT, x=1, w=1, r=1))
    sample_pmp_region(db, PmpRegion(l=0, a=PmpAddrMode.NAPOT, x=1, w=1, r=1))
    sample_pmp_region(db, PmpRegion(l=0, a=PmpAddrMode.TOR, x=0, w=0, r=1))
    assert db[CG_PMP_CFG] == {
        "NAPOT_unlocked_XWR": 2,
        "TOR_unlocked_--R": 1,
    }
