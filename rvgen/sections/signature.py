"""Signature emit templates — port of ``src/riscv_signature_pkg.sv`` and the
``gen_signature_handshake`` helper in ``src/riscv_asm_program_gen.sv``.

Signature protocol recap (SV comments at riscv_signature_pkg.sv:19-34):

- ``CORE_STATUS = 0x00`` — core status update. Emitted word layout:
  ``[bits 12:8] = core_status_t, [bits 7:0] = CORE_STATUS``.
- ``TEST_RESULT = 0x01`` — pass/fail. ``[bit 8] = test_result_t, [7:0] = TEST_RESULT``.
- ``WRITE_GPR   = 0x02`` — initial tag, then 32 stores of x0..x31.
- ``WRITE_CSR   = 0x03`` — ``[bits 19:8] = csr_addr, [7:0] = WRITE_CSR`` then
  ``csrr`` + ``sw`` of the CSR value.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    LABEL_STR_LEN,
    PrivilegedReg,
    RiscvReg,
)


# Signature-type tags (match SV enum bit[7:0] values).
CORE_STATUS = 0x00
TEST_RESULT = 0x01
WRITE_GPR = 0x02
WRITE_CSR = 0x03

# core_status_t values.
INITIALIZED = 0x00
IN_DEBUG_MODE = 0x01
IN_MACHINE_MODE = 0x02
IN_HYPERVISOR_MODE = 0x03
IN_SUPERVISOR_MODE = 0x04
IN_USER_MODE = 0x05
HANDLING_IRQ = 0x06
FINISHED_IRQ = 0x07
HANDLING_EXCEPTION = 0x08
INSTR_FAULT_EXCEPTION = 0x09
ILLEGAL_INSTR_EXCEPTION = 0x0A
LOAD_FAULT_EXCEPTION = 0x0B
STORE_FAULT_EXCEPTION = 0x0C
EBREAK_EXCEPTION = 0x0D

# test_result_t values.
TEST_PASS = 0x00
TEST_FAIL = 0x01


_INDENT = " " * LABEL_STR_LEN


def _line(body: str) -> str:
    return f"{_INDENT}{body}"


def emit_core_status(
    *,
    signature_addr: int,
    core_status: int,
    gpr0: RiscvReg,
    gpr1: RiscvReg,
) -> list[str]:
    """Emit a CORE_STATUS signature write.

    gpr0/gpr1 are the scratch GPRs (``cfg.gpr[0]`` / ``cfg.gpr[1]``); they are
    reserved so the rest of the generator won't touch them.
    """
    return [
        _line(f"li {gpr1.abi}, 0x{signature_addr:x}"),
        _line(f"li {gpr0.abi}, 0x{core_status:x}"),
        _line(f"slli {gpr0.abi}, {gpr0.abi}, 8"),
        _line(f"addi {gpr0.abi}, {gpr0.abi}, 0x{CORE_STATUS:x}"),
        _line(f"sw {gpr0.abi}, 0({gpr1.abi})"),
    ]


def emit_test_result(
    *,
    signature_addr: int,
    test_result: int,
    gpr0: RiscvReg,
    gpr1: RiscvReg,
) -> list[str]:
    """Emit a TEST_RESULT signature write (pass/fail)."""
    return [
        _line(f"li {gpr1.abi}, 0x{signature_addr:x}"),
        _line(f"li {gpr0.abi}, 0x{test_result:x}"),
        _line(f"slli {gpr0.abi}, {gpr0.abi}, 8"),
        _line(f"addi {gpr0.abi}, {gpr0.abi}, 0x{TEST_RESULT:x}"),
        _line(f"sw {gpr0.abi}, 0({gpr1.abi})"),
    ]


def emit_write_gpr(
    *,
    signature_addr: int,
    gpr0: RiscvReg,
    gpr1: RiscvReg,
) -> list[str]:
    """Emit a WRITE_GPR dump (tag + 32 GPR stores)."""
    out = [
        _line(f"li {gpr1.abi}, 0x{signature_addr:x}"),
        _line(f"li {gpr0.abi}, 0x{WRITE_GPR:x}"),
        _line(f"sw {gpr0.abi}, 0({gpr1.abi})"),
    ]
    for i in range(32):
        out.append(_line(f"sw x{i}, 0({gpr1.abi})"))
    return out


def emit_write_csr(
    *,
    signature_addr: int,
    csr: PrivilegedReg,
    gpr0: RiscvReg,
    gpr1: RiscvReg,
) -> list[str]:
    """Emit a WRITE_CSR dump (tag + csr addr, then csrr + store)."""
    return [
        _line(f"li {gpr1.abi}, 0x{signature_addr:x}"),
        _line(f"li {gpr0.abi}, 0x{csr.value:x}"),
        _line(f"slli {gpr0.abi}, {gpr0.abi}, 8"),
        _line(f"addi {gpr0.abi}, {gpr0.abi}, 0x{WRITE_CSR:x}"),
        _line(f"sw {gpr0.abi}, 0({gpr1.abi})"),
        _line(f"csrr {gpr0.abi}, 0x{csr.value:x}"),
        _line(f"sw {gpr0.abi}, 0({gpr1.abi})"),
    ]
