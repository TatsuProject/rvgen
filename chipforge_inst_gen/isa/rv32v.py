"""RVV registrations — port of ``src/isa/rv32v_instr.sv``.

All ~130 vector opcodes registered here. The filtering layer in
``chipforge_inst_gen/isa/filtering.py`` is responsible for dropping the
widening / narrowing / quad-widening / FP variants when the relevant
``vector_cfg`` knob is off.
"""

from __future__ import annotations

from chipforge_inst_gen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
    VaVariant as V,
)
from chipforge_inst_gen.isa.factory import define_instr, INSTR_REGISTRY, _assert_not_registered
from chipforge_inst_gen.isa.vector import VectorInstr, define_vector_instr


# ---------------------------------------------------------------------------
# VSET — vsetvl / vsetvli. Registered via plain define_instr with a CSR
# category so they don't pollute the random arithmetic stream. The asm
# program gen emits them directly as text when initializing the vector
# engine.
# ---------------------------------------------------------------------------

# These two get a thin VectorInstr subclass without allowed_va_variants.
for _name in (N.VSETVLI, N.VSETVL):
    _assert_not_registered(_name)
    _cls = type(
        f"riscv_{_name.name}_instr",
        (VectorInstr,),
        {
            "instr_name": _name,
            "format": F.VSET_FORMAT,
            "category": C.CSR,
            "group": G.RVV,
            "allowed_va_variants": (),
            "sub_extension": "",
        },
    )
    INSTR_REGISTRY[_name] = _cls


# ---------------------------------------------------------------------------
# Vector integer arithmetic
# ---------------------------------------------------------------------------

define_vector_instr(N.VADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VRSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VX, V.VI))
define_vector_instr(N.VWADDU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.WV, V.WX))
define_vector_instr(N.VWSUBU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.WV, V.WX))
define_vector_instr(N.VWADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.WV, V.WX))
define_vector_instr(N.VWSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.WV, V.WX))
define_vector_instr(N.VADC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VVM, V.VXM, V.VIM))
define_vector_instr(N.VMADC, F.VA_FORMAT, C.ARITHMETIC,
                     allowed_va_variants=(V.VVM, V.VXM, V.VIM, V.VV, V.VX, V.VI))
define_vector_instr(N.VSBC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VVM, V.VXM))
define_vector_instr(N.VMSBC, F.VA_FORMAT, C.ARITHMETIC,
                     allowed_va_variants=(V.VVM, V.VXM, V.VV, V.VX))
define_vector_instr(N.VAND, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VOR, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VXOR, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSLL, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSRL, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSRA, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VNSRL, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.WV, V.WX, V.WI))
define_vector_instr(N.VNSRA, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.WV, V.WX, V.WI))

define_vector_instr(N.VMSEQ, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VMSNE, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VMSLTU, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMSLT, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMSLEU, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VMSLE, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VMSGTU, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VX, V.VI))
define_vector_instr(N.VMSGT, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VX, V.VI))

define_vector_instr(N.VMINU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMIN, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMAXU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMAX, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))

define_vector_instr(N.VMUL, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMULH, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMULHU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMULHSU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))

define_vector_instr(N.VDIVU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VDIV, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VREMU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VREM, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))

define_vector_instr(N.VWMUL, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VWMULU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VWMULSU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))

define_vector_instr(N.VMACC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VNMSAC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VMADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VNMSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VWMACCU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VWMACC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VWMACCSU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VWMACCUS, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VX,))

define_vector_instr(N.VMERGE, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VVM, V.VXM, V.VIM))
define_vector_instr(N.VMV, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))

# Vector fixed-point arithmetic
define_vector_instr(N.VSADDU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSSUBU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VSSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VAADDU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VAADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VASUBU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VASUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX))
define_vector_instr(N.VSSRL, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VSSRA, F.VA_FORMAT, C.SHIFT, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VNCLIPU, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.WV, V.WX, V.WI))
define_vector_instr(N.VNCLIP, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.WV, V.WX, V.WI))


# ---------------------------------------------------------------------------
# Vector floating-point
# ---------------------------------------------------------------------------

define_vector_instr(N.VFADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFRSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VF,))
define_vector_instr(N.VFMUL, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFDIV, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFRDIV, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VF,))
define_vector_instr(N.VFWMUL, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFMACC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFNMACC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFMSAC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFNMSAC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFMADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFNMADD, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFMSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFNMSUB, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFWMACC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFWNMACC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFWMSAC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFWNMSAC, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFSQRT_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFMIN, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFMAX, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFSGNJ, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFSGNJN, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VFSGNJX, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VMFEQ, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VMFNE, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VMFLT, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VMFLE, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VV, V.VF))
define_vector_instr(N.VMFGT, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VF,))
define_vector_instr(N.VMFGE, F.VA_FORMAT, C.COMPARE, allowed_va_variants=(V.VF,))
define_vector_instr(N.VFCLASS_V, F.VS2_FORMAT, C.COMPARE)
define_vector_instr(N.VFMERGE, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VFM,))
define_vector_instr(N.VFMV, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VF,))

# FP conversions
define_vector_instr(N.VFCVT_XU_F_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFCVT_X_F_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFCVT_F_XU_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFCVT_F_X_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWCVT_XU_F_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWCVT_X_F_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWCVT_F_XU_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWCVT_F_X_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWCVT_F_F_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFNCVT_XU_F_W, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFNCVT_X_F_W, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFNCVT_F_XU_W, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFNCVT_F_X_W, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFNCVT_F_F_W, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFNCVT_ROD_F_F_W, F.VS2_FORMAT, C.ARITHMETIC)


# ---------------------------------------------------------------------------
# Vector reduction
# ---------------------------------------------------------------------------

define_vector_instr(N.VREDSUM_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDMAXU_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDMAX_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDMINU_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDMIN_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDAND_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDOR_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VREDXOR_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VWREDSUMU_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VWREDSUM_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFREDOSUM_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFREDSUM_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFREDMAX_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWREDOSUM_VS, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFWREDSUM_VS, F.VA_FORMAT, C.ARITHMETIC)


# ---------------------------------------------------------------------------
# Vector mask / permutation
# ---------------------------------------------------------------------------

define_vector_instr(N.VMAND_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMNAND_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMANDNOT_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMXOR_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMOR_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMNOR_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMORNOT_MM, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMXNOR_MM, F.VA_FORMAT, C.ARITHMETIC)

define_vector_instr(N.VPOPC_M, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFIRST_M, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMSBF_M, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMSIF_M, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMSOF_M, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VIOTA_M, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VID_V, F.VS2_FORMAT, C.ARITHMETIC)

define_vector_instr(N.VMV_X_S, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMV_S_X, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFMV_F_S, F.VA_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VFMV_S_F, F.VA_FORMAT, C.ARITHMETIC)

define_vector_instr(N.VSLIDEUP, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VI, V.VX))
define_vector_instr(N.VSLIDEDOWN, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VI, V.VX))
define_vector_instr(N.VSLIDE1UP, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VX,))
define_vector_instr(N.VSLIDE1DOWN, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VX,))
define_vector_instr(N.VRGATHER, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VV, V.VX, V.VI))
define_vector_instr(N.VCOMPRESS, F.VA_FORMAT, C.ARITHMETIC, allowed_va_variants=(V.VM,))

define_vector_instr(N.VMV1R_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMV2R_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMV4R_V, F.VS2_FORMAT, C.ARITHMETIC)
define_vector_instr(N.VMV8R_V, F.VS2_FORMAT, C.ARITHMETIC)


# ---------------------------------------------------------------------------
# Vector loads / stores (Section 7)
# ---------------------------------------------------------------------------

define_vector_instr(N.VLE_V, F.VL_FORMAT, C.LOAD)
define_vector_instr(N.VSE_V, F.VS_FORMAT, C.STORE)
define_vector_instr(N.VLSE_V, F.VLS_FORMAT, C.LOAD)
define_vector_instr(N.VSSE_V, F.VSS_FORMAT, C.STORE)
define_vector_instr(N.VLXEI_V, F.VLX_FORMAT, C.LOAD)
define_vector_instr(N.VSXEI_V, F.VSX_FORMAT, C.STORE)
define_vector_instr(N.VSUXEI_V, F.VSX_FORMAT, C.STORE)
define_vector_instr(N.VLEFF_V, F.VL_FORMAT, C.LOAD)

# Segmented load/store (Zvlsseg)
define_vector_instr(N.VLSEGE_V, F.VL_FORMAT, C.LOAD, sub_extension="zvlsseg")
define_vector_instr(N.VSSEGE_V, F.VS_FORMAT, C.STORE, sub_extension="zvlsseg")
define_vector_instr(N.VLSEGEFF_V, F.VL_FORMAT, C.LOAD, sub_extension="zvlsseg")
define_vector_instr(N.VLSSEGE_V, F.VLS_FORMAT, C.LOAD, sub_extension="zvlsseg")
define_vector_instr(N.VSSSEGE_V, F.VSS_FORMAT, C.STORE, sub_extension="zvlsseg")
define_vector_instr(N.VLXSEGEI_V, F.VLX_FORMAT, C.LOAD, sub_extension="zvlsseg")
define_vector_instr(N.VSXSEGEI_V, F.VSX_FORMAT, C.STORE, sub_extension="zvlsseg")
define_vector_instr(N.VSUXSEGEI_V, F.VSX_FORMAT, C.STORE, sub_extension="zvlsseg")


# ---------------------------------------------------------------------------
# Vector AMO (Zvamo)
# ---------------------------------------------------------------------------

define_vector_instr(N.VAMOSWAPE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOADDE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOXORE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOANDE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOORE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOMINE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOMAXE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOMINUE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
define_vector_instr(N.VAMOMAXUE_V, F.VAMO_FORMAT, C.AMO, sub_extension="zvamo")
