"""Tests for rvgen.isa.base — Instr base class behavior."""

from __future__ import annotations

import pytest

from rvgen.isa import rv32i  # noqa: F401 — registers RV32I instrs
from rvgen.isa.base import Instr, copy_instr
from rvgen.isa.enums import (
    ImmType,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.factory import get_instr


# ---------------------------------------------------------------------------
# set_rand_mode / set_imm_len
# ---------------------------------------------------------------------------


def test_r_format_disables_has_imm():
    add = get_instr(RiscvInstrName.ADD)
    assert add.has_rs1 is True
    assert add.has_rs2 is True
    assert add.has_rd is True
    assert add.has_imm is False


def test_i_format_disables_has_rs2():
    addi = get_instr(RiscvInstrName.ADDI)
    assert addi.has_rs1 is True
    assert addi.has_rs2 is False
    assert addi.has_rd is True
    assert addi.has_imm is True


def test_s_format_disables_has_rd():
    sw = get_instr(RiscvInstrName.SW)
    assert sw.has_rs1 is True
    assert sw.has_rs2 is True
    assert sw.has_rd is False


def test_b_format_disables_has_rd():
    beq = get_instr(RiscvInstrName.BEQ)
    assert beq.has_rs1 is True
    assert beq.has_rs2 is True
    assert beq.has_rd is False


def test_u_format_disables_both_rs():
    lui = get_instr(RiscvInstrName.LUI)
    assert lui.has_rs1 is False
    assert lui.has_rs2 is False
    assert lui.has_rd is True


def test_j_format_disables_both_rs():
    jal = get_instr(RiscvInstrName.JAL)
    assert jal.has_rs1 is False
    assert jal.has_rs2 is False
    assert jal.has_rd is True


def test_imm_len_u_and_j():
    assert get_instr(RiscvInstrName.LUI).imm_len == 20
    assert get_instr(RiscvInstrName.JAL).imm_len == 20


def test_imm_len_i_s_b_default_is_12():
    assert get_instr(RiscvInstrName.ADDI).imm_len == 12
    assert get_instr(RiscvInstrName.SW).imm_len == 12
    assert get_instr(RiscvInstrName.BEQ).imm_len == 12


def test_imm_len_uimm_is_5():
    # SLLI has imm_type=IMM actually in SV, but its constraint enforces 5-bit
    # on RV32 via imm_c; the enum imm_type remains IMM. Our default matches.
    # The real UIMM users are CSRRWI and friends (handled by CsrInstr) and
    # LUI/AUIPC (but those are U_FORMAT so they get 20 bits).
    # Check with CSRRWI directly.
    csrrwi = get_instr(RiscvInstrName.CSRRWI)
    assert csrrwi.imm_type == ImmType.UIMM
    assert csrrwi.imm_len == 5


# ---------------------------------------------------------------------------
# Class-level attributes
# ---------------------------------------------------------------------------


def test_class_level_attrs_persist():
    add = get_instr(RiscvInstrName.ADD)
    assert add.format == RiscvInstrFormat.R_FORMAT
    assert add.category == RiscvInstrCategory.ARITHMETIC
    assert add.group == RiscvInstrGroup.RV32I
    assert add.imm_type == ImmType.IMM


# ---------------------------------------------------------------------------
# extend_imm / update_imm_str
# ---------------------------------------------------------------------------


def test_extend_imm_i_format_positive():
    addi = get_instr(RiscvInstrName.ADDI)
    addi.imm = 42
    addi.post_randomize()
    assert addi.imm == 42
    assert addi.imm_str == "42"


def test_extend_imm_i_format_negative():
    # A 12-bit value with the high bit set (0x800 = -2048) should sign-extend.
    addi = get_instr(RiscvInstrName.ADDI)
    addi.imm = 0x800
    addi.post_randomize()
    assert addi.imm == 0xFFFFF800  # 32-bit sign-extended
    assert addi.imm_str == "-2048"


def test_extend_imm_u_format_no_sign():
    lui = get_instr(RiscvInstrName.LUI)
    lui.imm = 0xFFFFF  # 20-bit max
    lui.post_randomize()
    # U_FORMAT does not sign-extend (the shift into bits[31:12] is done at
    # encoding time, not in extend_imm).
    assert lui.imm == 0xFFFFF
    assert lui.imm_str == "1048575"


def test_extend_imm_s_format_negative():
    sw = get_instr(RiscvInstrName.SW)
    sw.imm = 0xFFF  # max 12-bit "all 1s" → -1
    sw.post_randomize()
    assert sw.imm == 0xFFFFFFFF
    assert sw.imm_str == "-1"


# ---------------------------------------------------------------------------
# Mnemonic rendering (get_instr_name underscore → dot)
# ---------------------------------------------------------------------------


def test_get_instr_name_simple():
    assert get_instr(RiscvInstrName.LW).get_instr_name() == "LW"
    assert get_instr(RiscvInstrName.ADD).get_instr_name() == "ADD"


def test_get_instr_name_with_dot():
    assert get_instr(RiscvInstrName.FENCE_I).get_instr_name() == "FENCE.I"
    assert get_instr(RiscvInstrName.SFENCE_VMA).get_instr_name() == "SFENCE.VMA"


# ---------------------------------------------------------------------------
# convert2asm — one test per format + special cases
# ---------------------------------------------------------------------------


def _make_i(name, rd, rs1, imm):
    i = get_instr(name)
    i.rd, i.rs1, i.imm = rd, rs1, imm
    i.post_randomize()
    return i


def _make_r(name, rd, rs1, rs2):
    i = get_instr(name)
    i.rd, i.rs1, i.rs2 = rd, rs1, rs2
    i.post_randomize()
    return i


def _make_s(name, rs1, rs2, imm):
    i = get_instr(name)
    i.rs1, i.rs2, i.imm = rs1, rs2, imm
    i.post_randomize()
    return i


def _make_b(name, rs1, rs2, imm):
    return _make_s(name, rs1, rs2, imm)  # same attrs


def _make_u(name, rd, imm):
    i = get_instr(name)
    i.rd, i.imm = rd, imm
    i.post_randomize()
    return i


def _make_j(name, rd, imm):
    return _make_u(name, rd, imm)


def test_asm_lw_loadstore_pseudo():
    i = _make_i(RiscvInstrName.LW, RiscvReg.A0, RiscvReg.SP, 4)
    # 13-char padded mnemonic then "a0, 4(sp)".
    assert i.convert2asm() == "lw           a0, 4(sp)"


def test_asm_sw_store_pseudo():
    i = _make_s(RiscvInstrName.SW, RiscvReg.SP, RiscvReg.A0, 8)
    assert i.convert2asm() == "sw           a0, 8(sp)"


def test_asm_addi_i_format():
    i = _make_i(RiscvInstrName.ADDI, RiscvReg.A0, RiscvReg.ZERO, 42)
    assert i.convert2asm() == "addi         a0, zero, 42"


def test_asm_add_r_format():
    i = _make_r(RiscvInstrName.ADD, RiscvReg.A0, RiscvReg.A1, RiscvReg.A2)
    assert i.convert2asm() == "add          a0, a1, a2"


def test_asm_lui_u_format():
    i = _make_u(RiscvInstrName.LUI, RiscvReg.A0, 0x12345)
    assert i.convert2asm() == "lui          a0, 74565"


def test_asm_jal_j_format():
    i = _make_j(RiscvInstrName.JAL, RiscvReg.RA, 8)
    assert i.convert2asm() == "jal          ra, 8"


def test_asm_beq_b_format():
    i = _make_b(RiscvInstrName.BEQ, RiscvReg.A0, RiscvReg.A1, 12)
    assert i.convert2asm() == "beq          a0, a1, 12"


def test_asm_nop_bare_mnemonic():
    assert get_instr(RiscvInstrName.NOP).convert2asm() == "nop"


def test_asm_wfi_bare_mnemonic():
    assert get_instr(RiscvInstrName.WFI).convert2asm() == "wfi"


def test_asm_fence_bare_mnemonic():
    assert get_instr(RiscvInstrName.FENCE).convert2asm() == "fence"
    assert get_instr(RiscvInstrName.FENCE_I).convert2asm() == "fence.i"


def test_asm_sfence_vma():
    assert get_instr(RiscvInstrName.SFENCE_VMA).convert2asm() == "sfence.vma x0, x0"


def test_asm_ebreak_special_encoding():
    assert get_instr(RiscvInstrName.EBREAK).convert2asm() == ".4byte 0x00100073 # ebreak"


def test_asm_ecall_padded_mnemonic():
    # SYSTEM non-EBREAK instructions keep the 13-char padded mnemonic (no
    # operands). Verify.
    asm = get_instr(RiscvInstrName.ECALL).convert2asm()
    assert asm == "ecall        "
    assert len(asm) == 13


def test_asm_mret_padded_mnemonic():
    assert get_instr(RiscvInstrName.MRET).convert2asm() == "mret         "


def test_asm_with_comment():
    i = _make_r(RiscvInstrName.ADD, RiscvReg.A0, RiscvReg.A1, RiscvReg.A2)
    i.comment = "first addition"
    assert i.convert2asm() == "add          a0, a1, a2 #first addition"


# ---------------------------------------------------------------------------
# convert2bin — spot-check known encodings against spec
# ---------------------------------------------------------------------------


def test_bin_nop_is_addi_x0_x0_0():
    nop = get_instr(RiscvInstrName.NOP)
    nop.rd = RiscvReg.ZERO
    nop.rs1 = RiscvReg.ZERO
    nop.imm = 0
    nop.post_randomize()
    # addi x0, x0, 0 = 0x00000013
    assert nop.convert2bin() == "00000013"


def test_bin_addi():
    # addi a0, sp, 4 → imm=4, rs1=sp(2), rd=a0(10), funct3=0, opcode=0b0010011
    # encoding: 0x00410513
    i = _make_i(RiscvInstrName.ADDI, RiscvReg.A0, RiscvReg.SP, 4)
    assert i.convert2bin() == "00410513"


def test_bin_lw():
    # lw a0, 4(sp) → 0x00412503
    i = _make_i(RiscvInstrName.LW, RiscvReg.A0, RiscvReg.SP, 4)
    assert i.convert2bin() == "00412503"


def test_bin_sw():
    # sw a0, 8(sp) → imm[11:5]=0, rs2=a0(10), rs1=sp(2), func3=2, imm[4:0]=8, opc=0x23
    # encoding: 0x00a12423
    i = _make_s(RiscvInstrName.SW, RiscvReg.SP, RiscvReg.A0, 8)
    assert i.convert2bin() == "00a12423"


def test_bin_add():
    # add x1, x2, x3 → 0x003100B3
    i = _make_r(RiscvInstrName.ADD, RiscvReg.RA, RiscvReg.SP, RiscvReg.GP)
    assert i.convert2bin() == "003100b3"


def test_bin_sub():
    # sub x1, x2, x3 → 0x403100B3 (func7 = 0b0100000)
    i = _make_r(RiscvInstrName.SUB, RiscvReg.RA, RiscvReg.SP, RiscvReg.GP)
    assert i.convert2bin() == "403100b3"


def test_bin_lui():
    # lui a0, 0x12345 → 0x12345537 (imm[31:12]=0x12345, rd=10, opc=0x37)
    i = _make_u(RiscvInstrName.LUI, RiscvReg.A0, 0x12345)
    assert i.convert2bin() == "12345537"


def test_bin_auipc():
    # auipc a0, 0x12345 → 0x12345517 (opc=0x17)
    i = _make_u(RiscvInstrName.AUIPC, RiscvReg.A0, 0x12345)
    assert i.convert2bin() == "12345517"


def test_bin_jal():
    # jal ra, 8 → encoding with imm=8 (sign-extended), rd=ra(1), opc=0x6F
    # imm[20]=0, imm[10:1]=0b0000000100, imm[11]=0, imm[19:12]=0
    # encoding: 0x008000EF
    i = _make_j(RiscvInstrName.JAL, RiscvReg.RA, 8)
    assert i.convert2bin() == "008000ef"


def test_bin_beq():
    # beq a0, a1, 8 → imm[12]=0, imm[10:5]=0, imm[4:1]=4, imm[11]=0
    # rs2=a1(11), rs1=a0(10), func3=0, opc=0x63
    # encoding: 0x00b50463
    i = _make_b(RiscvInstrName.BEQ, RiscvReg.A0, RiscvReg.A1, 8)
    assert i.convert2bin() == "00b50463"


def test_bin_ecall():
    # ecall: bits = {func7=0, 18'b0, opcode=0x73} = 0x00000073
    assert get_instr(RiscvInstrName.ECALL).convert2bin() == "00000073"


def test_bin_ebreak():
    # ebreak: func7=0, imm=1, rs1=0, func3=0, rd=0, opc=0x73
    # = {7'b0, 5'd1, 13'b0, 0x73} encoding → 0x00100073
    assert get_instr(RiscvInstrName.EBREAK).convert2bin() == "00100073"


def test_bin_mret():
    # mret: func7=0b0011000, 5'b00010, 13'b0, 0x73 → 0x30200073
    assert get_instr(RiscvInstrName.MRET).convert2bin() == "30200073"


def test_bin_sret():
    # sret: func7=0b0001000, 5'b00010, 13'b0, 0x73 → 0x10200073
    assert get_instr(RiscvInstrName.SRET).convert2bin() == "10200073"


def test_bin_dret():
    # dret: func7=0b0111101, 5'b10010, 13'b0, 0x73 → 0x7B200073
    assert get_instr(RiscvInstrName.DRET).convert2bin() == "7b200073"


def test_bin_wfi():
    # wfi: func7=0b0001000, 5'b00101, 13'b0, 0x73 → 0x10500073
    assert get_instr(RiscvInstrName.WFI).convert2bin() == "10500073"


def test_bin_fence():
    # fence: 17'b0, func3=0, 5'b0, opc=0x0F → 0x0000000F
    assert get_instr(RiscvInstrName.FENCE).convert2bin() == "0000000f"


def test_bin_fence_i():
    # fence.i: 17'b0, func3=1, 5'b0, opc=0x0F → 0x0000100F
    assert get_instr(RiscvInstrName.FENCE_I).convert2bin() == "0000100f"


def test_bin_sfence_vma():
    # sfence.vma: func7=0b0001001, 18'b0, 0x73 → 0x12000073
    assert get_instr(RiscvInstrName.SFENCE_VMA).convert2bin() == "12000073"


def test_bin_slli_shift_amount():
    # slli a0, a1, 3 → imm=3, rs1=a1(11), rd=a0(10), func3=1, func7=0, opc=0x13
    # encoding: 0x00359513
    i = _make_i(RiscvInstrName.SLLI, RiscvReg.A0, RiscvReg.A1, 3)
    assert i.convert2bin() == "00359513"


def test_bin_srai_has_func7_bit():
    # srai a0, a1, 3 → func7=0b0100000 → encoding: 0x4035D513
    i = _make_i(RiscvInstrName.SRAI, RiscvReg.A0, RiscvReg.A1, 3)
    assert i.convert2bin() == "4035d513"


# ---------------------------------------------------------------------------
# copy_instr
# ---------------------------------------------------------------------------


def test_copy_instr_independent():
    original = _make_i(RiscvInstrName.ADDI, RiscvReg.A0, RiscvReg.SP, 42)
    dup = copy_instr(original)
    assert dup.rd == RiscvReg.A0
    assert dup.rs1 == RiscvReg.SP
    assert dup.imm == 42
    # Mutating the copy must not affect the original.
    dup.rd = RiscvReg.T1
    assert original.rd == RiscvReg.A0


def test_copy_instr_preserves_class():
    original = get_instr(RiscvInstrName.CSRRW)
    dup = copy_instr(original)
    assert type(dup) is type(original)
