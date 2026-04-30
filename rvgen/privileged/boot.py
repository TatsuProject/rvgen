"""Boot CSR sequence — port of ``src/riscv_privileged_common_seq.sv`` +
``src/riscv_asm_program_gen.sv::pre_enter_privileged_mode``.

Responsibilities:

- Emit MISA setup for targets that have a writable MISA.
- Program MTVEC (and STVEC when S-mode exists on the core) with the
  trap-handler base address and the configured mode (DIRECT / VECTORED).
- Program MSTATUS.MPP so that the final ``mret`` drops to the requested
  initial privilege mode (M / S / U).
- Optionally program MEDELEG / MIDELEG to route traps to a lower-
  privilege handler (disabled by default — ``cfg.no_delegation=True`` —
  so all traps reach the M-mode handler regardless of trapping mode).
- Prime MIE for software / external / timer interrupts when
  ``cfg.enable_interrupt`` is set.
- End with ``mret`` so execution resumes at the ``init`` label in the
  requested mode.
"""

from __future__ import annotations

from rvgen.config import Config
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    MisaExt,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvReg,
    SatpMode,
)
from rvgen.isa.utils import hart_prefix
from rvgen.privileged.paging import (
    build_default_page_tables,
    gen_setup_satp,
    is_paging_enabled,
)
from rvgen.privileged.pmp import gen_setup_pmp, make_default_cfg as make_default_pmp_cfg
from rvgen.targets import TargetCfg


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


# ---------------------------------------------------------------------------
# MISA
# ---------------------------------------------------------------------------


def _misa_value(cfg: Config, target: TargetCfg) -> int:
    """Compute MISA = MXL (high bits) | extension bits.

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
        if PrivilegedMode.USER_MODE in target.supported_privileged_mode:
            ext_bits |= 1 << MisaExt.MISA_EXT_U.value
        if PrivilegedMode.SUPERVISOR_MODE in target.supported_privileged_mode:
            ext_bits |= 1 << MisaExt.MISA_EXT_S.value
    return (high_bits << (target.xlen - 2)) | ext_bits


def gen_setup_misa(cfg: Config, scratch: RiscvReg) -> list[str]:
    """Emit MISA setup: ``li + csrw MISA``."""
    val = _misa_value(cfg, cfg.target)
    return [
        _line(f"li {scratch.abi}, 0x{val:x}"),
        _line(f"csrw 0x{PrivilegedReg.MISA.value:x}, {scratch.abi}"),
    ]


# ---------------------------------------------------------------------------
# MSTATUS
# ---------------------------------------------------------------------------


def _mstatus_value(cfg: Config) -> int:
    """Compute the MSTATUS boot value.

    Key bits:
    - MPP[12:11] = init_privileged_mode (the mode ``mret`` will drop into).
    - MPIE[7] / SPIE[5] / UPIE[4] set when cfg.enable_interrupt → these
      populate xSTATUS.xIE when ``mret`` restores them.
    - MPRV[17], SUM[18], MXR[19], TVM[20], TW[21], FS[14:13], VS[10:9]
      reflect the cfg knobs one-for-one.
    """
    val = 0
    val |= (cfg.init_privileged_mode.value & 0b11) << 11
    if cfg.enable_interrupt:
        val |= 1 << 7   # MPIE
        val |= 1 << 5   # SPIE
        val |= 1 << 4   # UPIE
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
    """Compute MIE. Bits: MSIE=3, MTIE=7, MEIE=11."""
    val = 0
    if cfg.enable_interrupt:
        val |= 1 << 3   # MSIE
        val |= 1 << 11  # MEIE
        if cfg.enable_timer_irq:
            val |= 1 << 7   # MTIE
    return val


# ---------------------------------------------------------------------------
# Delegation helpers (optional — enabled only when cfg.no_delegation=False)
# ---------------------------------------------------------------------------


def _medeleg_value(cfg: Config) -> int:
    """MEDELEG bit layout: one bit per exception cause. SV's
    ``force_m_delegation`` / ``force_s_delegation`` semantics are simpler
    to emulate as "delegate everything benign" when delegation is on.

    Bit positions match ExceptionCause values (they are the bit index).
    We leave ECALL_MMODE (bit 11) undelegated — spec reserves that slot.
    """
    if cfg.no_delegation:
        return 0
    # Delegate the most useful exceptions (everything except ECALL_MMODE).
    return 0xB3FF  # bits 0..9 + 12..15 of the exception vector


def _mideleg_value(cfg: Config) -> int:
    """MIDELEG: bits for S-software (1), S-timer (5), S-external (9)."""
    if cfg.no_delegation:
        return 0
    return (1 << 1) | (1 << 5) | (1 << 9)


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
    """Emit the CSR-write sequence that transitions from M-mode reset into
    the configured initial privilege mode at label ``init``.

    Shape: kernel SP → [STVEC] → MTVEC → [MEDELEG / MIDELEG] → MEPC →
    MSTATUS → MIE → mret.
    """
    num_harts = cfg.num_of_harts
    scratch = cfg.scratch_reg
    gpr0 = cfg.gpr[0]
    prefix = hart_prefix(hart, num_harts)
    target = cfg.target
    assert target is not None

    lines: list[str] = []

    # 1) Kernel stack pointer (used by the trap handler's push/pop sequence).
    lines.append(_line(f"la {cfg.tp.abi}, {prefix}kernel_stack_end"))

    # 2) xTVEC setup. Always write MTVEC. If S-mode is in play and the
    #    core advertises STVEC, program it to the same handler (traps are
    #    handled uniformly in M-mode when delegation is off; but even so,
    #    writing STVEC prevents a spurious read hazard and keeps spike
    #    happy on delegated interrupts during nested-IRQ tests).
    #
    # The MODE bit is gated on enable_interrupt — when interrupts are
    # disabled we emit a DIRECT layout (trap.py agrees), so MTVEC.MODE
    # must be 0 to match. Importing trap here causes a cycle; inline the
    # gate instead.
    mtvec_mode_bit = cfg.mtvec_mode.value if cfg.enable_interrupt else 0

    def _write_xtvec(csr: PrivilegedReg, handler_label: str, *, prefixed: bool = True) -> None:
        # ``handler_label`` may already carry the hart prefix (the caller
        # passes e.g. "h1_mtvec_handler"). Only prepend ``prefix`` for
        # handlers the caller didn't already qualify (stvec_handler, which
        # is a per-mode label, not per-hart).
        full = handler_label if prefixed else f"{prefix}{handler_label}"
        lines.append(_line(f"la {gpr0.abi}, {full}"))
        lines.append(_line(f"ori {gpr0.abi}, {gpr0.abi}, {mtvec_mode_bit}"))
        lines.append(_line(f"csrw 0x{csr.value:x}, {gpr0.abi}"))

    _write_xtvec(PrivilegedReg.MTVEC, trap_handler_label, prefixed=True)
    # Write STVEC only when delegation is on — otherwise S-mode traps
    # never reach STVEC (they land on MTVEC instead), and some spike
    # builds WARL-reject the write when MISA.S is latched off, wasting
    # an illegal-instr trap on boot.
    if (
        not cfg.no_delegation
        and PrivilegedMode.SUPERVISOR_MODE in target.supported_privileged_mode
        and PrivilegedReg.STVEC in target.implemented_csr
    ):
        _write_xtvec(PrivilegedReg.STVEC, "stvec_handler", prefixed=False)

    # 3) Delegation (only when explicitly requested; default is no-delegation).
    if not cfg.no_delegation:
        if PrivilegedReg.MEDELEG in target.implemented_csr:
            val = _medeleg_value(cfg)
            lines.append(_line(f"li {gpr0.abi}, 0x{val:x}"))
            lines.append(_line(
                f"csrw 0x{PrivilegedReg.MEDELEG.value:x}, {gpr0.abi}"
            ))
        if PrivilegedReg.MIDELEG in target.implemented_csr:
            val = _mideleg_value(cfg)
            lines.append(_line(f"li {gpr0.abi}, 0x{val:x}"))
            lines.append(_line(
                f"csrw 0x{PrivilegedReg.MIDELEG.value:x}, {gpr0.abi}"
            ))

    # 4) MEPC = init — MRET will jump here in the target privilege mode.
    # ``init_label`` is pre-qualified by the caller (includes hart prefix).
    lines.append(_line(f"la {gpr0.abi}, {init_label}"))
    lines.append(_line(
        f"csrw 0x{PrivilegedReg.MEPC.value:x}, {gpr0.abi}"
    ))

    # 5) MSTATUS.
    mstatus = _mstatus_value(cfg)
    lines.append(_line(f"li {gpr0.abi}, 0x{mstatus:x}"))
    lines.append(_line(
        f"csrw 0x{PrivilegedReg.MSTATUS.value:x}, {gpr0.abi}"
    ))

    # 6) MIE (if target implements it).
    if PrivilegedReg.MIE in target.implemented_csr:
        mie = _mie_value(cfg)
        lines.append(_line(f"li {gpr0.abi}, 0x{mie:x}"))
        lines.append(_line(
            f"csrw 0x{PrivilegedReg.MIE.value:x}, {gpr0.abi}"
        ))

    # 7) PMP setup (opt-in). Programmed before paging so M-mode can
    #    still use unprotected memory while it builds page tables.
    if cfg.enable_pmp_setup:
        pmp_cfg = make_default_pmp_cfg(
            xlen=target.xlen,
            num_regions=cfg.pmp_num_regions,
        )
        lines.extend(gen_setup_pmp(cfg, pmp_cfg, scratch))

    # 8) Page-table linking + SATP setup. Must run while still in M-mode
    #    (link-PTE fix-up writes to the .page_table section) and *before*
    #    MRET drops us into S/U-mode where SATP-translated loads/stores
    #    apply.
    if is_paging_enabled(cfg):
        page_table_list = build_default_page_tables(
            mode=target.satp_mode,
            privileged_mode=cfg.init_privileged_mode,
        )
        # Fix up link PTEs to point at their child tables' runtime addresses.
        lines.extend(page_table_list.gen_process_page_table(cfg))
        # Program SATP and flush the TLB.
        lines.extend(gen_setup_satp(cfg, scratch))

    # 9) MRET — transition to init at MPP's privilege.
    lines.append(_line("mret"))
    return lines
