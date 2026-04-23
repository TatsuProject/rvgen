"""Vector extension configuration — port of ``src/riscv_vector_cfg.sv``.

Holds ``vtype`` (vlmul/vsew/vediv/fractional_lmul/ill), ``vl``, ``vstart``,
``vxrm``, ``vxsat``, plus the seven randomization knobs (``only_vec_instr``,
``vec_fp``, ``vec_narrowing_widening``, ``vec_quad_widening``,
``allow_illegal_vec_instr``, ``vec_reg_hazards``, ``enable_zvlsseg``,
``enable_fault_only_first_load``).

``legal_eew`` is computed in ``__post_init__`` per SV's ``post_randomize``:

    for emul in {1/8, 1/4, 1/2, 1, 2, 4, 8}:
        if !fractional_lmul: eew = vsew * emul / vlmul
        else:                eew = vsew * emul * vlmul
        if 8 <= eew <= 1024: legal_eew.push(int(eew))

Phase-1 defaults follow SV's ``bringup_c``: vstart=0, vl=VLEN/vsew, vediv=1,
fractional_lmul=0, and sensible defaults for the knobs (all-zero except
``enable_zvlsseg``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from chipforge_inst_gen.isa.enums import RiscvVreg, VxrmMode


@dataclass
class Vtype:
    """SV ``vtype_t`` — subset used by the generator.

    ``vlmul`` encodes the integer LMUL; ``fractional_lmul`` flips the meaning
    so LMUL becomes 1/vlmul. ``vsew`` is the standard-element width in bits.
    """

    vlmul: int = 1          # {1, 2, 4, 8}; w/ fractional_lmul=True means 1/vlmul
    vsew: int = 32          # {8, 16, 32, 64, 128}
    vediv: int = 1          # {1, 2, 4, 8}
    fractional_lmul: bool = False
    ill: bool = False


_LMUL_LEGAL: ClassVar = frozenset({1, 2, 4, 8})
_VSEW_LEGAL: ClassVar = frozenset({8, 16, 32, 64, 128})
_VEDIV_LEGAL: ClassVar = frozenset({1, 2, 4, 8})


@dataclass
class VectorConfig:
    """Vector configuration (SV: ``riscv_vector_cfg``)."""

    # -- vtype + vl/vstart --
    vtype: Vtype = field(default_factory=Vtype)
    vl: int = 0
    vstart: int = 0
    vxrm: VxrmMode = VxrmMode.RoundToNearestUp
    vxsat: bool = False

    # -- Randomization knobs --
    only_vec_instr: bool = False
    vec_fp: bool = False
    vec_narrowing_widening: bool = False
    vec_quad_widening: bool = False
    allow_illegal_vec_instr: bool = False
    vec_reg_hazards: bool = False
    enable_zvlsseg: bool = False
    enable_fault_only_first_load: bool = False

    # -- Reserved v-regs (v0 is the mask register, excluded from vd when
    # vm==0). Generator may add more.
    reserved_vregs: tuple[RiscvVreg, ...] = ()

    # -- Derived / target-level sizing (mirrored from TargetCfg) --
    vlen: int = 512
    elen: int = 32
    selen: int = 8
    max_lmul: int = 8
    num_vec_gpr: int = 32

    # -- Computed: legal EEW set (populated by __post_init__) --
    legal_eew: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # SV bringup_c: vstart=0, vl=VLEN/vsew, vediv=1.
        if self.vl == 0:
            self.vl = max(1, self.vlen // self.vtype.vsew)
        self._validate()
        self.legal_eew = self._compute_legal_eew()

    # ------------------------------------------------------------------
    # Validation (SV vsew_c / vlmul_c / vdeiv_c / legal_c)
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        if self.vtype.vlmul not in _LMUL_LEGAL:
            raise ValueError(f"vtype.vlmul={self.vtype.vlmul} not in {sorted(_LMUL_LEGAL)}")
        if self.vtype.vlmul > self.max_lmul:
            raise ValueError(
                f"vtype.vlmul={self.vtype.vlmul} exceeds target max_lmul={self.max_lmul}"
            )
        if self.vtype.vsew not in _VSEW_LEGAL:
            raise ValueError(f"vtype.vsew={self.vtype.vsew} not in {sorted(_VSEW_LEGAL)}")
        if self.vtype.vsew > self.elen:
            raise ValueError(f"vtype.vsew={self.vtype.vsew} > ELEN={self.elen}")
        if self.vec_fp and self.vtype.vsew != 32:
            raise ValueError("vec_fp requires vtype.vsew == 32 (Phase 1)")
        if self.vec_narrowing_widening and self.vtype.vsew >= self.elen:
            raise ValueError("vec_narrowing_widening requires vtype.vsew < ELEN")
        if self.vec_quad_widening and self.vtype.vsew >= (self.elen >> 1):
            raise ValueError("vec_quad_widening requires vtype.vsew < ELEN/2")
        if self.vtype.vediv not in _VEDIV_LEGAL:
            raise ValueError(f"vtype.vediv={self.vtype.vediv} not in {sorted(_VEDIV_LEGAL)}")
        max_vediv = self.vtype.vsew // self.selen if self.selen else 1
        if max_vediv and self.vtype.vediv > max_vediv:
            raise ValueError(
                f"vtype.vediv={self.vtype.vediv} > vsew/SELEN={max_vediv}"
            )
        if self.enable_zvlsseg and self.vtype.vlmul >= 8:
            raise ValueError("enable_zvlsseg requires vtype.vlmul < 8")
        if self.vstart < 0 or self.vstart > self.vl:
            raise ValueError(f"vstart={self.vstart} out of [0, vl={self.vl}]")
        vl_max = self.vlen // self.vtype.vsew
        if self.vl < 1 or self.vl > vl_max:
            raise ValueError(f"vl={self.vl} out of [1, VLEN/vsew={vl_max}]")

    # ------------------------------------------------------------------
    # legal_eew computation (SV post_randomize)
    # ------------------------------------------------------------------

    def _compute_legal_eew(self) -> tuple[int, ...]:
        """Return the sorted unique set of legal EEW values.

        SV uses real-valued arithmetic and keeps any ``temp_eew in [8, 1024]``.
        We reproduce that with float math but:

        - only retain integer / power-of-two values (fractional EEW and
          non-pow2 EEW aren't architectural),
        - clamp to ``[8, ELEN]`` so the emitted load/store can actually execute
          on a spec-conformant core with this ELEN. SV's broader range exists
          to exercise the illegal-instruction path; Phase 1 of our port keeps
          the output runnable and leaves illegal-EEW stress to Phase 2.
        """
        out: set[int] = set()
        # emul ∈ {1/8, 1/4, 1/2, 1, 2, 4, 8}
        for e_num, e_den in ((1, 8), (1, 4), (1, 2), (1, 1), (2, 1), (4, 1), (8, 1)):
            if self.vtype.fractional_lmul:
                num = self.vtype.vsew * e_num * self.vtype.vlmul
                den = e_den
            else:
                num = self.vtype.vsew * e_num
                den = e_den * self.vtype.vlmul
            if num % den != 0:
                continue
            eew = num // den
            if 8 <= eew <= self.elen and eew in (8, 16, 32, 64, 128, 256, 512, 1024):
                out.add(eew)
        return tuple(sorted(out))

    # ------------------------------------------------------------------
    # Rendering helpers (for asm_program_gen / vsetvli emission)
    # ------------------------------------------------------------------

    def lmul_str(self) -> str:
        """Return the ``m<N>`` or ``mf<N>`` form used by vsetvli."""
        if self.vtype.fractional_lmul and self.vtype.vlmul > 1:
            return f"mf{self.vtype.vlmul}"
        return f"m{self.vtype.vlmul}"
