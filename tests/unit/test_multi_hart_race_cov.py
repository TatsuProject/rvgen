"""Tests for the multi-hart race covergroup sampler."""

from __future__ import annotations

from rvgen.coverage.collectors import (
    CG_MULTI_HART_RACE,
    new_db,
    sample_multi_hart_race,
)


def test_no_harts_does_not_sample():
    db = new_db()
    sample_multi_hart_race(db, set())
    assert db[CG_MULTI_HART_RACE] == {}


def test_one_hart_only():
    db = new_db()
    sample_multi_hart_race(db, {0})
    assert db[CG_MULTI_HART_RACE] == {"only_one_hart": 1}


def test_two_harts():
    db = new_db()
    sample_multi_hart_race(db, {0, 1})
    assert db[CG_MULTI_HART_RACE] == {"two_harts": 1}


def test_three_harts_lands_in_three_to_seven_bin():
    db = new_db()
    sample_multi_hart_race(db, {0, 1, 2})
    assert db[CG_MULTI_HART_RACE] == {"three_to_seven_harts": 1}


def test_seven_harts_lands_in_three_to_seven_bin():
    db = new_db()
    sample_multi_hart_race(db, {0, 1, 2, 3, 4, 5, 6})
    assert db[CG_MULTI_HART_RACE] == {"three_to_seven_harts": 1}


def test_eight_or_more_harts_lands_in_all_harts_bin():
    db = new_db()
    sample_multi_hart_race(db, set(range(8)))
    assert db[CG_MULTI_HART_RACE] == {"all_harts": 1}
