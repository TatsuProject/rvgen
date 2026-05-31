"""Regression for the auto_regress per-test sub-seed reproducibility bug.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md, finding H2):
``auto_regress.py:212`` used ``hash(te.test)`` to mix the test name into
the per-test sub-seed. ``hash(str)`` in CPython is process-randomized
(``PYTHONHASHSEED``), so the same recorded seed regenerated a *different*
``.S`` on the next process run — silently breaking the promise that
``seed_archive/<test>_seedN.S`` can be replayed from the seed value alone.

The fix uses ``zlib.crc32`` which is stable across processes, machines,
and Python versions.

These tests pin the fix so a future refactor can't silently revert it.
"""

from __future__ import annotations

import subprocess
import sys
import zlib

import pytest


# These values are deliberately HARDCODED. If you change the sub-seed
# formula in rvgen/auto_regress.py, you've changed what every recorded
# seed regenerates — every seed_archive .S that customers have on disk
# will diverge. Update these constants ONLY together with that decision.
_PINNED_SUB_SEEDS = [
    (100, "riscv_rand_instr_test",        73771048),
    (200, "riscv_arithmetic_basic_test",  927307876),
    (0,   "riscv_rand_instr_test",        73771084),
]


@pytest.mark.parametrize("seed,test_name,expected", _PINNED_SUB_SEEDS)
def test_pinned_per_test_sub_seed(seed, test_name, expected):
    """The per-test sub-seed formula must be the documented stable mix."""
    actual = (seed ^ zlib.crc32(test_name.encode())) & 0xFFFF_FFFF
    assert actual == expected


def test_per_test_sub_seed_is_stable_across_processes():
    """Subprocess sanity: compute the sub-seed in a fresh Python with a
    randomized PYTHONHASHSEED (the default). If anyone reintroduces
    ``hash()`` into the formula, this test breaks because hash(str)
    differs across processes.
    """
    code = (
        "import zlib;"
        "print((100 ^ zlib.crc32(b'riscv_rand_instr_test')) & 0xFFFF_FFFF)"
    )
    out1 = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
    out2 = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
    assert out1 == out2 == "73771048", (
        f"per-test sub-seed not stable across processes: {out1!r} vs {out2!r}. "
        "Did someone reintroduce hash() into the sub-seed mix in auto_regress.py?"
    )


def test_auto_regress_does_not_use_builtin_hash():
    """Static guard: the formula must not use ``hash(`` anywhere in the
    sub-seed line. ``hash(str)`` is process-randomized and silently
    breaks seed-archive replay.
    """
    src = open("rvgen/auto_regress.py").read()
    # Find the rng_i assignment line — there should be exactly one.
    rng_lines = [L for L in src.splitlines() if "rng_i = random.Random(" in L]
    assert rng_lines, "auto_regress.py no longer assigns rng_i — test stale?"
    for L in rng_lines:
        assert "hash(" not in L, (
            f"H2 regression: auto_regress.py uses hash() in {L!r} — "
            "this is process-randomized. Use zlib.crc32 instead."
        )
