"""Interrupt-arming helpers: CLINT timer priming + test termination.

Spike exposes a CLINT-style controller at 0x02000000 with:

- MTIMECMP0 at CLINT + 0x4000  (64-bit, 8 bytes)
- MTIME     at CLINT + 0xBFF8  (64-bit, 8 bytes, global wall clock)
- MSIP0     at CLINT + 0x0000  (32-bit, write 1 to trigger M-software IRQ)

For a timer interrupt to fire we must:
 1. Load current MTIME.
 2. Compute a near deadline (MTIME + N).
 3. Store it to MTIMECMP.
 4. Have already set MSTATUS.MIE and MIE.MTIE (done in boot.py when
    ``cfg.enable_interrupt && cfg.enable_timer_irq``).

Once the timer fires, MTIP stays asserted until MTIMECMP is re-armed
(hardware behaviour). Our timer ISR pushes MTIMECMP to a far value
so the interrupt is deasserted before the handler returns.

Software (MSIP) interrupts are simpler: one 32-bit store kicks them.

These helpers emit the asm lines only; boot.py is responsible for
enabling interrupt globally. Call ``gen_arm_timer_irq()`` right before
the random-instruction body if you want a timer interrupt to fire inside
the test. ``gen_clear_timer_irq()`` is the first thing the ISR should do.
"""

from __future__ import annotations

from rvgen.config import Config
from rvgen.isa.enums import LABEL_STR_LEN, RiscvReg
from rvgen.isa.utils import hart_prefix


# Spike CLINT defaults (riscv-isa-sim: riscv/sim.h and sim.cc). These
# match QEMU virt too — if you target a different core, override via
# the cfg hook ``cfg.clint_base``.
_CLINT_BASE_DEFAULT = 0x02000000
_MTIMECMP_OFFSET = 0x4000
_MTIME_OFFSET = 0xBFF8
_MSIP_OFFSET = 0x0000

# How many cycles into the future MTIMECMP is primed. Too-small and the
# IRQ may fire before setup completes; too-large and the interrupt never
# reaches the test body. 64 cycles works empirically on spike.
_TIMER_DELTA_DEFAULT = 64

_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


def gen_arm_timer_irq(
    cfg: Config,
    *,
    hart: int = 0,
    delta: int = _TIMER_DELTA_DEFAULT,
    clint_base: int | None = None,
) -> list[str]:
    """Emit asm to schedule a timer interrupt ``delta`` cycles from now.

    The sequence:
        li    t0, CLINT + MTIMECMP_OFFSET
        li    t1, CLINT + MTIME_OFFSET
        l[wd] t2, 0(t1)              # t2 = current MTIME
        addi  t2, t2, delta
        s[wd] t2, 0(t0)              # MTIMECMP = MTIME + delta

    On RV32 MTIME is 64-bit but accessed as two 32-bit halves; we only
    care about the low word for small deltas (<< 2^32 cycles).

    Parameters
    ----------
    cfg : Config
        Instruction-generator config. Uses ``cfg.scratch_reg``, ``cfg.gpr``.
    hart : int
        Hart index (used only for the hart prefix on comments).
    delta : int
        How far in the future MTIMECMP lands.
    clint_base : int or None
        Override the default CLINT base. If None, uses 0x02000000.
    """
    if clint_base is None:
        clint_base = _CLINT_BASE_DEFAULT
    mtimecmp = clint_base + _MTIMECMP_OFFSET + hart * 8
    mtime = clint_base + _MTIME_OFFSET
    gpr0 = cfg.gpr[0]
    gpr1 = cfg.gpr[1]
    gpr2 = cfg.gpr[2]

    xlen = cfg.target.xlen if cfg.target else 32
    load = "lw" if xlen == 32 else "ld"
    store = "sw" if xlen == 32 else "sd"

    prefix = hart_prefix(hart, cfg.num_of_harts)
    comment = f"# arm timer IRQ ({prefix}hart={hart}, +{delta} cycles)"
    lines = [_line(comment)]
    lines.append(_line(f"li {gpr0.abi}, 0x{mtimecmp:x}"))
    lines.append(_line(f"li {gpr1.abi}, 0x{mtime:x}"))
    lines.append(_line(f"{load} {gpr2.abi}, 0({gpr1.abi})"))
    lines.append(_line(f"addi {gpr2.abi}, {gpr2.abi}, {delta}"))
    lines.append(_line(f"{store} {gpr2.abi}, 0({gpr0.abi})"))
    return lines


def gen_clear_timer_irq(
    cfg: Config,
    *,
    hart: int = 0,
    clint_base: int | None = None,
) -> list[str]:
    """Push MTIMECMP to a far value so MTIP deasserts before the ISR returns.

    Call this as the first thing in the timer ISR (or just after the
    standard push_gpr_to_kernel_stack prelude). Without it the ISR will
    re-enter immediately after ``mret`` because MTIP is still asserted.
    """
    if clint_base is None:
        clint_base = _CLINT_BASE_DEFAULT
    mtimecmp = clint_base + _MTIMECMP_OFFSET + hart * 8
    gpr0 = cfg.gpr[0]
    gpr1 = cfg.gpr[1]

    xlen = cfg.target.xlen if cfg.target else 32
    store = "sw" if xlen == 32 else "sd"

    # Far value: all-ones (deassertion for MTIP requires MTIMECMP > MTIME;
    # -1 trivially satisfies that).
    lines = [_line("# disarm timer IRQ (MTIMECMP = -1)")]
    lines.append(_line(f"li {gpr0.abi}, 0x{mtimecmp:x}"))
    lines.append(_line(f"li {gpr1.abi}, -1"))
    lines.append(_line(f"{store} {gpr1.abi}, 0({gpr0.abi})"))
    return lines


def gen_arm_software_irq(
    cfg: Config,
    *,
    hart: int = 0,
    clint_base: int | None = None,
) -> list[str]:
    """Trigger an M-software interrupt by writing 1 to MSIP[hart].

    Assumes MSTATUS.MIE and MIE.MSIE are already set at boot
    (``cfg.enable_interrupt=True`` does this via boot.py).
    """
    if clint_base is None:
        clint_base = _CLINT_BASE_DEFAULT
    msip = clint_base + _MSIP_OFFSET + hart * 4
    gpr0 = cfg.gpr[0]
    gpr1 = cfg.gpr[1]

    lines = [_line("# arm M-software IRQ (MSIP ← 1)")]
    lines.append(_line(f"li {gpr0.abi}, 0x{msip:x}"))
    lines.append(_line(f"li {gpr1.abi}, 1"))
    lines.append(_line(f"sw {gpr1.abi}, 0({gpr0.abi})"))
    return lines


def gen_clear_software_irq(
    cfg: Config,
    *,
    hart: int = 0,
    clint_base: int | None = None,
) -> list[str]:
    """Clear MSIP (software interrupt). Call from the ISR."""
    if clint_base is None:
        clint_base = _CLINT_BASE_DEFAULT
    msip = clint_base + _MSIP_OFFSET + hart * 4
    gpr0 = cfg.gpr[0]

    lines = [_line("# clear M-software IRQ (MSIP ← 0)")]
    lines.append(_line(f"li {gpr0.abi}, 0x{msip:x}"))
    lines.append(_line(f"sw zero, 0({gpr0.abi})"))
    return lines
