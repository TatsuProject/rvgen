"""Smoke tests for the directed-stream registry and each stream's output shape."""

from __future__ import annotations

import random

from rvgen.config import make_config
from rvgen.isa.enums import RiscvInstrCategory, RiscvInstrName, RiscvReg
from rvgen.isa.filtering import create_instr_list
from rvgen.streams import STREAM_REGISTRY, get_stream
from rvgen.targets import get_target


def _ctx(target="rv32imc"):
    cfg = make_config(get_target(target))
    avail = create_instr_list(cfg)
    return cfg, avail, random.Random(42)


def test_streams_registered():
    for required in (
        "riscv_int_numeric_corner_stream",
        "riscv_jal_instr",
        "riscv_loop_instr",
        "riscv_load_store_rand_instr_stream",
        "riscv_lr_sc_instr_stream",
        "riscv_amo_instr_stream",
    ):
        assert required in STREAM_REGISTRY, f"{required!r} missing"


def test_int_numeric_corner_stream_produces_lis_then_arith():
    cfg, avail, rng = _ctx()
    cls = get_stream("riscv_int_numeric_corner_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test")
    stream.generate()
    # First 10 should be LI pseudo (or similar init ops).
    from rvgen.streams.directed import _LiPseudo
    assert sum(1 for i in stream.instr_list if isinstance(i, _LiPseudo)) >= 5
    # Plus some arithmetic/logical/compare/shift body.
    body_count = sum(
        1 for i in stream.instr_list
        if not isinstance(i, _LiPseudo) and i.category in (
            RiscvInstrCategory.ARITHMETIC, RiscvInstrCategory.LOGICAL,
            RiscvInstrCategory.COMPARE, RiscvInstrCategory.SHIFT,
        )
    )
    assert body_count >= 15


def test_jal_instr_chain_atomic():
    cfg, avail, rng = _ctx()
    cls = get_stream("riscv_jal_instr")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_jal", num_of_jump_instr=15)
    stream.generate()
    # Layout: jump_start + N jal body + end-sentinel ADDI (2 extra instrs).
    assert len(stream.instr_list) == 15 + 2
    # All JALs except the trailing sentinel.
    jal_count = sum(1 for i in stream.instr_list if i.instr_name == RiscvInstrName.JAL)
    assert jal_count == 15 + 1  # body JALs + jump_start
    assert stream.instr_list[-1].instr_name == RiscvInstrName.ADDI
    # All atomic (DirectedInstrStream tags them on generate()).
    assert all(i.atomic for i in stream.instr_list)
    # All have unique labels.
    labels = [i.label for i in stream.instr_list]
    assert len(set(labels)) == 15 + 2


def test_loop_instr_has_counter_and_branch():
    cfg, avail, rng = _ctx()
    cls = get_stream("riscv_loop_instr")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_loop",
                 label="test_loop_0", num_of_instr_in_loop=5)
    stream.generate()
    # First instr initializes counter (addi rd, zero, N).
    first = stream.instr_list[0]
    assert first.instr_name == RiscvInstrName.ADDI
    assert first.rs1 == RiscvReg.ZERO
    # Last instr is BNE with symbol-based target.
    last = stream.instr_list[-1]
    assert last.instr_name == RiscvInstrName.BNE
    assert last.branch_assigned is True
    assert last.imm_str.endswith("_target")


def test_lr_sc_stream_has_la_lr_sc():
    cfg, avail, rng = _ctx("rv32ia")
    cls = get_stream("riscv_lr_sc_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_lrsc")
    stream.generate()
    names = [i.instr_name for i in stream.instr_list]
    assert RiscvInstrName.LR_W in names
    assert RiscvInstrName.SC_W in names


def test_amo_stream_contains_amos():
    cfg, avail, rng = _ctx("rv32ia")
    cls = get_stream("riscv_amo_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="test_amo", num_amo=5)
    stream.generate()
    from rvgen.isa.amo import AmoInstr
    amo_count = sum(1 for i in stream.instr_list if isinstance(i, AmoInstr))
    assert amo_count == 5


def test_cache_conflict_stream_offsets_share_cache_set():
    """Offsets emitted by CacheConflictInstrStream must collide on the same
    cache set: (offset / line) % num_sets must be constant within each
    set-group, regardless of the linker-assigned region base."""
    cfg, avail, rng = _ctx("rv32imc")
    cls = get_stream("riscv_cache_conflict_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="cache_conflict")
    stream.generate()

    line = stream.cache_line_bytes
    nsets = stream.num_sets
    # Group offsets by recorded set index and confirm congruence.
    by_set: dict[int, list[int]] = {}
    for set_idx, off in zip(stream._set_indices, stream._offsets):
        by_set.setdefault(set_idx, []).append(off)
    assert len(by_set) == nsets
    for set_idx, offs in by_set.items():
        for off in offs:
            assert (off // line) % nsets == set_idx, (
                f"offset {off} maps to set {(off//line)%nsets}, expected {set_idx}")


def test_cache_conflict_stream_drives_eviction_pressure():
    """For each targeted set, more than `ways` accesses must be emitted so
    a `ways`-way set-associative cache is forced to evict."""
    cfg, avail, rng = _ctx("rv32imc")
    cls = get_stream("riscv_cache_conflict_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="cache_conflict")
    stream.generate()

    ways = stream.cache_ways
    # Pressure counts per set: count how many accesses landed in each set.
    counts: dict[int, int] = {}
    for s in stream._set_indices:
        counts[s] = counts.get(s, 0) + 1
    assert counts, "stream produced no accesses"
    # Every targeted set must reach `ways + 1` (eviction-forcing).
    for set_idx, n in counts.items():
        assert n > ways, (
            f"set {set_idx} only got {n} accesses, need > {ways} to evict")


def test_cache_conflict_stream_pins_base_register():
    """rs1 must be pinned to the region's base across every emitted ld/st —
    if any access picked rs1 as rd, the base would be clobbered."""
    cfg, avail, rng = _ctx("rv32imc")
    cls = get_stream("riscv_cache_conflict_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=rng, stream_name="cache_conflict")
    stream.generate()

    base_reg = stream.rs1_reg
    assert base_reg is not None
    # First instr is the la pseudo writing rs1_reg.
    from rvgen.streams.directed import _LaPseudo
    assert isinstance(stream.instr_list[0], _LaPseudo)
    assert stream.instr_list[0].rd == base_reg

    # No load may pick base_reg as rd.
    for instr in stream.instr_list[1:]:
        rd = getattr(instr, "rd", None)
        if rd is None or instr.category != RiscvInstrCategory.LOAD:
            continue
        assert rd != base_reg, (
            f"load {instr.instr_name} clobbers base reg {base_reg.name}")


def test_cache_conflict_coverage_sampling_bins():
    """Running the static collector over the stream's output must populate
    cache_conflict_cg with way_pressure_* + eviction bins."""
    import random
    from rvgen.config import make_config
    from rvgen.coverage.collectors import CG_CACHE_CONFLICT, sample_sequence
    from rvgen.isa.filtering import create_instr_list
    from rvgen.streams import get_stream
    from rvgen.targets import get_target

    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    cls = get_stream("riscv_cache_conflict_instr_stream")
    stream = cls(cfg=cfg, avail=avail, rng=random.Random(7),
                 stream_name="cache_conflict")
    stream.generate()

    db: dict[str, dict[str, int]] = {}
    sample_sequence(db, stream.instr_list)
    cg = db.get(CG_CACHE_CONFLICT, {})
    # At least one way_pressure_1 (every set's first access) + eviction
    # (anything past ways).
    assert cg.get("way_pressure_1", 0) > 0
    assert cg.get("eviction", 0) > 0
    # Per-set bins should match the set indices actually targeted.
    targeted = set(stream._set_indices)
    for set_idx in targeted:
        assert cg.get(f"set_{set_idx}", 0) > 0
