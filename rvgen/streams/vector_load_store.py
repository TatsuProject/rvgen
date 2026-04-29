"""Vector load/store + AMO directed streams — port of
``riscv_load_store_instr_lib.sv:522`` (``riscv_vector_load_store_instr_stream``)
and ``riscv_amo_instr_lib.sv:214`` (``riscv_vector_amo_instr_stream``).

Three address modes mirror the SV enum exactly:

- ``UNIT_STRIDED`` — sequential elements at ``base, base+EEW/8, base+2*EEW/8, ...``.
  Mnemonics: ``VLE<EEW>.V`` / ``VSE<EEW>.V`` (+ ``VLE<EEW>FF.V`` if
  ``enable_fault_only_first_load``, + segmented ``VLSEG..`` if ``enable_zvlsseg``).
- ``STRIDED`` — elements at ``base, base+stride, base+2*stride, ...`` where
  the stride is held in ``rs2_reg``. Mnemonics: ``VLSE<EEW>.V`` / ``VSSE<EEW>.V``
  (+ segmented ``VLSSEGE..`` if ``enable_zvlsseg``).
- ``INDEXED`` — element addresses are ``base + vs2[i]``; ``vs2`` is initialized
  via ``li gpr, idx; vmv.v.x vs2, gpr`` (SV ``add_init_vector_gpr_instr``).
  Mnemonics: ``VLXEI<EEW>.V`` / ``VSXEI<EEW>.V`` / ``VSUXEI<EEW>.V``
  (+ segmented variants).

The vector-AMO stream is a subclass that pins ``address_mode = INDEXED`` and
restricts the mnemonic pool to the nine ``VAMO*E.V`` ops.

Key Phase-1 simplifications vs SV:

- Same index value for every element (``vmv.v.x`` initializes the whole
  index vector with one scalar — SV has the same TODO comment).
- ``num_mixed_instr`` is 0..10 (matches SV ``vec_mixed_instr_c``).
- ``stride_byte_offset`` ranges 1..128 (matches SV) but we ensure it is a
  multiple of ``EEW/8`` so SEW-correct accesses stay inside the region.
- ``index_addr`` ranges 0..128 with the same alignment.
- ``mask`` (``vm``) randomized per-op honoring the SV
  ``load_store_mask_overlap_c`` rule (vd != v0 when masked + LMUL>1).

These streams DO emit hart-prefixed region labels via ``hart_prefix(...)``,
matching the scalar load/store family so multi-hart layouts link cleanly.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import ClassVar

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrName,
    RiscvReg,
    RiscvVreg,
    VaVariant,
)
from rvgen.isa.factory import get_instr
from rvgen.isa.filtering import get_rand_instr, randomize_gpr_operands
from rvgen.isa.utils import hart_prefix
from rvgen.sections.data_page import DEFAULT_MEM_REGIONS
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream
from rvgen.streams.directed import _LaPseudo, _LiPseudo


class AddressMode(enum.Enum):
    UNIT_STRIDED = "unit_strided"
    STRIDED = "strided"
    INDEXED = "indexed"


_UNIT_STRIDED_LOAD_STORE = (RiscvInstrName.VLE_V, RiscvInstrName.VSE_V)
_STRIDED_LOAD_STORE = (RiscvInstrName.VLSE_V, RiscvInstrName.VSSE_V)
_INDEXED_LOAD_STORE = (
    RiscvInstrName.VLXEI_V, RiscvInstrName.VSXEI_V, RiscvInstrName.VSUXEI_V,
)

_UNIT_STRIDED_FF = (RiscvInstrName.VLEFF_V,)
_UNIT_STRIDED_SEG = (RiscvInstrName.VLSEGE_V, RiscvInstrName.VSSEGE_V)
_UNIT_STRIDED_SEG_FF = (RiscvInstrName.VLSEGEFF_V,)
_STRIDED_SEG = (RiscvInstrName.VLSSEGE_V, RiscvInstrName.VSSSEGE_V)
_INDEXED_SEG = (
    RiscvInstrName.VLXSEGEI_V, RiscvInstrName.VSXSEGEI_V,
    RiscvInstrName.VSUXSEGEI_V,
)


_VECTOR_AMO_NAMES = (
    RiscvInstrName.VAMOSWAPE_V,
    RiscvInstrName.VAMOADDE_V,
    RiscvInstrName.VAMOXORE_V,
    RiscvInstrName.VAMOANDE_V,
    RiscvInstrName.VAMOORE_V,
    RiscvInstrName.VAMOMINE_V,
    RiscvInstrName.VAMOMAXE_V,
    RiscvInstrName.VAMOMINUE_V,
    RiscvInstrName.VAMOMAXUE_V,
)


@dataclass
class VectorLoadStoreInstrStream(DirectedInstrStream):
    """Vector load/store stream (SV: ``riscv_vector_load_store_instr_stream``)."""

    num_mixed_instr: int = 0
    address_mode: AddressMode | None = None
    eew: int = 0
    stride_byte_offset: int = 0
    index_addr: int = 0
    rs1_reg: RiscvReg | None = None
    rs2_reg: RiscvReg | None = None
    vs2_reg: RiscvVreg | None = None
    base: int = 0
    region_name: str = ""
    data_page_id: int = 0

    # Subclasses may force the address mode (riscv_vector_amo_instr_stream
    # pins INDEXED).
    _force_address_mode: ClassVar[AddressMode | None] = None
    _instr_pool_override: ClassVar[tuple[RiscvInstrName, ...] | None] = None

    # ------------------------------------------------------------------
    # Build entry point
    # ------------------------------------------------------------------

    def build(self) -> None:
        vcfg = self.cfg.vector_cfg
        if vcfg is None or not self.cfg.enable_vector_extension:
            # Target doesn't have vector — emit nothing rather than crash.
            self.instr_list = []
            return
        if not vcfg.legal_eew:
            self.instr_list = []
            return

        # ---- Pick address mode + EEW + alignment ----
        if self._force_address_mode is not None:
            self.address_mode = self._force_address_mode
        elif self.address_mode is None:
            self.address_mode = self.rng.choice(list(AddressMode))

        self.eew = self.rng.choice(vcfg.legal_eew)
        eew_bytes = max(1, self.eew // 8)

        # SV: stride_byte_offset_c ⇒ inside [1:128], stride % (eew/8) == 1.
        # The "% (eew/8) == 1" constraint in SV is suspicious (likely intended
        # "== 0", since stride%bytes==1 produces unaligned non-multiple strides).
        # For functional correctness on spike we use a multiple of eew/8 so
        # every access lands on an EEW-aligned boundary. To still exercise
        # mis-aligned strides we let stride sometimes be a non-multiple when
        # the target advertises support_unaligned_load_store.
        if self.stride_byte_offset == 0:
            stride_choices = [eew_bytes * k for k in range(1, 17)
                              if eew_bytes * k <= 128]
            if self.cfg.target.support_unaligned_load_store:
                # Add a few odd offsets to exercise unaligned loads.
                stride_choices += [eew_bytes + 1, eew_bytes * 2 + 1]
            self.stride_byte_offset = self.rng.choice(stride_choices) if stride_choices else eew_bytes

        # Index addr: 0..128 in EEW-byte multiples.
        if self.index_addr == 0 and self.address_mode == AddressMode.INDEXED:
            idx_choices = [eew_bytes * k for k in range(0, 17)
                           if eew_bytes * k <= 128]
            self.index_addr = self.rng.choice(idx_choices) if idx_choices else 0

        # ---- Pick region + base address ----
        regions = (
            self.cfg.mem_regions()
            if hasattr(self.cfg, "mem_regions") else DEFAULT_MEM_REGIONS
        )
        # Prefer a region big enough for the access span.
        span = self._address_span(vcfg)
        eligible = [(i, r) for i, r in enumerate(regions) if r.size_in_bytes >= span + eew_bytes]
        if not eligible:
            eligible = list(enumerate(regions))
        idx, region = self.rng.choice(eligible)
        self.data_page_id = idx
        num_harts = self.cfg.num_of_harts if self.cfg else 1
        self.region_name = hart_prefix(self.hart, num_harts) + region.name

        max_base = max(0, region.size_in_bytes - span - eew_bytes)
        if max_base <= 0:
            self.base = 0
        else:
            # Keep base EEW-aligned to avoid spurious access faults.
            self.base = (self.rng.randint(0, max_base) // eew_bytes) * eew_bytes

        # ---- Pick scalar / vector operand registers ----
        reserved = set(self.cfg.reserved_regs) | {RiscvReg.ZERO, RiscvReg.GP, RiscvReg.RA}
        gpr_pool = [r for r in RiscvReg if r not in reserved]
        if len(gpr_pool) < 2:
            gpr_pool = [r for r in RiscvReg if r != RiscvReg.ZERO]
        # rs1 is the base, rs2 is the stride (when STRIDED).
        self.rs1_reg = self.rng.choice(gpr_pool)
        rs2_pool = [r for r in gpr_pool if r != self.rs1_reg]
        self.rs2_reg = self.rng.choice(rs2_pool) if rs2_pool else self.rs1_reg

        # vs2 is the index vector (when INDEXED). Avoid v0 if mask is enabled
        # for any of the load/store ops; v0 is the mask register.
        vreg_reserved = set(vcfg.reserved_vregs) | {RiscvVreg.V0}
        vreg_pool = [v for v in RiscvVreg if v not in vreg_reserved]
        self.vs2_reg = self.rng.choice(vreg_pool) if vreg_pool else RiscvVreg.V1

        # ---- Number of mixed filler instructions ----
        if self.num_mixed_instr == 0:
            self.num_mixed_instr = self.rng.randint(0, 10)

        # ---- Emit ----
        body: list[Instr] = []

        # Mixed instructions go BEFORE the load/store (interleaved).
        body.extend(self._gen_mixed_instr({self.rs1_reg, self.rs2_reg}))

        # The actual vector load/store.
        ls_instr = self._gen_load_store_instr(vcfg)
        if ls_instr is not None:
            body.append(ls_instr)

        # ---- Prelude: init rs1 with `la rs1, region`, optionally addi base ----
        prelude: list[Instr] = []
        la = _LaPseudo()
        la.rd = self.rs1_reg
        la.imm_str = self.region_name
        la.atomic = True
        prelude.append(la)
        if self.base:
            addi = get_instr(RiscvInstrName.ADDI)
            addi.rd = self.rs1_reg
            addi.rs1 = self.rs1_reg
            addi.imm = self.base & 0xFFF
            addi.imm_str = str(self.base)
            addi.process_load_store = False
            addi.post_randomize()
            prelude.append(addi)

        # STRIDED: init rs2 with the stride literal.
        if self.address_mode == AddressMode.STRIDED:
            li_stride = _LiPseudo()
            li_stride.rd = self.rs2_reg
            li_stride.imm = self.stride_byte_offset
            li_stride.imm_str = str(self.stride_byte_offset)
            li_stride.atomic = True
            prelude.append(li_stride)

        # INDEXED: init vs2 = vmv.v.x vs2, gpr0 (broadcast index_addr).
        if self.address_mode == AddressMode.INDEXED:
            scratch = self.cfg.gpr[0]
            li_idx = _LiPseudo()
            li_idx.rd = scratch
            li_idx.imm = self.index_addr
            li_idx.imm_str = str(self.index_addr)
            li_idx.atomic = True
            prelude.append(li_idx)
            prelude.append(_VmvVxPseudo(self.vs2_reg, scratch))

        self.instr_list = prelude + body

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _address_span(self, vcfg) -> int:
        """SV ``address_span`` — bytes consumed by the worst-case access."""
        eew_bytes = max(1, self.eew // 8) if self.eew else 1
        # SV: num_elements = VLEN * LMUL / SEW. We use legal_eew so eew may
        # exceed SEW; pick the conservative span.
        sew = vcfg.vtype.vsew
        lmul = vcfg.vtype.vlmul if not vcfg.vtype.fractional_lmul else 1
        num_elements = max(1, vcfg.vlen * lmul // sew)
        if self.address_mode == AddressMode.UNIT_STRIDED:
            return num_elements * eew_bytes
        if self.address_mode == AddressMode.STRIDED:
            return num_elements * max(eew_bytes, self.stride_byte_offset or eew_bytes)
        if self.address_mode == AddressMode.INDEXED:
            return (self.index_addr or 0) + num_elements * eew_bytes
        return num_elements * eew_bytes

    def _allowed_instr(self, vcfg) -> tuple[RiscvInstrName, ...]:
        if self._instr_pool_override is not None:
            return self._instr_pool_override
        out: list[RiscvInstrName] = []
        if self.address_mode == AddressMode.UNIT_STRIDED:
            out.extend(_UNIT_STRIDED_LOAD_STORE)
            if vcfg.enable_fault_only_first_load:
                out.extend(_UNIT_STRIDED_FF)
            if vcfg.enable_zvlsseg:
                out.extend(_UNIT_STRIDED_SEG)
                if vcfg.enable_fault_only_first_load:
                    out.extend(_UNIT_STRIDED_SEG_FF)
        elif self.address_mode == AddressMode.STRIDED:
            out.extend(_STRIDED_LOAD_STORE)
            if vcfg.enable_zvlsseg:
                out.extend(_STRIDED_SEG)
        elif self.address_mode == AddressMode.INDEXED:
            out.extend(_INDEXED_LOAD_STORE)
            if vcfg.enable_zvlsseg:
                out.extend(_INDEXED_SEG)
        return tuple(out)

    def _gen_load_store_instr(self, vcfg) -> Instr | None:
        names = self._allowed_instr(vcfg)
        # Filter to the names that are actually registered in this build.
        registered = [n for n in names if n in self.avail.names]
        # If a name is unregistered (e.g., zvlsseg gated off in this target's
        # cover-gen) but is in `names` because the user set the knob, fall
        # back to ALL declared names so we still emit something legal.
        if not registered:
            from rvgen.isa.factory import INSTR_REGISTRY
            registered = [n for n in names if n in INSTR_REGISTRY]
        if not registered:
            return None
        pick = self.rng.choice(registered)
        instr = get_instr(pick)

        # Wire scalar / vector operands.
        instr.rs1 = self.rs1_reg
        if self.address_mode == AddressMode.STRIDED:
            instr.rs2 = self.rs2_reg
        if self.address_mode == AddressMode.INDEXED:
            instr.vs2 = self.vs2_reg

        # vd / vs3 picks honor the same operand-group constraints as the rest
        # of the vector subsystem. We let the standard randomizer pick.
        instr.eew = self.eew
        # Disable rs1/imm randomization in the standard vector_operands path —
        # the stream wires rs1 explicitly. Reuse the existing helper so vd /
        # vs3 / vm get picked legally.
        instr.has_rs1 = False
        instr.has_imm = False
        instr.randomize_vector_operands(self.rng, vcfg)
        # Re-pin the slots the helper may have re-randomized.
        instr.rs1 = self.rs1_reg
        if self.address_mode == AddressMode.STRIDED:
            instr.rs2 = self.rs2_reg
        if self.address_mode == AddressMode.INDEXED:
            instr.vs2 = self.vs2_reg
            # Reserve the index vector so subsequent random ops don't clobber.
            vcfg.reserved_vregs = tuple(set(vcfg.reserved_vregs) | {self.vs2_reg})

        instr.process_load_store = False
        instr.atomic = True
        return instr

    def _gen_mixed_instr(self, locked_regs: set[RiscvReg]) -> list[Instr]:
        if self.num_mixed_instr <= 0:
            return []
        out: list[Instr] = []
        xlen = self.cfg.target.xlen
        avail_regs = tuple(r for r in RiscvReg if r not in locked_regs)
        # SV: mixed_instr is scalar-only — vector ops have their own stream.
        # We exclude RVV/Zve* groups and STORE/LOAD/AMO categories so the
        # filler is plain scalar arithmetic that won't reach into memory.
        from rvgen.isa.enums import RiscvInstrGroup
        _VECTOR_GROUPS = frozenset({
            RiscvInstrGroup.RVV, RiscvInstrGroup.ZVE32X, RiscvInstrGroup.ZVE32F,
            RiscvInstrGroup.ZVE64X, RiscvInstrGroup.ZVE64F, RiscvInstrGroup.ZVE64D,
        })
        exclude_group = tuple(g for g in _VECTOR_GROUPS if g in self.avail.by_group)
        for _ in range(self.num_mixed_instr):
            instr = get_rand_instr(
                self.rng,
                self.avail,
                include_category=[
                    RiscvInstrCategory.ARITHMETIC,
                    RiscvInstrCategory.LOGICAL,
                    RiscvInstrCategory.COMPARE,
                    RiscvInstrCategory.SHIFT,
                ],
                exclude_group=list(exclude_group),
            )
            forbidden_rd = tuple(locked_regs - {RiscvReg.ZERO})
            randomize_gpr_operands(
                instr, self.rng, self.cfg,
                avail_regs=avail_regs,
                reserved_rd=forbidden_rd,
            )
            if instr.has_imm:
                instr.randomize_imm(self.rng, xlen=xlen)
            instr.post_randomize()
            out.append(instr)
        return out


@dataclass
class VectorAmoInstrStream(VectorLoadStoreInstrStream):
    """Vector AMO stream (SV: ``riscv_vector_amo_instr_stream``).

    Pins ``address_mode = INDEXED`` and restricts the mnemonic pool to the
    nine ratified ``VAMO*E.V`` ops.

    .. note::
       Vector-AMO was **removed in RVV 1.0** ratified. Mainline spike-vector
       and current GCC binutils reject ``vamoaddei.v`` etc. Targets must
       opt in via ``vector_amo_supported=True`` (pre-1.0 RVV draft only).
       When the target doesn't opt in, this stream falls back to a plain
       vector load/store stream so the testlist entry doesn't error.
    """

    _force_address_mode: ClassVar[AddressMode] = AddressMode.INDEXED
    _instr_pool_override: ClassVar[tuple[RiscvInstrName, ...]] = _VECTOR_AMO_NAMES

    def build(self) -> None:
        target = self.cfg.target
        if not getattr(target, "vector_amo_supported", False):
            # Fall back to indexed vector load/store; vector AMO mnemonics
            # don't assemble on RVV 1.0 toolchains. Logged once via the
            # comment on the first emitted instruction.
            self._instr_pool_override = None  # noqa: pragma: no cover - shadows class
            super().build()
            if self.instr_list:
                self.instr_list[0].comment = (
                    "vector AMO unsupported by target — fell back to indexed LS"
                )
            return
        super().build()


# ---------------------------------------------------------------------------
# vmv.v.x pseudo — used to broadcast a scalar GPR into a vector register
# (the SV `add_init_vector_gpr_instr` helper).
# ---------------------------------------------------------------------------


class _VmvVxPseudo(Instr):
    """``vmv.v.x v<dst>, x<src>`` minimal pseudo-instr."""

    instr_name = RiscvInstrName.VMV
    format = None  # type: ignore[assignment]
    category = RiscvInstrCategory.ARITHMETIC
    group = None  # type: ignore[assignment]

    def __init__(self, vd: RiscvVreg, gpr: RiscvReg) -> None:
        self.rs1 = gpr
        self.rs2 = RiscvReg.ZERO
        self.rd = RiscvReg.ZERO
        self.csr = 0
        self.imm = 0
        self.has_rs1 = True
        self.has_rs2 = False
        self.has_rd = False
        self.has_imm = False
        self.imm_len = 0
        self.imm_mask = 0
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
        self._vd = vd

    def set_imm_len(self) -> None:
        pass

    def set_rand_mode(self) -> None:
        pass

    def post_randomize(self) -> None:
        pass

    def get_instr_name(self) -> str:
        return "vmv.v.x"

    def convert2asm(self, prefix: str = "") -> str:
        body = f"vmv.v.x      {self._vd.abi}, {self.rs1.abi}"
        if self.comment:
            body = f"{body} #{self.comment}"
        return body

    def convert2bin(self, prefix: str = "") -> str:
        raise NotImplementedError("Vector pseudo — let the assembler expand it")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_stream("riscv_vector_load_store_instr_stream", VectorLoadStoreInstrStream)
register_stream("riscv_vector_amo_instr_stream", VectorAmoInstrStream)
