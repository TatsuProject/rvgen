"""Hypervisor-extension directed stream.

Emits a short atomic block of H-ext instructions (``hlv*`` / ``hsv*`` /
``hfence.*``) so functional-coverage collectors see them in random tests
without having to wait for the basic random walker to pick a SYNCH or
LOAD/STORE op of the right kind.

Pull into a test with::

    +directed_instr_0=riscv_hypervisor_instr,4

The stream guards every load/store with a base register that has been
loaded with the per-hart user-stack pointer, so the address dereferences
inside something resembling actual mapped memory. Two-stage translation
isn't yet active so spike will treat these as regular loads/stores —
the value is exercising the *opcode path* and the asm column shape, not
the privilege-level effect.
"""

from __future__ import annotations

from dataclasses import dataclass

from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.isa.factory import INSTR_REGISTRY, get_instr
from rvgen.isa.h_ext import (
    H_FENCE_INSTR_NAMES,
    H_LOAD_INSTR_NAMES,
    H_STORE_INSTR_NAMES,
)
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams.directed import _LaPseudo


@dataclass
class HypervisorInstrStream(DirectedInstrStream):
    """Emit a small mix of H-ext loads, stores, and TLB-flushes."""

    num_of_h_instr: int = 0

    def build(self) -> None:
        # If the target doesn't actually advertise H-ext, skip silently.
        if RiscvInstrName.HFENCE_VVMA not in INSTR_REGISTRY:
            return
        if self.num_of_h_instr == 0:
            self.num_of_h_instr = self.rng.randint(3, 6)
        reserved = set(self.cfg.reserved_regs)
        reg_pool = [r for r in RiscvReg if r not in reserved and r != RiscvReg.ZERO]
        if not reg_pool:
            return

        # Pre-pin a base register to the user-stack symbol so loads/stores
        # land in mapped memory (boot-time region allocator carved this out).
        base = self.rng.choice(reg_pool)
        la = _LaPseudo()
        la.rd = base
        la.imm_str = f"h{self.hart}_user_stack_start"
        self.instr_list.append(la)

        for _ in range(self.num_of_h_instr):
            kind = self.rng.choices(
                ["load", "store", "fence"], weights=[5, 5, 1]
            )[0]
            if kind == "load":
                name = self.rng.choice(H_LOAD_INSTR_NAMES)
                ins = get_instr(name)
                ins.set_rand_mode()
                ins.rd = self.rng.choice(reg_pool)
                ins.rs1 = base
                ins.post_randomize()
                self.instr_list.append(ins)
            elif kind == "store":
                name = self.rng.choice(H_STORE_INSTR_NAMES)
                ins = get_instr(name)
                ins.set_rand_mode()
                ins.rs1 = base
                ins.rs2 = self.rng.choice(reg_pool)
                ins.post_randomize()
                self.instr_list.append(ins)
            else:
                name = self.rng.choice(H_FENCE_INSTR_NAMES)
                ins = get_instr(name)
                ins.set_rand_mode()
                ins.rs1 = self.rng.choice(reg_pool)
                ins.rs2 = self.rng.choice(reg_pool)
                ins.post_randomize()
                self.instr_list.append(ins)


register_stream("riscv_hypervisor_instr", HypervisorInstrStream)
