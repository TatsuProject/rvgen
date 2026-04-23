"""Privileged-mode generation: boot CSR sequence, trap handlers, interrupts.

Paging / PMP / debug ROM live under separate modules (not yet ported).
"""

from rvgen.privileged.boot import (
    gen_pre_enter_privileged_mode,
    gen_setup_misa,
)
from rvgen.privileged.interrupts import (
    gen_arm_software_irq,
    gen_arm_timer_irq,
    gen_clear_software_irq,
    gen_clear_timer_irq,
)
from rvgen.privileged.trap import gen_trap_handler

__all__ = [
    "gen_arm_software_irq",
    "gen_arm_timer_irq",
    "gen_clear_software_irq",
    "gen_clear_timer_irq",
    "gen_pre_enter_privileged_mode",
    "gen_setup_misa",
    "gen_trap_handler",
]
