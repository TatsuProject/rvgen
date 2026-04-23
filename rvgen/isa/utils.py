"""Helper functions ported from riscv_instr_pkg.sv (format_string, format_data,
hart_prefix, get_label, push/pop_gpr_to/from_kernel_stack).

Every function here is a direct translation of SystemVerilog behavior — when in
doubt, prefer what the SV source does over what seems like "cleaner" Python.
"""

from __future__ import annotations

from typing import Iterable

from rvgen.isa.enums import (
    MAX_USED_VADDR_BITS,
    PrivilegedReg,
    RiscvReg,
    SatpMode,
)


# ---------------------------------------------------------------------------
# String / label formatting
# ---------------------------------------------------------------------------


def format_string(s: str, length: int = 10) -> str:
    """Right-pad ``s`` with spaces to exactly ``length`` characters.

    Matches ``format_string`` in ``riscv_instr_pkg.sv`` (line 1336). If the
    string is already at least ``length`` characters, it is returned as-is
    (SV: the function returns ``str`` when ``len < str.len()``).
    """
    if length < len(s):
        return s
    return s + " " * (length - len(s))


def format_data(data: Iterable[int], byte_per_group: int = 4) -> str:
    """Format a byte sequence as ``0x<aa><bb>..., 0x<..>...`` grouping rows.

    Port of ``format_data`` in ``riscv_instr_pkg.sv`` (line 1346). Groups of
    ``byte_per_group`` bytes are separated by ``", 0x"``; the leading ``0x``
    is emitted once. Matches SV behavior on the edge case where the final
    byte sits exactly at a group boundary (no trailing separator).
    """
    bytes_list = list(data)
    s = "0x"
    last = len(bytes_list) - 1
    for i, b in enumerate(bytes_list):
        if i != 0 and i != last and i % byte_per_group == 0:
            s += ", 0x"
        s += f"{b & 0xFF:02x}"
    return s


def hart_prefix(hart: int = 0, num_harts: int = 1) -> str:
    """Return ``""`` if single-hart, else ``"h<hart>_"`` (riscv_instr_pkg.sv:1268)."""
    if num_harts <= 1:
        return ""
    return f"h{hart}_"


def get_label(label: str, hart: int = 0, num_harts: int = 1) -> str:
    """Prefix ``label`` with the hart tag (riscv_instr_pkg.sv:1276)."""
    return hart_prefix(hart, num_harts) + label


# ---------------------------------------------------------------------------
# Assembly line helpers
# ---------------------------------------------------------------------------


from rvgen.isa.enums import LABEL_STR_LEN


def indent_line(body: str, label: str | None = None) -> str:
    """Produce a final ``.S`` line with the 18-char label column.

    - If ``label`` is ``None`` or empty: ``" " * 18 + body``.
    - Else: ``format_string(f"{label}:", 18) + body``.

    This matches ``riscv_instr_sequence.generate_instr_stream`` at line 262–266.
    """
    if not label:
        prefix = " " * LABEL_STR_LEN
    else:
        prefix = format_string(f"{label}:", LABEL_STR_LEN)
    return prefix + body


# ---------------------------------------------------------------------------
# Kernel-stack push/pop (riscv_instr_pkg.sv:1374-1440)
# ---------------------------------------------------------------------------


def push_gpr_to_kernel_stack(
    status: PrivilegedReg,
    scratch: PrivilegedReg,
    mprv: bool,
    sp: RiscvReg,
    tp: RiscvReg,
    *,
    xlen: int,
    satp_mode: SatpMode,
    scratch_implemented: bool,
) -> list[str]:
    """Emit the assembly sequence that pushes the user context onto the kernel stack.

    Direct port of SV ``push_gpr_to_kernel_stack`` (riscv_instr_pkg.sv:1374).

    Returned strings are *body-only* — prepend the 18-char label column (or
    ``" " * 18`` for unlabeled lines) via :func:`indent_line`.

    Parameters
    ----------
    status, scratch : PrivilegedReg
        The status CSR (MSTATUS / SSTATUS / USTATUS) and scratch CSR
        (MSCRATCH / SSCRATCH / USCRATCH) for the trap being handled.
    mprv : bool
        Value of ``cfg.mstatus_mprv``.
    sp, tp : RiscvReg
        Config-selected SP and TP registers (may not be the canonical SP/TP if
        the config randomized them).
    xlen : int
        32 or 64.
    satp_mode : SatpMode
        Target's SATP mode; controls whether the virtual-address translation
        guard is emitted.
    scratch_implemented : bool
        Whether ``scratch`` is in the target's implemented CSR list (SV
        ``scratch inside {implemented_csr}``). Gates the USP save/restore.
    """
    store_instr = "sw" if xlen == 32 else "sd"
    out: list[str] = []

    if scratch_implemented:
        # Save USP onto the kernel stack, then move KSP to gpr.SP.
        out.append(f"addi x{tp.value}, x{tp.value}, -4")
        out.append(f"{store_instr}  x{sp.value}, (x{tp.value})")
        out.append(f"add x{sp.value}, x{tp.value}, zero")

    if status == PrivilegedReg.MSTATUS and satp_mode != SatpMode.BARE and mprv:
        # When MPRV is set and MPP ≠ M, memory accesses use MPP's translation,
        # so derive a virtual SP from the current SP.
        out.append(f"csrr x{tp.value}, 0x{status.value:x} // MSTATUS")
        out.append(f"srli x{tp.value}, x{tp.value}, 11")
        out.append(f"andi x{tp.value}, x{tp.value}, 0x3")
        out.append(f"xori x{tp.value}, x{tp.value}, 0x3")
        out.append(f"bnez x{tp.value}, 1f")
        shift = xlen - MAX_USED_VADDR_BITS
        out.append(f"slli x{sp.value}, x{sp.value}, {shift}")
        out.append(f"srli x{sp.value}, x{sp.value}, {shift}")
        out.append("1: nop")

    frame = 32 * (xlen // 8)
    out.append(f"addi x{sp.value}, x{sp.value}, -{frame}")
    for i in range(1, 32):
        out.append(f"{store_instr}  x{i}, {i * (xlen // 8)}(x{sp.value})")
    # Move KSP back to gpr.TP so a nested trap can reuse the pattern.
    out.append(f"add x{tp.value}, x{sp.value}, zero")
    return out


def pop_gpr_from_kernel_stack(
    status: PrivilegedReg,
    scratch: PrivilegedReg,
    mprv: bool,
    sp: RiscvReg,
    tp: RiscvReg,
    *,
    xlen: int,
    satp_mode: SatpMode,
    scratch_implemented: bool,
) -> list[str]:
    """Emit the inverse sequence (riscv_instr_pkg.sv:1419)."""
    del status, mprv, satp_mode  # These are not consumed by the pop sequence
    del scratch  # only its "is implemented" flag matters here
    load_instr = "lw" if xlen == 32 else "ld"
    out: list[str] = []

    out.append(f"add x{sp.value}, x{tp.value}, zero")
    for i in range(1, 32):
        out.append(f"{load_instr}  x{i}, {i * (xlen // 8)}(x{sp.value})")
    out.append(f"addi x{sp.value}, x{sp.value}, {32 * (xlen // 8)}")

    if scratch_implemented:
        out.append(f"add x{tp.value}, x{sp.value}, zero")
        out.append(f"{load_instr}  x{sp.value}, (x{tp.value})")
        out.append(f"addi x{tp.value}, x{tp.value}, 4")
    return out


# ---------------------------------------------------------------------------
# Immediate helpers (used later by the Instr base class)
# ---------------------------------------------------------------------------


def sign_extend(value: int, width: int) -> int:
    """Sign-extend ``value`` treated as a ``width``-bit two's-complement int.

    Returns a Python int in ``[-2**(width-1), 2**(width-1))``.
    """
    mask = (1 << width) - 1
    v = value & mask
    if v & (1 << (width - 1)):
        v -= 1 << width
    return v


def mask_imm(value: int, width: int) -> int:
    """Zero-extended ``width``-bit slice of ``value`` (``value & ((1 << width) - 1)``)."""
    return value & ((1 << width) - 1)
