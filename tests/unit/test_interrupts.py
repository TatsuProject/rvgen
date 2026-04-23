"""Tests for rvgen.privileged.interrupts — CLINT priming helpers."""

from __future__ import annotations

from rvgen.config import make_config
from rvgen.privileged.interrupts import (
    gen_arm_software_irq,
    gen_arm_timer_irq,
    gen_clear_software_irq,
    gen_clear_timer_irq,
)
from rvgen.targets import get_target


def _cfg_rv32imc(**kw):
    return make_config(get_target("rv32imc"), **kw)


def _cfg_rv64gc(**kw):
    return make_config(get_target("rv64gc"), **kw)


# ---------- arm_timer_irq ----------


def test_arm_timer_irq_emits_mtimecmp_address_rv32():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv32imc()))
    assert "0x2004000" in asm  # CLINT + 0x4000 = MTIMECMP0
    assert "0x200bff8" in asm  # CLINT + 0xBFF8 = MTIME


def test_arm_timer_irq_uses_lw_on_rv32():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv32imc()))
    assert "lw t2," in asm
    assert "sw t2," in asm


def test_arm_timer_irq_uses_ld_on_rv64():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv64gc()))
    assert "ld t2," in asm
    assert "sd t2," in asm


def test_arm_timer_irq_delta_customizable():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv32imc(), delta=1024))
    assert "addi t2, t2, 1024" in asm


def test_arm_timer_irq_per_hart_offset():
    # MTIMECMP[hart] = CLINT + 0x4000 + 8*hart.
    asm0 = "\n".join(gen_arm_timer_irq(_cfg_rv32imc(), hart=0))
    asm1 = "\n".join(gen_arm_timer_irq(_cfg_rv32imc(), hart=1))
    assert "0x2004000" in asm0
    assert "0x2004008" in asm1


def test_arm_timer_irq_custom_clint_base():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv32imc(), clint_base=0x10000000))
    assert "0x10004000" in asm


# ---------- clear_timer_irq ----------


def test_clear_timer_irq_writes_minus_one_to_mtimecmp():
    asm = "\n".join(gen_clear_timer_irq(_cfg_rv32imc()))
    assert "0x2004000" in asm
    assert "li t1, -1" in asm


# ---------- software IRQ ----------


def test_arm_software_irq_writes_msip():
    asm = "\n".join(gen_arm_software_irq(_cfg_rv32imc()))
    assert "0x2000000" in asm  # CLINT + 0 = MSIP0
    assert "li t1, 1" in asm


def test_arm_software_irq_per_hart_offset():
    asm1 = "\n".join(gen_arm_software_irq(_cfg_rv32imc(), hart=1))
    assert "0x2000004" in asm1


def test_clear_software_irq_zeroes_msip():
    asm = "\n".join(gen_clear_software_irq(_cfg_rv32imc()))
    assert "0x2000000" in asm
    assert "sw zero, 0(t0)" in asm
