"""Directed streams — int corner values, JAL chain, jump, push/pop stack.

Port of key streams from ``src/riscv_directed_instr_lib.sv``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import get_rand_instr, randomize_gpr_operands
from rvgen.streams.base import DirectedInstrStream


# ---------------------------------------------------------------------------
# riscv_int_numeric_corner_stream
# ---------------------------------------------------------------------------


_CORNER_BUCKETS = ("NormalValue", "Zero", "AllOne", "NegativeMax")


@dataclass
class IntNumericCornerStream(DirectedInstrStream):
    """SV: ``riscv_int_numeric_corner_stream`` (riscv_directed_instr_lib.sv:421).

    Initializes a small pool of registers to corner values (0, all-ones,
    min-signed, random) via ``li`` and then emits 15-30 random arithmetic
    instructions using only those registers.
    """

    num_of_avail_regs: int = 10

    def build(self) -> None:
        xlen = self.cfg.target.xlen
        reserved = set(self.cfg.reserved_regs) | {RiscvReg.ZERO, RiscvReg.RA, RiscvReg.GP}
        pool = [r for r in RiscvReg if r not in reserved]
        if len(pool) < self.num_of_avail_regs:
            self.num_of_avail_regs = len(pool)
        regs = self.rng.sample(pool, self.num_of_avail_regs)

        # 1) li each register to a corner value.
        for i, reg in enumerate(regs):
            bucket = self.rng.choice(_CORNER_BUCKETS)
            if bucket == "Zero":
                val = 0
            elif bucket == "AllOne":
                val = (1 << xlen) - 1
            elif bucket == "NegativeMax":
                val = 1 << (xlen - 1)
            else:
                val = self.rng.randint(0, (1 << xlen) - 1)
            li_instr = _make_li(reg, val)
            self.instr_list.append(li_instr)

        # 2) Emit 15-30 random ARITHMETIC/LOGICAL/COMPARE/SHIFT instructions
        #    constrained to the pool.
        count = self.rng.randint(15, 30)
        for _ in range(count):
            instr = get_rand_instr(
                self.rng,
                self.avail,
                include_category=[
                    RiscvInstrCategory.ARITHMETIC,
                    RiscvInstrCategory.LOGICAL,
                    RiscvInstrCategory.COMPARE,
                    RiscvInstrCategory.SHIFT,
                ],
            )
            randomize_gpr_operands(instr, self.rng, self.cfg, avail_regs=regs)
            if instr.has_imm:
                instr.randomize_imm(self.rng, xlen=xlen)
            instr.post_randomize()
            self.instr_list.append(instr)


def _make_li(rd: RiscvReg, value: int) -> Instr:
    """Build an ``li rd, 0x<value>`` pseudo-instruction.

    We don't have a real pseudo-instruction class yet; fake it by creating
    an ADDI and overriding :meth:`convert2asm`.
    """
    instr = _LiPseudo()
    instr.rd = rd
    instr.imm = value
    instr.imm_str = f"0x{value:x}"
    instr.atomic = True
    return instr


class _LiPseudo(Instr):
    """Minimal pseudo-instruction: ``li rd, imm``.

    We inherit from ``Instr`` but override convert2asm. The instr_name enum
    is LI (the pseudo enum value); we re-use the RiscvInstrName.LI member.
    """

    instr_name = RiscvInstrName.NOP  # placeholder; convert2asm hand-writes the output
    format = None  # type: ignore[assignment]
    category = RiscvInstrCategory.ARITHMETIC
    group = None  # type: ignore[assignment]

    def __init__(self) -> None:
        # Avoid the base Instr.__init__ which wants class-level `format`.
        self.rs1 = RiscvReg.ZERO
        self.rs2 = RiscvReg.ZERO
        self.rd = RiscvReg.ZERO
        self.csr = 0
        self.imm = 0
        self.has_rs1 = False
        self.has_rs2 = False
        self.has_rd = True
        self.has_imm = True
        self.imm_len = 32
        self.imm_mask = 0xFFFFFFFF
        self.imm_str = ""
        self.atomic = True
        self.branch_assigned = False
        self.is_branch_target = False
        self.has_label = False
        self.label = ""
        self.is_local_numeric_label = False
        self.is_illegal_instr = False
        self.is_hint_instr = False
        self.is_compressed = False
        self.is_floating_point = False
        self.process_load_store = False
        self.comment = ""
        self.idx = -1
        self.gpr_hazard = 0

    def set_imm_len(self) -> None:
        pass

    def set_rand_mode(self) -> None:
        pass

    def post_randomize(self) -> None:
        pass

    def get_instr_name(self) -> str:
        return "li"

    def convert2asm(self, prefix: str = "") -> str:
        body = f"li           {self.rd.abi}, {self.imm_str}"
        if self.comment:
            body = f"{body} #{self.comment}"
        return body

    def convert2bin(self, prefix: str = "") -> str:
        # LI expands at assembly time; no fixed binary.
        raise NotImplementedError("LI is a pseudo — let the assembler expand it")


# ---------------------------------------------------------------------------
# riscv_jal_instr — back-to-back JAL chain
# ---------------------------------------------------------------------------


@dataclass
class JalInstr(DirectedInstrStream):
    """SV: ``riscv_jal_instr`` (riscv_directed_instr_lib.sv:204).

    Emits a shuffled Hamiltonian JAL chain::

        jump_start:  jal ra, <order[0]>f
        0:           jal ra, <order[1]>f       # was at shuffled position order[0]
        1:           jal ra, ...
        …
        N:           <arithmetic end-sentinel>

    The key property (vs. our previous buggy impl) is that the jumps form a
    single linear traversal ``order[0] → order[1] → … → order[N-1] → end``;
    the old code picked the target directly from ``order[i]`` which made a
    random permutation that could contain multiple cycles — leaving spike in
    an infinite loop when it entered the cycle that didn't include the end.
    """

    num_of_jump_instr: int = 0

    def build(self) -> None:
        if self.num_of_jump_instr == 0:
            self.num_of_jump_instr = self.rng.randint(10, 30)

        n = self.num_of_jump_instr
        order = list(range(n))
        self.rng.shuffle(order)

        label_prefix = self.label or "jal"
        # Emit the sequence in physical position order (0..n-1), but the
        # target of each jump is determined by the shuffled ``order`` so the
        # dynamic traversal is order[0] → order[1] → … → order[n-1] → end.
        next_target: dict[int, str] = {}
        for rank, pos in enumerate(order):
            if rank == n - 1:
                next_target[pos] = f"{label_prefix}_end"
            else:
                next_target[pos] = f"{label_prefix}_{order[rank + 1]}"

        jumps: list[Instr] = []

        # jump_start: always entered in flow order, kicks off the chain by
        # jumping to the first shuffled instruction.
        start = get_instr(RiscvInstrName.JAL)
        start.rd = RiscvReg.RA
        start.label = f"{label_prefix}_start"
        start.has_label = True
        start.imm_str = f"{label_prefix}_{order[0]}"
        jumps.append(start)

        # Middle: one JAL per physical position.
        for pos in range(n):
            jal = get_instr(RiscvInstrName.JAL)
            jal.rd = RiscvReg.RA
            jal.label = f"{label_prefix}_{pos}"
            jal.has_label = True
            jal.imm_str = next_target[pos]
            jumps.append(jal)

        # End sentinel: a simple ADDI so execution just falls through. Labelled
        # as ``<label_prefix>_end`` so the chain terminates here.
        end = get_instr(RiscvInstrName.ADDI)
        end.rd = RiscvReg.ZERO
        end.rs1 = RiscvReg.ZERO
        end.imm = 0
        end.label = f"{label_prefix}_end"
        end.has_label = True
        end.post_randomize()
        jumps.append(end)

        self.instr_list = jumps


# ---------------------------------------------------------------------------
# riscv_load_store_rand_instr_stream — basic version
# ---------------------------------------------------------------------------


@dataclass
class LoadStoreRandInstrStream(DirectedInstrStream):
    """Very simplified load/store stream (SV:375 family).

    For Phase 1 we emit ``la rs1, <region>`` then a few ``lw``/``sw`` using
    a fresh base register. Full locality / alignment / multi-page variants
    come in step 7 proper.
    """

    num_load_store: int = 0

    def build(self) -> None:
        if self.num_load_store == 0:
            self.num_load_store = self.rng.randint(10, 30)

        base_reg = RiscvReg.T6  # scratch; avoid reserved
        reserved = set(self.cfg.reserved_regs)
        while base_reg in reserved:
            base_reg = self.rng.choice(list(RiscvReg))

        # LA pseudo to a region.
        region = "region_0"
        la = _LaPseudo()
        la.rd = base_reg
        la.imm_str = region
        la.atomic = True
        self.instr_list.append(la)

        # ``base_reg`` MUST stay pinned to region_0 for the whole stream —
        # any load that picks it as rd would clobber the address and send
        # subsequent accesses into random memory (easy load-fault territory).
        # SV's LoadStoreStream reserves base_reg from being chosen as rd via
        # a constraint; we replicate with an explicit exclude set.
        base_locked = reserved | {base_reg, RiscvReg.ZERO}

        for _ in range(self.num_load_store):
            pick = self.rng.choice((RiscvInstrName.LW, RiscvInstrName.SW))
            instr = get_instr(pick)
            if pick == RiscvInstrName.LW:
                pool = [r for r in RiscvReg if r not in base_locked]
                instr.rd = self.rng.choice(pool)
                instr.rs1 = base_reg
                instr.imm = self.rng.randint(0, 2000) & ~0x3
                instr.post_randomize()
            else:
                # For SW, rs2 is the VALUE source — ZERO is fine here (store 0),
                # but still keep base_reg out so we don't store ``&region`` on
                # top of itself via another path.
                pool = [r for r in RiscvReg if r not in (reserved | {base_reg})]
                instr.rs1 = base_reg
                instr.rs2 = self.rng.choice(pool)
                instr.imm = self.rng.randint(0, 2000) & ~0x3
                instr.post_randomize()
            instr.process_load_store = False
            self.instr_list.append(instr)


class _LaPseudo(_LiPseudo):
    """``la rd, <symbol>`` pseudo — identical shape to LI but different mnemonic."""

    def convert2asm(self, prefix: str = "") -> str:
        body = f"la           {self.rd.abi}, {self.imm_str}"
        if self.comment:
            body = f"{body} #{self.comment}"
        return body


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


from rvgen.streams import register_stream

register_stream("riscv_int_numeric_corner_stream", IntNumericCornerStream)
register_stream("riscv_jal_instr", JalInstr)


# ---------------------------------------------------------------------------
# riscv_jalr_instr — emit a few JALR instructions that jump into the same
# sequence (via an AUIPC + JALR pair). Coverage-driven addition: JALR was
# uncovered by every existing stream because it's only used by boot/trap
# scaffolding, not by the random stream.
# ---------------------------------------------------------------------------


@dataclass
class JalrInstr(DirectedInstrStream):
    """Emit ``AUIPC t0, 0; JALR ra, t0, <label_offset>`` blocks.

    The actual offset is set to 0 (or a small constant); spike executes
    the fall-through so the net effect on control flow is minimal. The
    point is to ensure the JALR opcode appears in the emitted stream so
    functional-coverage collectors see it.
    """

    num_of_jalr: int = 0

    def build(self) -> None:
        if self.num_of_jalr == 0:
            self.num_of_jalr = self.rng.randint(3, 6)
        reserved = set(self.cfg.reserved_regs)
        reg_pool = [r for r in RiscvReg if r not in reserved and r != RiscvReg.ZERO]
        for _ in range(self.num_of_jalr):
            scratch = self.rng.choice(reg_pool)
            auipc = get_instr(RiscvInstrName.AUIPC)
            auipc.rd = scratch
            auipc.imm = 0
            auipc.imm_str = "0"
            auipc.post_randomize()
            self.instr_list.append(auipc)

            jalr = get_instr(RiscvInstrName.JALR)
            jalr.rd = RiscvReg.ZERO  # do not perturb RA
            jalr.rs1 = scratch
            jalr.imm = 8  # jump past this pair (auipc + jalr = 8 bytes)
            jalr.imm_str = "8"
            jalr.post_randomize()
            self.instr_list.append(jalr)


register_stream("riscv_jalr_instr", JalrInstr)
# The scalar load/store family — riscv_load_store_rand_instr_stream and
# friends — is now provided by streams/load_store.py with SV-faithful
# distinctive behavior per subclass (hazard / multi-page / locality-aware).
# The LoadStoreRandInstrStream in THIS module is the legacy simplified
# class kept only as a fallback if load_store.py fails to import.
