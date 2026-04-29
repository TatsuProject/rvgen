"""Vector base class — port of ``src/isa/riscv_vector_instr.sv``.

``VectorInstr`` extends :class:`FloatingPointInstr` because every vector op
may carry an integer ``rs1`` (for load/store base, VX variant, VMV_S_X, …),
an FP ``fs1`` (for VF variant, VFMV_V_F), or an integer ``rd`` / FP ``fd``
(for VMV_X_S / VFMV_F_S). The SV inheritance chain is
``riscv_instr -> riscv_floating_point_instr -> riscv_vector_instr``.

In addition to the inherited slots, each instance carries:

- ``vs1, vs2, vs3, vd`` — vector register operands.
- ``vm`` — mask bit (0 means "mask with v0.t", 1 means "unmasked").
- ``eew`` — element width (bits) for loads/stores/AMOs.
- ``emul`` — element multiplier (EEW/SEW * LMUL).
- ``nfields`` — Zvlsseg segment count (0..7 → 1..8 fields).
- ``wd`` — AMO write-destination flag (VAMO*).
- ``va_variant`` — VV / VI / VX / VF / WV / WI / WX / VVM / VIM / VXM /
  VFM / VS / VM (picked from ``allowed_va_variants``).

Widening / narrowing / quad-widening / convert detection is done by scanning
the mnemonic at construction time (SV lines 520-533).
"""

from __future__ import annotations

import random
from typing import ClassVar, Sequence

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    MAX_INSTR_STR_LEN,
    RiscvFpr,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
    RiscvVreg,
    VaVariant,
    ImmType,
)
from rvgen.isa.factory import INSTR_REGISTRY, _assert_not_registered
from rvgen.isa.floating_point import FloatingPointInstr
from rvgen.isa.utils import format_string


_FMT = RiscvInstrFormat
_CAT = RiscvInstrCategory
_N = RiscvInstrName


# Names that always carry vm=0 (mask-enabled).
_VM_ALWAYS_MASKED = frozenset({_N.VMERGE, _N.VFMERGE, _N.VADC, _N.VSBC})

# Names that always carry vm=1 (mask-disabled / unmasked).
_VM_ALWAYS_UNMASKED = frozenset({
    _N.VMV, _N.VFMV,
    _N.VCOMPRESS,
    _N.VFMV_F_S, _N.VFMV_S_F, _N.VMV_X_S, _N.VMV_S_X,
    _N.VMV1R_V, _N.VMV2R_V, _N.VMV4R_V, _N.VMV8R_V,
})

# .vi instructions whose 5-bit immediate is encoded unsigned (0..31):
# all shifts and the slide/rgather index ops. The rest (arithmetic +
# compare + vmv.v.i + vmerge.vim) use signed -16..15.
_UNSIGNED_VI_IMM_NAMES = frozenset({
    _N.VSLL, _N.VSRL, _N.VSRA,
    _N.VNSRL, _N.VNSRA,
    _N.VSSRL, _N.VSSRA,
    _N.VNCLIP, _N.VNCLIPU,
    _N.VSLIDEUP, _N.VSLIDEDOWN,
    _N.VRGATHER,
})


# Mask-register logical ops VMAND_MM..VMXNOR_MM — always vm=1.
_VM_MASK_LOGIC_OPS = frozenset({
    _N.VMAND_MM, _N.VMNAND_MM, _N.VMANDNOT_MM, _N.VMXOR_MM,
    _N.VMOR_MM, _N.VMNOR_MM, _N.VMORNOT_MM, _N.VMXNOR_MM,
})

# Compare-category names where vd must differ from vs1/vs2 when LMUL>1.
_COMPARE_NAMES = frozenset({
    _N.VMSEQ, _N.VMSNE, _N.VMSLTU, _N.VMSLT,
    _N.VMSLEU, _N.VMSLE, _N.VMSGTU, _N.VMSGT,
    _N.VMFEQ, _N.VMFNE, _N.VMFLT, _N.VMFLE, _N.VMFGT, _N.VMFGE,
})

# Slide / gather / compress / itoa — vd != vs1, vd != vs2; vm=0 ⇒ vd != v0.
_NONOVERLAP_NAMES = frozenset({
    _N.VSLIDEUP, _N.VSLIDEDOWN, _N.VSLIDE1UP, _N.VSLIDE1DOWN,
    _N.VRGATHER, _N.VCOMPRESS, _N.VIOTA_M,
})


def _is_widening_name(n: RiscvInstrName) -> bool:
    s = n.name
    return s.startswith("VW") or s.startswith("VFW")


def _is_narrowing_name(n: RiscvInstrName) -> bool:
    s = n.name
    return s.startswith("VN") or s.startswith("VFN")


def _is_quad_widening_name(n: RiscvInstrName) -> bool:
    return n.name.startswith("VQW")


def _is_convert_name(n: RiscvInstrName) -> bool:
    return "CVT" in n.name


class VectorInstr(FloatingPointInstr):
    """Base class for every RVV instruction (SV: ``riscv_vector_instr``)."""

    # Class-level attrs populated by :func:`define_vector_instr`.
    allowed_va_variants: ClassVar[tuple[VaVariant, ...]] = ()
    sub_extension: ClassVar[str] = ""

    __slots__ = (
        "vs1", "vs2", "vs3", "vd", "vm", "wd",
        "eew", "emul", "nfields", "va_variant",
        "has_vs1", "has_vs2", "has_vs3", "has_vd", "has_vm", "has_va_variant",
        "is_widening_instr", "is_narrowing_instr",
        "is_quad_widening_instr", "is_convert_instr",
    )

    def __init__(self) -> None:
        super().__init__()

        self.vs1: RiscvVreg = RiscvVreg.V0
        self.vs2: RiscvVreg = RiscvVreg.V0
        self.vs3: RiscvVreg = RiscvVreg.V0
        self.vd: RiscvVreg = RiscvVreg.V0
        self.vm: int = 1
        self.wd: int = 0
        self.eew: int = 0
        self.emul: int = 1
        self.nfields: int = 0
        self.va_variant: VaVariant = (
            self.allowed_va_variants[0] if self.allowed_va_variants else VaVariant.VV
        )

        self.has_vs1: bool = True
        self.has_vs2: bool = True
        self.has_vs3: bool = True
        self.has_vd: bool = True
        self.has_vm: bool = False
        self.has_va_variant: bool = bool(self.allowed_va_variants)

        self.is_widening_instr: bool = _is_widening_name(self.instr_name)
        self.is_narrowing_instr: bool = _is_narrowing_name(self.instr_name)
        self.is_quad_widening_instr: bool = _is_quad_widening_name(self.instr_name)
        if self.is_quad_widening_instr:
            self.is_widening_instr = True
        self.is_convert_instr: bool = _is_convert_name(self.instr_name)
        if self.is_convert_instr:
            self.has_vs1 = False

        # FloatingPointInstr.__init__ already set this; keep explicit for clarity.
        self.is_floating_point = False

        # Re-apply the vector-aware rand mode now that has_va_variant is known.
        self.set_rand_mode()

    # ------------------------------------------------------------------
    # Rand mode (SV: riscv_vector_instr::set_rand_mode, line 507)
    # ------------------------------------------------------------------

    def set_rand_mode(self) -> None:
        # On the *first* call (from Instr.__init__ super chain) our vector
        # attrs aren't populated yet. Skip — __init__ will call again after.
        if not hasattr(self, "has_vs1"):
            return
        # Default: rs1 enabled, rest of int/fp slots disabled.
        self.has_rs1 = True
        self.has_rs2 = False
        self.has_rd = False
        self.has_fs1 = False
        self.has_fs2 = False
        self.has_fs3 = False
        self.has_fd = False
        self.has_imm = False
        if self.format == _FMT.VA_FORMAT:
            self.has_imm = True
            self.has_rs1 = True
            self.has_fs1 = True

    def set_imm_len(self) -> None:
        # Vector 5-bit immediates (VI / WI / VIM variants). SV treats these
        # as signed IMM; in practice GCC 15 rejects negative literals on
        # shift/slide/rgather variants (which require unsigned 0..31), so we
        # treat the whole family as unsigned 5-bit for Phase 1. This changes
        # vadd.vi/vxor.vi semantics from "signed -16..15" to "unsigned 0..31"
        # — functionally equivalent under bit randomization, and every
        # mnemonic assembles cleanly.
        if self.format == _FMT.VA_FORMAT:
            self.imm_len = 5
            self.imm_mask = 0  # disable sign-extension in extend_imm
        else:
            self.imm_len = 0
            self.imm_mask = 0xFFFFFFFF

    def randomize_imm(self, rng: random.Random, xlen: int) -> None:
        if self.imm_len:
            self.imm = rng.getrandbits(self.imm_len)

    def extend_imm(self) -> None:
        # Keep imm as a 5-bit unsigned value internally; update_imm_str
        # decides whether to render it as signed (-16..15) or unsigned (0..31)
        # per-instruction.
        if self.imm_len:
            self.imm &= (1 << self.imm_len) - 1

    def update_imm_str(self) -> None:
        # Unsigned shift/slide/rgather ops → 0..31. Otherwise sign-extend to
        # -16..15 so negative values render as "-N" (GCC 15 requires signed
        # literals for vadd.vi / vxor.vi / vmseq.vi etc).
        if not self.imm_len:
            self.imm_str = ""
            return
        if self.instr_name in _UNSIGNED_VI_IMM_NAMES:
            self.imm_str = str(self.imm)
        else:
            # Sign-extend 5-bit → Python int.
            v = self.imm
            if v & (1 << (self.imm_len - 1)):
                v -= 1 << self.imm_len
            self.imm_str = str(v)

    # ------------------------------------------------------------------
    # Operand randomization
    # ------------------------------------------------------------------

    def randomize_vector_operands(
        self,
        rng: random.Random,
        vector_cfg,  # VectorConfig, avoid circular import
    ) -> None:
        """Pick vs1/vs2/vs3/vd/vm/va_variant/eew/emul honoring SV constraints.

        Implements the constraints from ``riscv_vector_instr.sv``:

        - ``va_variant_c`` — pick from ``allowed_va_variants``.
        - ``operand_group_c`` — vd/vs*/vs3 must be multiples of vlmul.
        - ``widening_instr_c`` — vd multiple of 2*vlmul; vs1/vs2 must not
          overlap the 2*vlmul group starting at vd.
        - ``narrowing_instr_c`` — vs2 multiple of 2*vlmul; vd not in that
          group.
        - ``compare_instr_c`` — vd != vs1 and vd != vs2 when LMUL>1.
        - ``vector_slide_c`` / ``vector_gather_c`` / ``vector_compress_c`` /
          ``vector_itoa_c`` / ``vector_element_index_c`` — analogous no-overlap
          rules.
        - ``vector_mask_{enable,disable,instr}_c`` + ``vmask_overlap_c`` —
          force vm per instruction category; when vm==0 and LMUL>1, vd != v0.
        - ``load_store_eew_emul_c`` — pick eew from legal_eew; emul = eew/vsew
          when eew>vsew else 1; registers must be multiple of emul.
        """
        vtype = vector_cfg.vtype
        lmul = vtype.vlmul
        reserved = set(vector_cfg.reserved_vregs)
        all_vregs = tuple(RiscvVreg)
        name = self.instr_name

        # ---- va_variant ----
        if self.allowed_va_variants:
            variants = list(self.allowed_va_variants)
            if not vector_cfg.vec_fp:
                variants = [v for v in variants if v not in (VaVariant.VF, VaVariant.VFM)]
            if variants:
                self.va_variant = rng.choice(variants)

        # ---- vm ----
        if name in _VM_ALWAYS_MASKED:
            self.vm = 0
        elif name in _VM_ALWAYS_UNMASKED or name in _VM_MASK_LOGIC_OPS:
            self.vm = 1
        else:
            # Hazard mode: smaller pool; pick vm random otherwise.
            self.vm = rng.randint(0, 1)

        # ---- Determine emul / eew (for loads/stores/AMOs) ----
        is_load_store_amo = self.category in (_CAT.LOAD, _CAT.STORE, _CAT.AMO)
        if is_load_store_amo and vector_cfg.legal_eew:
            self.eew = rng.choice(vector_cfg.legal_eew)
            if self.eew > vtype.vsew:
                # emul = eew/vsew * 1 (LMUL already baked in legal_eew derivation)
                self.emul = max(1, self.eew // vtype.vsew)
            else:
                self.emul = 1
            grp = max(lmul, self.emul)
        else:
            self.eew = vtype.vsew
            self.emul = 1
            grp = lmul

        # ---- Constraints for widening / narrowing ----
        vd_step = grp
        if self.is_widening_instr:
            vd_step = max(grp, lmul * 2)
        elif self.is_narrowing_instr:
            # vs2 is double-width; vd remains lmul-aligned.
            pass

        # ---- Non-overlap rules we need to apply ----
        vd_must_differ_from_sources = (
            name in _COMPARE_NAMES
            or name in _NONOVERLAP_NAMES
            or self.is_widening_instr
            or self.is_narrowing_instr
        ) and lmul >= 1

        # ---- Pick registers ----
        def _candidates(step: int) -> list[RiscvVreg]:
            if step <= 1:
                return [v for v in all_vregs if v not in reserved]
            return [v for v in all_vregs if int(v) % step == 0 and v not in reserved]

        # vd: respect step, v0 restriction when masked + LMUL>1.
        vd_pool = _candidates(vd_step)
        if self.vm == 0 and lmul > 1:
            vd_pool = [v for v in vd_pool if v != RiscvVreg.V0]
        if not vd_pool:
            vd_pool = [v for v in all_vregs if v not in reserved] or list(all_vregs)
        self.vd = rng.choice(vd_pool)

        # vs1: step=grp normally; for widening va_variant WV/WX the double-width
        # source is vs2 (not vs1). Don't apply the no-overlap constraint on vs1
        # for widening in general except to avoid equality with vd.
        vs1_step = grp
        vs1_pool = _candidates(vs1_step)
        if vd_must_differ_from_sources:
            vs1_pool = [v for v in vs1_pool if v != self.vd]
        if not vs1_pool:
            vs1_pool = _candidates(vs1_step) or list(all_vregs)
        self.vs1 = rng.choice(vs1_pool)

        # vs2: for narrowing (or W-variant of a widening op) step=2*lmul.
        vs2_step = grp
        if self.is_narrowing_instr or (
            self.is_widening_instr and self.va_variant in (VaVariant.WV, VaVariant.WX)
        ):
            vs2_step = max(grp, lmul * 2)
        vs2_pool = _candidates(vs2_step)
        if vd_must_differ_from_sources:
            vs2_pool = [v for v in vs2_pool if v != self.vd]
        if not vs2_pool:
            vs2_pool = _candidates(vs2_step) or list(all_vregs)
        self.vs2 = rng.choice(vs2_pool)

        # vs3 (used by stores and AMO write-dst).
        vs3_pool = _candidates(grp)
        if self.category == _CAT.STORE:
            # SV load_store_mask_overlap_c: when masked, vs3 != v0.
            if self.vm == 0:
                vs3_pool = [v for v in vs3_pool if v != RiscvVreg.V0]
            # vs2 != vs3 per SV.
            vs3_pool = [v for v in vs3_pool if v != self.vs2]
        if not vs3_pool:
            vs3_pool = _candidates(grp) or list(all_vregs)
        self.vs3 = rng.choice(vs3_pool)

        # Instruction-specific fixed constraints.
        if name == _N.VID_V:
            self.vs2 = RiscvVreg.V0  # SV: vs2 field must be 0
        if name == _N.VIOTA_M and self.vd == self.vs2:
            # Retry once to break the overlap.
            alt = [v for v in _candidates(vd_step) if v != self.vs2]
            if alt:
                self.vd = rng.choice(alt)

        # AMO write-destination flag.
        if self.category == _CAT.AMO:
            self.wd = rng.randint(0, 1)

        # Zvlsseg nfields.
        if self.sub_extension == "zvlsseg":
            if vtype.vlmul < 8:
                max_fields = min(7, (8 // vtype.vlmul) - 1)
                if max_fields < 1:
                    max_fields = 1
                self.nfields = rng.randint(1, max_fields) - 1
            else:
                self.nfields = 0

    # ------------------------------------------------------------------
    # Instr name suffix (SV: get_instr_name override)
    # ------------------------------------------------------------------

    def get_instr_name(self) -> str:
        """Assembly mnemonic, with EEW suffix for loads/stores.

        Two layered concerns vs the SV reference:

        1. SV uses ``string::substr(0, len - N)`` (inclusive both ends), which
           we translate to Python's ``[:-(N-1)]`` — for trailing ``.V`` (2
           chars) that's ``[:-2]``; for ``FF.V`` (4 chars) that's ``[:-4]``.
           Earlier ``[:-3]`` / ``[:-5]`` were off-by-one — only noticed once
           a stream actually emitted vector loads/stores.

        2. The SV enum names (e.g., ``VLXEI_V``, ``VSXEI_V``) are from the
           pre-1.0 RVV draft. RVV 1.0 split indexed loads/stores into
           ordered (``vloxei``, ``vsoxei``) and unordered (``vluxei``,
           ``vsuxei``) variants, removing the ambiguous ``vlxei`` /
           ``vsxei`` mnemonics. We map onto the unordered ratified form by
           default (matches the indexing semantics our randomizer assumes).
           Vector AMO uses the pre-1.0 ``vamo*ei`` form as ratified.
        """
        n = self.instr_name
        # RVV 1.0 mnemonic root for indexed loads/stores. Inserted in front
        # of the EEW digits.
        _RVV1_INDEXED_ROOT = {
            _N.VLXEI_V: "vluxei",
            _N.VSXEI_V: "vsuxei",
            _N.VSUXEI_V: "vsuxei",
            _N.VLXSEGEI_V: "vluxseg",
            _N.VSXSEGEI_V: "vsuxseg",
            _N.VSUXSEGEI_V: "vsuxseg",
        }
        if (
            self.category in (_CAT.LOAD, _CAT.STORE)
            and self.eew
            and n in _RVV1_INDEXED_ROOT
        ):
            root = _RVV1_INDEXED_ROOT[n]
            if "seg" in root:
                # vluxseg<NF>ei<EEW>.v form. nfields holds (N - 1).
                nf = self.nfields + 1 if self.nfields else 2
                return f"{root}{nf}ei{self.eew}.v"
            return f"{root}{self.eew}.v"

        name = super().get_instr_name()
        if self.category in (_CAT.LOAD, _CAT.STORE) and self.eew:
            if n in (_N.VLEFF_V, _N.VLSEGEFF_V):
                stem = name[:-4]  # strip trailing "FF.V"
                # Zvlsseg fault-only-first form: vlseg<NF>e<EEW>ff.v
                if n == _N.VLSEGEFF_V:
                    nf = self.nfields + 1 if self.nfields else 2
                    return f"vlseg{nf}e{self.eew}ff.v"
                return f"{stem}{self.eew}ff.v"
            if n in (_N.VLSEGE_V, _N.VSSEGE_V, _N.VLSSEGE_V, _N.VSSSEGE_V):
                # Ratified Zvlsseg: vlseg<NF>e<EEW>.v / vsseg<NF>e<EEW>.v
                # (and vlsseg / vssseg for the strided variants).
                nf = self.nfields + 1 if self.nfields else 2
                if n == _N.VLSEGE_V:
                    return f"vlseg{nf}e{self.eew}.v"
                if n == _N.VSSEGE_V:
                    return f"vsseg{nf}e{self.eew}.v"
                if n == _N.VLSSEGE_V:
                    return f"vlsseg{nf}e{self.eew}.v"
                if n == _N.VSSSEGE_V:
                    return f"vssseg{nf}e{self.eew}.v"
            stem = name[:-2]  # strip trailing ".V"
            return f"{stem}{self.eew}.v"
        if self.category == _CAT.AMO and self.eew:
            stem = name[:-2]  # strip trailing ".V"
            # Pre-1.0 vector-AMO ratified form: vamo<op>ei<EEW>.v. The SV
            # enum drops the trailing ``i`` (so ``VAMOADDE_V`` is "VAMOADDE.V")
            # — re-insert it before the EEW. Compare case-insensitively
            # since `super().get_instr_name()` keeps the upper-case name.
            if stem.lower().endswith("e"):
                return f"{stem}i{self.eew}.v"
            return f"{stem}{self.eew}.v"
        return name

    # ------------------------------------------------------------------
    # convert2asm (SV: riscv_vector_instr::convert2asm, line 358)
    # ------------------------------------------------------------------

    def convert2asm(self, prefix: str = "") -> str:
        name = self.instr_name
        fmt = self.format

        if fmt == _FMT.VSET_FORMAT:
            # vsetvli/vsetvl are emitted verbatim by init/boot code; the random
            # stream doesn't pick them (category=CSR is filtered). Fall back to
            # a plain mnemonic; caller overrides.
            mnemonic = format_string(self.get_instr_name().lower(), MAX_INSTR_STR_LEN)
            asm_str = f"{mnemonic}{self.rd.abi}, {self.rs1.abi}, e{32}, m1, d1"
            return asm_str

        asm_str = ""

        if fmt == _FMT.VS2_FORMAT:
            if name == _N.VID_V:
                asm_str = f"vid.v {self.vd.abi}"
            elif name in (_N.VPOPC_M, _N.VFIRST_M):
                asm_str = f"{self.get_instr_name().lower()} {self.rd.abi}, {self.vs2.abi}"
            else:
                asm_str = f"{self.get_instr_name().lower()} {self.vd.abi}, {self.vs2.abi}"

        elif fmt == _FMT.VA_FORMAT:
            asm_str = self._convert_va_format()

        elif fmt == _FMT.VL_FORMAT:
            asm_str = (
                f"{self.get_instr_name().lower()} {self.vd.abi}, ({self.rs1.abi})"
            )
        elif fmt == _FMT.VS_FORMAT:
            asm_str = (
                f"{self.get_instr_name().lower()} {self.vs3.abi}, ({self.rs1.abi})"
            )
        elif fmt == _FMT.VLS_FORMAT:
            asm_str = (
                f"{self.get_instr_name().lower()} "
                f"{self.vd.abi}, ({self.rs1.abi}), {self.rs2.abi}"
            )
        elif fmt == _FMT.VSS_FORMAT:
            asm_str = (
                f"{self.get_instr_name().lower()} "
                f"{self.vs3.abi}, ({self.rs1.abi}), {self.rs2.abi}"
            )
        elif fmt == _FMT.VLX_FORMAT:
            asm_str = (
                f"{self.get_instr_name().lower()} "
                f"{self.vd.abi}, ({self.rs1.abi}), {self.vs2.abi}"
            )
        elif fmt == _FMT.VSX_FORMAT:
            asm_str = (
                f"{self.get_instr_name().lower()} "
                f"{self.vs3.abi}, ({self.rs1.abi}), {self.vs2.abi}"
            )
        elif fmt == _FMT.VAMO_FORMAT:
            if self.wd:
                asm_str = (
                    f"{self.get_instr_name().lower()} "
                    f"{self.vd.abi}, ({self.rs1.abi}), {self.vs2.abi}, {self.vd.abi}"
                )
            else:
                asm_str = (
                    f"{self.get_instr_name().lower()} "
                    f"x0, ({self.rs1.abi}), {self.vs2.abi}, {self.vs3.abi}"
                )
        else:
            raise ValueError(f"Unsupported vector format {fmt.name} for {name.name}")

        # Append mask suffix.
        asm_str += self._vec_vm_str()

        if self.comment:
            asm_str = f"{asm_str} #{self.comment}"
        return asm_str.lower()

    # ------------------------------------------------------------------
    # VA_FORMAT helper (long dispatch mirrors SV)
    # ------------------------------------------------------------------

    def _convert_va_format(self) -> str:
        name = self.instr_name
        mnemonic_pad = MAX_INSTR_STR_LEN
        imm_str = self.imm_str or str(self.imm)

        if name == _N.VMV:
            if self.va_variant == VaVariant.VV:
                return f"vmv.v.v {self.vd.abi}, {self.vs1.abi}"
            if self.va_variant == VaVariant.VX:
                return f"vmv.v.x {self.vd.abi}, {self.rs1.abi}"
            if self.va_variant == VaVariant.VI:
                return f"vmv.v.i {self.vd.abi}, {imm_str}"
        if name == _N.VFMV:
            return f"vfmv.v.f {self.vd.abi}, {self.fs1.abi}"
        if name == _N.VMV_X_S:
            return f"vmv.x.s {self.rd.abi}, {self.vs2.abi}"
        if name == _N.VMV_S_X:
            return f"vmv.s.x {self.vd.abi}, {self.rs1.abi}"
        if name == _N.VFMV_F_S:
            return f"vfmv.f.s {self.fd.abi}, {self.vs2.abi}"
        if name == _N.VFMV_S_F:
            return f"vfmv.s.f {self.vd.abi}, {self.fs1.abi}"

        if not self.has_va_variant:
            mnemonic = format_string(f"{self.get_instr_name().lower()} ", mnemonic_pad)
            return f"{mnemonic}{self.vd.abi}, {self.vs2.abi}, {self.vs1.abi}"

        variant = self.va_variant
        mnemonic = format_string(
            f"{self.get_instr_name().lower()}.{variant.name.lower()} ", mnemonic_pad
        )

        if variant in (VaVariant.WV, VaVariant.VV, VaVariant.VVM, VaVariant.VM):
            return f"{mnemonic}{self.vd.abi}, {self.vs2.abi}, {self.vs1.abi}"
        if variant in (VaVariant.WI, VaVariant.VI, VaVariant.VIM):
            return f"{mnemonic}{self.vd.abi}, {self.vs2.abi}, {imm_str}"
        if variant in (VaVariant.VF, VaVariant.VFM):
            # Some FP macc ops use fs1 in the middle slot.
            if name in (
                _N.VFMADD, _N.VFNMADD, _N.VFMACC, _N.VFNMACC, _N.VFNMSUB,
                _N.VFWNMSAC, _N.VFWMACC, _N.VFMSUB, _N.VFMSAC, _N.VFNMSAC,
                _N.VFWNMACC, _N.VFWMSAC,
            ):
                return f"{mnemonic}{self.vd.abi}, {self.fs1.abi}, {self.vs2.abi}"
            return f"{mnemonic}{self.vd.abi}, {self.vs2.abi}, {self.fs1.abi}"
        if variant in (VaVariant.WX, VaVariant.VX, VaVariant.VXM):
            if name in (
                _N.VMADD, _N.VNMSUB, _N.VMACC, _N.VNMSAC,
                _N.VWMACCSU, _N.VWMACCU, _N.VWMACCUS, _N.VWMACC,
            ):
                return f"{mnemonic}{self.vd.abi}, {self.rs1.abi}, {self.vs2.abi}"
            return f"{mnemonic}{self.vd.abi}, {self.vs2.abi}, {self.rs1.abi}"

        # Fallback.
        return f"{mnemonic}{self.vd.abi}, {self.vs2.abi}, {self.vs1.abi}"

    def _vec_vm_str(self) -> str:
        if self.vm:
            return ""
        if self.instr_name in (_N.VMERGE, _N.VFMERGE, _N.VADC, _N.VSBC, _N.VMADC, _N.VMSBC):
            return ", v0"
        return ", v0.t"

    # ------------------------------------------------------------------
    # Binary encoding deferred (rely on GCC assembler)
    # ------------------------------------------------------------------

    def convert2bin(self, prefix: str = "") -> str:
        raise NotImplementedError(
            "Vector instruction binary encoding not implemented in Phase 1. "
            "Use convert2asm + GCC/spike --isa=rv64gcv."
        )

    def get_opcode(self) -> int:
        # OP-V opcode (0x57) for arithmetic; LOAD-FP / STORE-FP for loads/stores.
        if self.category == _CAT.LOAD:
            return 0b0000111
        if self.category == _CAT.STORE:
            return 0b0100111
        if self.category == _CAT.AMO:
            return 0b0101111
        return 0b1010111

    def get_func3(self) -> int:
        raise NotImplementedError("Vector get_func3 deferred (binary encoding)")


# ---------------------------------------------------------------------------
# Registration helper — mirrors SV DEFINE_VA_INSTR
# ---------------------------------------------------------------------------


def define_vector_instr(
    instr_name: RiscvInstrName,
    fmt: RiscvInstrFormat,
    category: RiscvInstrCategory,
    group: RiscvInstrGroup = RiscvInstrGroup.RVV,
    allowed_va_variants: Sequence[VaVariant] = (),
    sub_extension: str = "",
) -> type:
    """Register a vector instruction subclass.

    Equivalent of SV's ``DEFINE_VA_INSTR`` + ``DEFINE_INSTR`` (for VSET).
    ``allowed_va_variants`` is stored as a class attr so the instance can
    pick one at randomization time.
    """
    _assert_not_registered(instr_name)

    class_name = f"riscv_{instr_name.name}_instr"
    cls = type(
        class_name,
        (VectorInstr,),
        {
            "instr_name": instr_name,
            "format": fmt,
            "category": category,
            "group": group,
            "imm_type": ImmType.IMM,
            "allowed_va_variants": tuple(allowed_va_variants),
            "sub_extension": sub_extension,
        },
    )
    INSTR_REGISTRY[instr_name] = cls
    return cls
