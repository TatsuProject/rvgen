"""Loop directed stream — port of ``src/riscv_loop_instr.sv`` (simplified)."""

from __future__ import annotations

from dataclasses import dataclass

from rvgen.isa.base import Instr
from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import get_rand_instr
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams import register_stream


@dataclass
class LoopInstr(DirectedInstrStream):
    """Port of SV ``riscv_loop_instr`` (simplified for Phase 1 MVP).

    Emits a single countdown loop:

        addi <cnt>, zero, <N>
     0: <body instr>
        addi <cnt>, <cnt>, -1
        bne <cnt>, zero, 0b
    """

    num_of_instr_in_loop: int = 0

    def build(self) -> None:
        from rvgen.isa.enums import RiscvInstrCategory
        reserved = set(self.cfg.reserved_regs)
        pool = [r for r in RiscvReg if r not in reserved and r != RiscvReg.ZERO]
        cnt_reg = self.rng.choice(pool)

        if self.num_of_instr_in_loop == 0:
            self.num_of_instr_in_loop = self.rng.randint(5, 15)

        # 1) Initialize counter to a positive N in [3..20].
        init_val = self.rng.randint(3, 20)
        init = get_instr(RiscvInstrName.ADDI)
        init.rd = cnt_reg
        init.rs1 = RiscvReg.ZERO
        init.imm = init_val
        init.post_randomize()
        self.instr_list.append(init)

        # 2) Body instructions (arithmetic / logical only). The loop counter
        # must not appear as any operand (rs1, rs2, or rd) — compressed 2-op
        # forms like C.ANDI/C.SRLI put the write-back register in the rs1
        # slot, so ``reserved_rd`` alone isn't enough. Pass an ``avail_regs``
        # pool that explicitly excludes ``cnt_reg`` to cover all operand slots.
        body_labeled = False
        avail_regs = tuple(
            r for r in pool if r != cnt_reg
        )
        for _ in range(self.num_of_instr_in_loop):
            body = get_rand_instr(
                self.rng,
                self.avail,
                include_category=[
                    RiscvInstrCategory.ARITHMETIC,
                    RiscvInstrCategory.LOGICAL,
                    RiscvInstrCategory.COMPARE,
                    RiscvInstrCategory.SHIFT,
                ],
                exclude_instr=[RiscvInstrName.ADDI],  # leave ADDI for counter dec
            )
            from rvgen.isa.filtering import randomize_gpr_operands
            randomize_gpr_operands(
                body,
                self.rng,
                self.cfg,
                avail_regs=avail_regs,
                reserved_rd=[cnt_reg],
            )
            if body.has_imm:
                body.randomize_imm(self.rng, xlen=self.cfg.target.xlen)
            body.post_randomize()
            if not body_labeled:
                body.label = f"{self.label or 'loop'}_target"
                body.has_label = True
                body_labeled = True
            self.instr_list.append(body)

        # 3) Decrement counter.
        dec = get_instr(RiscvInstrName.ADDI)
        dec.rd = cnt_reg
        dec.rs1 = cnt_reg
        dec.imm = (1 << 12) - 1  # -1 in 12-bit two's complement (0xFFF)
        dec.post_randomize()
        self.instr_list.append(dec)

        # 4) Conditional branch back to loop body label. GCC treats the
        # label as a plain symbol — the relocation resolves to a PC-relative
        # offset at link time. We set imm_str *after* post_randomize so it
        # doesn't get clobbered by the default signed-decimal stringification.
        bne = get_instr(RiscvInstrName.BNE)
        bne.rs1 = cnt_reg
        bne.rs2 = RiscvReg.ZERO
        bne.branch_assigned = True
        bne.post_randomize()
        bne.imm_str = f"{self.label or 'loop'}_target"
        self.instr_list.append(bne)


register_stream("riscv_loop_instr", LoopInstr)
