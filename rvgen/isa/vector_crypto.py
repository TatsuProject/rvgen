"""Vector v1.0 ratified extensions: Zvbb / Zvbc / Zvkn / Zvfh.

riscv-dv's SV reference does not include any of these — rvgen-first.
All four are in current GCC binutils + spike-vector mainline:

- Zvbb (basic bitmanip): vandn / vbrev / vbrev8 / vrev8 / vclz / vctz /
  vcpop / vrol / vror / vwsll. Ratified 2022.
- Zvbc (carry-less multiply): vclmul / vclmulh.
- Zvkn (NIST crypto suite): vaesef / vaesem / vaesdf / vaesdm / vaeskf1 /
  vaeskf2 / vaesz + vsha2ms / vsha2cl / vsha2ch (Zvknha / Zvknhb).
- Zvfh (half-precision): no new mnemonics — the existing VF*/VFW*/VFN*
  family runs at SEW=16 once Zvfh is in the target's ``-march`` string.

Why a dedicated subclass? Each enum name already encodes its operand
shape (``VANDN_VV`` / ``VANDN_VX`` / ``VROR_VI``). The base
:class:`VectorInstr` would then double-suffix the mnemonic
(``vandn.vv.vv``), and AES ops can't be masked, etc. We carry that
metadata as class attrs so the randomizer + asm-emit DTRT.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
    MAX_INSTR_STR_LEN,
)
from rvgen.isa.factory import INSTR_REGISTRY, _assert_not_registered
from rvgen.isa.utils import format_string
from rvgen.isa.vector import VectorInstr


# ---------------------------------------------------------------------------
# Operand-shape tags (read by VectorCryptoInstr.convert2asm)
# ---------------------------------------------------------------------------


class _Shape:
    """Operand layout tag for the asm emitter.

    - ``VV`` → ``<mnem>.vv vd, vs2, vs1[, mask]``
    - ``VX`` → ``<mnem>.vx vd, vs2, rs1[, mask]``
    - ``VI`` → ``<mnem>.vi vd, vs2, imm[, mask]``
    - ``V``  → ``<mnem>.v vd, vs2[, mask]`` (single src, no scalar/imm)
    - ``VV2`` → ``<mnem>.vv vd, vs2[, mask]`` (two-operand .vv — AES vector form)
    - ``VS2`` → ``<mnem>.vs vd, vs2[, mask]`` (two-operand .vs — AES scalar form)
    """
    VV = "vv"
    VX = "vx"
    VI = "vi"
    V = "v"
    VV2 = "vv2"
    VS2 = "vs2"


_AES_NEVER_MASKED = frozenset({
    N.VAESEF_VV, N.VAESEF_VS, N.VAESEM_VV, N.VAESEM_VS,
    N.VAESDF_VV, N.VAESDF_VS, N.VAESDM_VV, N.VAESDM_VS,
    N.VAESKF1_VI, N.VAESKF2_VI, N.VAESZ_VS,
    # SHA-2 ops are also unconditionally executed (per Zvknha/Zvknhb spec):
    N.VSHA2MS_VV, N.VSHA2CL_VV, N.VSHA2CH_VV,
})


class VectorCryptoInstr(VectorInstr):
    """Base for Zvbb / Zvbc / Zvkn ops.

    Each subclass sets ``shape`` to a string from :class:`_Shape`. The
    randomizer leaves ``vm = 1`` for AES/SHA-2 (unmaskable per spec) and
    routes the operand layout via ``shape`` rather than ``va_variant``.
    """

    shape: str = _Shape.VV

    def set_rand_mode(self) -> None:
        # We don't use va_variant — read the operand layout from `shape`.
        # Skip the parent's VA-variant logic so has_va_variant stays False.
        if not hasattr(self, "has_vs1"):
            return
        self.has_va_variant = False
        self.has_rs1 = self.shape == _Shape.VX
        self.has_rs2 = False
        self.has_rd = False
        self.has_fs1 = False
        self.has_fs2 = False
        self.has_fs3 = False
        self.has_fd = False
        self.has_imm = self.shape == _Shape.VI

    def set_imm_len(self) -> None:
        if self.shape == _Shape.VI:
            self.imm_len = 5
            self.imm_mask = 0
        else:
            self.imm_len = 0
            self.imm_mask = 0xFFFFFFFF

    def update_imm_str(self) -> None:
        # Zvbb shifts (vror.vi, vwsll.vi) and Zvkn key-schedule
        # (vaeskf*.vi) all want UNSIGNED 5-bit. Override the SV-style
        # signed-render in VectorInstr.
        if self.imm_len:
            self.imm_str = str(self.imm & ((1 << self.imm_len) - 1))
        else:
            self.imm_str = ""

    def randomize_vector_operands(self, rng, vector_cfg) -> None:
        super().randomize_vector_operands(rng, vector_cfg)
        # Force unmasked for AES/SHA-2 ops.
        if self.instr_name in _AES_NEVER_MASKED:
            self.vm = 1

    def get_instr_name(self) -> str:
        """Return the mnemonic without the implicit variant suffix.

        Enum names encode the variant: ``VANDN_VV`` → "VANDN.VV". We strip
        the trailing ``.<shape>`` so :meth:`convert2asm` re-attaches the
        right one (and only one).
        """
        # Use the SV-style transform first (super's parent).
        name = self.instr_name.name.replace("_", ".").lower()
        for tail in (".vv", ".vx", ".vi", ".vs", ".v"):
            if name.endswith(tail):
                return name[: -len(tail)]
        return name

    def convert2asm(self, prefix: str = "") -> str:
        # Asm suffix to attach (vv / vx / vi / v / vs).
        suffix_map = {
            _Shape.VV: "vv", _Shape.VX: "vx", _Shape.VI: "vi",
            _Shape.V: "v", _Shape.VV2: "vv", _Shape.VS2: "vs",
        }
        suffix = suffix_map[self.shape]
        mnem = format_string(f"{self.get_instr_name()}.{suffix}", MAX_INSTR_STR_LEN)
        body: str
        if self.shape in (_Shape.V, _Shape.VV2, _Shape.VS2):
            body = f"{mnem}{self.vd.abi}, {self.vs2.abi}"
        elif self.shape == _Shape.VV:
            body = f"{mnem}{self.vd.abi}, {self.vs2.abi}, {self.vs1.abi}"
        elif self.shape == _Shape.VX:
            body = f"{mnem}{self.vd.abi}, {self.vs2.abi}, {self.rs1.abi}"
        elif self.shape == _Shape.VI:
            imm = self.imm_str if self.imm_str else str(self.imm)
            body = f"{mnem}{self.vd.abi}, {self.vs2.abi}, {imm}"
        else:
            raise ValueError(f"Unknown shape {self.shape!r}")
        body += self._vec_vm_str()
        if self.comment:
            body = f"{body} #{self.comment}"
        return body.lower()


def _define_crypto(
    instr_name: N,
    fmt: F,
    category: C,
    shape: str,
    sub_extension: str,
) -> type:
    _assert_not_registered(instr_name)
    cls = type(
        f"riscv_{instr_name.name}_instr",
        (VectorCryptoInstr,),
        {
            "instr_name": instr_name,
            "format": fmt,
            "category": category,
            "group": G.RVV,
            "shape": shape,
            "sub_extension": sub_extension,
            "allowed_va_variants": (),
        },
    )
    INSTR_REGISTRY[instr_name] = cls
    return cls


# ---------------------------------------------------------------------------
# Zvbb — Vector Basic Bitmanip
# ---------------------------------------------------------------------------

_define_crypto(N.VANDN_VV, F.VA_FORMAT, C.LOGICAL, _Shape.VV, "zvbb")
_define_crypto(N.VANDN_VX, F.VA_FORMAT, C.LOGICAL, _Shape.VX, "zvbb")

_define_crypto(N.VBREV_V, F.VS2_FORMAT, C.LOGICAL, _Shape.V, "zvbb")
_define_crypto(N.VBREV8_V, F.VS2_FORMAT, C.LOGICAL, _Shape.V, "zvbb")
_define_crypto(N.VREV8_V, F.VS2_FORMAT, C.LOGICAL, _Shape.V, "zvbb")
_define_crypto(N.VCLZ_V, F.VS2_FORMAT, C.LOGICAL, _Shape.V, "zvbb")
_define_crypto(N.VCTZ_V, F.VS2_FORMAT, C.LOGICAL, _Shape.V, "zvbb")
_define_crypto(N.VCPOP_V, F.VS2_FORMAT, C.LOGICAL, _Shape.V, "zvbb")

_define_crypto(N.VROL_VV, F.VA_FORMAT, C.SHIFT, _Shape.VV, "zvbb")
_define_crypto(N.VROL_VX, F.VA_FORMAT, C.SHIFT, _Shape.VX, "zvbb")
_define_crypto(N.VROR_VV, F.VA_FORMAT, C.SHIFT, _Shape.VV, "zvbb")
_define_crypto(N.VROR_VX, F.VA_FORMAT, C.SHIFT, _Shape.VX, "zvbb")
_define_crypto(N.VROR_VI, F.VA_FORMAT, C.SHIFT, _Shape.VI, "zvbb")

# Widening shift left logical: vd is 2*lmul of vs2.
_define_crypto(N.VWSLL_VV, F.VA_FORMAT, C.SHIFT, _Shape.VV, "zvbb")
_define_crypto(N.VWSLL_VX, F.VA_FORMAT, C.SHIFT, _Shape.VX, "zvbb")
_define_crypto(N.VWSLL_VI, F.VA_FORMAT, C.SHIFT, _Shape.VI, "zvbb")


# ---------------------------------------------------------------------------
# Zvbc — Vector Carry-Less Multiply
# ---------------------------------------------------------------------------

_define_crypto(N.VCLMUL_VV, F.VA_FORMAT, C.ARITHMETIC, _Shape.VV, "zvbc")
_define_crypto(N.VCLMUL_VX, F.VA_FORMAT, C.ARITHMETIC, _Shape.VX, "zvbc")
_define_crypto(N.VCLMULH_VV, F.VA_FORMAT, C.ARITHMETIC, _Shape.VV, "zvbc")
_define_crypto(N.VCLMULH_VX, F.VA_FORMAT, C.ARITHMETIC, _Shape.VX, "zvbc")


# ---------------------------------------------------------------------------
# Zvkn — Vector NIST AES + SHA-2
# ---------------------------------------------------------------------------

# AES round ops: vd, vs2 — single source per encoding. .vv / .vs distinguish
# whether vs2 supplies all elements or just the scalar lane (asm suffix only).
_define_crypto(N.VAESEF_VV, F.VS2_FORMAT, C.LOGICAL, _Shape.VV2, "zvkn")
_define_crypto(N.VAESEF_VS, F.VS2_FORMAT, C.LOGICAL, _Shape.VS2, "zvkn")
_define_crypto(N.VAESEM_VV, F.VS2_FORMAT, C.LOGICAL, _Shape.VV2, "zvkn")
_define_crypto(N.VAESEM_VS, F.VS2_FORMAT, C.LOGICAL, _Shape.VS2, "zvkn")
_define_crypto(N.VAESDF_VV, F.VS2_FORMAT, C.LOGICAL, _Shape.VV2, "zvkn")
_define_crypto(N.VAESDF_VS, F.VS2_FORMAT, C.LOGICAL, _Shape.VS2, "zvkn")
_define_crypto(N.VAESDM_VV, F.VS2_FORMAT, C.LOGICAL, _Shape.VV2, "zvkn")
_define_crypto(N.VAESDM_VS, F.VS2_FORMAT, C.LOGICAL, _Shape.VS2, "zvkn")
_define_crypto(N.VAESZ_VS, F.VS2_FORMAT, C.LOGICAL, _Shape.VS2, "zvkn")

# AES key-schedule: vd, vs2, imm[4:0]
_define_crypto(N.VAESKF1_VI, F.VA_FORMAT, C.LOGICAL, _Shape.VI, "zvkn")
_define_crypto(N.VAESKF2_VI, F.VA_FORMAT, C.LOGICAL, _Shape.VI, "zvkn")

# SHA-2: vd, vs2, vs1
_define_crypto(N.VSHA2MS_VV, F.VA_FORMAT, C.LOGICAL, _Shape.VV, "zvkn")
_define_crypto(N.VSHA2CL_VV, F.VA_FORMAT, C.LOGICAL, _Shape.VV, "zvkn")
_define_crypto(N.VSHA2CH_VV, F.VA_FORMAT, C.LOGICAL, _Shape.VV, "zvkn")
