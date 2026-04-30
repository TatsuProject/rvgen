"""Tests for multi-hart shared-memory regions."""

from __future__ import annotations

import random

from rvgen.sections.data_page import (
    DEFAULT_AMO_REGION,
    DEFAULT_MEM_REGIONS,
    DEFAULT_SHARED_REGIONS,
    DataPattern,
    MemRegion,
    gen_data_page,
)


def test_shared_region_is_marked_shared():
    assert all(r.shared for r in DEFAULT_SHARED_REGIONS)


def test_amo_region_is_shared():
    # AMO regions are always shared (no per-hart prefix).
    assert all(r.shared for r in DEFAULT_AMO_REGION)


def test_default_user_regions_are_not_shared():
    assert all(not r.shared for r in DEFAULT_MEM_REGIONS)


def test_shared_region_emits_no_hart_prefix():
    rng = random.Random(0)
    out = gen_data_page(
        DEFAULT_SHARED_REGIONS, DataPattern.ALL_ZERO,
        hart=2, num_harts=4, rng=rng,
    )
    text = "\n".join(out)
    # Label is exactly "shared_region_0", with no h2_ prefix.
    assert "shared_region_0:" in text
    assert "h2_shared_region_0" not in text
    # Section name carries no prefix either.
    assert ".section .shared_region_0" in text


def test_private_region_keeps_hart_prefix():
    rng = random.Random(0)
    out = gen_data_page(
        DEFAULT_MEM_REGIONS, DataPattern.ALL_ZERO,
        hart=1, num_harts=4, rng=rng,
    )
    text = "\n".join(out)
    # h1_region_0 is the canonical multi-hart label.
    assert "h1_region_0:" in text


def test_explicit_shared_region_overrides_amo_flag():
    # Even if `amo=False`, a region with shared=True must skip the prefix.
    rng = random.Random(0)
    custom = (MemRegion("custom_shared", 64, shared=True),)
    out = gen_data_page(
        custom, DataPattern.ALL_ZERO,
        hart=3, num_harts=4, rng=rng,
        amo=False,
    )
    assert any("custom_shared:" in s for s in out)
    assert not any("h3_custom_shared" in s for s in out)
