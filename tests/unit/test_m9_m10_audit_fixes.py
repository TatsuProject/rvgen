"""Regression suite for MEDIUM-severity QA audit findings M9 + M10.

Audit 2026-05-31 (research/12_qa_audit_2026-05-31.md):

  * M9  — ``no_load_store=1`` was silently a no-op for every directed
          load/store family stream (LoadStoreBase/Stress/Rand/Hazard/
          RandAddr/CacheConflict/SharedMem/MultiPage/MemRegionStress,
          LR/SC, AMO, vector load/store, vector AMO, hypervisor HLV/HSV).
          Same shape as the H3-H5 leaks. Fix: declare
          ``BANNED_BY = ("no_load_store",)`` on the family parents
          (subclasses inherit). Extended ``HypervisorInstrStream`` to
          also list ``no_load_store`` (it emits HLV/HSV).

  * M10 — ``include_write_csr`` is the random CSR walker's whitelist;
          directed streams are EXEMPT (the user explicitly requested
          them). The previous code did not surface this scope to the
          verification engineer, so writes to ``vstart`` / ``vtype`` /
          ``vl`` via vector streams looked like silent bypass. Fix:
          add ``WRITES_CSRS`` ClassVar metadata on streams that
          intentionally write specific CSRs and have the splicer log
          INFO when a stream's CSR writes fall outside the whitelist.
          Behavior unchanged; visibility added.
"""

from __future__ import annotations

import logging
import random as _rnd

from rvgen.asm_program_gen import AsmProgramGen
from rvgen.config import make_config
from rvgen.isa.filtering import create_instr_list
from rvgen.streams import get_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.targets import get_target


# ---------------------------------------------------------------------
# M9 — every LS-family stream declares BANNED_BY = ("no_load_store",)
# ---------------------------------------------------------------------

_LS_FAMILY_STREAMS = [
    # LoadStoreBaseInstrStream family (load_store.py):
    "riscv_load_store_rand_instr_stream",
    "riscv_load_store_stress_instr_stream",
    "riscv_load_store_hazard_instr_stream",
    "riscv_hazard_instr_stream",
    "riscv_load_store_rand_addr_instr_stream",
    "riscv_cache_conflict_instr_stream",
    "riscv_load_store_shared_mem_stream",
    # MultiPageLoadStoreInstrStream family:
    "riscv_multi_page_load_store_instr_stream",
    "riscv_mem_region_stress_test",
    # AMO / LR-SC (amo_streams.py):
    "riscv_lr_sc_instr_stream",
    "riscv_amo_instr_stream",
    # Vector LS (vector_load_store.py):
    "riscv_vector_load_store_instr_stream",
    "riscv_vector_amo_instr_stream",
]


def test_every_ls_stream_declares_no_load_store_in_banned_by():
    """No LS-family stream may quietly omit the no_load_store ban."""
    for name in _LS_FAMILY_STREAMS:
        cls = get_stream(name)
        assert "no_load_store" in cls.BANNED_BY, (
            f"M9 regression: {name} has BANNED_BY={cls.BANNED_BY!r} — "
            "must include 'no_load_store' so +no_load_store=1 honors the "
            "knob instead of silently emitting loads/stores."
        )


def test_hypervisor_stream_lists_no_fence_AND_no_load_store():
    """The hypervisor stream mixes HFENCE (fence) with HLV/HSV (LS).
    Either knob alone must drop the whole stream — there's no useful
    subset to emit under either restriction."""
    cls = get_stream("riscv_hypervisor_instr")
    assert "no_fence" in cls.BANNED_BY
    assert "no_load_store" in cls.BANNED_BY


def test_e2e_no_load_store_drops_ls_amo_streams():
    """End-to-end smoke: +no_load_store=1 with a mixed LS/AMO directive
    produces zero stream blocks in the .S."""
    target = get_target("rv32imac")
    cfg = make_config(
        target,
        gen_opts=(
            "+no_load_store=1 "
            "+directed_instr_1=riscv_load_store_rand_instr_stream,3 "
            "+directed_instr_2=riscv_lr_sc_instr_stream,2 "
            "+directed_instr_3=riscv_amo_instr_stream,2 "
            "+directed_instr_4=riscv_cache_conflict_instr_stream,2"
        ),
    )
    cfg.seed = 42
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(42))
    lines = gen.gen_program()

    for stream_name in (
        "riscv_load_store_rand_instr_stream",
        "riscv_lr_sc_instr_stream",
        "riscv_amo_instr_stream",
        "riscv_cache_conflict_instr_stream",
    ):
        marker = f"start {stream_name}".lower()
        assert not any(marker in L.lower() for L in lines), (
            f"M9 regression: {stream_name} block emitted despite +no_load_store=1"
        )


# ---------------------------------------------------------------------
# M10 — WRITES_CSRS metadata + informational log
# ---------------------------------------------------------------------

def test_base_class_defaults_writes_csrs_to_empty():
    """A stream that doesn't override WRITES_CSRS doesn't pretend to."""
    assert DirectedInstrStream.WRITES_CSRS == ()


def test_vstart_corner_declares_vstart():
    cls = get_stream("riscv_vstart_corner_instr_stream")
    assert cls.WRITES_CSRS == ("VSTART",)


def test_vsetvli_stress_declares_vtype_and_vl():
    cls = get_stream("riscv_vsetvli_stress_instr_stream")
    assert cls.WRITES_CSRS == ("VTYPE", "VL")


def test_csrs_outside_whitelist_helper():
    """The helper must report CSRs missing from cfg.include_write_csr,
    case-insensitively."""
    target = get_target("rv64gcv")
    cfg = make_config(target)
    # Default whitelist is ("MSCRATCH",) — VSTART/VTYPE/VL are missing.
    vstart_cls = get_stream("riscv_vstart_corner_instr_stream")
    assert vstart_cls.csrs_outside_whitelist(cfg) == ("VSTART",)

    vsetvli_cls = get_stream("riscv_vsetvli_stress_instr_stream")
    assert vsetvli_cls.csrs_outside_whitelist(cfg) == ("VTYPE", "VL")

    # After explicitly whitelisting them, the helper returns empty.
    cfg2 = make_config(
        target, gen_opts="+include_write_reg=VSTART,VTYPE,VL,MSCRATCH",
    )
    assert vstart_cls.csrs_outside_whitelist(cfg2) == ()
    assert vsetvli_cls.csrs_outside_whitelist(cfg2) == ()


def test_splicer_logs_info_when_stream_writes_unwhitelisted_csr(caplog):
    """End-to-end: invoking vstart_corner + vsetvli_stress without
    extending the whitelist surfaces the conflict via INFO log."""
    target = get_target("rv64gcv")
    cfg = make_config(
        target,
        gen_opts=(
            "+no_csr_instr=0 "  # canonical testlist sets this; clear it
            "+directed_instr_1=riscv_vstart_corner_instr_stream,2 "
            "+directed_instr_2=riscv_vsetvli_stress_instr_stream,1"
        ),
    )
    cfg.seed = 42
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(42))

    with caplog.at_level(logging.INFO, logger="rvgen.cli"):
        gen.gen_program()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "VSTART" in msgs and "outside include_write_csr" in msgs
    assert "VTYPE" in msgs and "VL" in msgs


def test_no_info_log_when_csrs_are_whitelisted(caplog):
    """If the verif engineer extends the whitelist, the INFO log
    falls silent — no spurious spam."""
    target = get_target("rv64gcv")
    cfg = make_config(
        target,
        gen_opts=(
            "+no_csr_instr=0 "
            "+include_write_reg=VSTART,VTYPE,VL,MSCRATCH "
            "+directed_instr_1=riscv_vstart_corner_instr_stream,1"
        ),
    )
    cfg.seed = 42
    avail = create_instr_list(cfg)
    gen = AsmProgramGen(cfg=cfg, avail=avail, rng=_rnd.Random(42))

    with caplog.at_level(logging.INFO, logger="rvgen.cli"):
        gen.gen_program()

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "outside include_write_csr" not in msgs
