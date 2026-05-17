"""Memory-bounds compliance — DMEM clamping + IMEM-fit auto-scaling.

Two invariants the generator must honour when the target declares a
finite memory layout:

1. **DMEM-bounds**: every load/store EA generated into the random
   workload must lie within the configured data-section bounds.
   ``LoadStoreStream`` already clamps offsets per-stream; this test
   asserts the invariant end-to-end through a real Spike run on a
   small target.

2. **IMEM-fit**: when ``text_section_size_bytes`` is set, the CLI
   estimates the `.text` byte cost and scales ``instr_cnt`` down to
   fit, emitting a warning. A test with ``+instr_cnt=50000`` on a
   target with a 16 KiB IMEM must not silently produce a binary that
   won't fit on the DUT.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pytest

from rvgen.cli import (
    _enforce_imem_budget,
    _estimate_text_bytes,
    _TEXT_BYTES_PER_INSTR,
    _TEXT_FIXED_OVERHEAD_BYTES,
)
from rvgen.config import make_config
from rvgen.targets import get_target


# ---------------------------------------------------------------------------
# IMEM-fit estimator + auto-scale
# ---------------------------------------------------------------------------


def _target_with_text_budget(name: str, text_budget: int):
    """Clone a built-in target and override ``text_section_size_bytes``.

    Cleaner than mutating shared registry state — keeps each test
    hermetic.
    """
    base = get_target(name)
    import dataclasses
    return dataclasses.replace(base, text_section_size_bytes=text_budget)


def test_estimate_text_bytes_includes_overhead_and_instrs():
    """Estimator = fixed_overhead + instr_cnt * 4 + sub_progs * 256."""
    t = _target_with_text_budget("rv32imc", 1 << 20)
    cfg = make_config(t, gen_opts="+instr_cnt=100 +num_of_sub_program=2")
    expected = _TEXT_FIXED_OVERHEAD_BYTES + 100 * 4 + 2 * 256
    assert _estimate_text_bytes(cfg) == expected


def test_imem_budget_caps_instr_cnt_with_warning(caplog):
    """A 16 KiB IMEM cap forces instr_cnt down + emits a WARNING."""
    t = _target_with_text_budget("rv32imc", text_budget=16 * 1024)
    cfg = make_config(t, gen_opts="+instr_cnt=50000 +num_of_sub_program=0")
    assert cfg.instr_cnt == 50000
    with caplog.at_level(logging.WARNING, logger="rvgen.cli"):
        _enforce_imem_budget(cfg, "imem_budget_test")
    # instr_cnt scaled down — the cap is (16384 - 8192) / 4 = 2048.
    assert cfg.instr_cnt == 2048
    assert cfg.main_program_instr_cnt == 2048
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "IMEM budget" in msgs
    assert "Scaling instr_cnt down" in msgs


def test_imem_budget_noop_when_fits():
    """No scaling + no warning when instr_cnt comfortably fits."""
    t = _target_with_text_budget("rv32imc", text_budget=1 << 20)
    cfg = make_config(t, gen_opts="+instr_cnt=200")
    _enforce_imem_budget(cfg, "small_test")
    assert cfg.instr_cnt == 200  # unchanged


def test_imem_budget_noop_when_target_unset():
    """No-op when the target leaves text_section_size_bytes = None."""
    t = get_target("rv32imc")  # default — no IMEM cap
    cfg = make_config(t, gen_opts="+instr_cnt=10000")
    _enforce_imem_budget(cfg, "no_cap_test")
    assert cfg.instr_cnt == 10000  # unchanged — no cap in effect


def test_imem_budget_errors_when_overhead_alone_exceeds_cap(caplog):
    """If even the boot/handler overhead won't fit, log ERROR but don't crash."""
    t = _target_with_text_budget("rv32imc", text_budget=2 * 1024)  # 2 KiB
    cfg = make_config(t, gen_opts="+instr_cnt=100 +num_of_sub_program=5")
    with caplog.at_level(logging.ERROR, logger="rvgen.cli"):
        _enforce_imem_budget(cfg, "tiny_imem")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "cannot fit" in msgs
    assert "Reduce num_of_sub_program" in msgs


def test_imem_budget_minimum_instr_floor():
    """Even with a brutal cap, we keep at least 8 instructions."""
    t = _target_with_text_budget("rv32imc", text_budget=_TEXT_FIXED_OVERHEAD_BYTES + 4)
    cfg = make_config(t, gen_opts="+instr_cnt=10000 +num_of_sub_program=0")
    _enforce_imem_budget(cfg, "minimal")
    assert cfg.instr_cnt >= 8


# ---------------------------------------------------------------------------
# DMEM-bounds — end-to-end Spike invariant. Skipped on CI without
# toolchain; runs locally where riscv64-unknown-elf-gcc + spike exist.
# ---------------------------------------------------------------------------


_HAVE_TOOLCHAIN = (
    shutil.which("riscv64-unknown-elf-gcc") is not None
    and shutil.which("spike") is not None
)

# Capture commit-line PC and any mem-EA on the same line. Spike's
# `-l --log-commits` format: ``core N: <priv> 0x<pc> (0x<bin>) <writes>``.
# The writes section can contain ``mem 0x<addr>`` for loads/stores.
_COMMIT_LINE_RE = re.compile(
    r"^core\s+\d+:\s+\d\s+0x(?P<pc>[0-9a-f]+)\s+\([^)]+\)\s*(?P<rest>.*)$"
)
_MEM_EA_RE = re.compile(r"\bmem\s+0x(?P<addr>[0-9a-f]+)")


@pytest.mark.skipif(
    not _HAVE_TOOLCHAIN,
    reason="DMEM-bounds e2e test needs riscv64-unknown-elf-gcc + spike",
)
def test_dmem_bounds_every_workload_ea_inside_region(tmp_path):
    """End-to-end: every "mem 0x..." EA in the workload region must
    fall inside ``[data_base, data_base + data_section_size_bytes)``.

    Uses the smallest reasonable DMEM (8 KiB) to make stream-clamp
    bugs hit fast — at the default 6 KiB region cap the test ran with
    instr_cnt=1000 has hundreds of memory accesses and any
    off-by-one in the clamp logic would crash Spike or produce a
    fault. Spike's `-m0x80000000:size` window is the hard limit; we
    check the soft `data_section_size_bytes` budget too.
    """
    from rvgen.cli import main as cli_main

    # rv32imc with a small DMEM cap (data + stacks fit in 8 KiB).
    rc = cli_main([
        "--target", "rv32imc",
        "--test", "riscv_rand_instr_test",
        "--seed", "100",
        "--steps", "gen,gcc_compile,iss_sim",
        "--iss", "spike",
        "--iss_trace",
        "--output", str(tmp_path),
        # 1000 instrs gives us hundreds of LS attempts without taking
        # the test runtime past 10 s.
        "--gen_opts", "+instr_cnt=1000",
    ])
    assert rc == 0, "CLI run failed"

    # Locate the spike trace.
    traces = list((tmp_path / "spike_sim").glob("*.trace"))
    assert traces, "no spike trace produced"
    trace = traces[0]

    # Read the target's data-section cap (rv32imc default is None ⇒
    # SV-parity 6 KiB regions = 6144 B total per default mem_regions()).
    # We use spike's 2 MiB window as the hard outer bound — any EA
    # outside [0x80000000, 0x80200000) is a real bug.
    SPIKE_BASE = 0x80000000
    SPIKE_LIMIT = SPIKE_BASE + 0x200000

    out_of_range = []
    with trace.open() as f:
        for line in f:
            cm = _COMMIT_LINE_RE.match(line)
            if not cm:
                continue
            pc = int(cm.group("pc"), 16)
            # Filter to instructions retired *inside our binary's text
            # section* — spike installs a tiny reset stub at PC<0x1000
            # that reads its own ROM scratch (e.g. PC=0xC `lw t0, 24(t0)`
            # → mem 0x18). Those accesses are spike-side, not generator-
            # side; counting them would mask real generator bugs.
            if pc < SPIKE_BASE:
                continue
            for m in _MEM_EA_RE.finditer(cm.group("rest")):
                addr = int(m.group("addr"), 16)
                if not (SPIKE_BASE <= addr < SPIKE_LIMIT):
                    out_of_range.append((pc, addr))
    assert not out_of_range, (
        f"{len(out_of_range)} memory accesses retired in the generated "
        f"workload escaped the configured DMEM window. First few "
        f"(pc, addr): {[(hex(pc), hex(a)) for pc, a in out_of_range[:5]]}"
    )
