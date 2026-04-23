"""Interrupt-arming helpers: CLINT timer priming + software-IRQ kick.

All CLINT addresses come from :attr:`Config.target`:

- ``target.clint_base`` — SoC CLINT base (SiFive-CLINT default ``0x02000000``).
- ``target.mtimecmp_offset`` — MTIMECMP[0] = ``clint_base + mtimecmp_offset``.
  Per-hart entry = ``MTIMECMP[0] + 8 * hart`` (MTIMECMP is 64-bit).
- ``target.mtime_offset``    — MTIME (shared across harts), 64-bit read-only.
- ``target.msip_offset``     — MSIP[0] = ``clint_base + msip_offset``.
  Per-hart entry = ``MSIP[0] + 4 * hart`` (MSIP is 32-bit).

For a test to fire a timer interrupt:

1. Boot enables ``MIE.MTIE`` and ``MSTATUS.MIE`` (via ``enable_interrupt``
   + ``enable_timer_irq`` in the Config).
2. :func:`gen_arm_timer_irq` emits the asm that reads MTIME and writes
   ``MTIMECMP = MTIME + delta``.
3. Hardware raises ``MIP.MTIP`` when ``MTIME >= MTIMECMP``.
4. The ISR in :mod:`rvgen.privileged.trap` calls
   :func:`gen_clear_timer_irq` (via the enable_timer_irq gate) to push
   MTIMECMP forward so MTIP deasserts before ``mret``.

NOTE (RV32): MTIMECMP is architecturally 64-bit on all XLENs. On RV32
the safe update pattern writes 0xFFFFFFFF to the low half first, so the
comparator is "too big" while the high half is being updated. The
``-1`` disarm path implemented here is safe regardless of order because
both halves land at 0xFFFFFFFF — the full 64-bit register tops out at
0xFFFFFFFF_FFFFFFFF and MTIP can't re-assert. Scheduled-deadline
arming (:func:`gen_arm_timer_irq` with a finite delta) on RV32 goes
through the proper two-halves-safe sequence.
"""

from __future__ import annotations

from rvgen.config import Config
from rvgen.isa.enums import LABEL_STR_LEN
from rvgen.isa.utils import hart_prefix


# When :func:`gen_arm_timer_irq` picks a deadline N cycles in the
# future, too-small N risks firing before the arming sequence itself
# completes; too-large pushes the IRQ beyond a short test body.
# 64 cycles is empirically safe on Spike for the typical random body.
_TIMER_DELTA_DEFAULT = 64

_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


def _mtimecmp_addr(cfg: Config, hart: int, clint_base: int | None) -> int:
    base = clint_base if clint_base is not None else cfg.target.clint_base
    return base + cfg.target.mtimecmp_offset + hart * 8


def _mtime_addr(cfg: Config, clint_base: int | None) -> int:
    base = clint_base if clint_base is not None else cfg.target.clint_base
    return base + cfg.target.mtime_offset


def _msip_addr(cfg: Config, hart: int, clint_base: int | None) -> int:
    base = clint_base if clint_base is not None else cfg.target.clint_base
    return base + cfg.target.msip_offset + hart * 4


def gen_arm_timer_irq(
    cfg: Config,
    *,
    hart: int = 0,
    delta: int = _TIMER_DELTA_DEFAULT,
    clint_base: int | None = None,
) -> list[str]:
    """Emit asm to schedule a timer interrupt ``delta`` cycles from now.

    RV32 — two-halves-safe sequence:
        li   tA, MTIMECMP
        li   tB, -1
        sw   tB, 4(tA)                  # MTIMECMP_HI ← 0xFFFFFFFF (safe high)
        li   tC, MTIME
        lw   tD, 4(tC)                  # mtime_hi
        lw   tB, 0(tC)                  # mtime_lo
        addi tB, tB, delta              # lo += delta (ignoring carry — delta << 2^32)
        sw   tB, 0(tA)                  # MTIMECMP_LO ← new lo
        sw   tD, 4(tA)                  # MTIMECMP_HI ← mtime_hi

    RV64 — single 64-bit write:
        li   tA, MTIMECMP
        li   tB, MTIME
        ld   tC, 0(tB)                  # MTIME
        addi tC, tC, delta
        sd   tC, 0(tA)                  # MTIMECMP = MTIME + delta

    Parameters
    ----------
    cfg : Config
        The instruction-generator config. Uses ``cfg.gpr`` as scratch
        and ``cfg.target`` for CLINT addresses.
    hart : int
        Hart index.
    delta : int
        How far in the future MTIMECMP lands, in MTIME ticks.
    clint_base : int or None
        If provided, overrides ``cfg.target.clint_base``. Intended for
        unit tests; production code should configure the target instead.
    """
    mtimecmp = _mtimecmp_addr(cfg, hart, clint_base)
    mtime = _mtime_addr(cfg, clint_base)
    gpr0, gpr1, gpr2 = cfg.gpr[0], cfg.gpr[1], cfg.gpr[2]
    xlen = cfg.target.xlen if cfg.target else 32

    prefix = hart_prefix(hart, cfg.num_of_harts)
    lines = [_line(f"# arm timer IRQ ({prefix}hart={hart}, +{delta} cycles)")]
    lines.append(_line(f"li {gpr0.abi}, 0x{mtimecmp:x}"))

    if xlen == 32:
        # Step 1: write MTIMECMP_HI = 0xFFFFFFFF so the comparator is
        # "unreachable" while we update the low half.
        gpr3 = cfg.gpr[3]
        lines.append(_line(f"li {gpr1.abi}, -1"))
        lines.append(_line(f"sw {gpr1.abi}, 4({gpr0.abi})"))
        # Step 2: read MTIME_HI then MTIME_LO.
        lines.append(_line(f"li {gpr2.abi}, 0x{mtime:x}"))
        lines.append(_line(f"lw {gpr3.abi}, 4({gpr2.abi})"))
        lines.append(_line(f"lw {gpr1.abi}, 0({gpr2.abi})"))
        # Step 3: apply delta to the low half. Ignore carry — delta is
        # small compared to 2^32, so a wrap is only a concern for tests
        # that sit near MTIME_LO's top-of-range boundary (rare; spike
        # MTIME starts at 0 and counts up slowly relative to delta).
        lines.append(_line(f"addi {gpr1.abi}, {gpr1.abi}, {delta}"))
        # Step 4: write MTIMECMP_LO, then MTIMECMP_HI to commit the
        # new 64-bit value.
        lines.append(_line(f"sw {gpr1.abi}, 0({gpr0.abi})"))
        lines.append(_line(f"sw {gpr3.abi}, 4({gpr0.abi})"))
    else:
        # RV64: a single sd is atomic vs. MTIP re-evaluation.
        lines.append(_line(f"li {gpr1.abi}, 0x{mtime:x}"))
        lines.append(_line(f"ld {gpr2.abi}, 0({gpr1.abi})"))
        lines.append(_line(f"addi {gpr2.abi}, {gpr2.abi}, {delta}"))
        lines.append(_line(f"sd {gpr2.abi}, 0({gpr0.abi})"))
    return lines


def gen_clear_timer_irq(
    cfg: Config,
    *,
    hart: int = 0,
    clint_base: int | None = None,
) -> list[str]:
    """Push MTIMECMP to a far value so MTIP deasserts before ``mret``.

    On RV32 MTIMECMP is 64-bit; writing only the low half leaves the
    high half at whatever it was. After reset that's 0, which means
    the disarm is only effective for ~4.3 billion cycles — long enough
    for any spike test but still a latent bug. Emit both halves so the
    full 64-bit register lands at 0xFFFFFFFF_FFFFFFFF (effectively
    infinity).
    """
    mtimecmp = _mtimecmp_addr(cfg, hart, clint_base)
    gpr0, gpr1 = cfg.gpr[0], cfg.gpr[1]
    xlen = cfg.target.xlen if cfg.target else 32

    lines = [_line("# disarm timer IRQ (MTIMECMP = -1)")]
    lines.append(_line(f"li {gpr0.abi}, 0x{mtimecmp:x}"))
    lines.append(_line(f"li {gpr1.abi}, -1"))
    if xlen == 32:
        # Write low then high so the 64-bit value lands at all-ones
        # without a transient where one half stays stale.
        lines.append(_line(f"sw {gpr1.abi}, 0({gpr0.abi})"))
        lines.append(_line(f"sw {gpr1.abi}, 4({gpr0.abi})"))
    else:
        lines.append(_line(f"sd {gpr1.abi}, 0({gpr0.abi})"))
    return lines


def gen_arm_software_irq(
    cfg: Config,
    *,
    hart: int = 0,
    clint_base: int | None = None,
) -> list[str]:
    """Trigger an M-software interrupt by writing 1 to MSIP[hart].

    Assumes MSTATUS.MIE and MIE.MSIE are already set at boot
    (``cfg.enable_interrupt=True`` does this via boot.py). MSIP is
    always a 32-bit write — valid on both RV32 and RV64.
    """
    msip = _msip_addr(cfg, hart, clint_base)
    gpr0, gpr1 = cfg.gpr[0], cfg.gpr[1]

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
    msip = _msip_addr(cfg, hart, clint_base)
    gpr0 = cfg.gpr[0]

    lines = [_line("# clear M-software IRQ (MSIP ← 0)")]
    lines.append(_line(f"li {gpr0.abi}, 0x{msip:x}"))
    lines.append(_line(f"sw zero, 0({gpr0.abi})"))
    return lines
