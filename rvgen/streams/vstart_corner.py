"""vstart corner-case directed stream.

Emits ``csrwi vstart, N`` before each vector op so partial-vector
execution paths get exercised. ``vstart`` controls where in the
element vector a vector op begins — it auto-resets to 0 on
successful completion of most vector ops, so tests need to set it
explicitly to drive non-zero starts.

riscv-dv has no support for this; rvgen-first.

The CSR address for vstart is 0x008 (per the RVV spec). We use
``csrwi`` so the immediate is a 5-bit unsigned literal (0..31) — that
covers the typical "restart" range. Values > vl are implementation-
defined; we only emit values ≤ 16 to stay safe across cores.

A new :data:`CG_VEC_VSTART` covergroup is sampled by the per-instr
sampler when it sees a vsetvli pseudo with a `_vstart` attr.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrGroup,
    RiscvInstrName,
)
from rvgen.isa.filtering import get_rand_instr, randomize_gpr_operands
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams.directed import _LiPseudo


_VSTART_CSR = 0x008


class _VstartWritePseudo(_LiPseudo):
    """``csrwi vstart, N`` minimal pseudo.

    Carries the ``vstart_value`` as a python attr so the coverage
    sampler can bin it (zero / one / small / mid / max).
    """

    def __init__(self, value: int) -> None:
        super().__init__()
        self.imm = value
        self.imm_str = str(value)
        self.has_imm = True
        self.has_rd = False
        self.has_rs1 = False
        self._vstart_value = value

    def get_instr_name(self) -> str:
        return "csrwi"

    def convert2asm(self, prefix: str = "") -> str:
        body = f"csrwi        vstart, {self.imm}"
        if self.comment:
            body = f"{body} #{self.comment}"
        return body


@dataclass
class VstartCornerInstrStream(DirectedInstrStream):
    """Emit `csrwi vstart, N; <vector op>` pairs across N corner values.

    The corner values are picked from {0, 1, 2, 4, 8, 16}. After each
    pair, vstart auto-resets to 0 on hardware (we don't need to clean up).
    """

    num_pairs: int = 0

    _CORNERS = (0, 1, 2, 4, 8, 16)

    def build(self) -> None:
        if not self.cfg.enable_vector_extension or self.cfg.vector_cfg is None:
            self.instr_list = []
            return
        if self.num_pairs == 0:
            self.num_pairs = self.rng.randint(4, 10)

        vcfg = self.cfg.vector_cfg
        out: list[Instr] = []
        for _ in range(self.num_pairs):
            value = self.rng.choice(self._CORNERS)
            wr = _VstartWritePseudo(value)
            wr.atomic = True
            out.append(wr)
            instr = self._pick_vector_op(vcfg)
            if instr is not None:
                out.append(instr)
        self.instr_list = out

    def _pick_vector_op(self, vcfg) -> Instr | None:
        for _retry in range(8):
            try:
                cand = get_rand_instr(
                    self.rng,
                    self.avail,
                    include_group=[RiscvInstrGroup.RVV],
                    exclude_instr=[
                        RiscvInstrName.VSETVLI, RiscvInstrName.VSETVL,
                    ],
                )
            except Exception:  # noqa: BLE001
                return None
            if cand.category not in (
                RiscvInstrCategory.ARITHMETIC,
                RiscvInstrCategory.LOGICAL,
                RiscvInstrCategory.SHIFT,
                RiscvInstrCategory.COMPARE,
            ):
                continue
            randomize_gpr_operands(cand, self.rng, self.cfg)
            vec_rand = getattr(cand, "randomize_vector_operands", None)
            if vec_rand is not None:
                vec_rand(self.rng, vcfg)
            if cand.has_imm:
                cand.randomize_imm(self.rng, xlen=self.cfg.target.xlen)
            cand.post_randomize()
            cand.atomic = True
            return cand
        return None


register_stream("riscv_vstart_corner_instr_stream", VstartCornerInstrStream)
