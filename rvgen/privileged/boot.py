"""Boot CSR sequence — port of ``src/riscv_privileged_common_seq.sv`` +
``src/riscv_asm_program_gen.sv::pre_enter_privileged_mode``.

For Phase 1 step 5 we implement the minimum viable M-mode-only path. S/U
mode, PMP, and paging are deferred to step 8.
"""

from __future__ import annotations

from dataclasses import dataclass

from rvgen.config import Config
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    MisaExt,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvReg,
)
from rvgen.isa.utils import hart_prefix
from rvgen.targets import TargetCfg


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


# ---------------------------------------------------------------------------
# MISA
# ---------------------------------------------------------------------------


def _misa_value(cfg: Config, target: TargetCfg) -> int:
    """Compute the MISA value: MXL + extension bits.

    SV: ``setup_misa`` (riscv_asm_program_gen.sv:565).
    """
    high_bits = 0b01 if target.xlen == 32 else 0b10
    ext_bits = 0
    group_to_ext: dict[RiscvInstrGroup, MisaExt] = {
        RiscvInstrGroup.RV32I: MisaExt.MISA_EXT_I,
        RiscvInstrGroup.RV64I: MisaExt.MISA_EXT_I,
        RiscvInstrGroup.RV32M: MisaExt.MISA_EXT_M,
        RiscvInstrGroup.RV64M: MisaExt.MISA_EXT_M,
        RiscvInstrGroup.RV32A: MisaExt.MISA_EXT_A,
        RiscvInstrGroup.RV64A: MisaExt.MISA_EXT_A,
        RiscvInstrGroup.RV32F: MisaExt.MISA_EXT_F,
        RiscvInstrGroup.RV64F: MisaExt.MISA_EXT_F,
        RiscvInstrGroup.RV32D: MisaExt.MISA_EXT_D,
        RiscvInstrGroup.RV64D: MisaExt.MISA_EXT_D,
        RiscvInstrGroup.RV32C: MisaExt.MISA_EXT_C,
        RiscvInstrGroup.RV64C: MisaExt.MISA_EXT_C,
        RiscvInstrGroup.RVV: MisaExt.MISA_EXT_V,
        RiscvInstrGroup.RV32B: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV64B: MisaExt.MISA_EXT_B,
        # Ratified bitmanip sub-groups all report as MISA.B.
        RiscvInstrGroup.RV32ZBA: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV32ZBB: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV32ZBC: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV32ZBS: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV64ZBA: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV64ZBB: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV64ZBC: MisaExt.MISA_EXT_B,
        RiscvInstrGroup.RV64ZBS: MisaExt.MISA_EXT_B,
    }
    for group in target.supported_isa:
        if group in group_to_ext:
            ext_bits |= 1 << group_to_ext[group].value
    if target.supported_privileged_mode != (PrivilegedMode.MACHINE_MODE,):
        # U/S modes enable MISA.U and .S bits.
        if PrivilegedMode.USER_MODE in target.supported_privileged_mode:
            ext_bits |= 1 << MisaExt.MISA_EXT_U.value
        if PrivilegedMode.SUPERVISOR_MODE in target.supported_privileged_mode:
            ext_bits |= 1 << MisaExt.MISA_EXT_S.value
    return (high_bits << (target.xlen - 2)) | ext_bits


def gen_setup_misa(cfg: Config, scratch: RiscvReg) -> list[str]:
    """Emit the MISA setup sequence: li + csrw MISA."""
    val = _misa_value(cfg, cfg.target)
    return [
        _line(f"li {scratch.abi}, 0x{val:x}"),
        _line(f"csrw 0x{PrivilegedReg.MISA.value:x}, {scratch.abi}"),
    ]


# ---------------------------------------------------------------------------
# MSTATUS
# ---------------------------------------------------------------------------


def _mstatus_value(cfg: Config) -> int:
    """Compute MSTATUS for boot: MPP=<init_mode>, MIE=0, optional MPIE/SPIE/UPIE.

    SV: ``setup_mmode_reg`` (riscv_privileged_common_seq.sv:56).
    """
    val = 0
    # MPP (bits [12:11]).
    val |= (cfg.init_privileged_mode.value & 0b11) << 11
    # MPIE/SPIE/UPIE set per enable_interrupt (approximate — full SV logic is
    # per-mode; for Phase 1 we tie them all to enable_interrupt).
    if cfg.enable_interrupt:
        val |= 1 << 7   # MPIE
        val |= 1 << 5   # SPIE
        val |= 1 << 4   # UPIE
        # MIE/SIE remain zero at boot (enabled via MPIE on mret).
    # mstatus bits
    if cfg.mstatus_mprv:
        val |= 1 << 17
    if cfg.mstatus_sum:
        val |= 1 << 18
    if cfg.mstatus_mxr:
        val |= 1 << 19
    if cfg.mstatus_tvm:
        val |= 1 << 20
    if cfg.set_mstatus_tw:
        val |= 1 << 21
    if cfg.mstatus_fs:
        val |= (cfg.mstatus_fs & 0b11) << 13
    if cfg.mstatus_vs:
        val |= (cfg.mstatus_vs & 0b11) << 9
    return val


def _mie_value(cfg: Config) -> int:
    """Compute MIE for boot (approximate — just enables top-level IE bits)."""
    val = 0
    if cfg.enable_interrupt:
        val |= 1 << 3   # MSIE
        val |= 1 << 11  # MEIE
        if cfg.enable_timer_irq:
            val |= 1 << 7  # MTIE
    return val


# ---------------------------------------------------------------------------
# Pre-enter privileged mode — full boot CSR sequence
# ---------------------------------------------------------------------------


def gen_pre_enter_privileged_mode(
    cfg: Config,
    *,
    hart: int = 0,
    init_label: str = "init",
    trap_handler_label: str = "mtvec_handler",
) -> list[str]:
    """Emit: kernel SP + MTVEC + MEPC + MSTATUS + MIE + MRET.

    SV reference: riscv_asm_program_gen.sv pre_enter_privileged_mode (line
    ~470) and riscv_privileged_common_seq.sv gen_csr_instr.
    """
    num_harts = cfg.num_of_harts
    scratch = cfg.scratch_reg
    gpr0 = cfg.gpr[0]
    prefix = hart_prefix(hart, num_harts)

    lines: list[str] = []

    # 1) Kernel stack pointer.
    lines.append(_line(f"la {cfg.tp.abi}, {prefix}kernel_stack_end"))

    # 2) MTVEC setup.
    mtvec_mode_bit = cfg.mtvec_mode.value
    lines.append(_line(f"la {gpr0.abi}, {prefix}{trap_handler_label}"))
    lines.append(_line(f"ori {gpr0.abi}, {gpr0.abi}, {mtvec_mode_bit}"))
    lines.append(_line(
        f"csrw 0x{PrivilegedReg.MTVEC.value:x}, {gpr0.abi}"
    ))

    # 3) MEPC = init label (MRET will jump here).
    lines.append(_line(f"la {gpr0.abi}, {prefix}{init_label}"))
    lines.append(_line(
        f"csrw 0x{PrivilegedReg.MEPC.value:x}, {gpr0.abi}"
    ))

    # 4) MSTATUS.
    mstatus = _mstatus_value(cfg)
    lines.append(_line(f"li {gpr0.abi}, 0x{mstatus:x}"))
    lines.append(_line(
        f"csrw 0x{PrivilegedReg.MSTATUS.value:x}, {gpr0.abi}"
    ))

    # 5) MIE (if target implements it).
    if PrivilegedReg.MIE in cfg.target.implemented_csr:
        mie = _mie_value(cfg)
        lines.append(_line(f"li {gpr0.abi}, 0x{mie:x}"))
        lines.append(_line(
            f"csrw 0x{PrivilegedReg.MIE.value:x}, {gpr0.abi}"
        ))

    # 6) MRET — transition to init (at MPP's privilege).
    lines.append(_line("mret"))
    return lines
