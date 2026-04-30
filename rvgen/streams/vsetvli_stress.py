"""Vector vtype-stress directed stream — exercises vsetvli transitions.

riscv-dv emits **one** vsetvli at boot and never changes vtype mid-stream.
That leaves an enormous coverage hole: every vtype-transition path
(SEW changes, LMUL changes, fractional vs integer LMUL, tail-agnostic
vs tail-undisturbed, ill flag toggling) is uncovered. Real cores have
to handle these transitions correctly — they invalidate scoreboards,
require pipeline drains, and surface micro-architectural bugs that
constant-vtype tests will never find.

This stream:

1. Picks a random LEGAL (SEW, LMUL) pair within the target's profile,
2. Emits a `vsetvli` to that vtype,
3. Emits 3-8 random vector ops at the new vtype (so the generator
   gets a chance to sample bins for the new SEW/LMUL pair),
4. Optionally restores the original vtype with a second vsetvli.

Each invocation appends the (prev_vtype, new_vtype) transition to
:data:`rvgen.coverage.collectors.CG_VTYPE_TRANS` via the per-instr
sampler when the cfg's coverage runtime is on.

Phase-1 simplifications:
- We don't try to maintain a running vector_cfg; the in-stream
  vsetvli changes the SV ``vtype`` field on hardware, but our
  ``vector_cfg`` state object stays put. Future versions can mutate
  it so subsequent ops pick legal_eew under the new vtype.
- The "new vtype" is sampled from a curated list of LEGAL combinations
  for the target's ELEN. We don't generate intentionally illegal
  combinations (vsetvl(i) sets vtype.ill=1 on illegal SEW/LMUL — that
  path is left for a future ``riscv_vector_illegal_vtype_stream``).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from rvgen.isa.base import Instr
from rvgen.isa.enums import RiscvReg
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams.directed import _LiPseudo
from rvgen.streams.vector_pick import pick_random_vector_op


# Legal (sew, lmul, fractional_lmul) tuples for a target with ELEN=32.
# RVV 1.0 spec: SEW must be ≤ ELEN; LMUL ∈ {1/8, 1/4, 1/2, 1, 2, 4, 8};
# fractional LMUL with SEW=ELEN is illegal (no element type would fit).
_LMUL_INTEGER = (1, 2, 4, 8)
_LMUL_FRACTIONAL = (2, 4, 8)  # 1/2, 1/4, 1/8


def _legal_vtypes(elen: int, max_lmul: int) -> list[tuple[int, int, bool]]:
    out: list[tuple[int, int, bool]] = []
    for sew in (8, 16, 32, 64, 128):
        if sew > elen:
            break
        for lmul in _LMUL_INTEGER:
            if lmul > max_lmul:
                continue
            out.append((sew, lmul, False))
        for lmul in _LMUL_FRACTIONAL:
            if lmul > max_lmul:
                continue
            # Fractional LMUL needs SEW < ELEN (elements scale down).
            if sew >= elen:
                continue
            out.append((sew, lmul, True))
    return out


class _VsetvliPseudo(_LiPseudo):
    """``vsetvli rd, rs1, e<sew>, m<lmul>, t<a/u>, m<a/u>`` minimal pseudo.

    We re-use the LI pseudo's bare bones to stay compatible with the
    sequence's branch-resolution pass (it skips instructions whose
    ``has_label`` is False and whose ``format`` is None).
    """

    def __init__(self, rd: RiscvReg, rs1: RiscvReg, sew: int, lmul: int,
                 fractional: bool, ta: bool, ma: bool) -> None:
        super().__init__()
        self.rd = rd
        self.rs1 = rs1
        self._sew = sew
        self._lmul = lmul
        self._fractional = fractional
        self._ta = ta
        self._ma = ma
        self.has_imm = False
        self.imm = 0
        self.imm_str = ""

    def get_instr_name(self) -> str:
        return "vsetvli"

    def convert2asm(self, prefix: str = "") -> str:
        lmul = (
            f"mf{self._lmul}" if self._fractional and self._lmul > 1
            else f"m{self._lmul}"
        )
        ta_str = "ta" if self._ta else "tu"
        ma_str = "ma" if self._ma else "mu"
        body = (
            f"vsetvli      {self.rd.abi}, {self.rs1.abi}, "
            f"e{self._sew}, {lmul}, {ta_str}, {ma_str}"
        )
        if self.comment:
            body = f"{body} #{self.comment}"
        return body


@dataclass
class VsetvliStressInstrStream(DirectedInstrStream):
    """Emit `vsetvli` blocks that change vtype mid-stream.

    Each "block" is:

        li     rs1, <vl>
        vsetvli  rd, rs1, e<SEW>, m<LMUL>, ta|tu, ma|mu
        <3..8 random vector arithmetic / mask ops at new vtype>

    ``num_blocks`` controls how many transitions get emitted.
    """

    num_blocks: int = 0

    def build(self) -> None:
        if not self.cfg.enable_vector_extension or self.cfg.vector_cfg is None:
            self.instr_list = []
            return
        vcfg = self.cfg.vector_cfg
        candidates = _legal_vtypes(vcfg.elen, vcfg.max_lmul)
        if not candidates:
            self.instr_list = []
            return
        if self.num_blocks == 0:
            self.num_blocks = self.rng.randint(3, 6)

        # Reserved scratch GPRs — use cfg.gpr[0]/[1] like the boot code does.
        rd = self.cfg.gpr[0]
        rs1 = self.cfg.gpr[1]

        out: list[Instr] = []
        for _ in range(self.num_blocks):
            sew, lmul, frac = self.rng.choice(candidates)
            ta = bool(self.rng.getrandbits(1))
            ma = bool(self.rng.getrandbits(1))
            # Random vl — pick from {0, 1, vlmax} corner ∪ middle range.
            vlmax = max(1, vcfg.vlen * lmul // sew) if not frac \
                else max(1, vcfg.vlen // (sew * lmul))
            vl_choices = [0, 1, vlmax, max(1, vlmax // 2)]
            vl_pick = self.rng.choice(vl_choices)
            li = _LiPseudo()
            li.rd = rs1
            li.imm = vl_pick
            li.imm_str = str(vl_pick)
            li.atomic = True
            out.append(li)
            vset = _VsetvliPseudo(rd=rd, rs1=rs1, sew=sew, lmul=lmul,
                                   fractional=frac, ta=ta, ma=ma)
            vset.atomic = True
            out.append(vset)

            # 3..8 random vector ops at the new vtype.
            count = self.rng.randint(3, 8)
            for _i in range(count):
                instr = pick_random_vector_op(
                    self.rng, self.avail, self.cfg, vcfg,
                )
                if instr is None:
                    break
                out.append(instr)

        self.instr_list = out


register_stream("riscv_vsetvli_stress_instr_stream", VsetvliStressInstrStream)
