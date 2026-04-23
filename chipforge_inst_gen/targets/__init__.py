"""Per-target processor configuration — port of ``target/*/riscv_core_setting.sv``.

Every target riscv-dv ships with has a corresponding :class:`TargetCfg`
instance here. The configuration is intentionally declarative so that tests
and higher layers can query ``get_target("rv32imc")`` and ask for XLEN,
supported ISA groups, implemented CSRs, or privilege modes.

If a new target is added in riscv-dv's ``target/`` tree, add an entry to
:data:`_TARGETS` below; nothing else should need to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from chipforge_inst_gen.isa.enums import (
    ExceptionCause,
    InterruptCause,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvInstrName,
    SatpMode,
)


# ---------------------------------------------------------------------------
# TargetCfg — declarative configuration record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TargetCfg:
    """Fully-populated per-target configuration.

    All fields have the same semantics as the corresponding SV ``parameter``
    / ``bit`` in ``target/<name>/riscv_core_setting.sv``. Missing SV fields
    default to the SV source's value.
    """

    name: str
    xlen: int
    supported_isa: tuple[RiscvInstrGroup, ...]
    supported_privileged_mode: tuple[PrivilegedMode, ...]
    satp_mode: SatpMode = SatpMode.BARE
    supported_interrupt_mode: tuple[MtvecMode, ...] = (MtvecMode.DIRECT, MtvecMode.VECTORED)
    max_interrupt_vector_num: int = 16
    num_harts: int = 1
    num_gpr: int = 32
    num_float_gpr: int = 32
    num_vec_gpr: int = 32
    vlen: int = 512
    elen: int = 32
    selen: int = 8
    max_lmul: int = 8
    vector_extension_enable: bool = False
    support_pmp: bool = False
    support_epmp: bool = False
    support_debug_mode: bool = False
    support_umode_trap: bool = False
    support_sfence: bool = False
    support_unaligned_load_store: bool = True
    unsupported_instr: tuple[RiscvInstrName, ...] = ()
    implemented_csr: tuple[PrivilegedReg, ...] = ()
    custom_csr: tuple[int, ...] = ()
    implemented_interrupt: tuple[InterruptCause, ...] = ()
    implemented_exception: tuple[ExceptionCause, ...] = ()


# ---------------------------------------------------------------------------
# CSR / interrupt / exception presets (reused across many targets)
# ---------------------------------------------------------------------------


#: Machine-mode only CSR set — the default for simple M-only cores.
_MMODE_CSRS: tuple[PrivilegedReg, ...] = (
    PrivilegedReg.MVENDORID,
    PrivilegedReg.MARCHID,
    PrivilegedReg.MIMPID,
    PrivilegedReg.MHARTID,
    PrivilegedReg.MSTATUS,
    PrivilegedReg.MISA,
    PrivilegedReg.MIE,
    PrivilegedReg.MTVEC,
    PrivilegedReg.MCOUNTEREN,
    PrivilegedReg.MSCRATCH,
    PrivilegedReg.MEPC,
    PrivilegedReg.MCAUSE,
    PrivilegedReg.MTVAL,
    PrivilegedReg.MIP,
)

#: User-mode CSRs present in privileged targets.
_UMODE_CSRS: tuple[PrivilegedReg, ...] = (
    PrivilegedReg.USTATUS,
    PrivilegedReg.UIE,
    PrivilegedReg.UTVEC,
    PrivilegedReg.USCRATCH,
    PrivilegedReg.UEPC,
    PrivilegedReg.UCAUSE,
    PrivilegedReg.UTVAL,
    PrivilegedReg.UIP,
)

#: Supervisor-mode CSRs present in privileged targets.
_SMODE_CSRS: tuple[PrivilegedReg, ...] = (
    PrivilegedReg.SSTATUS,
    PrivilegedReg.SEDELEG,
    PrivilegedReg.SIDELEG,
    PrivilegedReg.SIE,
    PrivilegedReg.STVEC,
    PrivilegedReg.SCOUNTEREN,
    PrivilegedReg.SSCRATCH,
    PrivilegedReg.SEPC,
    PrivilegedReg.SCAUSE,
    PrivilegedReg.STVAL,
    PrivilegedReg.SIP,
    PrivilegedReg.SATP,
)

#: Minimal implemented interrupt set (M-only targets).
_MMODE_INTERRUPTS: tuple[InterruptCause, ...] = (
    InterruptCause.M_SOFTWARE_INTR,
    InterruptCause.M_TIMER_INTR,
    InterruptCause.M_EXTERNAL_INTR,
)

#: Exception set for M-only targets.
_MMODE_EXCEPTIONS: tuple[ExceptionCause, ...] = (
    ExceptionCause.INSTRUCTION_ACCESS_FAULT,
    ExceptionCause.ILLEGAL_INSTRUCTION,
    ExceptionCause.BREAKPOINT,
    ExceptionCause.LOAD_ADDRESS_MISALIGNED,
    ExceptionCause.LOAD_ACCESS_FAULT,
    ExceptionCause.ECALL_MMODE,
)

#: Full interrupt set for U/S/M-capable targets.
_USM_INTERRUPTS: tuple[InterruptCause, ...] = (
    InterruptCause.U_SOFTWARE_INTR,
    InterruptCause.S_SOFTWARE_INTR,
    InterruptCause.M_SOFTWARE_INTR,
    InterruptCause.U_TIMER_INTR,
    InterruptCause.S_TIMER_INTR,
    InterruptCause.M_TIMER_INTR,
    InterruptCause.U_EXTERNAL_INTR,
    InterruptCause.S_EXTERNAL_INTR,
    InterruptCause.M_EXTERNAL_INTR,
)

#: Full exception set for U/S/M-capable targets.
_USM_EXCEPTIONS: tuple[ExceptionCause, ...] = (
    ExceptionCause.INSTRUCTION_ADDRESS_MISALIGNED,
    ExceptionCause.INSTRUCTION_ACCESS_FAULT,
    ExceptionCause.ILLEGAL_INSTRUCTION,
    ExceptionCause.BREAKPOINT,
    ExceptionCause.LOAD_ADDRESS_MISALIGNED,
    ExceptionCause.LOAD_ACCESS_FAULT,
    ExceptionCause.STORE_AMO_ADDRESS_MISALIGNED,
    ExceptionCause.STORE_AMO_ACCESS_FAULT,
    ExceptionCause.ECALL_UMODE,
    ExceptionCause.ECALL_SMODE,
    ExceptionCause.ECALL_MMODE,
    ExceptionCause.INSTRUCTION_PAGE_FAULT,
    ExceptionCause.LOAD_PAGE_FAULT,
    ExceptionCause.STORE_AMO_PAGE_FAULT,
)


# ---------------------------------------------------------------------------
# Per-target definitions
# ---------------------------------------------------------------------------


_G = RiscvInstrGroup
_M = PrivilegedMode


def _m_only() -> dict:
    return dict(
        supported_privileged_mode=(_M.MACHINE_MODE,),
        implemented_csr=_MMODE_CSRS,
        implemented_interrupt=_MMODE_INTERRUPTS,
        implemented_exception=_MMODE_EXCEPTIONS,
    )


def _privileged() -> dict:
    return dict(
        supported_privileged_mode=(_M.USER_MODE, _M.SUPERVISOR_MODE, _M.MACHINE_MODE),
        implemented_csr=_UMODE_CSRS + _SMODE_CSRS + (PrivilegedReg.FCSR,) + _MMODE_CSRS,
        implemented_interrupt=_USM_INTERRUPTS,
        implemented_exception=_USM_EXCEPTIONS,
    )


def _privileged_no_fp() -> dict:
    return dict(
        supported_privileged_mode=(_M.USER_MODE, _M.SUPERVISOR_MODE, _M.MACHINE_MODE),
        implemented_csr=_UMODE_CSRS + _SMODE_CSRS + _MMODE_CSRS,
        implemented_interrupt=_USM_INTERRUPTS,
        implemented_exception=_USM_EXCEPTIONS,
    )


def _bare_rv32() -> dict:
    """No privileged mode, no CSRs, no traps — for rv32ui-style minimal cores.

    ``bare_program_mode`` in the Config should be set True when targeting one
    of these; otherwise the generator would emit boot CSR writes that the core
    cannot execute.
    """
    return dict(
        supported_privileged_mode=(_M.MACHINE_MODE,),  # notional — no CSRs
        implemented_csr=(),                            # no CSR at all
        implemented_interrupt=(),
        implemented_exception=(),
        support_unaligned_load_store=True,
    )


_TARGETS: dict[str, TargetCfg] = {
    # ---- RV32 single-extension ----
    "rv32i": TargetCfg(
        name="rv32i", xlen=32,
        supported_isa=(_G.RV32I,),
        **_m_only(),
    ),
    # Minimal RV32UI (user-mode ISA only; no CSR, no trap) — test output runs
    # on cores that lack privileged infrastructure entirely. Use with
    # ``+bare_program_mode=1 +no_csr_instr=1 +no_fence=1 +no_ebreak=1
    # +no_ecall=1 +no_wfi=1 +no_dret=1``.
    "rv32ui": TargetCfg(
        name="rv32ui", xlen=32,
        supported_isa=(_G.RV32I,),
        **_bare_rv32(),
    ),
    # RV32IMC + Zkn umbrella (Zbkb + Zbkc + Zbkx + Zkne + Zknd + Zknh).
    # All six sub-extensions are ratified RISC-V crypto. This is the ISA
    # string the chipforge MCU accepts — NO Zbb, since the MCU only
    # implements the Zbb∩Zbkb overlap (andn/orn/xnor/rol/ror/rori/pack/
    # packh) which are covered by Zbkb. SHA-512 split-pair opcodes aren't
    # in the MCU either (SHA-256 only), so they're explicitly unsupported.
    "rv32imc_zkn": TargetCfg(
        name="rv32imc_zkn", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32ZBKB, _G.RV32ZBKC, _G.RV32ZBKX,
            _G.RV32ZKND, _G.RV32ZKNE, _G.RV32ZKNH,
        ),
        unsupported_instr=(
            RiscvInstrName.SHA512SIG0L, RiscvInstrName.SHA512SIG0H,
            RiscvInstrName.SHA512SIG1L, RiscvInstrName.SHA512SIG1H,
            RiscvInstrName.SHA512SUM0R, RiscvInstrName.SHA512SUM1R,
        ),
        **_m_only(),
    ),
    # Full crypto + bitmanip on RV32: the complete ratified K family including
    # SM3/SM4. Useful for exhaustive crypto stress tests.
    "rv32imc_zkn_zks": TargetCfg(
        name="rv32imc_zkn_zks", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32ZBA, _G.RV32ZBB, _G.RV32ZBC, _G.RV32ZBS,
            _G.RV32ZBKB, _G.RV32ZBKC, _G.RV32ZBKX,
            _G.RV32ZKND, _G.RV32ZKNE, _G.RV32ZKNH,
            _G.RV32ZKSH, _G.RV32ZKSED,
        ),
        **_m_only(),
    ),
    # RV64 crypto baseline — AES64 / SHA-512 single-instruction + SHA-256.
    "rv64imc_zkn": TargetCfg(
        name="rv64imc_zkn", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV64I, _G.RV64M, _G.RV64C,
            _G.RV32ZBKB, _G.RV64ZBKB, _G.RV32ZBKC, _G.RV64ZBKC,
            _G.RV32ZBKX, _G.RV64ZBKX,
            _G.RV32ZKNH, _G.RV64ZKNH,
            _G.RV64ZKND, _G.RV64ZKNE,
        ),
        **_m_only(),
    ),
    "rv32im": TargetCfg(
        name="rv32im", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32M),
        # rv32im's SV source marks MUL/MULH/MULHSU/MULHU as unsupported
        # (it's a cost-constrained "M without high-mul" variant).
        unsupported_instr=(
            RiscvInstrName.MUL, RiscvInstrName.MULH,
            RiscvInstrName.MULHSU, RiscvInstrName.MULHU,
        ),
        **_m_only(),
    ),
    "rv32ic": TargetCfg(
        name="rv32ic", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32C),
        # Note: support_unaligned_load_store is 1 for rv32ic per SV; default.
        **_m_only(),
    ),
    "rv32ia": TargetCfg(
        name="rv32ia", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32A),
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    "rv32iac": TargetCfg(
        name="rv32iac", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32C, _G.RV32A),
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    "rv32if": TargetCfg(
        name="rv32if", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32F),
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    # ---- RV32 combined ----
    "rv32imc": TargetCfg(
        name="rv32imc", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32M, _G.RV32C),
        **_m_only(),
    ),
    "rv32imac": TargetCfg(
        name="rv32imac", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32M, _G.RV32A, _G.RV32C),
        **_m_only(),
    ),
    "rv32imafdc": TargetCfg(
        name="rv32imafdc", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32F, _G.RV32FC, _G.RV32D, _G.RV32DC, _G.RV32A,
        ),
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    "rv32imcb": TargetCfg(
        name="rv32imcb", xlen=32,
        # SV riscv_core_setting.sv declares RV32B (draft 0.93), but current
        # GCC/spike only implement ratified Zba/Zbb/Zbc/Zbs; using the
        # ratified groups keeps generated .S assemblable. To exercise the
        # draft-B opcodes add RV32B here and wire a matching binary-emission
        # path that bypasses GCC.
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32ZBA, _G.RV32ZBB, _G.RV32ZBC, _G.RV32ZBS,
        ),
        **_m_only(),
    ),
    "rv32imc_sv32": TargetCfg(
        name="rv32imc_sv32", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32M, _G.RV32C),
        satp_mode=SatpMode.SV32,
        supported_privileged_mode=(_M.MACHINE_MODE, _M.USER_MODE),
        # SV32 target exposes U-mode CSRs in addition to M-mode.
        implemented_csr=_UMODE_CSRS + _MMODE_CSRS,
        implemented_interrupt=_MMODE_INTERRUPTS,
        implemented_exception=_MMODE_EXCEPTIONS,
    ),
    # ---- RV64 M-only ----
    "rv64imc": TargetCfg(
        name="rv64imc", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV64I, _G.RV64M, _G.RV64C,
        ),
        **_m_only(),
    ),
    "rv64imcb": TargetCfg(
        name="rv64imcb", xlen=64,
        # See rv32imcb note — only ratified Zba/Zbb/Zbc/Zbs here.
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32ZBA, _G.RV32ZBB, _G.RV32ZBC, _G.RV32ZBS,
            _G.RV64I, _G.RV64M, _G.RV64C,
        ),
        **_m_only(),
    ),
    # ---- RV64 privileged (U/S/M) ----
    "rv64gc": TargetCfg(
        name="rv64gc", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X,
        ),
        satp_mode=SatpMode.SV39,
        support_sfence=True,
        **_privileged(),
    ),
    "rv64imafdc": TargetCfg(
        name="rv64imafdc", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X,
        ),
        satp_mode=SatpMode.SV39,
        support_sfence=True,
        support_unaligned_load_store=False,
        **_privileged(),
    ),
    # ---- RV64 with vector ----
    "rv64gcv": TargetCfg(
        name="rv64gcv", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RVV,
        ),
        vector_extension_enable=True,
        vlen=512, elen=32, selen=8, max_lmul=8,
        **_m_only(),
    ),
    # ---- Embedded vector (Zve*) profiles ----
    #
    # coralnpu-v2 — Google's open-source NPU.
    # Public ISA string: rv32imf_zve32x_zicsr_zifencei_zbb.
    # So: RV32I/M, single-precision scalar F, Zve32x (embedded vector with
    # 32-bit element, integer+fixed-point, NO FP vector), Zbb bitmanip.
    # VLEN=256 is a common embedded choice; ELEN=32 is mandatory for Zve32x.
    "coralnpu": TargetCfg(
        name="coralnpu", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32F,
            _G.ZVE32X,
            _G.RV32ZBB,
        ),
        vlen=256, elen=32, selen=8, max_lmul=8,
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    # Zve32x reference target without scalar F / Zbb — baseline embedded.
    "rv32imc_zve32x": TargetCfg(
        name="rv32imc_zve32x", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.ZVE32X,
        ),
        vlen=256, elen=32, selen=8, max_lmul=8,
        **_m_only(),
    ),
    # Zve32f — adds FP32 vector on top of Zve32x (needs scalar F).
    "rv32imfc_zve32f": TargetCfg(
        name="rv32imfc_zve32f", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C, _G.RV32F,
            _G.ZVE32F,
        ),
        vlen=256, elen=32, selen=8, max_lmul=8,
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    # Zve64x — 64-bit integer vector, no FP vector.
    "rv64imc_zve64x": TargetCfg(
        name="rv64imc_zve64x", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV64I, _G.RV64M, _G.RV64C,
            _G.ZVE64X,
        ),
        vlen=512, elen=64, selen=8, max_lmul=8,
        **_m_only(),
    ),
    # Zve64d — full embedded-vector-with-double profile (closest to RVV).
    "rv64imafdc_zve64d": TargetCfg(
        name="rv64imafdc_zve64d", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV64I, _G.RV64M, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.ZVE64D,
        ),
        vlen=512, elen=64, selen=8, max_lmul=8,
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    # ---- Specialty ----
    "ml": TargetCfg(
        name="ml", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32M, _G.RV32C, _G.RV32A),
        **_m_only(),
    ),
    "multi_harts": TargetCfg(
        name="multi_harts", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32M, _G.RV32C, _G.RV32A),
        num_harts=2,
        **_m_only(),
    ),
}


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_target(name: str) -> TargetCfg:
    """Return the :class:`TargetCfg` for the given target name.

    Raises ``KeyError`` with a helpful message if the target is unknown.
    """
    try:
        return _TARGETS[name]
    except KeyError:
        raise KeyError(
            f"Unknown target {name!r}. Known targets: {sorted(_TARGETS)}"
        ) from None


def target_names() -> tuple[str, ...]:
    """Return all known target names, sorted alphabetically."""
    return tuple(sorted(_TARGETS))


def iter_targets() -> Iterable[TargetCfg]:
    """Iterate over all :class:`TargetCfg` values in name-sorted order."""
    for n in target_names():
        yield _TARGETS[n]
