"""Tests for rvgen.isa.utils."""

from __future__ import annotations

from rvgen.isa.enums import (
    LABEL_STR_LEN,
    PrivilegedReg,
    RiscvReg,
    SatpMode,
)
from rvgen.isa.utils import (
    format_data,
    format_string,
    get_label,
    hart_prefix,
    indent_line,
    mask_imm,
    pop_gpr_from_kernel_stack,
    push_gpr_to_kernel_stack,
    sign_extend,
)


# ---------------------------------------------------------------------------
# format_string
# ---------------------------------------------------------------------------


def test_format_string_pads_to_length():
    assert format_string("abc", 6) == "abc   "
    assert format_string("", 4) == "    "


def test_format_string_returns_str_when_too_long():
    # SV: if(len < str.len()) return str;
    assert format_string("abcdef", 3) == "abcdef"


def test_format_string_exact_length_no_change():
    assert format_string("abc", 3) == "abc"


def test_format_string_default_length_is_10():
    assert format_string("abc") == "abc       "


def test_label_column_uses_18_chars():
    # Critical invariant: every labeled instruction line starts with an
    # 18-char column. "main:" expands to 18 chars total.
    assert len(format_string("main:", LABEL_STR_LEN)) == 18
    assert format_string("main:", LABEL_STR_LEN) == "main:             "


# ---------------------------------------------------------------------------
# format_data
# ---------------------------------------------------------------------------


def test_format_data_single_group():
    assert format_data([0xAA, 0xBB, 0xCC, 0xDD]) == "0xaabbccdd"


def test_format_data_multi_group_separator_between_groups():
    assert format_data([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]) == (
        "0x11223344, 0x55667788"
    )


def test_format_data_group_boundary_on_last_byte_does_not_add_trailing_sep():
    # SV guard: `i != data.size() - 1` — when the last byte is at a group
    # boundary, no separator is inserted before it. Matches SV behavior.
    # 5 bytes, group=4: i=4 is both group-aligned and last → no ", 0x" before it.
    assert format_data([0xAA, 0xBB, 0xCC, 0xDD, 0xEE]) == "0xaabbccddee"


def test_format_data_masks_to_byte():
    # Values beyond 0xFF should be masked (defensive — SV uses 8-bit typed).
    assert format_data([0x1AA, 0xFF]) == "0xaaff"


def test_format_data_custom_group_size():
    assert format_data([0x11, 0x22, 0x33, 0x44, 0x55, 0x66], byte_per_group=2) == (
        "0x1122, 0x3344, 0x5566"
    )


# ---------------------------------------------------------------------------
# hart_prefix / get_label
# ---------------------------------------------------------------------------


def test_hart_prefix_single_hart():
    assert hart_prefix(0, num_harts=1) == ""
    assert hart_prefix(5, num_harts=1) == ""


def test_hart_prefix_multi_hart():
    assert hart_prefix(0, num_harts=2) == "h0_"
    assert hart_prefix(3, num_harts=4) == "h3_"


def test_get_label_prepends_hart():
    assert get_label("main") == "main"
    assert get_label("main", hart=1, num_harts=2) == "h1_main"


# ---------------------------------------------------------------------------
# indent_line
# ---------------------------------------------------------------------------


def test_indent_line_with_no_label():
    assert indent_line("li gp, 1") == " " * 18 + "li gp, 1"


def test_indent_line_with_label():
    assert indent_line("li gp, 1", label="test_done") == (
        format_string("test_done:", 18) + "li gp, 1"
    )
    # Exact golden-file shape: "test_done:        li gp, 1"
    #  (10 chars + 8 spaces = 18)
    assert indent_line("li gp, 1", label="test_done")[:18] == "test_done:        "


# ---------------------------------------------------------------------------
# Immediate helpers
# ---------------------------------------------------------------------------


def test_sign_extend():
    assert sign_extend(0x7FF, 12) == 2047
    assert sign_extend(0x800, 12) == -2048
    assert sign_extend(0xFFFFFFFF, 32) == -1


def test_mask_imm():
    assert mask_imm(0x12345, 12) == 0x345
    assert mask_imm(-1, 12) == 0xFFF


# ---------------------------------------------------------------------------
# push_gpr_to_kernel_stack / pop_gpr_from_kernel_stack
# ---------------------------------------------------------------------------


def test_push_no_scratch_no_mprv_rv32():
    out = push_gpr_to_kernel_stack(
        status=PrivilegedReg.MSTATUS,
        scratch=PrivilegedReg.MSCRATCH,
        mprv=False,
        sp=RiscvReg.SP,
        tp=RiscvReg.TP,
        xlen=32,
        satp_mode=SatpMode.BARE,
        scratch_implemented=False,
    )
    # No USP save (scratch not implemented), no MPRV block, just frame + 31 stores + move.
    # Frame: 32*(32/8) = 128 bytes.
    assert out[0] == "addi x2, x2, -128"
    # First store: sw  x1, 4(x2)
    assert out[1] == "sw  x1, 4(x2)"
    # Final store: sw  x31, 124(x2)
    assert out[31] == "sw  x31, 124(x2)"
    # Then copy KSP back to TP.
    assert out[-1] == "add x4, x2, zero"
    # Length: 1 (alloc) + 31 (stores) + 1 (move) = 33.
    assert len(out) == 33


def test_push_with_scratch_rv64():
    out = push_gpr_to_kernel_stack(
        status=PrivilegedReg.MSTATUS,
        scratch=PrivilegedReg.MSCRATCH,
        mprv=False,
        sp=RiscvReg.SP,
        tp=RiscvReg.TP,
        xlen=64,
        satp_mode=SatpMode.BARE,
        scratch_implemented=True,
    )
    assert out[0] == "addi x4, x4, -4"  # save USP slot (literal -4 per SV)
    assert out[1] == "sd  x2, (x4)"  # store USP to kernel stack
    assert out[2] == "add x2, x4, zero"  # move KSP to gpr.SP
    # Frame 32*8 = 256.
    assert out[3] == "addi x2, x2, -256"
    # First register store: sd  x1, 8(x2)
    assert out[4] == "sd  x1, 8(x2)"
    # Last register store: sd  x31, 248(x2)
    assert out[34] == "sd  x31, 248(x2)"
    assert out[-1] == "add x4, x2, zero"


def test_push_with_mprv_and_sv39_emits_translation_guard():
    out = push_gpr_to_kernel_stack(
        status=PrivilegedReg.MSTATUS,
        scratch=PrivilegedReg.MSCRATCH,
        mprv=True,
        sp=RiscvReg.SP,
        tp=RiscvReg.TP,
        xlen=64,
        satp_mode=SatpMode.SV39,
        scratch_implemented=True,
    )
    # After the USP save (3 lines), we should see the MPRV-guard sequence.
    joined = "\n".join(out)
    assert "csrr x4, 0x300 // MSTATUS" in joined
    assert "srli x4, x4, 11" in joined
    assert "andi x4, x4, 0x3" in joined
    assert "xori x4, x4, 0x3" in joined
    assert "bnez x4, 1f" in joined
    # Shift amount: 64 - 30 = 34.
    assert "slli x2, x2, 34" in joined
    assert "srli x2, x2, 34" in joined
    assert "1: nop" in joined


def test_pop_mirrors_push_frame():
    out = pop_gpr_from_kernel_stack(
        status=PrivilegedReg.MSTATUS,
        scratch=PrivilegedReg.MSCRATCH,
        mprv=False,
        sp=RiscvReg.SP,
        tp=RiscvReg.TP,
        xlen=32,
        satp_mode=SatpMode.BARE,
        scratch_implemented=True,
    )
    assert out[0] == "add x2, x4, zero"  # KSP from TP into gpr.SP
    assert out[1] == "lw  x1, 4(x2)"
    assert out[31] == "lw  x31, 124(x2)"
    # Deallocate frame + USP restore (3 tail lines).
    assert "addi x2, x2, 128" in out
    assert "add x4, x2, zero" in out
    assert "lw  x2, (x4)" in out
    assert "addi x4, x4, 4" in out
