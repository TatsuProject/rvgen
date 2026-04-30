"""Built-in :class:`TargetCfg` definitions — the 27 targets riscv-dv
ships, plus rvgen's Zve* embedded-vector additions.

User-declared targets live as YAML under the user area
(see :mod:`rvgen.targets.loader`). Do not add user-specific cores
here — this file is framework-owned.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvInstrName,
    SatpMode,
)
from rvgen.targets.core_setting import TargetCfg
from rvgen.targets.presets import (
    HAIA_CSRS,
    MMODE_CSRS,
    MMODE_EXCEPTIONS,
    MMODE_INTERRUPTS,
    SMAIA_CSRS,
    SMODE_CSRS,
    SSAIA_CSRS,
    UMODE_CSRS,
    USM_EXCEPTIONS,
    USM_INTERRUPTS,
)


_G = RiscvInstrGroup
_M = PrivilegedMode


def _m_only() -> dict:
    return dict(
        supported_privileged_mode=(_M.MACHINE_MODE,),
        implemented_csr=MMODE_CSRS,
        implemented_interrupt=MMODE_INTERRUPTS,
        implemented_exception=MMODE_EXCEPTIONS,
    )


def _privileged() -> dict:
    return dict(
        supported_privileged_mode=(_M.USER_MODE, _M.SUPERVISOR_MODE, _M.MACHINE_MODE),
        implemented_csr=UMODE_CSRS + SMODE_CSRS + (PrivilegedReg.FCSR,) + MMODE_CSRS,
        implemented_interrupt=USM_INTERRUPTS,
        implemented_exception=USM_EXCEPTIONS,
    )


def _privileged_no_fp() -> dict:
    return dict(
        supported_privileged_mode=(_M.USER_MODE, _M.SUPERVISOR_MODE, _M.MACHINE_MODE),
        implemented_csr=UMODE_CSRS + SMODE_CSRS + MMODE_CSRS,
        implemented_interrupt=USM_INTERRUPTS,
        implemented_exception=USM_EXCEPTIONS,
    )


def _bare_rv32() -> dict:
    """No privileged mode, no CSRs, no traps — for rv32ui-style minimal cores.

    ``bare_program_mode`` in the Config should be set True when targeting one
    of these; otherwise the generator would emit boot CSR writes that the core
    cannot execute.
    """
    return dict(
        supported_privileged_mode=(_M.MACHINE_MODE,),  # notional — no CSRs
        implemented_csr=(),
        implemented_interrupt=(),
        implemented_exception=(),
        support_unaligned_load_store=True,
    )


BUILTIN_TARGETS: dict[str, TargetCfg] = {
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
    "rv32imckf": TargetCfg(
        # RV32 I + M + C + K (scalar crypto) + F (single-precision FP only).
        # No D, no A. Matches the chipforge Challenge-0014 core ISA.
        name="rv32imckf", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32F, _G.RV32FC,
            _G.RV32ZBKB, _G.RV32ZBKC, _G.RV32ZBKX,
            _G.RV32ZKND, _G.RV32ZKNE, _G.RV32ZKNH,
        ),
        unsupported_instr=(
            # SHA-512 split-pair instructions are RV32-only helpers not all
            # cores implement; mirror rv32imc_zkn's deny list.
            RiscvInstrName.SHA512SIG0L, RiscvInstrName.SHA512SIG0H,
            RiscvInstrName.SHA512SIG1L, RiscvInstrName.SHA512SIG1H,
            RiscvInstrName.SHA512SUM0R, RiscvInstrName.SHA512SUM1R,
        ),
        support_unaligned_load_store=False,
        **_m_only(),
    ),
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
        unsupported_instr=(
            RiscvInstrName.MUL, RiscvInstrName.MULH,
            RiscvInstrName.MULHSU, RiscvInstrName.MULHU,
        ),
        **_m_only(),
    ),
    "rv32ic": TargetCfg(
        name="rv32ic", xlen=32,
        supported_isa=(_G.RV32I, _G.RV32C),
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
    # ---- RV32IMAFDC + Zfh (half-precision scalar FP) ----
    "rv32imafdc_zfh": TargetCfg(
        name="rv32imafdc_zfh", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.RV32F, _G.RV32FC, _G.RV32D, _G.RV32DC, _G.RV32A,
            _G.RV32ZFH,
        ),
        isa_string="rv32imafdc_zfh_zicsr_zifencei",
        mabi="ilp32d",
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    "rv32imcb": TargetCfg(
        name="rv32imcb", xlen=32,
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
        implemented_csr=UMODE_CSRS + MMODE_CSRS,
        implemented_interrupt=MMODE_INTERRUPTS,
        implemented_exception=MMODE_EXCEPTIONS,
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
    # ---- RV64GC + Sv48 paging ----
    # Same scalar/FP profile as rv64gc but advertises 4-level Sv48
    # virtual addressing (1+2+4+8 = 15 page tables). Use --priv msu
    # so the boot mret drops to S/U-mode and SATP-driven translation
    # actually runs.
    "rv64gc_sv48": TargetCfg(
        name="rv64gc_sv48", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X,
        ),
        satp_mode=SatpMode.SV48,
        support_sfence=True,
        isa_string="rv64gc_zicsr_zifencei",
        mabi="lp64d",
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
    # ---- RV64GC + Smaia + Ssaia (Advanced Interrupt Architecture) ----
    # AIA v1.0 (ratified 2023-08) adds register-indirect IMSIC access and
    # priority-resolution CSRs. No new instructions — the surface lives in
    # the M/S-mode CSR namespace. Targets advertise the new CSRs to make
    # them reachable from CSR-write streams; the actual interrupt fabric
    # (IMSIC + APLIC) is a separate hardware component.
    "rv64gc_aia": TargetCfg(
        name="rv64gc_aia", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X,
        ),
        satp_mode=SatpMode.SV39,
        support_sfence=True,
        supported_privileged_mode=(_M.USER_MODE, _M.SUPERVISOR_MODE, _M.MACHINE_MODE),
        implemented_csr=(
            UMODE_CSRS + SMODE_CSRS + (PrivilegedReg.FCSR,) + MMODE_CSRS
            + SMAIA_CSRS + SSAIA_CSRS
        ),
        implemented_interrupt=USM_INTERRUPTS,
        implemented_exception=USM_EXCEPTIONS,
        isa_string="rv64gc_zicsr_zifencei_smaia_ssaia",
        mabi="lp64d",
    ),
    # ---- RV64GC + H + Smaia + Ssaia (everything in one target) ----
    "rv64gch_aia": TargetCfg(
        name="rv64gch_aia", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X, _G.RV64H,
        ),
        satp_mode=SatpMode.SV39,
        support_sfence=True,
        supported_privileged_mode=(_M.USER_MODE, _M.SUPERVISOR_MODE, _M.MACHINE_MODE),
        implemented_csr=(
            UMODE_CSRS + SMODE_CSRS + (PrivilegedReg.FCSR,) + MMODE_CSRS
            + SMAIA_CSRS + SSAIA_CSRS + HAIA_CSRS
        ),
        implemented_interrupt=USM_INTERRUPTS,
        implemented_exception=USM_EXCEPTIONS,
        isa_string="rv64gch_zicsr_zifencei_smaia_ssaia",
        mabi="lp64d",
    ),
    # ---- RV64GC + Hypervisor (H) ----
    # Adds HS-mode + Virtual-S/U-mode + the H-ext instruction set
    # (hfence.vvma/gvma, hlv*/hlvx*/hsv*). Two-stage translation lives
    # on the privileged side and is not yet wired up; for Phase-1 the
    # target advertises the new ``RV64H`` group enum so the random
    # stream can emit H-ext mnemonics, and the H-mode CSRs become
    # reachable from CSR-write streams.
    "rv64gch": TargetCfg(
        name="rv64gch", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X,
            _G.RV64H,
        ),
        satp_mode=SatpMode.SV39,
        support_sfence=True,
        isa_string="rv64gch_zicsr_zifencei",
        mabi="lp64d",
        **_privileged(),
    ),
    # ---- RV64IMAFDC + Zfh ----
    "rv64imafdc_zfh": TargetCfg(
        name="rv64imafdc_zfh", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32ZFH, _G.RV64ZFH,
        ),
        isa_string="rv64imafdc_zfh_zicsr_zifencei",
        mabi="lp64d",
        support_unaligned_load_store=False,
        **_m_only(),
    ),
    # ---- RV64GC + modern checkbox extensions (Zicond/Zicbom/Zicboz/Zicbop/
    # Zihintpause/Zihintntl/Zimop/Zcmop). Same privileged shape as rv64gc;
    # advertises every ratified-but-recent extension a 2024-era core tends
    # to implement.
    "rv64gc_modern": TargetCfg(
        name="rv64gc_modern", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RV32X,
            _G.RV32ZICOND, _G.RV64ZICOND,
            _G.RV32ZICBOM, _G.RV32ZICBOZ, _G.RV32ZICBOP,
            _G.RV32ZIHINTPAUSE, _G.RV32ZIHINTNTL,
            _G.RV32ZIMOP, _G.RV64ZIMOP, _G.RV32ZCMOP,
        ),
        satp_mode=SatpMode.SV39,
        support_sfence=True,
        # GCC accepts the new extension shorthands once they are ratified;
        # spell them all out so older toolchains don't silently drop ones
        # they don't recognise.
        isa_string="rv64gc_zicond_zicbom_zicboz_zicbop"
                   "_zihintpause_zihintntl_zimop_zcmop"
                   "_zicsr_zifencei",
        mabi="lp64d",
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
    # ---- RV64 with vector + ratified Zv* extensions ----
    # Same scalar/vector base as rv64gcv but with Zvbb / Zvbc / Zvkn / Zvfh
    # turned on. Uses ``-march=rv64gcv_zvbb_zvbc_zvkn_zvfh`` at compile time.
    "rv64gcv_crypto": TargetCfg(
        name="rv64gcv_crypto", xlen=64,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV64I, _G.RV64M,
            _G.RV32C, _G.RV64C,
            _G.RV32A, _G.RV64A,
            _G.RV32F, _G.RV64F, _G.RV32D, _G.RV64D,
            _G.RVV,
        ),
        vector_extension_enable=True,
        vlen=512, elen=32, selen=8, max_lmul=8,
        enable_zvbb=True, enable_zvbc=True, enable_zvkn=True, enable_zvfh=True,
        isa_string="rv64gcv_zvbb_zvbc_zvkn_zvfh",
        mabi="lp64d",
        **_m_only(),
    ),
    # ---- Embedded vector (Zve*) profiles ----
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
    "rv32imc_zve32x": TargetCfg(
        name="rv32imc_zve32x", xlen=32,
        supported_isa=(
            _G.RV32I, _G.RV32M, _G.RV32C,
            _G.ZVE32X,
        ),
        vlen=256, elen=32, selen=8, max_lmul=8,
        **_m_only(),
    ),
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
