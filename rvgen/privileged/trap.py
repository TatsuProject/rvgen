"""Trap-handler emit — port of ``src/riscv_asm_program_gen.sv``'s
``gen_trap_handlers`` family. Supports:

- M / S / U privileged modes (any mode at or above ``cfg.init_privileged_mode``
  whose delegation configuration says traps still reach it).
- DIRECT and VECTORED ``xtvec`` layouts. VECTORED emits a 16-entry jump
  table; entry 0 = exception handler, entries 1..15 = interrupt vectors.
- Full exception dispatch (ebreak, ecall-from-U/S/M, illegal instruction,
  instruction/load/store access faults, instruction/load/store page faults).
- Nested-interrupt rearm via the scratch CSR + xSTATUS.xIE sticky bit
  (``cfg.enable_nested_interrupt``).

The emitted handler shape follows the SV source at
``src/riscv_asm_program_gen.sv`` lines 1012..1400. Labels match exactly so
directed tests that jump into ``ebreak_handler`` / ``ecall_handler`` etc.
keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

from rvgen.config import Config
from rvgen.isa.enums import (
    ExceptionCause,
    LABEL_STR_LEN,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    SatpMode,
)
from rvgen.isa.utils import (
    format_string,
    hart_prefix,
    pop_gpr_from_kernel_stack,
    push_gpr_to_kernel_stack,
)


# How many vectored-interrupt slots VECTORED MTVEC dispatches. SV's
# ``max_interrupt_vector_num`` is 16 (one exception slot + 15 interrupt
# vectors). Architectural interrupt codes only use 0..11, but riscv-dv
# still emits 16 so cores with custom interrupt IDs have room.
_MAX_VECTOR_NUM = 16


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


def _labeled(label: str, body: str = "") -> str:
    return format_string(f"{label}:", LABEL_STR_LEN) + body


# ---------------------------------------------------------------------------
# Mode-context struct: which CSRs to touch for M / S / U trap handlers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ModeCtx:
    """All per-mode CSRs + label tokens for one trap handler instance."""

    mode: PrivilegedMode
    prefix: str                 # "m" / "s" / "u" — drives label names.
    status: PrivilegedReg
    cause: PrivilegedReg
    epc: PrivilegedReg
    tval: PrivilegedReg
    ip: PrivilegedReg
    ie: PrivilegedReg
    scratch: PrivilegedReg
    tvec: PrivilegedReg
    ret_insn: str               # "mret" / "sret" / "uret"
    xie_bit: int                # MSTATUS.MIE=3, SSTATUS.SIE=1, USTATUS.UIE=0
    tvec_label: str             # "mtvec_handler" etc.


_M_CTX = _ModeCtx(
    mode=PrivilegedMode.MACHINE_MODE,
    prefix="m",
    status=PrivilegedReg.MSTATUS,
    cause=PrivilegedReg.MCAUSE,
    epc=PrivilegedReg.MEPC,
    tval=PrivilegedReg.MTVAL,
    ip=PrivilegedReg.MIP,
    ie=PrivilegedReg.MIE,
    scratch=PrivilegedReg.MSCRATCH,
    tvec=PrivilegedReg.MTVEC,
    ret_insn="mret",
    xie_bit=3,
    tvec_label="mtvec_handler",
)

_S_CTX = _ModeCtx(
    mode=PrivilegedMode.SUPERVISOR_MODE,
    prefix="s",
    status=PrivilegedReg.SSTATUS,
    cause=PrivilegedReg.SCAUSE,
    epc=PrivilegedReg.SEPC,
    tval=PrivilegedReg.STVAL,
    ip=PrivilegedReg.SIP,
    ie=PrivilegedReg.SIE,
    scratch=PrivilegedReg.SSCRATCH,
    tvec=PrivilegedReg.STVEC,
    ret_insn="sret",
    xie_bit=1,
    tvec_label="stvec_handler",
)


def _mode_contexts(cfg: Config) -> list[_ModeCtx]:
    """Return the ordered list of ``_ModeCtx`` that need trap handlers.

    Rule (matches SV ``gen_interrupt_handler_section``):
    emit handlers for every supported mode ``m`` such that
    ``m >= cfg.init_privileged_mode``. Lower-privilege traps get delegated
    or simply never fire, so they don't need a handler.

    U-mode handlers are always skipped — no mainstream core implements
    U-mode traps (``support_umode_trap=0`` in SV), and attempting to write
    UTVEC/UIE/UIP on spike raises an illegal-CSR trap.
    """
    out: list[_ModeCtx] = []
    supported = cfg.target.supported_privileged_mode if cfg.target else ()
    init_mode = cfg.init_privileged_mode

    # M-mode is always present on any RISC-V core.
    if PrivilegedMode.MACHINE_MODE.value >= init_mode.value:
        out.append(_M_CTX)
    # S-mode: skip unless delegation is explicitly enabled. With
    # ``no_delegation=True`` (default), MEDELEG/MIDELEG are zero and
    # every trap goes to M-mode — the S-mode handler would be dead code.
    if (
        not cfg.no_delegation
        and PrivilegedMode.SUPERVISOR_MODE in supported
        and PrivilegedMode.SUPERVISOR_MODE.value >= init_mode.value
    ):
        out.append(_S_CTX)
    return out


# ---------------------------------------------------------------------------
# Context save / restore wrappers
# ---------------------------------------------------------------------------


def _push(cfg: Config, ctx: _ModeCtx) -> list[str]:
    implemented = ctx.scratch in cfg.target.implemented_csr
    body = push_gpr_to_kernel_stack(
        ctx.status, ctx.scratch,
        mprv=cfg.mstatus_mprv,
        sp=cfg.sp, tp=cfg.tp,
        xlen=cfg.target.xlen,
        satp_mode=cfg.target.satp_mode,
        scratch_implemented=implemented,
    )
    return [_line(b) for b in body]


def _pop(cfg: Config, ctx: _ModeCtx) -> list[str]:
    implemented = ctx.scratch in cfg.target.implemented_csr
    body = pop_gpr_from_kernel_stack(
        ctx.status, ctx.scratch,
        mprv=cfg.mstatus_mprv,
        sp=cfg.sp, tp=cfg.tp,
        xlen=cfg.target.xlen,
        satp_mode=cfg.target.satp_mode,
        scratch_implemented=implemented,
    )
    return [_line(b) for b in body]


# ---------------------------------------------------------------------------
# Per-mode handler emission
# ---------------------------------------------------------------------------


def _effective_mtvec_mode(cfg: Config) -> MtvecMode:
    """VECTORED is only worth its .text cost when interrupts are enabled.

    When ``cfg.enable_interrupt=False`` — which is every test except the
    interrupt-specific ones — the VECTORED jump table (16 * 4 = 64 bytes)
    plus 15 per-vector stubs (~450 bytes total) is pure bloat. Force
    DIRECT in that case. The MTVEC.MODE bit emitted by boot.py also
    consults this, so the hardware view stays consistent.
    """
    if not cfg.enable_interrupt:
        return MtvecMode.DIRECT
    return cfg.mtvec_mode


def _emit_xtvec_entry(cfg: Config, ctx: _ModeCtx, hart: int) -> list[str]:
    """Emit the xtvec entry label.

    DIRECT: pushes GPRs, reads xCAUSE, branches to xmode_intr_handler if
            interrupt bit set, else falls through into the exception
            handler (emitted separately — it's the next section).
    VECTORED: emits the 16-entry jump table (entry 0 = exception, 1..15 =
              per-interrupt vectors).
    """
    prefix = hart_prefix(hart, cfg.num_of_harts)
    gpr0 = cfg.gpr[0]
    xlen = cfg.target.xlen

    lines: list[str] = [_labeled(f"{prefix}{ctx.tvec_label}")]

    if _effective_mtvec_mode(cfg) == MtvecMode.VECTORED:
        # SV note: the jump table must not use compressed instructions —
        # each entry is a 4-byte J so the MTVEC base + 4*cause arithmetic
        # lands on the right slot. Toggle RVC off for the table.
        if not cfg.disable_compressed_instr:
            lines.append(_line(".option norvc;"))
        # Entry 0 = shared with exception handler (hardware rule).
        lines.append(_line(f"j {prefix}{ctx.prefix}mode_exception_handler"))
        # Entries 1..15 dispatch to per-cause vectors.
        for i in range(1, _MAX_VECTOR_NUM):
            lines.append(_line(f"j {prefix}{ctx.prefix}mode_intr_vector_{i}"))
        if not cfg.disable_compressed_instr:
            lines.append(_line(".option rvc;"))
        return lines

    # DIRECT mode: push context, dispatch on cause MSB.
    lines.extend(_push(cfg, ctx))
    if cfg.check_xstatus:
        lines.append(_line(
            f"csrr {gpr0.abi}, 0x{ctx.status.value:x} # {ctx.status.name}"
        ))
    lines.append(_line(
        f"csrr {gpr0.abi}, 0x{ctx.cause.value:x} # {ctx.cause.name}"
    ))
    lines.append(_line(f"srli {gpr0.abi}, {gpr0.abi}, {xlen - 1}"))
    lines.append(_line(
        f"bne {gpr0.abi}, zero, {prefix}{ctx.prefix}mode_intr_handler"
    ))
    return lines


def _emit_exception_dispatch(cfg: Config, ctx: _ModeCtx, hart: int) -> list[str]:
    """Emit ``<prefix><mode>mode_exception_handler``: table-dispatch on xCAUSE.

    In VECTORED mode this label is the target of entry 0 of the jump
    table, so we must re-push GPRs at the top (DIRECT did that in the
    MTVEC entry).
    """
    prefix = hart_prefix(hart, cfg.num_of_harts)
    gpr0 = cfg.gpr[0]
    gpr1 = cfg.gpr[1]
    scratch = cfg.scratch_reg

    lines: list[str] = [_labeled(f"{prefix}{ctx.prefix}mode_exception_handler")]
    if _effective_mtvec_mode(cfg) == MtvecMode.VECTORED:
        lines.extend(_push(cfg, ctx))

    # Re-read xEPC then xCAUSE (SV does both, purely for trace-check coverage).
    lines.append(_line(
        f"csrr {gpr0.abi}, 0x{ctx.epc.value:x} # {ctx.epc.name}"
    ))
    lines.append(_line(
        f"csrr {gpr0.abi}, 0x{ctx.cause.value:x} # {ctx.cause.name}"
    ))

    # Dispatch table. Every entry is an 8-byte "li; beq" pair.
    dispatch = [
        (ExceptionCause.BREAKPOINT,              "ebreak_handler"),
        (ExceptionCause.ECALL_UMODE,             "ecall_handler"),
        (ExceptionCause.ECALL_SMODE,             "ecall_handler"),
        (ExceptionCause.ECALL_MMODE,             "ecall_handler"),
        (ExceptionCause.INSTRUCTION_ACCESS_FAULT, "instr_fault_handler"),
        (ExceptionCause.LOAD_ACCESS_FAULT,       "load_fault_handler"),
        (ExceptionCause.STORE_AMO_ACCESS_FAULT,  "store_fault_handler"),
        (ExceptionCause.INSTRUCTION_PAGE_FAULT,  "pt_fault_handler"),
        (ExceptionCause.LOAD_PAGE_FAULT,         "pt_fault_handler"),
        (ExceptionCause.STORE_AMO_PAGE_FAULT,    "pt_fault_handler"),
        (ExceptionCause.ILLEGAL_INSTRUCTION,     "illegal_instr_handler"),
    ]
    for cause, target in dispatch:
        lines.append(_line(f"li {gpr1.abi}, 0x{cause.value:x} # {cause.name}"))
        lines.append(_line(
            f"beq {gpr0.abi}, {gpr1.abi}, {prefix}{target}"
        ))

    # Fallthrough: read xTVAL (for spike-trace coverage), then jump to test_done.
    lines.append(_line(
        f"csrr {gpr1.abi}, 0x{ctx.tval.value:x} # {ctx.tval.name}"
    ))
    lines.append(_line(f"la {scratch.abi}, test_done"))
    lines.append(_line(f"jalr x0, {scratch.abi}, 0"))
    return lines


def _emit_exception_subhandlers(cfg: Config, ctx: _ModeCtx, hart: int) -> list[str]:
    """Emit the sub-handlers shared across all modes: ebreak/illegal/fault/ecall.

    Sub-handlers always run in M-mode's frame (they are targets of the
    M-mode dispatcher). For S-mode we'd need duplicated sub-handlers, but
    the current wiring delegates S-mode traps up to M-mode (default
    ``no_delegation=True``), so all xCAUSE values arrive at the M-mode
    dispatcher and share one set of sub-handlers. This matches
    riscv-dv's behavior when MEDELEG/MIDELEG are zero.

    These are emitted once (for the M-mode context) — the caller skips
    this function for subsequent contexts.
    """
    prefix = hart_prefix(hart, cfg.num_of_harts)
    gpr0 = cfg.gpr[0]
    scratch = cfg.scratch_reg
    lines: list[str] = []

    # ecall_handler: set gp=1 (spike tohost exit protocol) then jump to
    # write_tohost. Any ecall (from the test_done prologue OR a stray
    # random ecall) cleanly terminates the test — without this, a random
    # ecall would hang spike because write_tohost keeps reposting gp
    # until a non-zero word lands.
    lines.append(_labeled(f"{prefix}ecall_handler"))
    lines.append(_line("li gp, 1"))
    lines.append(_line(f"la {scratch.abi}, write_tohost"))
    lines.append(_line(f"jalr x0, {scratch.abi}, 0"))

    # All non-ecall sub-handlers share one body: bump xEPC by 4, pop, mret.
    # We emit multiple labels pointing at the same body (label stacking)
    # — this keeps the generated .text small. The +4 fixup matches SV's
    # convention: the generator guarantees PC+4 is a valid instruction
    # boundary for BREAKPOINT / ILLEGAL / ACCESS / PAGE_FAULT. For random
    # stress tests this is critical — an oversized handler eats address
    # range that random stores can accidentally target, corrupting code.
    for label in (
        "ebreak_handler",
        "illegal_instr_handler",
        "instr_fault_handler",
        "load_fault_handler",
        "store_fault_handler",
        "pt_fault_handler",
    ):
        lines.append(_labeled(f"{prefix}{label}"))
    lines.append(_line(f"csrr {gpr0.abi}, 0x{ctx.epc.value:x}"))
    lines.append(_line(f"addi {gpr0.abi}, {gpr0.abi}, 4"))
    lines.append(_line(f"csrw 0x{ctx.epc.value:x}, {gpr0.abi}"))
    lines.extend(_pop(cfg, ctx))
    lines.append(_line(ctx.ret_insn))

    return lines


def _emit_intr_vectors(cfg: Config, ctx: _ModeCtx, hart: int) -> list[str]:
    """VECTORED-mode per-vector handlers (vectors 1..15). Each one pushes
    GPRs, validates that xCAUSE[MSB]=1 (else test_done — spurious), then
    jumps to the shared ``<prefix>mode_intr_handler``.

    In DIRECT mode this returns empty.
    """
    if _effective_mtvec_mode(cfg) != MtvecMode.VECTORED:
        return []

    prefix = hart_prefix(hart, cfg.num_of_harts)
    gpr0 = cfg.gpr[0]
    scratch = cfg.scratch_reg
    xlen = cfg.target.xlen
    lines: list[str] = []

    for i in range(1, _MAX_VECTOR_NUM):
        lines.append(_labeled(f"{prefix}{ctx.prefix}mode_intr_vector_{i}"))
        lines.extend(_push(cfg, ctx))
        # Verify interrupt bit is set (defense against spurious entries
        # that shouldn't hit this slot at all).
        lines.append(_line(
            f"csrr {gpr0.abi}, 0x{ctx.cause.value:x} # {ctx.cause.name}"
        ))
        lines.append(_line(f"srli {gpr0.abi}, {gpr0.abi}, {xlen - 1}"))
        lines.append(_line(f"beqz {gpr0.abi}, 1f"))
        lines.append(_line(f"j {prefix}{ctx.prefix}mode_intr_handler"))
        lines.append(_line(f"1: la {scratch.abi}, test_done"))
        lines.append(_line(f"jalr x0, {scratch.abi}, 0"))
    return lines


def _emit_intr_handler(cfg: Config, ctx: _ModeCtx, hart: int) -> list[str]:
    """Emit the common interrupt-handler body for ``ctx``'s mode.

    Layout (matches SV ``gen_interrupt_handler_section``):

    - [if nested interrupts] guard via scratch CSR + set xSTATUS.xIE
    - read xSTATUS, xIE, xIP (pure trace instrumentation)
    - clear pending interrupts: ``csrrc xIP, xIP, gpr0``
      (uses value we just read, which clears only the bits currently
      pending — safe, and matches riscv-dv).
    - pop GPRs
    - xret

    In DIRECT mode the handler is the direct target of the xtvec branch.
    In VECTORED mode it's the shared tail the per-vector handlers jump to.
    """
    prefix = hart_prefix(hart, cfg.num_of_harts)
    gpr0 = cfg.gpr[0]
    xlen = cfg.target.xlen  # noqa: F841 (documentation-only reference)

    lines: list[str] = [_labeled(f"{prefix}{ctx.prefix}mode_intr_handler")]

    if cfg.enable_nested_interrupt:
        # Sticky-lock on scratch: if it's 0 we're the outermost ISR, so
        # set scratch=1 and enable xSTATUS.xIE to allow nested entry.
        lines.append(_line(
            f"csrr {gpr0.abi}, 0x{ctx.scratch.value:x}"
        ))
        lines.append(_line(f"bgtz {gpr0.abi}, 1f"))
        lines.append(_line(f"csrwi 0x{ctx.scratch.value:x}, 0x1"))
        lines.append(_line(
            f"csrsi 0x{ctx.status.value:x}, 0x{1 << ctx.xie_bit:x}"
        ))
        lines.append(_line(f"1: csrwi 0x{ctx.scratch.value:x}, 0"))

    # Read xStatus / xIE / xIP (trace instrumentation + required for xIP clear).
    lines.append(_line(
        f"csrr {gpr0.abi}, 0x{ctx.status.value:x} # {ctx.status.name}"
    ))
    lines.append(_line(
        f"csrr {gpr0.abi}, 0x{ctx.ie.value:x} # {ctx.ie.name}"
    ))
    lines.append(_line(
        f"csrr {gpr0.abi}, 0x{ctx.ip.value:x} # {ctx.ip.name}"
    ))
    # Clear software-pending bits via csrrc. NOTE: MTIP is hardware-driven
    # (1 iff MTIME >= MTIMECMP) and ignores CSR writes — for timer IRQs
    # we also push MTIMECMP forward, below.
    lines.append(_line(
        f"csrrc {gpr0.abi}, 0x{ctx.ip.value:x}, {gpr0.abi} # {ctx.ip.name}"
    ))

    # Timer-IRQ disarm: write MTIMECMP = -1 so MTIP deasserts before xret.
    # Without this the test re-enters the ISR forever once MTIP fires.
    # We emit this unconditionally when timer-IRQ is enabled — it's cheap
    # and correct even when the current IRQ wasn't a timer one.
    if cfg.enable_timer_irq and ctx.mode == PrivilegedMode.MACHINE_MODE:
        from rvgen.privileged.interrupts import gen_clear_timer_irq
        lines.extend(gen_clear_timer_irq(cfg, hart=hart))

    # Restore context + return.
    lines.extend(_pop(cfg, ctx))
    lines.append(_line(ctx.ret_insn))
    return lines


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def gen_trap_handler(cfg: Config, *, hart: int = 0) -> list[str]:
    """Emit the complete trap-handler section for ``hart``.

    Emits (per supported mode, in order M then S):
      - ``<prefix>xtvec_handler`` (DIRECT pushes+dispatches; VECTORED
        emits the 16-entry jump table).
      - ``<prefix>xmode_exception_handler``: cause-dispatch table.
      - ``<prefix>xmode_intr_handler``: shared interrupt tail.
      - ``<prefix>xmode_intr_vector_[1..15]``: VECTORED only.

    Sub-handlers (ebreak/ecall/illegal/fault) are emitted once, after the
    M-mode section. They're shared across all modes because delegation is
    off by default (``cfg.no_delegation=True``) — every trap ends up in
    the M-mode dispatcher regardless of the trapping mode.
    """
    if cfg.bare_program_mode:
        return []

    lines: list[str] = []
    ctxs = _mode_contexts(cfg)
    if not ctxs:  # Should not happen — M is always supported.
        return []

    for i, ctx in enumerate(ctxs):
        lines.extend(_emit_xtvec_entry(cfg, ctx, hart))
        lines.extend(_emit_exception_dispatch(cfg, ctx, hart))
        lines.extend(_emit_intr_vectors(cfg, ctx, hart))
        lines.extend(_emit_intr_handler(cfg, ctx, hart))

    # Sub-handlers once, rooted on the first (M-mode) context.
    lines.extend(_emit_exception_subhandlers(cfg, ctxs[0], hart))
    return lines
