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


def test_arm_timer_irq_uses_two_halves_on_rv32():
    # RV32: MTIMECMP is 64-bit; the safe update sequence writes the
    # high half to 0xFFFFFFFF first (unreachable), then commits the
    # new low + high, so the comparator is never transiently low.
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv32imc()))
    lines = asm.splitlines()
    # Look for the signature shape rather than exact register names —
    # those depend on cfg.gpr[] and shouldn't be hardcoded in tests.
    hi_safe = next(i for i, l in enumerate(lines) if "sw" in l and "4(t0)" in l)
    lo_commit = next(
        i for i, l in enumerate(lines)
        if "sw" in l and "0(t0)" in l and i > hi_safe
    )
    hi_commit = next(
        i for i, l in enumerate(lines)
        if "sw" in l and "4(t0)" in l and i > lo_commit
    )
    assert hi_safe < lo_commit < hi_commit  # hi-safe → lo-commit → hi-commit


def test_arm_timer_irq_uses_ld_on_rv64():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv64gc()))
    assert "ld t2," in asm
    assert "sd t2," in asm


def test_arm_timer_irq_delta_customizable():
    asm = "\n".join(gen_arm_timer_irq(_cfg_rv32imc(), delta=1024))
    # On RV32 the delta lands in the low-half register (gpr1 = t1).
    assert "addi t1, t1, 1024" in asm
    asm64 = "\n".join(gen_arm_timer_irq(_cfg_rv64gc(), delta=1024))
    # On RV64 the delta lands in the MTIME value register (gpr2 = t2).
    assert "addi t2, t2, 1024" in asm64


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


def test_clear_timer_irq_writes_minus_one_to_both_halves_on_rv32():
    asm = "\n".join(gen_clear_timer_irq(_cfg_rv32imc()))
    assert "0x2004000" in asm
    assert "li t1, -1" in asm
    # Both halves must be written — leaving the high half stale makes
    # the disarm effective only for ~4.3B cycles instead of ~infinity.
    assert "sw t1, 0(t0)" in asm
    assert "sw t1, 4(t0)" in asm


def test_clear_timer_irq_writes_minus_one_single_sd_on_rv64():
    asm = "\n".join(gen_clear_timer_irq(_cfg_rv64gc()))
    assert "li t1, -1" in asm
    assert "sd t1, 0(t0)" in asm
    # No half-write on RV64 — one sd covers the whole 64-bit register.
    assert "sw t1, 4(t0)" not in asm


def test_clint_base_read_from_target_cfg():
    # When a target overrides clint_base, the emitted asm must reflect
    # that — regression against the old hardcoded 0x02000000.
    from rvgen.targets import TargetCfg, get_target
    base = get_target("rv32imc")
    # Rebuild a TargetCfg with a different CLINT base (simulating a
    # custom SoC).
    custom = TargetCfg(**{**{f.name: getattr(base, f.name)
                             for f in base.__dataclass_fields__.values()},
                          "clint_base": 0x10000000})
    from rvgen.config import Config
    from rvgen.isa.enums import PrivilegedMode
    cfg = Config(target=custom, init_privileged_mode=PrivilegedMode.MACHINE_MODE)
    asm = "\n".join(gen_arm_timer_irq(cfg))
    assert "0x10004000" in asm  # mtimecmp at base + 0x4000
    assert "0x1000bff8" in asm  # mtime at base + 0xBFF8
    assert "0x2004000" not in asm


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
