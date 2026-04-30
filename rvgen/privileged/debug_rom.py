"""Debug ROM section generator — port of ``src/riscv_debug_rom_gen.sv``.

The debug ROM is a small piece of asm placed at a hart-prefixed
``debug_rom`` label. It runs whenever the core enters Debug Mode (via
ebreak with DCSR.ebreakX = 1, or an external halt request). For
verification we emit a sequence that:

* Pushes GPRs to the kernel stack (so the debug code can use whatever
  registers it wants without trashing the program's state).
* Optionally signals ``IN_DEBUG_MODE`` to the testbench.
* Optionally programs DCSR.ebreak{m/s/u} bits.
* Optionally enables single-stepping for ``single_step_iterations``
  passes (via the DCSR.step bit + a DSCRATCH0 counter).
* Bumps DPC by 4 if DCSR.cause == ebreak (so dret returns past the
  ebreak rather than into an infinite loop).
* Pops GPRs.
* Issues ``dret`` to leave Debug Mode.

Phase-1 scope: emit the structural skeleton (push/pop, single-step
logic, DPC bump). Random sub-program insertion + signature handshake
ride along at the same hooks the trap handler uses.
"""

from __future__ import annotations

from rvgen.config import Config
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    PrivilegedMode,
    PrivilegedReg,
    RiscvReg,
)
from rvgen.isa.utils import hart_prefix


_INDENT = " " * LABEL_STR_LEN


def _line(s: str) -> str:
    return f"{_INDENT}{s}"


# ---------------------------------------------------------------------------
# Sub-sequences
# ---------------------------------------------------------------------------


def _gen_dpc_update(scratch: RiscvReg, gpr0: RiscvReg) -> list[str]:
    """Bump DPC by 4 when the entry cause is ebreak (DCSR.cause == 1).

    DCSR layout: ``[31:28] XDEBUGVER, [15] ebreakm, [13] ebreaks,
    [12] ebreaku, [10] stepie, [9] stopcount, [8] stoptime, [7:6] reserved,
    [8:6] cause, [3] step, [2:1] prv, [0] reserved``. Cause occupies bits
    8..6 (3 bits, 1=ebreak, 4=step, 5=haltreq).

    SV does ``slli 0x17 ; srli 0x1d`` (sign-extend bits 8:6 to LSBs).
    """
    return [
        _line(f"csrr {scratch.abi}, 0x{PrivilegedReg.DCSR.value:x}"),
        _line(f"slli {scratch.abi}, {scratch.abi}, 0x17"),
        _line(f"srli {scratch.abi}, {scratch.abi}, 0x1d"),
        _line(f"li {gpr0.abi}, 0x1"),
        _line(f"bne {scratch.abi}, {gpr0.abi}, 4f"),
        # Increment DPC by 4 (port of SV ``increment_csr(DPC, 4, ...)``).
        _line(f"csrr {scratch.abi}, 0x{PrivilegedReg.DPC.value:x}"),
        _line(f"addi {scratch.abi}, {scratch.abi}, 0x4"),
        _line(f"csrw 0x{PrivilegedReg.DPC.value:x}, {scratch.abi}"),
        "4:               nop",
    ]


def _gen_dcsr_ebreak(cfg: Config, scratch: RiscvReg) -> list[str]:
    """Set DCSR.ebreak{m/s/u} for whichever modes the target supports."""
    out: list[str] = []
    modes = cfg.target.supported_privileged_mode
    dcsr_addr = PrivilegedReg.DCSR.value
    if PrivilegedMode.MACHINE_MODE in modes:
        out.append(_line(f"li {scratch.abi}, 0x8000"))
        out.append(_line(f"csrs 0x{dcsr_addr:x}, {scratch.abi}"))
    if PrivilegedMode.SUPERVISOR_MODE in modes:
        out.append(_line(f"li {scratch.abi}, 0x2000"))
        out.append(_line(f"csrs 0x{dcsr_addr:x}, {scratch.abi}"))
    if PrivilegedMode.USER_MODE in modes:
        out.append(_line(f"li {scratch.abi}, 0x1000"))
        out.append(_line(f"csrs 0x{dcsr_addr:x}, {scratch.abi}"))
    return out


def _gen_single_step_logic(cfg: Config, scratch: RiscvReg,
                           iterations: int = 16) -> list[str]:
    """Emit the DCSR.step toggle + DSCRATCH0 counter loop.

    See SV ``gen_single_step_logic`` (riscv_debug_rom_gen.sv:178). The
    sequence:

      1. Save scratch into DSCRATCH1.
      2. Read DCSR; if .step is already 1, decrement DSCRATCH0 and
         optionally clear .step when the counter hits 0.
      3. If .step is 0, set it and load DSCRATCH0 with `iterations`.
      4. Restore scratch from DSCRATCH1.
    """
    dcsr = PrivilegedReg.DCSR.value
    dscratch0 = PrivilegedReg.DSCRATCH0.value
    dscratch1 = PrivilegedReg.DSCRATCH1.value
    return [
        _line(f"csrw 0x{dscratch1:x}, {scratch.abi}"),
        _line(f"csrr {scratch.abi}, 0x{dcsr:x}"),
        _line(f"andi {scratch.abi}, {scratch.abi}, 4"),
        _line(f"beqz {scratch.abi}, 1f"),
        # step==1: decrement counter or clear .step.
        _line(f"csrr {scratch.abi}, 0x{dscratch0:x}"),
        _line(f"bgtz {scratch.abi}, 2f"),
        _line(f"csrc 0x{dcsr:x}, 0x4"),
        _line("beqz x0, 3f"),
        # 1: step==0 → enable stepping for `iterations` more entries.
        _line(f"1: csrs 0x{dcsr:x}, 0x4"),
        _line(f"li {scratch.abi}, {iterations}"),
        _line(f"csrw 0x{dscratch0:x}, {scratch.abi}"),
        _line("beqz x0, 3f"),
        # 2: decrement DSCRATCH0.
        _line(f"2: csrr {scratch.abi}, 0x{dscratch0:x}"),
        _line(f"addi {scratch.abi}, {scratch.abi}, -1"),
        _line(f"csrw 0x{dscratch0:x}, {scratch.abi}"),
        # 3: restore scratch from DSCRATCH1.
        _line(f"3: csrr {scratch.abi}, 0x{dscratch1:x}"),
    ]


# ---------------------------------------------------------------------------
# Top-level emit
# ---------------------------------------------------------------------------


def gen_debug_rom_section(cfg: Config, hart: int = 0) -> list[str]:
    """Emit the ``h<N>_debug_rom:`` section (and ``h<N>_debug_end``).

    Returns an empty list when ``cfg.gen_debug_section`` is False — the
    ROM is opt-in. When False the core still has somewhere to land
    (Spike provides a default ROM); we just don't add ours.

    When True the section ends in ``dret`` so execution returns to
    DPC on debug exit.
    """
    if not cfg.gen_debug_section:
        return []

    prefix = hart_prefix(hart, cfg.num_of_harts)
    scratch = cfg.scratch_reg
    gpr0 = cfg.gpr[0]

    out: list[str] = ["", f"{prefix}debug_rom:"]

    if cfg.set_dcsr_ebreak:
        out.extend(_gen_dcsr_ebreak(cfg, scratch))

    if cfg.enable_debug_single_step:
        out.extend(_gen_single_step_logic(cfg, scratch,
                                          cfg.single_step_iterations))

    out.extend(_gen_dpc_update(scratch, gpr0))

    # Trampoline to the debug_end label so the debug body lives
    # separately from the entry sequence. Useful for users who want to
    # interleave random instructions in debug_main without disturbing
    # the entry preamble.
    out.append(_line(f"la {scratch.abi}, {prefix}debug_end"))
    out.append(_line(f"jalr x0, {scratch.abi}, 0"))

    out.append("")
    out.append(f"{prefix}debug_end:")
    out.append(_line("dret"))
    return out


def gen_debug_exception_handler(cfg: Config, hart: int = 0) -> list[str]:
    """Emit the ``h<N>_debug_exception:`` stub.

    Mirrors SV's empty handler — just dret. Real verification cores
    plug in an actual handler via cfg.gen_debug_section + override.
    """
    if not cfg.gen_debug_section:
        return []
    prefix = hart_prefix(hart, cfg.num_of_harts)
    return ["", f"{prefix}debug_exception:", _line("dret")]
