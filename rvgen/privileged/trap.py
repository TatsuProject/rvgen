"""Trap-handler emit helpers — DIRECT-mode M-mode only for Phase 1 step 5.

A minimal handler that:

- Pushes x1..x31 to the kernel stack (via :func:`push_gpr_to_kernel_stack`).
- Reads MCAUSE, checks MSB to split exception vs. interrupt.
- For exceptions, dispatches on the cause code to:
    - ``ecall_handler``  : jumps to ``write_tohost`` (ends the test).
    - ``ebreak_handler`` / ``illegal_instr_handler`` : bump MEPC by 4, pop, ``mret``.
    - Default          : jump to ``test_done`` so spike exits cleanly.
- Interrupts are routed to ``<mode>_intr_handler`` which just MRETs back.
"""

from __future__ import annotations

from rvgen.config import Config
from rvgen.isa.enums import (
    ExceptionCause,
    LABEL_STR_LEN,
    PrivilegedReg,
    SatpMode,
)
from rvgen.isa.utils import (
    format_string,
    hart_prefix,
    pop_gpr_from_kernel_stack,
    push_gpr_to_kernel_stack,
)


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


def _labeled(label: str, body: str = "") -> str:
    """Build a labeled line: ``<label>:<padding><body>``."""
    return format_string(f"{label}:", LABEL_STR_LEN) + body


def _push(cfg: Config) -> list[str]:
    scratch_implemented = PrivilegedReg.MSCRATCH in cfg.target.implemented_csr
    body = push_gpr_to_kernel_stack(
        PrivilegedReg.MSTATUS,
        PrivilegedReg.MSCRATCH,
        mprv=cfg.mstatus_mprv,
        sp=cfg.sp,
        tp=cfg.tp,
        xlen=cfg.target.xlen,
        satp_mode=cfg.target.satp_mode,
        scratch_implemented=scratch_implemented,
    )
    return [_line(b) for b in body]


def _pop(cfg: Config) -> list[str]:
    scratch_implemented = PrivilegedReg.MSCRATCH in cfg.target.implemented_csr
    body = pop_gpr_from_kernel_stack(
        PrivilegedReg.MSTATUS,
        PrivilegedReg.MSCRATCH,
        mprv=cfg.mstatus_mprv,
        sp=cfg.sp,
        tp=cfg.tp,
        xlen=cfg.target.xlen,
        satp_mode=cfg.target.satp_mode,
        scratch_implemented=scratch_implemented,
    )
    return [_line(b) for b in body]


def gen_trap_handler(cfg: Config, *, hart: int = 0) -> list[str]:
    """Emit the trap-handler section (Phase 1 MVP, M-mode DIRECT).

    Handles the ecall that ``test_done`` issues by jumping to ``write_tohost``
    — that's what terminates the test on spike.
    """
    prefix = hart_prefix(hart, cfg.num_of_harts)
    gpr0 = cfg.gpr[0]
    gpr1 = cfg.gpr[1]
    scratch = cfg.scratch_reg
    xlen = cfg.target.xlen

    lines: list[str] = []

    # ------------------------------------------------------------------
    # mtvec_handler: entry point (aligned, first line labeled)
    # ------------------------------------------------------------------
    lines.append(_labeled(f"{prefix}mtvec_handler"))

    # Push context.
    lines.extend(_push(cfg))

    # Read MCAUSE; check MSB (interrupt bit).
    lines.append(_line(f"csrr {gpr0.abi}, 0x{PrivilegedReg.MCAUSE.value:x}"))
    lines.append(_line(f"srli {gpr0.abi}, {gpr0.abi}, {xlen - 1}"))
    lines.append(_line(f"bne {gpr0.abi}, zero, {prefix}mmode_intr_handler"))

    # ------------------------------------------------------------------
    # Exception dispatch.
    # ------------------------------------------------------------------
    lines.append(_labeled(f"{prefix}mmode_exception_handler"))
    lines.append(_line(f"csrr {gpr0.abi}, 0x{PrivilegedReg.MCAUSE.value:x}"))
    lines.append(_line(f"li {gpr1.abi}, 0x{ExceptionCause.BREAKPOINT.value:x}"))
    lines.append(_line(f"beq {gpr0.abi}, {gpr1.abi}, {prefix}ebreak_handler"))
    lines.append(_line(f"li {gpr1.abi}, 0x{ExceptionCause.ECALL_MMODE.value:x}"))
    lines.append(_line(f"beq {gpr0.abi}, {gpr1.abi}, {prefix}ecall_handler"))
    lines.append(_line(f"li {gpr1.abi}, 0x{ExceptionCause.ILLEGAL_INSTRUCTION.value:x}"))
    lines.append(_line(f"beq {gpr0.abi}, {gpr1.abi}, {prefix}illegal_instr_handler"))
    # Fallback: end test.
    lines.append(_line(f"la {scratch.abi}, test_done"))
    lines.append(_line(f"jalr x0, {scratch.abi}, 0"))

    # ------------------------------------------------------------------
    # ecall_handler: jump to write_tohost (where gp=1 is stored and spike exits).
    # ------------------------------------------------------------------
    lines.append(_labeled(f"{prefix}ecall_handler"))
    lines.append(_line(f"la {scratch.abi}, write_tohost"))
    lines.append(_line(f"jalr x0, {scratch.abi}, 0"))

    # ------------------------------------------------------------------
    # ebreak_handler and illegal_instr_handler: bump MEPC by 4, pop, mret.
    # ------------------------------------------------------------------
    for label in (f"{prefix}ebreak_handler", f"{prefix}illegal_instr_handler"):
        lines.append(_labeled(label))
        lines.append(_line(f"csrr {gpr0.abi}, 0x{PrivilegedReg.MEPC.value:x}"))
        lines.append(_line(f"addi {gpr0.abi}, {gpr0.abi}, 4"))
        lines.append(_line(f"csrw 0x{PrivilegedReg.MEPC.value:x}, {gpr0.abi}"))
        lines.extend(_pop(cfg))
        lines.append(_line("mret"))

    # ------------------------------------------------------------------
    # mmode_intr_handler: no-op (just pop + mret).
    # ------------------------------------------------------------------
    lines.append(_labeled(f"{prefix}mmode_intr_handler"))
    lines.extend(_pop(cfg))
    lines.append(_line("mret"))

    return lines
