"""Tests for the failure-minimizer."""

from __future__ import annotations

from pathlib import Path

import pytest

from rvgen.minimize import (
    AsmStructure,
    assemble,
    ddmin,
    minimize_asm,
    parse_asm,
)


# ---------- parse_asm ----------


def test_parse_asm_splits_at_main_and_test_done():
    src = [
        '.include "user_define.h"',
        '_start:',
        '  j h0_start',
        'h0_start:',
        '  li a0, 0',
        'init:',
        '  li t0, 0',
        'main:',
        '  add a0, a1, a2',
        '  sub a0, a0, a1',
        'test_done:',
        '  li gp, 1',
        '  ecall',
    ]
    s = parse_asm(src)
    assert "main:" in s.preamble[-1]
    assert "test_done:" in s.trailer[0]
    # Body has the two main-section instructions.
    assert any("add a0, a1, a2" in line for line in s.main_body)
    assert any("sub a0, a0, a1" in line for line in s.main_body)
    assert len(s.main_body) == 2


def test_parse_asm_handles_hart_prefix():
    src = [
        '_start:',
        'h0_main:',
        '  add a0, a1, a2',
        'h0_test_done:',
        '  ecall',
    ]
    s = parse_asm(src)
    assert "main:" in s.preamble[-1]
    assert "test_done:" in s.trailer[0]


def test_parse_asm_no_main_raises():
    with pytest.raises(ValueError):
        parse_asm(['_start:', '  nop'])


def test_parse_asm_no_test_done_treats_as_eof_trailer():
    src = ['_start:', 'main:', '  add a0, a1, a2']
    s = parse_asm(src)
    assert s.main_body == ['  add a0, a1, a2']
    assert s.trailer == []


def test_assemble_round_trips_body():
    src = ['_start:', 'main:', '  add a0, a1, a2', 'test_done:', '  ecall']
    s = parse_asm(src)
    rebuilt = assemble(s, s.main_body)
    assert rebuilt == src


def test_assemble_with_subset_replaces_body():
    src = ['_start:', 'main:', '  add a0, a1, a2', '  nop', 'test_done:', '  ecall']
    s = parse_asm(src)
    rebuilt = assemble(s, ['  sub x1, x2, x3'])
    assert '  sub x1, x2, x3' in rebuilt
    assert '  add a0, a1, a2' not in rebuilt


# ---------- ddmin ----------


def test_ddmin_returns_minimum_when_only_one_element_fails():
    """The fail-inducing element is item 7; ddmin should isolate it."""
    items = list(range(10))
    target = 7

    def predicate(subset):
        return target in subset

    result = ddmin(items, predicate)
    assert result == [target]


def test_ddmin_returns_pair_when_two_must_be_present():
    """Both items 3 and 5 must be present to fail; ddmin returns [3, 5]."""
    items = list(range(10))

    def predicate(subset):
        return 3 in subset and 5 in subset

    result = ddmin(items, predicate)
    assert sorted(result) == [3, 5]


def test_ddmin_returns_full_input_when_predicate_doesnt_fire():
    items = list(range(10))

    def predicate(subset):
        return False

    result = ddmin(items, predicate)
    assert result == items


def test_ddmin_handles_empty_input():
    items = []

    def predicate(subset):
        return True

    # Predicate fires on the empty list — minimal is the empty list.
    result = ddmin(items, predicate)
    assert result == []


def test_ddmin_iteration_count_is_logarithmic_in_input_size():
    items = list(range(100))
    target = 42
    call_count = {"n": 0}

    def predicate(subset):
        call_count["n"] += 1
        return target in subset

    result = ddmin(items, predicate)
    assert result == [target]
    # ddmin's worst case is O(n^2) but typical is O(n log n). For n=100
    # we expect well under 1000 predicate calls.
    assert call_count["n"] < 1000


def test_ddmin_progress_callback():
    items = list(range(20))
    progress_calls = []

    def predicate(subset):
        return 5 in subset

    def on_progress(iteration, current_size):
        progress_calls.append((iteration, current_size))

    ddmin(items, predicate, on_progress=on_progress)
    assert len(progress_calls) > 0
    # Iteration counter is monotonic.
    iterations = [p[0] for p in progress_calls]
    assert iterations == sorted(iterations)


# ---------- minimize_asm with synthetic predicate ----------


def test_minimize_asm_shrinks_body(tmp_path):
    """End-to-end: build a synthetic .S, define a "this line fails"
    predicate, run minimize_asm, verify the result keeps only that line."""
    asm = tmp_path / "fail.S"
    body = [
        "  add a0, a1, a2",
        "  sub a0, a0, a1",
        "  THE_BAD_INSTRUCTION",   # synthetic marker
        "  xor a0, a0, a1",
        "  or  a0, a0, a1",
    ]
    src = ["_start:", "main:"] + body + ["test_done:", "  ecall"]
    asm.write_text("\n".join(src))

    # Predicate: any candidate that contains the bad instruction "fails".
    def predicate(lines):
        return any("THE_BAD_INSTRUCTION" in line for line in lines)

    minimal = minimize_asm(asm, predicate)
    text = "\n".join(minimal)
    assert "THE_BAD_INSTRUCTION" in text
    # Should NOT contain the unrelated lines.
    assert "  sub a0, a0, a1" not in text
    assert "  xor a0, a0, a1" not in text


def test_minimize_asm_preserves_preamble_and_trailer(tmp_path):
    """The minimizer must not touch the preamble or trailer."""
    asm = tmp_path / "fail.S"
    src = [
        '.include "user_define.h"',
        '_start:',
        '  j h0_start',
        'main:',
        '  add a0, a1, a2',
        '  THE_BAD_INSTRUCTION',
        '  sub a0, a0, a1',
        'test_done:',
        '  li gp, 1',
        '  ecall',
    ]
    asm.write_text("\n".join(src))

    def predicate(lines):
        return any("THE_BAD_INSTRUCTION" in line for line in lines)

    minimal = minimize_asm(asm, predicate)
    text = "\n".join(minimal)
    # Preamble survives.
    assert '.include "user_define.h"' in text
    assert '_start:' in text
    # Trailer survives.
    assert 'test_done:' in text
    assert 'li gp, 1' in text
    assert 'ecall' in text
    # The bad instruction is preserved.
    assert "THE_BAD_INSTRUCTION" in text


def test_minimize_asm_returns_full_when_no_fail(tmp_path):
    """If the predicate never fires, minimize_asm returns the input unchanged."""
    asm = tmp_path / "ok.S"
    src = ['_start:', 'main:', '  nop', '  nop', 'test_done:', '  ecall']
    asm.write_text("\n".join(src))

    def predicate(lines):
        return False

    minimal = minimize_asm(asm, predicate)
    assert minimal == src
