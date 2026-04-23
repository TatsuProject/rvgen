"""Instruction stream primitives — port of ``src/riscv_instr_stream.sv``.

:class:`InstrStream` is a queue of :class:`Instr` objects. It supports the
same ``insert_instr``, ``insert_instr_stream``, ``mix_instr_stream`` methods
the SV base class exposes.

:class:`RandInstrStream` extends with ``gen_instr`` — pickes random
instructions according to the active config's allowed-instr set.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from chipforge_inst_gen.config import Config
from chipforge_inst_gen.isa.base import Instr
from chipforge_inst_gen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrName,
    RiscvReg,
)
from chipforge_inst_gen.isa.filtering import (
    AvailableInstrs,
    get_rand_instr,
    randomize_gpr_operands,
)


@dataclass
class InstrStream:
    """Ordered list of :class:`Instr` — port of ``riscv_instr_stream``."""

    instr_list: list[Instr] = field(default_factory=list)
    instr_cnt: int = 0
    label: str = ""
    avail_regs: tuple[RiscvReg, ...] = ()
    reserved_rd: tuple[RiscvReg, ...] = ()
    hart: int = 0

    # ---- Stream mutation ----

    def insert_instr(self, instr: Instr, *, idx: int = -1, rng: random.Random | None = None) -> None:
        """Insert ``instr`` at ``idx`` (or at a random non-atomic location).

        Port of SV ``insert_instr`` (riscv_instr_stream.sv:53).
        """
        current = len(self.instr_list)
        if current == 0:
            self.instr_list.append(instr)
            return
        if idx == -1:
            if rng is None:
                raise ValueError("insert_instr with idx=-1 needs an rng")
            idx = rng.randint(0, current - 1)
            while self.instr_list[idx].atomic:
                idx += 1
                if idx >= current:
                    self.instr_list.append(instr)
                    return
        elif idx < 0 or idx > current:
            raise IndexError(f"Cannot insert instr at idx {idx} (stream size {current})")
        self.instr_list.insert(idx, instr)

    def insert_instr_stream(
        self,
        new_instr: Sequence[Instr],
        *,
        idx: int = -1,
        replace: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        """Insert (or replace) a sub-stream at ``idx``.

        Port of SV ``insert_instr_stream`` (riscv_instr_stream.sv:76).
        """
        current = len(self.instr_list)
        new_list = list(new_instr)
        if current == 0:
            self.instr_list = new_list
            return

        if idx == -1:
            if rng is None:
                raise ValueError("insert_instr_stream with idx=-1 needs an rng")
            idx = rng.randint(0, current - 1)
            # SV retries up to 10 times for a non-atomic slot.
            for _ in range(10):
                if not self.instr_list[idx].atomic:
                    break
                idx = rng.randint(0, current - 1)
            # Final fallback: scan linearly for a non-atomic slot.
            if self.instr_list[idx].atomic:
                for i, existing in enumerate(self.instr_list):
                    if not existing.atomic:
                        idx = i
                        break
                else:
                    raise RuntimeError(
                        "Cannot inject instruction stream — every instruction is atomic"
                    )
        elif idx < 0 or idx > current:
            raise IndexError(f"Cannot insert stream at idx {idx} (stream size {current})")

        if replace:
            # Carry the old instr's label onto the first new instruction.
            new_list[0].label = self.instr_list[idx].label
            new_list[0].has_label = self.instr_list[idx].has_label
            self.instr_list = self.instr_list[:idx] + new_list + self.instr_list[idx + 1:]
        else:
            self.instr_list = self.instr_list[:idx] + new_list + self.instr_list[idx:]

    def mix_instr_stream(
        self,
        new_instr: Sequence[Instr],
        *,
        rng: random.Random,
        contained: bool = False,
    ) -> None:
        """Sprinkle ``new_instr`` into the current stream in preserved order.

        Port of SV ``mix_instr_stream`` (riscv_instr_stream.sv:125).
        """
        current = len(self.instr_list)
        new_list = list(new_instr)
        new_cnt = len(new_list)
        positions = sorted(rng.randint(0, current - 1) for _ in range(new_cnt))
        if contained and new_cnt:
            positions[0] = 0
            if new_cnt > 1:
                positions[-1] = current - 1
        for i, instr in enumerate(new_list):
            self.insert_instr(instr, idx=positions[i] + i)

    # ---- String rendering ----

    def convert2string(self) -> str:
        """Concatenate each instr's ``convert2asm()`` with newlines."""
        return "\n".join(i.convert2asm() for i in self.instr_list)


@dataclass
class RandInstrStream(InstrStream):
    """Randomized stream — picks per-slot via :func:`get_rand_instr`.

    Port of SV ``riscv_rand_instr_stream``. Stores ``cfg`` and ``avail`` for
    subsequent per-instruction randomization.
    """

    cfg: Config | None = None
    avail: AvailableInstrs | None = None
    kernel_mode: bool = False
    allowed_instr: tuple[RiscvInstrName, ...] = ()

    def initialize_instr_list(self, instr_cnt: int) -> None:
        """Allocate ``instr_cnt`` placeholder slots (filled by gen_instr)."""
        self.instr_cnt = instr_cnt
        # Placeholder: allocate None; gen_instr will overwrite.
        self.instr_list = []  # SV appends real Instrs as gen_instr runs.

    def setup_allowed_instr(
        self,
        *,
        no_branch: bool = False,
        no_load_store: bool = True,
    ) -> None:
        """Compute the allowed-instr set for this stream (SV: setup_allowed_instr)."""
        assert self.avail is not None
        allowed = list(self.avail.basic_instr)
        if not no_branch:
            allowed += list(self.avail.by_category.get(RiscvInstrCategory.BRANCH, ()))
        if not no_load_store:
            allowed += list(self.avail.by_category.get(RiscvInstrCategory.LOAD, ()))
            allowed += list(self.avail.by_category.get(RiscvInstrCategory.STORE, ()))
        self.allowed_instr = tuple(allowed)

    def gen_instr(
        self,
        rng: random.Random,
        *,
        no_branch: bool = False,
        no_load_store: bool = True,
        is_debug_program: bool = False,
    ) -> None:
        """Generate ``instr_cnt`` random instructions (SV: gen_instr).

        Port of SV ``riscv_rand_instr_stream.gen_instr`` (riscv_instr_stream.sv:217).
        """
        assert self.cfg is not None and self.avail is not None
        self.setup_allowed_instr(no_branch=no_branch, no_load_store=no_load_store)

        exclude: list[RiscvInstrName] = []
        # If SP is reserved / not in avail_regs, exclude SP-using compressed ops.
        sp_unavailable = (
            RiscvReg.SP in self.reserved_rd
            or RiscvReg.SP in self.cfg.reserved_regs
            or (self.avail_regs and RiscvReg.SP not in self.avail_regs)
        )
        if sp_unavailable:
            exclude += [
                RiscvInstrName.C_ADDI4SPN,
                RiscvInstrName.C_ADDI16SP,
                RiscvInstrName.C_LWSP,
                RiscvInstrName.C_LDSP,
            ]

        # Debug rom specifically adds/removes ebreak. For Phase 1 we honor the
        # simple case: respect cfg.enable_ebreak_in_debug_rom / cfg.no_ebreak.
        allowed = list(self.allowed_instr)
        if is_debug_program:
            if self.cfg.no_ebreak and self.cfg.enable_ebreak_in_debug_rom:
                allowed.extend((RiscvInstrName.EBREAK, RiscvInstrName.C_EBREAK))
            elif not self.cfg.no_ebreak and not self.cfg.enable_ebreak_in_debug_rom:
                exclude.extend((RiscvInstrName.EBREAK, RiscvInstrName.C_EBREAK))

        from chipforge_inst_gen.isa.csr_ops import CsrInstr
        from chipforge_inst_gen.isa.enums import (
            PrivilegedReg, RiscvInstrName as _CsrN, RiscvReg as _CsrReg,
        )
        # Target's implemented CSR set — used for READ-type CSR ops so the
        # generator can spray csrr over any CSR the core knows about.
        impl_csrs: tuple = tuple(self.cfg.target.implemented_csr) if self.cfg.target else ()
        # WRITE-type CSR ops must stay in a whitelist. Writing random values to
        # e.g. MISA disables the C extension and traps every subsequent
        # compressed instruction. SV riscv-dv's ``include_write_reg`` defaults
        # to ``{MSCRATCH}`` (riscv_instr_gen_config.sv:~470). We match that —
        # it's the minimum viable set that keeps the test runnable.
        writable_csrs: tuple = (PrivilegedReg.MSCRATCH,)
        if PrivilegedReg.MSCRATCH not in self.cfg.target.implemented_csr:
            # If the target doesn't implement MSCRATCH, fall back to no-write.
            writable_csrs = ()
        _CSR_WRITE_INSTRS = (_CsrN.CSRRW, _CsrN.CSRRWI)
        _CSR_SETCLR_INSTRS = (_CsrN.CSRRS, _CsrN.CSRRC, _CsrN.CSRRSI, _CsrN.CSRRCI)

        self.instr_list = []
        for _ in range(self.instr_cnt):
            instr = get_rand_instr(
                rng,
                self.avail,
                include_instr=allowed,
                exclude_instr=exclude,
            )
            randomize_gpr_operands(
                instr,
                rng,
                self.cfg,
                avail_regs=self.avail_regs,
                reserved_rd=self.reserved_rd,
            )
            # Randomize FP operands if the class exposes them (FloatingPointInstr).
            fp_rand = getattr(instr, "randomize_fpr_operands", None)
            if fp_rand is not None:
                fp_rand(rng)
            # Randomize vector operands when the instr exposes them and the
            # cfg has a vector_cfg stamped in.
            vec_rand = getattr(instr, "randomize_vector_operands", None)
            if vec_rand is not None and self.cfg.vector_cfg is not None:
                vec_rand(rng, self.cfg.vector_cfg)
            # Randomize the CSR address for CSR ops — SV riscv_csr_instr's
            # ``csr_addr_c`` + ``write_csr_c`` constraints separate READ-only
            # and WRITE-type CSR ops:
            #  - CSRRW/CSRRWI always write; target must be in the writable set.
            #  - CSRRS/CSRRC/CSRRSI/CSRRCI write only when rs1 (or imm) != 0.
            # We conservatively treat set/clear ops as writing — blindly
            # writing random nonzero bits to e.g. MISA/MSTATUS/MTVEC is the
            # fastest way to brick the rest of the test stream.
            if isinstance(instr, CsrInstr) and impl_csrs:
                name = instr.instr_name
                if name in _CSR_WRITE_INSTRS:
                    instr.csr = (
                        rng.choice(writable_csrs).value if writable_csrs
                        else rng.choice(impl_csrs).value
                    )
                elif name in _CSR_SETCLR_INSTRS:
                    # For set/clear, restrict to writable CSRs too — SV's
                    # write_csr_c treats rs1==x0 as read-only, but we'd need
                    # post-randomize register awareness to honor that. The
                    # conservative choice is always writable.
                    instr.csr = (
                        rng.choice(writable_csrs).value if writable_csrs
                        else rng.choice(impl_csrs).value
                    )
                else:
                    instr.csr = rng.choice(impl_csrs).value
            # Randomize the immediate within the instruction's range (and
            # honor shift imm_c via Instr.randomize_imm).
            if instr.has_imm:
                instr.randomize_imm(rng, xlen=self.cfg.target.xlen)
            instr.post_randomize()
            self.instr_list.append(instr)

        # Trim trailing branches — no forward target.
        while self.instr_list and self.instr_list[-1].category == RiscvInstrCategory.BRANCH:
            self.instr_list.pop()
