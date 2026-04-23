"""LR/SC and AMO directed streams — port of ``src/riscv_amo_instr_lib.sv``."""

from __future__ import annotations

import random
from dataclasses import dataclass

from rvgen.isa.enums import RiscvInstrGroup, RiscvInstrName, RiscvReg
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import get_rand_instr, randomize_gpr_operands
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams import register_stream
from rvgen.streams.directed import _LaPseudo


@dataclass
class LrScInstrStream(DirectedInstrStream):
    """Single LR/SC pair (SV: ``riscv_lr_sc_instr_stream``, src/riscv_amo_instr_lib.sv:138)."""

    num_mixed_instr: int = 0

    def build(self) -> None:
        if self.num_mixed_instr == 0:
            self.num_mixed_instr = self.rng.randint(0, 10)

        xlen = self.cfg.target.xlen
        # Pick LR/SC pair based on XLEN + supported ISA.
        has_rv32a = RiscvInstrGroup.RV32A in self.cfg.target.supported_isa
        has_rv64a = RiscvInstrGroup.RV64A in self.cfg.target.supported_isa and xlen >= 64
        lr_choices = []
        sc_choices = []
        if has_rv32a:
            lr_choices.append(RiscvInstrName.LR_W)
            sc_choices.append(RiscvInstrName.SC_W)
        if has_rv64a:
            lr_choices.append(RiscvInstrName.LR_D)
            sc_choices.append(RiscvInstrName.SC_D)
        if not lr_choices:
            return  # No atomic support on this target.
        lr_name = self.rng.choice(lr_choices)
        sc_name = self.rng.choice(sc_choices)

        reserved = set(self.cfg.reserved_regs)
        pool = [r for r in RiscvReg if r not in reserved and r != RiscvReg.ZERO]
        base = self.rng.choice(pool)
        data = self.rng.choice(pool)
        dest = self.rng.choice(pool)
        status = self.rng.choice(pool)

        la = _LaPseudo()
        la.rd = base
        la.imm_str = "amo_0"
        la.atomic = True
        self.instr_list.append(la)

        lr = get_instr(lr_name)
        lr.rd = dest
        lr.rs1 = base
        self.instr_list.append(lr)

        # A handful of filler arithmetic instructions between LR and SC
        # (SV restricts to base I integer). We just use ARITHMETIC.
        from rvgen.isa.enums import RiscvInstrCategory
        for _ in range(self.num_mixed_instr):
            instr = get_rand_instr(
                self.rng,
                self.avail,
                include_category=[RiscvInstrCategory.ARITHMETIC, RiscvInstrCategory.LOGICAL],
                exclude_instr=[RiscvInstrName.CSRRW],  # keep it tame
            )
            randomize_gpr_operands(
                instr, self.rng, self.cfg,
                reserved_rd=[base, dest],  # don't clobber the LR-reserved regs
            )
            if instr.has_imm:
                instr.randomize_imm(self.rng, xlen=xlen)
            instr.post_randomize()
            self.instr_list.append(instr)

        sc = get_instr(sc_name)
        sc.rd = status
        sc.rs1 = base
        sc.rs2 = data
        self.instr_list.append(sc)


@dataclass
class AmoInstrStream(DirectedInstrStream):
    """Back-to-back AMO instructions (SV: ``riscv_amo_instr_stream``, src/riscv_amo_instr_lib.sv:215)."""

    num_amo: int = 0

    def build(self) -> None:
        if self.num_amo == 0:
            self.num_amo = self.rng.randint(1, 10)

        xlen = self.cfg.target.xlen
        has_rv32a = RiscvInstrGroup.RV32A in self.cfg.target.supported_isa
        has_rv64a = RiscvInstrGroup.RV64A in self.cfg.target.supported_isa and xlen >= 64

        from rvgen.isa.amo import _W_INSTRS, _D_INSTRS, _LR_INSTRS
        candidates = []
        if has_rv32a:
            candidates.extend(n for n in _W_INSTRS if n not in _LR_INSTRS
                              and n != RiscvInstrName.SC_W)
        if has_rv64a:
            candidates.extend(n for n in _D_INSTRS if n not in _LR_INSTRS
                              and n != RiscvInstrName.SC_D)
        if not candidates:
            return

        reserved = set(self.cfg.reserved_regs)
        pool = [r for r in RiscvReg if r not in reserved and r != RiscvReg.ZERO]
        base = self.rng.choice(pool)

        la = _LaPseudo()
        la.rd = base
        la.imm_str = "amo_0"
        la.atomic = True
        self.instr_list.append(la)

        for _ in range(self.num_amo):
            name = self.rng.choice(candidates)
            amo = get_instr(name)
            # rd != base (can't self-overwrite), rs1 = base.
            amo.rs1 = base
            amo.rs2 = self.rng.choice([r for r in pool if r != base])
            amo.rd = self.rng.choice([r for r in pool if r != base])
            amo.randomize_imm(self.rng, xlen=xlen)  # sets aq/rl
            amo.post_randomize()
            self.instr_list.append(amo)


register_stream("riscv_lr_sc_instr_stream", LrScInstrStream)
register_stream("riscv_amo_instr_stream", AmoInstrStream)
