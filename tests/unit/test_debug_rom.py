"""Tests for rvgen.privileged.debug_rom."""

from __future__ import annotations

import pytest

from rvgen.config import Config
from rvgen.privileged.debug_rom import (
    gen_debug_exception_handler,
    gen_debug_rom_section,
)
from rvgen.targets.builtin import BUILTIN_TARGETS


@pytest.fixture
def rv64gc():
    return BUILTIN_TARGETS["rv64gc"]


def test_disabled_by_default_returns_empty(rv64gc):
    cfg = Config(target=rv64gc)
    assert cfg.gen_debug_section is False
    assert gen_debug_rom_section(cfg) == []
    assert gen_debug_exception_handler(cfg) == []


def test_enabled_emits_label_and_dret(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True)
    out = gen_debug_rom_section(cfg)
    text = "\n".join(out)
    assert "debug_rom:" in text
    assert "debug_end:" in text
    assert "dret" in text


def test_dpc_update_inspects_dcsr_cause(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True)
    text = "\n".join(gen_debug_rom_section(cfg))
    # DCSR is 0x7b0; we read it, slli/srli to extract cause, branch.
    assert "csrr" in text and "0x7b0" in text
    assert "slli" in text and "0x17" in text
    assert "srli" in text and "0x1d" in text


def test_dpc_update_increments_dpc_when_cause_is_ebreak(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True)
    text = "\n".join(gen_debug_rom_section(cfg))
    # DPC is 0x7b1.
    assert "0x7b1" in text


def test_dcsr_ebreak_bits_emitted_per_supported_mode(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True, set_dcsr_ebreak=True)
    text = "\n".join(gen_debug_rom_section(cfg))
    # rv64gc supports M+S+U → all three li values should appear.
    assert "0x8000" in text   # ebreakm
    assert "0x2000" in text   # ebreaks
    assert "0x1000" in text   # ebreaku


def test_dcsr_ebreak_only_m_for_m_only_target():
    cfg = Config(target=BUILTIN_TARGETS["rv64imc"], gen_debug_section=True,
                 set_dcsr_ebreak=True)
    text = "\n".join(gen_debug_rom_section(cfg))
    # rv64imc is M-mode only.
    assert "0x8000" in text
    assert "0x2000" not in text
    assert "0x1000" not in text


def test_single_step_logic_uses_dscratch0_and_dscratch1(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True,
                 enable_debug_single_step=True, single_step_iterations=8)
    text = "\n".join(gen_debug_rom_section(cfg))
    # DSCRATCH0 = 0x7b2, DSCRATCH1 = 0x7b3.
    assert "0x7b2" in text
    assert "0x7b3" in text
    # The iterations literal must appear.
    assert "8" in text


def test_debug_exception_handler_emits_dret(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True)
    out = gen_debug_exception_handler(cfg)
    assert "debug_exception:" in "\n".join(out)
    assert any("dret" in s for s in out)


def test_hart_prefix_when_multi_hart(rv64gc):
    cfg = Config(target=rv64gc, gen_debug_section=True, num_of_harts=2)
    out = gen_debug_rom_section(cfg, hart=1)
    text = "\n".join(out)
    assert "h1_debug_rom:" in text
    assert "h1_debug_end:" in text
