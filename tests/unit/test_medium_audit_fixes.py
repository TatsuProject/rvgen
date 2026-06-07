"""Regression suite for MEDIUM-severity QA audit findings.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md):

  * M8  — ``per_test_cov`` key collision when a testlist has two entries
          with the same test name + same iteration index. The previous
          ``test_id = f"{te.test}_{it}"`` formula let entry-1 overwrite
          entry-0 in ``coverage_per_test.json``. Fix: include entry index.

  * M11 — ``gen_data_page()`` silently wall-clock-seeded a fresh
          ``random.Random()`` when called without an ``rng=`` kwarg,
          producing non-reproducible .data payloads for library-API
          users. Fix: raise ``ValueError`` so the misuse is loud.

  * M12 — ``vec_vstart_cg`` sampler emitted bin name ``"high"`` but the
          covergroup docstring documented ``"max"``. Stream corner set
          {0,1,2,4,8,16} never produced a value large enough to reach
          the bin anyway. Fix: rename to ``"max"`` and extend corner
          set so the bin is actually reachable.
"""

from __future__ import annotations

import pytest

from rvgen.coverage.collectors import CG_VEC_VSTART, new_db, sample_instr
from rvgen.sections.data_page import gen_data_page


# ---------------------------------------------------------------------
# M8 — per_test_cov key uniqueness
# ---------------------------------------------------------------------

def test_per_test_key_includes_entry_index():
    """Static guard: cli.py builds test_id from (test_name, entry_idx, it)
    so duplicate test-name entries don't collide in per_test_cov."""
    src = open("rvgen/cli.py").read()
    # The formula must include entry_idx — the variable name introduced
    # by the M8 fix.
    assert 'f"{te.test}_{entry_idx}_{it}"' in src, (
        "M8 regression: cli.py no longer encodes entry_idx into test_id. "
        "Two duplicate-named testlist entries will collide in per_test_cov."
    )
    # And the outer loop must be enumerate(tests) so entry_idx is bound.
    assert "for entry_idx, te in enumerate(tests):" in src, (
        "M8 regression: cli.py outer loop should be `enumerate(tests)` "
        "so entry_idx is in scope for the test_id formula."
    )


# ---------------------------------------------------------------------
# M11 — gen_data_page() must demand explicit rng
# ---------------------------------------------------------------------

def test_gen_data_page_raises_on_missing_rng():
    """Library-API call without rng= must fail loudly instead of
    silently wall-clock-seeding a fresh Random()."""
    from rvgen.isa.enums import DataPattern
    from rvgen.sections.data_page import DEFAULT_MEM_REGIONS

    with pytest.raises(ValueError, match="explicit rng"):
        gen_data_page(
            regions=list(DEFAULT_MEM_REGIONS),
            pattern=DataPattern.RAND_DATA,
        )


def test_gen_data_page_accepts_explicit_rng():
    """Sanity: with rng= passed, the function does what it always did."""
    import random
    from rvgen.isa.enums import DataPattern
    from rvgen.sections.data_page import DEFAULT_MEM_REGIONS

    lines_a = gen_data_page(
        regions=list(DEFAULT_MEM_REGIONS),
        pattern=DataPattern.RAND_DATA,
        rng=random.Random(100),
    )
    lines_b = gen_data_page(
        regions=list(DEFAULT_MEM_REGIONS),
        pattern=DataPattern.RAND_DATA,
        rng=random.Random(100),
    )
    # Same seed → identical bytes. This is exactly the reproducibility
    # property that the silent fallback broke.
    assert lines_a == lines_b
    assert len(lines_a) > 0


# ---------------------------------------------------------------------
# M12 — vec_vstart_cg ``max`` bin reachable + renamed
# ---------------------------------------------------------------------

def test_vec_vstart_sampler_uses_documented_bin_name():
    """The collectors.py sampler must emit ``"max"`` for the largest
    corner bin (matches CG_VEC_VSTART docstring), not the
    previously-drifted ``"high"``."""
    src = open("rvgen/coverage/collectors.py").read()
    # Locate the vstart binning block — it sets bin_name across the
    # zero / one / small / mid / max ladder. The "max" branch must
    # exist; "high" must not (under the same vstart block).
    block_start = src.index("vstart-corner pseudo carries _vstart_value")
    block_end = src.index("_bump(db, CG_VEC_VSTART", block_start)
    binning_block = src[block_start:block_end]
    assert 'bin_name = "max"' in binning_block, (
        "M12 regression: vstart binning block lost the `max` bin name "
        "(should match the CG_VEC_VSTART docstring)."
    )
    assert 'bin_name = "high"' not in binning_block, (
        "M12 regression: vstart binning block reintroduced `high` — "
        "the documented bin name is `max`."
    )


def test_vstart_corner_set_includes_value_above_mid():
    """The stream must emit at least one corner > 16 so the ``max`` bin
    can fire end-to-end (not just under synthetic input)."""
    from rvgen.streams.vstart_corner import VstartCornerInstrStream
    corners = VstartCornerInstrStream._CORNERS
    assert max(corners) > 16, (
        f"M12 regression: _CORNERS={corners} contains no value > 16, "
        "so the vec_vstart_cg `max` bin can never fire from real generation."
    )


def test_vstart_max_bin_reachable_end_to_end():
    """End-to-end: generate a vector test with the vstart-corner stream
    enabled, sample the emitted sequence with a real vector_cfg, and
    confirm the ``max`` bin populates."""
    import random as _rnd
    from rvgen.asm_program_gen import AsmProgramGen
    from rvgen.config import make_config
    from rvgen.coverage import sample_sequence
    from rvgen.isa.filtering import create_instr_list
    from rvgen.targets import get_target

    target = get_target("rv64gcv")
    cfg = make_config(
        target,
        gen_opts="+directed_instr_1=riscv_vstart_corner_instr_stream,3 +instr_cnt=200",
    )
    cfg.seed = 42
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(42))
    gen.gen_program()

    db = new_db()
    for seq in gen.hart_sequences:
        sample_sequence(
            db, seq.instr_stream.instr_list, vector_cfg=cfg.vector_cfg,
        )

    vstart_bins = db.get(CG_VEC_VSTART, {})
    # At minimum we should hit zero/one/small/mid (and ideally max) over
    # 3 stream blocks × ~6 pairs each. ``max`` is the bin we're pinning.
    assert "max" in vstart_bins, (
        f"M12 regression: vec_vstart_cg `max` bin still unreachable. "
        f"observed bins: {vstart_bins!r}"
    )
