"""Vector RAW/WAR/WAW hazard directed stream.

Forces back-to-back register dependencies on a small pool of v-regs
so the test exercises pipeline forwarding, scoreboard logic, and
write-buffer hazards. Mirrors the scalar :class:`HazardInstrStream`
at the vector level.

Three hazard kinds, randomly mixed per-emitted-pair:

- **RAW** — instr K writes vd; instr K+1 reads it as vs1 (or vs2).
- **WAW** — instr K writes vd; instr K+1 also writes the same vd.
- **WAR** — instr K reads a register; instr K+1 writes the same register.

We pick the hazard register from a small pool (default 4 v-regs) so
the chain dominates the dependency pattern. Pool defaults exclude
v0 (the mask register) to avoid spurious mask-use bins.

Phase-1: only handles VV-form arithmetic ops (the common case). Mixed
VX / VI variants are emitted via the parent vector randomizer; we
override the picked op's vd / vs1 / vs2 after the fact.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrName,
    RiscvVreg,
)
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams.vector_pick import pick_random_vector_op


_HAZARD_KINDS = ("RAW", "WAW", "WAR")


@dataclass
class VectorHazardInstrStream(DirectedInstrStream):
    """Force vector RAW/WAW/WAR chains via a tight v-reg pool."""

    num_pairs: int = 0
    pool_size: int = 4

    def build(self) -> None:
        if not self.cfg.enable_vector_extension or self.cfg.vector_cfg is None:
            self.instr_list = []
            return
        if self.num_pairs == 0:
            self.num_pairs = self.rng.randint(8, 20)

        vcfg = self.cfg.vector_cfg
        vregs = [v for v in RiscvVreg if v != RiscvVreg.V0]
        # Honor LMUL alignment for vd: vlmul-aligned only.
        lmul = vcfg.vtype.vlmul
        if lmul > 1:
            vregs = [v for v in vregs if int(v) % lmul == 0]
        if len(vregs) < self.pool_size:
            self.pool_size = len(vregs)
        pool = self.rng.sample(vregs, self.pool_size)

        out: list[Instr] = []
        prev_vd: RiscvVreg | None = None
        prev_vs1: RiscvVreg | None = None
        for _ in range(self.num_pairs):
            kind = self.rng.choice(_HAZARD_KINDS)
            instr = self._pick_vector_arith(vcfg)
            if instr is None:
                break
            # All picks within the small pool.
            instr.vd = self.rng.choice(pool)
            instr.vs1 = self.rng.choice(pool)
            instr.vs2 = self.rng.choice(pool)
            # Force the dependency by overriding the relevant slot.
            if prev_vd is not None and prev_vs1 is not None:
                if kind == "RAW":
                    instr.vs1 = prev_vd
                elif kind == "WAW":
                    instr.vd = prev_vd
                elif kind == "WAR":
                    instr.vd = prev_vs1
            instr.atomic = True
            out.append(instr)
            prev_vd, prev_vs1 = instr.vd, instr.vs1

        self.instr_list = out

    def _pick_vector_arith(self, vcfg) -> Instr | None:
        """Pick a VV-form vector arith op then force vd/vs1/vs2 to chain.

        Excludes ops with non-overlap / alignment constraints (compares,
        reductions, slides, gathers, whole-reg moves) since we override
        operands post-randomize and would violate their semantics.
        """
        from rvgen.isa.enums import VaVariant
        for _ in range(8):
            cand = pick_random_vector_op(
                self.rng, self.avail, self.cfg, vcfg,
                allowed_categories=(
                    RiscvInstrCategory.ARITHMETIC,
                    RiscvInstrCategory.LOGICAL,
                    RiscvInstrCategory.SHIFT,
                ),
                extra_excludes=_HAZARD_INELIGIBLE,
                max_retries=1,
            )
            if cand is None:
                continue
            if not getattr(cand, "has_va_variant", False):
                continue
            if VaVariant.VV not in getattr(cand, "allowed_va_variants", ()):
                continue
            cand.va_variant = VaVariant.VV
            cand.vm = 1  # unmasked — dependency chain is the focus
            return cand
        return None


_HAZARD_INELIGIBLE: tuple[RiscvInstrName, ...] = (
    # Compare ops constrain vd != vs1/vs2 when LMUL>1.
    RiscvInstrName.VMSEQ, RiscvInstrName.VMSNE,
    RiscvInstrName.VMSLT, RiscvInstrName.VMSLTU,
    RiscvInstrName.VMSLE, RiscvInstrName.VMSLEU,
    RiscvInstrName.VMSGT, RiscvInstrName.VMSGTU,
    # Reductions read vs1[0] only — different chain semantics.
    RiscvInstrName.VREDSUM_VS,
    RiscvInstrName.VREDMAX_VS, RiscvInstrName.VREDMAXU_VS,
    RiscvInstrName.VREDMIN_VS, RiscvInstrName.VREDMINU_VS,
    RiscvInstrName.VREDAND_VS, RiscvInstrName.VREDOR_VS,
    RiscvInstrName.VREDXOR_VS,
    # Slide / gather / compress have non-overlap rules.
    RiscvInstrName.VSLIDEUP, RiscvInstrName.VSLIDEDOWN,
    RiscvInstrName.VSLIDE1UP, RiscvInstrName.VSLIDE1DOWN,
    RiscvInstrName.VRGATHER, RiscvInstrName.VCOMPRESS,
    # Whole-reg moves carry alignment constraints.
    RiscvInstrName.VMV1R_V, RiscvInstrName.VMV2R_V,
    RiscvInstrName.VMV4R_V, RiscvInstrName.VMV8R_V,
)


register_stream("riscv_vector_hazard_instr_stream", VectorHazardInstrStream)
