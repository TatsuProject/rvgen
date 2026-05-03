"""Per-config instruction filtering and random picking.

Port of ``riscv_instr::create_instr_list`` + ``build_basic_instruction_list``
+ ``get_rand_instr`` from ``src/isa/riscv_instr.sv``. These are static methods
in SV — here they're module-level functions that operate on
:class:`AvailableInstrs` (the filtered catalog) instead of SV's static class
state.

Key differences from SV:

- We don't use UVM's constraint solver. ``get_rand_instr`` does rejection
  sampling (``random.Random.choice`` + filter) until a valid candidate is
  found, or raises a clear error if the filter rules out every registered
  instruction.
- ``create_instr_list(cfg)`` is deterministic wrt ``cfg`` — call it once per
  generator run and cache the result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from rvgen.config import Config
from rvgen.isa.base import Instr, copy_instr
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import INSTR_REGISTRY


# Groups that carry the "compressed" family — filtered out by
# ``cfg.disable_compressed_instr``.
_COMPRESSED_GROUPS = frozenset({
    RiscvInstrGroup.RV32C,
    RiscvInstrGroup.RV64C,
    RiscvInstrGroup.RV32FC,
    RiscvInstrGroup.RV32DC,
    RiscvInstrGroup.RV128C,
})

_FP_GROUPS = frozenset({
    RiscvInstrGroup.RV32F,
    RiscvInstrGroup.RV64F,
    RiscvInstrGroup.RV32D,
    RiscvInstrGroup.RV64D,
    RiscvInstrGroup.RV32FC,
    RiscvInstrGroup.RV32DC,
})

_VECTOR_GROUPS = frozenset({RiscvInstrGroup.RVV})

# Any vector-capable group (full RVV or an embedded Zve* subset).
_ANY_VECTOR_CAPABLE_GROUPS = frozenset({
    RiscvInstrGroup.RVV,
    RiscvInstrGroup.ZVE32X,
    RiscvInstrGroup.ZVE32F,
    RiscvInstrGroup.ZVE64X,
    RiscvInstrGroup.ZVE64F,
    RiscvInstrGroup.ZVE64D,
})

# Vector-capable groups that support FP-vector ops (vec_fp=True can emit them).
_FP_VECTOR_CAPABLE_GROUPS = frozenset({
    RiscvInstrGroup.RVV,
    RiscvInstrGroup.ZVE32F,
    RiscvInstrGroup.ZVE64F,
    RiscvInstrGroup.ZVE64D,
})

# Vector-capable groups that support SEW=64 (and EEW=64 for loads/stores).
_SEW64_VECTOR_CAPABLE_GROUPS = frozenset({
    RiscvInstrGroup.RVV,
    RiscvInstrGroup.ZVE64X,
    RiscvInstrGroup.ZVE64F,
    RiscvInstrGroup.ZVE64D,
})

# Vector-capable groups that support FP64 vector (Zve64d + full RVV).
_FP64_VECTOR_CAPABLE_GROUPS = frozenset({
    RiscvInstrGroup.RVV,
    RiscvInstrGroup.ZVE64D,
})


def target_has_any_vector(target) -> bool:
    """Return True if the target's supported_isa advertises any vector profile."""
    return any(g in target.supported_isa for g in _ANY_VECTOR_CAPABLE_GROUPS)


def target_supports_fp_vector(target) -> bool:
    """Return True if the target's supported_isa covers FP vector ops."""
    return any(g in target.supported_isa for g in _FP_VECTOR_CAPABLE_GROUPS)


def target_supports_sew64_vector(target) -> bool:
    """Return True if the target's supported_isa covers SEW=64 vector ops."""
    return any(g in target.supported_isa for g in _SEW64_VECTOR_CAPABLE_GROUPS)


def target_supports_fp64_vector(target) -> bool:
    return any(g in target.supported_isa for g in _FP64_VECTOR_CAPABLE_GROUPS)


# ---------------------------------------------------------------------------
# Vector-specific filters — gate widening / narrowing / FP / Zvlsseg / Zvamo.
# Applied when group == RVV.
# ---------------------------------------------------------------------------

#: Ops that SV's riscv_vector_instr::is_supported explicitly drops. Keeping
#: the same deny-list makes our output behave like SV's random stream.
_VECTOR_ALWAYS_DROP = frozenset({
    RiscvInstrName.VWMACCSU,
    RiscvInstrName.VMERGE,
    RiscvInstrName.VFMERGE,
    RiscvInstrName.VMADC,
    RiscvInstrName.VMSBC,
})

#: Names with "VF"/"VMF" prefix — suppressed when vec_fp is off.
def _is_fp_vector_name(n: RiscvInstrName) -> bool:
    s = n.name
    return s.startswith("VF") or s.startswith("VMF")

#: Names with "VW"/"VFW" prefix — widening; gated by vec_narrowing_widening.
def _is_widening_vector_name(n: RiscvInstrName) -> bool:
    s = n.name
    return s.startswith("VW") or s.startswith("VFW")

#: Names with "VN"/"VFN" prefix — narrowing; gated by vec_narrowing_widening.
def _is_narrowing_vector_name(n: RiscvInstrName) -> bool:
    s = n.name
    return s.startswith("VN") or s.startswith("VFN")


@dataclass(frozen=True, slots=True)
class AvailableInstrs:
    """Instructions that survive ``create_instr_list(cfg)``'s filters."""

    names: tuple[RiscvInstrName, ...]
    by_group: dict[RiscvInstrGroup, tuple[RiscvInstrName, ...]]
    by_category: dict[RiscvInstrCategory, tuple[RiscvInstrName, ...]]
    basic_instr: tuple[RiscvInstrName, ...]


def create_instr_list(cfg: Config) -> AvailableInstrs:
    """Build the filtered instruction catalog for ``cfg``.

    Mirrors SV ``create_instr_list`` + ``build_basic_instruction_list``
    (riscv_instr.sv:96 and :152).
    """
    target = cfg.target
    xlen = target.xlen if target else 32
    supported_isa = set(target.supported_isa) if target else set()
    unsupported = set(target.unsupported_instr) if target else set()
    reserved = set(cfg.reserved_regs)

    names: list[RiscvInstrName] = []
    by_group: dict[RiscvInstrGroup, list[RiscvInstrName]] = {}
    by_category: dict[RiscvInstrCategory, list[RiscvInstrName]] = {}

    for name, cls in INSTR_REGISTRY.items():
        if name in unsupported:
            continue
        # C_JAL is RV32C only per SV.
        if xlen != 32 and name == RiscvInstrName.C_JAL:
            continue
        # ZIP/UNZIP are RV32 Zbkb only per the ratified K-ext spec.
        if xlen != 32 and name in (RiscvInstrName.ZIP, RiscvInstrName.UNZIP):
            continue
        # SHA-512 split H/L pair and SUM R-form are RV32 Zknh only; on RV64
        # the single-instruction SHA512SIG0/SIG1/SUM0/SUM1 are used instead.
        # Also, AES32* are RV32 Zkne/Zknd only.
        if xlen != 32 and name in (
            RiscvInstrName.SHA512SIG0L, RiscvInstrName.SHA512SIG0H,
            RiscvInstrName.SHA512SIG1L, RiscvInstrName.SHA512SIG1H,
            RiscvInstrName.SHA512SUM0R, RiscvInstrName.SHA512SUM1R,
            RiscvInstrName.AES32ESI, RiscvInstrName.AES32ESMI,
            RiscvInstrName.AES32DSI, RiscvInstrName.AES32DSMI,
        ):
            continue
        # AES64* and RV64 SHA-512 single-instruction are RV64-only.
        if xlen == 32 and name in (
            RiscvInstrName.AES64ES, RiscvInstrName.AES64ESM,
            RiscvInstrName.AES64DS, RiscvInstrName.AES64DSM,
            RiscvInstrName.AES64KS1I, RiscvInstrName.AES64KS2,
            RiscvInstrName.AES64IM,
            RiscvInstrName.SHA512SIG0, RiscvInstrName.SHA512SIG1,
            RiscvInstrName.SHA512SUM0, RiscvInstrName.SHA512SUM1,
        ):
            continue
        # C_ADDI16SP needs SP usable.
        if RiscvReg.SP in reserved and name == RiscvInstrName.C_ADDI16SP:
            continue
        if not cfg.enable_sfence and name == RiscvInstrName.SFENCE_VMA:
            continue
        if cfg.no_fence and name in (
            RiscvInstrName.FENCE, RiscvInstrName.FENCE_I, RiscvInstrName.SFENCE_VMA,
        ):
            continue

        group = cls.group
        # Vector-group instrs (group == RVV) are allowed when the target
        # advertises *any* vector profile — full RVV or one of the Zve*
        # subsets. The per-instr FP/SEW/etc. gates below do the further
        # narrowing for embedded-vector targets.
        if group in _VECTOR_GROUPS:
            if target and not target_has_any_vector(target):
                continue
        elif group not in supported_isa:
            continue
        if cfg.disable_compressed_instr and group in _COMPRESSED_GROUPS:
            continue
        if not cfg.enable_floating_point and group in _FP_GROUPS:
            continue
        if not cfg.enable_vector_extension and group in _VECTOR_GROUPS:
            continue
        if cfg.vector_instr_only and group not in _VECTOR_GROUPS:
            continue
        # Vector-specific gating (SV: riscv_vector_instr::is_supported).
        if group in _VECTOR_GROUPS and cfg.vector_cfg is not None:
            vcfg = cfg.vector_cfg
            if name in _VECTOR_ALWAYS_DROP:
                continue
            # FP-vector ops: need vec_fp=True AND target must advertise an
            # FP-vector-capable profile (full RVV / Zve32f / Zve64f / Zve64d).
            if _is_fp_vector_name(name):
                if not vcfg.vec_fp:
                    continue
                if target is not None and not target_supports_fp_vector(target):
                    continue
            if not vcfg.vec_narrowing_widening:
                if _is_widening_vector_name(name) or _is_narrowing_vector_name(name):
                    continue
            if not vcfg.enable_zvlsseg and getattr(cls, "sub_extension", "") == "zvlsseg":
                continue
            # Zvamo: ratified pre-1.0 only; current spike-vector / GCC reject
            # ``vamoaddei.v`` etc. since RVV 1.0 ratified removed vector-AMO
            # entirely. Gate on the per-target ``vector_amo_supported`` flag.
            if getattr(cls, "sub_extension", "") == "zvamo":
                if target is None or not getattr(target, "vector_amo_supported", False):
                    continue
            # Zvbb / Zvbc / Zvkn / Zvfh — ratified RVV-1.0 follow-on extensions.
            # Each is gated by an explicit per-target ``enable_zv*`` knob so
            # mainline rv64gcv (which doesn't advertise them in its -march by
            # default) doesn't pull in mnemonics GCC will reject.
            sub_ext = getattr(cls, "sub_extension", "")
            if sub_ext in ("zvbb", "zvbc", "zvkn"):
                target_flag = f"enable_{sub_ext}"
                if target is None or not getattr(target, target_flag, False):
                    continue
            # VSETVLI/VSETVL are emitted by the init section, not the random
            # stream (category=CSR, but keep an explicit drop in case a future
            # config ever puts CSR ops into the vector stream).
            if name in (RiscvInstrName.VSETVLI, RiscvInstrName.VSETVL):
                continue
            # ADC/SBC need explicit mask setup in the stream — Phase 1 skips.
            if name in (RiscvInstrName.VADC, RiscvInstrName.VSBC):
                continue

        names.append(name)
        by_group.setdefault(group, []).append(name)
        by_category.setdefault(cls.category, []).append(name)

    basic_instr = list(by_category.get(RiscvInstrCategory.SHIFT, ()))
    basic_instr += list(by_category.get(RiscvInstrCategory.ARITHMETIC, ()))
    basic_instr += list(by_category.get(RiscvInstrCategory.LOGICAL, ()))
    basic_instr += list(by_category.get(RiscvInstrCategory.COMPARE, ()))

    if not cfg.no_ebreak:
        basic_instr.append(RiscvInstrName.EBREAK)
        if RiscvInstrGroup.RV32C in supported_isa and not cfg.disable_compressed_instr:
            basic_instr.append(RiscvInstrName.C_EBREAK)
    if not cfg.no_ecall:
        basic_instr.append(RiscvInstrName.ECALL)
    if not cfg.no_dret:
        basic_instr.append(RiscvInstrName.DRET)
    if not cfg.no_fence:
        basic_instr += list(by_category.get(RiscvInstrCategory.SYNCH, ()))
    from rvgen.isa.enums import PrivilegedMode
    if not cfg.no_csr_instr and cfg.init_privileged_mode == PrivilegedMode.MACHINE_MODE:
        basic_instr += list(by_category.get(RiscvInstrCategory.CSR, ()))
    if not cfg.no_wfi:
        basic_instr.append(RiscvInstrName.WFI)

    return AvailableInstrs(
        names=tuple(names),
        by_group={g: tuple(ns) for g, ns in by_group.items()},
        by_category={c: tuple(ns) for c, ns in by_category.items()},
        basic_instr=tuple(basic_instr),
    )


# ---------------------------------------------------------------------------
# Random picking
# ---------------------------------------------------------------------------


def _resolve_candidate_set(
    avail: AvailableInstrs,
    *,
    include_instr: Sequence[RiscvInstrName] = (),
    exclude_instr: Sequence[RiscvInstrName] = (),
    include_category: Sequence[RiscvInstrCategory] = (),
    exclude_category: Sequence[RiscvInstrCategory] = (),
    include_group: Sequence[RiscvInstrGroup] = (),
    exclude_group: Sequence[RiscvInstrGroup] = (),
) -> list[RiscvInstrName]:
    """SV-compatible candidate filtering (riscv_instr.sv:182 ``get_rand_instr``)."""
    allowed: set[RiscvInstrName] = set()
    for cat in include_category:
        allowed.update(avail.by_category.get(cat, ()))
    for grp in include_group:
        allowed.update(avail.by_group.get(grp, ()))
    if include_instr:
        if not (include_category or include_group):
            allowed = set(include_instr)
        else:
            allowed &= set(include_instr)

    disallowed: set[RiscvInstrName] = set(exclude_instr)
    for cat in exclude_category:
        disallowed.update(avail.by_category.get(cat, ()))
    for grp in exclude_group:
        disallowed.update(avail.by_group.get(grp, ()))

    if not (include_instr or include_category or include_group):
        allowed = set(avail.names)

    result = [n for n in allowed if n not in disallowed]
    return result


def get_rand_instr(
    rng: random.Random,
    avail: AvailableInstrs,
    *,
    include_instr: Sequence[RiscvInstrName] = (),
    exclude_instr: Sequence[RiscvInstrName] = (),
    include_category: Sequence[RiscvInstrCategory] = (),
    exclude_category: Sequence[RiscvInstrCategory] = (),
    include_group: Sequence[RiscvInstrGroup] = (),
    exclude_group: Sequence[RiscvInstrGroup] = (),
    steerer=None,
) -> Instr:
    """Pick a random instruction subject to the filter, and return a fresh instance.

    Port of SV ``riscv_instr::get_rand_instr`` (riscv_instr.sv:182).

    When ``steerer`` is provided, the per-pick choice is biased by the
    steerer's per-mnemonic weight map (see
    :mod:`rvgen.coverage.steering`). Falls back to uniform choice when
    None — keeping the no-steering path a single ``rng.choice`` call.
    """
    candidates = _resolve_candidate_set(
        avail,
        include_instr=include_instr,
        exclude_instr=exclude_instr,
        include_category=include_category,
        exclude_category=exclude_category,
        include_group=include_group,
        exclude_group=exclude_group,
    )
    if not candidates:
        raise RuntimeError(
            "get_rand_instr: no candidates survived filtering. "
            f"include_instr={include_instr}, include_category={include_category}, "
            f"include_group={include_group}, exclude_instr={exclude_instr}, "
            f"exclude_category={exclude_category}, exclude_group={exclude_group}"
        )
    if steerer is None:
        name = rng.choice(candidates)
    else:
        from rvgen.coverage.steering import steer_choice
        name = steer_choice(rng, candidates, steerer)
    return copy_instr_from_registry(name)


def copy_instr_from_registry(name: RiscvInstrName) -> Instr:
    """Instantiate a fresh copy of the registered class for ``name``.

    SV's ``get_instr`` uses a template copy; we just construct anew.
    """
    try:
        cls = INSTR_REGISTRY[name]
    except KeyError as e:
        raise KeyError(
            f"Instruction {name.name} is not registered"
        ) from e
    return cls()


# ---------------------------------------------------------------------------
# Register operand randomization
# ---------------------------------------------------------------------------


_NON_CSR_REGS: tuple[RiscvReg, ...] = tuple(RiscvReg)


_COMPRESSED_3BIT_FORMATS = None  # Resolved lazily to avoid circular imports.


def _compressed_3bit_set() -> frozenset:
    global _COMPRESSED_3BIT_FORMATS
    if _COMPRESSED_3BIT_FORMATS is None:
        from rvgen.isa.enums import RiscvInstrFormat as F
        _COMPRESSED_3BIT_FORMATS = frozenset({
            F.CIW_FORMAT, F.CL_FORMAT, F.CS_FORMAT, F.CB_FORMAT, F.CA_FORMAT,
        })
    return _COMPRESSED_3BIT_FORMATS


_COMPRESSED_REGS = (
    RiscvReg.S0, RiscvReg.S1,
    RiscvReg.A0, RiscvReg.A1, RiscvReg.A2, RiscvReg.A3, RiscvReg.A4, RiscvReg.A5,
)


def randomize_gpr_operands(
    instr: Instr,
    rng: random.Random,
    cfg: Config,
    *,
    avail_regs: Sequence[RiscvReg] = (),
    reserved_rd: Sequence[RiscvReg] = (),
) -> None:
    """Assign rs1/rs2/rd honoring reserved-reg + compressed-format constraints.

    SV references:
      - ``riscv_instr_stream.sv:256`` — general ``randomize_gpr`` rules.
      - ``riscv_compressed_instr.sv:21`` (``rvc_csr_c``) — for CIW/CL/CS/CB/CA
        formats, rs1/rs2/rd must be in ``[S0:A5]`` (x8..x15).
      - ``riscv_compressed_instr.sv:38`` — C_JR/C_JALR: rs2 == 0, rs1 != 0.
      - ``riscv_compressed_instr.sv:68`` — no-HINT/illegal constraints for
        C_ADDI/C_ADDIW/C_LI/C_LUI/C_SLLI/C_LWSP/C_LDSP: rd != ZERO;
        C_JR: rs1 != ZERO; C_ADD/C_MV: rs2 != ZERO; C_LUI: rd != SP.
    """
    if cfg.target is None:
        raise ValueError("randomize_gpr_operands requires cfg.target to be set")

    is_compressed = getattr(instr, "is_compressed", False)
    compressed_3bit = is_compressed and instr.format in _compressed_3bit_set()

    if compressed_3bit:
        allowed = set(_COMPRESSED_REGS)
        # Caller's avail_regs is an additional restriction, not a replacement
        # (compressed 3-bit encoding physically cannot emit regs outside S0..A5).
        if avail_regs:
            allowed &= set(avail_regs)
    elif avail_regs:
        allowed = set(avail_regs)
    else:
        allowed = set(_NON_CSR_REGS)

    rd_forbidden = set(reserved_rd) | set(cfg.reserved_regs)

    # Compressed no-HINT guards (riscv_compressed_instr.sv:68).
    from rvgen.isa.enums import RiscvInstrName as N
    name = instr.instr_name
    if name in (
        N.C_ADDI, N.C_ADDIW, N.C_LI, N.C_LUI,
        N.C_SLLI, N.C_LWSP, N.C_LDSP, N.C_LQSP,
        N.C_MV, N.C_ADD,
    ):
        rd_forbidden.add(RiscvReg.ZERO)
    rs1_forbidden: set[RiscvReg] = set()
    rs2_forbidden: set[RiscvReg] = set()
    if name == N.C_JR:
        rs1_forbidden.add(RiscvReg.ZERO)
        rs2_forbidden.add(RiscvReg.ZERO)  # rs2 forced to ZERO via class state
    if name in (N.C_ADD, N.C_MV):
        rs2_forbidden.add(RiscvReg.ZERO)
    if name == N.C_LUI:
        rd_forbidden.add(RiscvReg.SP)
    if name == N.C_ADDI16SP:
        # Must use SP as rd (handled below by fixed assignment).
        pass

    # CB_FORMAT parity with SV (riscv_instr_stream.sv:randomize_gpr):
    # C_ANDI / C_SRLI / C_SRAI read AND write rs1 (there's no separate rd
    # field in the encoding). If any of those instructions lands with rs1 ==
    # a reserved register, it silently corrupts reserved state — e.g.
    # C_SRLI on rs1=x8 (s0) clobbers s0 even when s0 is a load/store base.
    # C_BEQZ / C_BNEZ are read-only and skip this rule.
    if name in (N.C_ANDI, N.C_SRLI, N.C_SRAI):
        rs1_forbidden |= rd_forbidden

    def _pick(pool: set[RiscvReg], exclude: set[RiscvReg] = frozenset()) -> RiscvReg:
        choices = [r for r in pool if r not in exclude]
        if not choices:
            # Fallback widens to the physically-legal set, not the full
            # GPR pool. For 3-bit compressed formats (CIW/CL/CS/CB/CA)
            # the encoding cannot represent registers outside S0..A5 —
            # widening to _NON_CSR_REGS would emit illegal asm like
            # "c.addi4spn s3, sp, ..." which gas rejects with "illegal
            # operands". Mirrors SV rvc_csr_c: riscv_compressed_instr.sv:21.
            fallback = _COMPRESSED_REGS if compressed_3bit else _NON_CSR_REGS
            choices = [r for r in fallback if r not in exclude]
        return rng.choice(choices)

    if instr.has_rs1:
        instr.rs1 = _pick(allowed, rs1_forbidden)
    if instr.has_rs2:
        instr.rs2 = _pick(allowed, rs2_forbidden)
    if instr.has_rd:
        instr.rd = _pick(allowed, rd_forbidden)
    # Fixed-register compressed specials.
    if name == N.C_ADDI16SP:
        instr.rd = RiscvReg.SP
    if name in (N.C_JR, N.C_JALR):
        instr.rs2 = RiscvReg.ZERO  # SV: rvc_csr_c forces this
