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
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvVreg,
)
from rvgen.isa.filtering import get_rand_instr, randomize_gpr_operands
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream


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
        """Pick a vector arithmetic VV-form op + run the standard randomizer.

        We post-override vd/vs1/vs2 so the operand-group constraint may
        get violated (e.g. compare ops require vd != vs1). For the
        small set of ops where that is fatal at runtime we'd need to
        re-pick — Phase 1 trusts spike-vector to accept all VV-form
        arithmetic with arbitrary operand picks (it does, modulo
        constraint warnings that don't fail simulation).
        """
        for _retry in range(8):
            try:
                cand = get_rand_instr(
                    self.rng,
                    self.avail,
                    include_group=[RiscvInstrGroup.RVV],
                    exclude_instr=[
                        RiscvInstrName.VSETVLI, RiscvInstrName.VSETVL,
                        # Compare ops constrain vd != vs1/vs2 (LMUL>1) — skip.
                        RiscvInstrName.VMSEQ, RiscvInstrName.VMSNE,
                        RiscvInstrName.VMSLT, RiscvInstrName.VMSLTU,
                        RiscvInstrName.VMSLE, RiscvInstrName.VMSLEU,
                        RiscvInstrName.VMSGT, RiscvInstrName.VMSGTU,
                        # Reductions read vs1[0] only — hazard chain meaning
                        # is different; skip for clarity.
                        RiscvInstrName.VREDSUM_VS,
                        RiscvInstrName.VREDMAX_VS, RiscvInstrName.VREDMAXU_VS,
                        RiscvInstrName.VREDMIN_VS, RiscvInstrName.VREDMINU_VS,
                        RiscvInstrName.VREDAND_VS, RiscvInstrName.VREDOR_VS,
                        RiscvInstrName.VREDXOR_VS,
                        # Slide / gather / compress have non-overlap rules.
                        RiscvInstrName.VSLIDEUP, RiscvInstrName.VSLIDEDOWN,
                        RiscvInstrName.VSLIDE1UP, RiscvInstrName.VSLIDE1DOWN,
                        RiscvInstrName.VRGATHER, RiscvInstrName.VCOMPRESS,
                        # whole-reg moves have alignment constraints we skip.
                        RiscvInstrName.VMV1R_V, RiscvInstrName.VMV2R_V,
                        RiscvInstrName.VMV4R_V, RiscvInstrName.VMV8R_V,
                    ],
                )
            except Exception:  # noqa: BLE001
                return None
            if cand.category not in (
                RiscvInstrCategory.ARITHMETIC,
                RiscvInstrCategory.LOGICAL,
                RiscvInstrCategory.SHIFT,
            ):
                continue
            # Must be the VV form (we override vs1/vs2 with vector regs).
            from rvgen.isa.enums import VaVariant
            if not getattr(cand, "has_va_variant", False):
                continue
            allowed = getattr(cand, "allowed_va_variants", ())
            if VaVariant.VV not in allowed:
                continue
            # Apply standard randomization, then override va_variant to VV.
            randomize_gpr_operands(cand, self.rng, self.cfg)
            vec_rand = getattr(cand, "randomize_vector_operands", None)
            if vec_rand is not None:
                vec_rand(self.rng, vcfg)
            cand.va_variant = VaVariant.VV
            cand.vm = 1  # unmasked — the dependency chain is the focus
            cand.post_randomize()
            return cand
        return None


register_stream("riscv_vector_hazard_instr_stream", VectorHazardInstrStream)
