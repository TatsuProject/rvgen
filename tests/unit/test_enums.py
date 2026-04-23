"""Sanity checks that our Python enums mirror riscv-dv's SV source exactly.

If these tests fail after editing ``isa/enums.py``, cross-check the SV source
at ``~/Desktop/verif_env_tatsu/riscv-dv/src/riscv_instr_pkg.sv`` —
Phase-1 parity depends on declaration order staying identical.
"""

from __future__ import annotations

from rvgen.isa import enums


# ---------------------------------------------------------------------------
# Fixed-value enums (the SV source pins specific integer values)
# ---------------------------------------------------------------------------


def test_satp_mode_values():
    assert enums.SatpMode.BARE == 0
    assert enums.SatpMode.SV32 == 1
    assert enums.SatpMode.SV39 == 8
    assert enums.SatpMode.SV48 == 9
    assert enums.SatpMode.SV57 == 10
    assert enums.SatpMode.SV64 == 11


def test_rounding_mode_values():
    assert enums.FRoundingMode.RNE == 0
    assert enums.FRoundingMode.RTZ == 1
    assert enums.FRoundingMode.RDN == 2
    assert enums.FRoundingMode.RUP == 3
    assert enums.FRoundingMode.RMM == 4


def test_mtvec_mode_values():
    assert enums.MtvecMode.DIRECT == 0
    assert enums.MtvecMode.VECTORED == 1


def test_privileged_mode_values():
    assert enums.PrivilegedMode.USER_MODE == 0
    assert enums.PrivilegedMode.SUPERVISOR_MODE == 1
    assert enums.PrivilegedMode.RESERVED_MODE == 2
    assert enums.PrivilegedMode.MACHINE_MODE == 3


def test_privileged_level_values():
    assert enums.PrivilegedLevel.U_LEVEL == 0
    assert enums.PrivilegedLevel.S_LEVEL == 1
    assert enums.PrivilegedLevel.M_LEVEL == 3


def test_pmp_addr_mode_values():
    assert enums.PmpAddrMode.OFF == 0
    assert enums.PmpAddrMode.TOR == 1
    assert enums.PmpAddrMode.NA4 == 2
    assert enums.PmpAddrMode.NAPOT == 3


def test_pte_permission_values():
    # Critical: the xwr encoding is checked bit-for-bit against page-table PTEs.
    assert enums.PtePermission.NEXT_LEVEL_PAGE == 0b000
    assert enums.PtePermission.READ_ONLY_PAGE == 0b001
    assert enums.PtePermission.READ_WRITE_PAGE == 0b011
    assert enums.PtePermission.EXECUTE_ONLY_PAGE == 0b100
    assert enums.PtePermission.READ_EXECUTE_PAGE == 0b101
    assert enums.PtePermission.R_W_EXECUTE_PAGE == 0b111


def test_interrupt_and_exception_causes():
    assert enums.InterruptCause.M_EXTERNAL_INTR == 0xB
    assert enums.InterruptCause.M_TIMER_INTR == 0x7
    assert enums.ExceptionCause.ECALL_UMODE == 0x8
    assert enums.ExceptionCause.ECALL_MMODE == 0xB
    assert enums.ExceptionCause.STORE_AMO_PAGE_FAULT == 0xF
    assert enums.ExceptionCause.INSTRUCTION_PAGE_FAULT == 0xC


# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------


def test_gpr_abi_names():
    assert enums.RiscvReg.ZERO.value == 0
    assert enums.RiscvReg.ZERO.abi == "zero"
    assert enums.RiscvReg.RA.value == 1
    assert enums.RiscvReg.RA.abi == "ra"
    assert enums.RiscvReg.SP.value == 2
    assert enums.RiscvReg.GP.value == 3
    assert enums.RiscvReg.TP.value == 4
    assert enums.RiscvReg.A0.value == 10
    assert enums.RiscvReg.A0.abi == "a0"
    assert enums.RiscvReg.T6.value == 31


def test_fpr_abi_names():
    assert enums.RiscvFpr.FT0.value == 0
    assert enums.RiscvFpr.FT0.abi == "ft0"
    assert enums.RiscvFpr.FS0.value == 8
    assert enums.RiscvFpr.FA0.value == 10
    assert enums.RiscvFpr.FA0.abi == "fa0"
    assert enums.RiscvFpr.FT11.value == 31


def test_vreg_order():
    assert enums.RiscvVreg.V0.value == 0
    assert enums.RiscvVreg.V31.value == 31
    assert enums.RiscvVreg.V10.abi == "v10"


def test_compressed_gpr_set():
    # Must be x8..x15 in that exact order (compressed instruction format bits).
    got = tuple(r.value for r in enums.COMPRESSED_GPR)
    assert got == (8, 9, 10, 11, 12, 13, 14, 15)


# ---------------------------------------------------------------------------
# Instruction group / category / format
# ---------------------------------------------------------------------------


def test_instr_group_order():
    # The first 9 groups + V + Zb* anchor the testlist import chain.
    assert enums.RiscvInstrGroup.RV32I == 0
    assert enums.RiscvInstrGroup.RV64I == 1
    assert enums.RiscvInstrGroup.RV32M == 2
    assert enums.RiscvInstrGroup.RV32A == 4
    assert enums.RiscvInstrGroup.RV32F == 6
    assert enums.RiscvInstrGroup.RV32D == 9
    assert enums.RiscvInstrGroup.RV32C == 12
    assert enums.RiscvInstrGroup.RVV == 16
    assert enums.RiscvInstrGroup.RV32ZBA == 18
    assert enums.RiscvInstrGroup.RV64X == 28


def test_instr_format_first_members_are_zero_based():
    assert enums.RiscvInstrFormat.J_FORMAT == 0
    assert enums.RiscvInstrFormat.U_FORMAT == 1
    assert enums.RiscvInstrFormat.R4_FORMAT == 6
    assert enums.RiscvInstrFormat.CI_FORMAT == 7
    assert enums.RiscvInstrFormat.VSET_FORMAT == 16
    assert enums.RiscvInstrFormat.VAMO_FORMAT == 25


def test_instr_category_order_with_amo_last():
    assert enums.RiscvInstrCategory.LOAD == 0
    assert enums.RiscvInstrCategory.STORE == 1
    # AMO is declared last in SV (line 744); we keep the same for parity.
    assert enums.RiscvInstrCategory.AMO == max(c.value for c in enums.RiscvInstrCategory)


def test_va_variant_order():
    # va_variant_t: VV, VI, VX, VF, WV, WI, WX, VVM, VIM, VXM, VFM, VS, VM
    expected = ["VV", "VI", "VX", "VF", "WV", "WI", "WX", "VVM", "VIM", "VXM", "VFM", "VS", "VM"]
    got = [v.name for v in enums.VaVariant]
    assert got == expected


# ---------------------------------------------------------------------------
# Instruction name enum — spot-check critical opcodes and that INVALID_INSTR
# is the final member.
# ---------------------------------------------------------------------------


def test_rv32i_instr_names_present():
    names = {n.name for n in enums.RiscvInstrName}
    for required in (
        "LUI", "AUIPC", "JAL", "JALR", "BEQ", "BNE", "BLT", "BGE", "BLTU", "BGEU",
        "LB", "LH", "LW", "LBU", "LHU", "SB", "SH", "SW",
        "ADDI", "SLTI", "SLTIU", "XORI", "ORI", "ANDI", "SLLI", "SRLI", "SRAI",
        "ADD", "SUB", "SLL", "SLT", "SLTU", "XOR", "SRL", "SRA", "OR", "AND",
        "NOP", "FENCE", "FENCE_I", "ECALL", "EBREAK",
        "CSRRW", "CSRRS", "CSRRC", "CSRRWI", "CSRRSI", "CSRRCI",
    ):
        assert required in names


def test_lui_is_first_and_invalid_is_last():
    members = list(enums.RiscvInstrName)
    assert members[0].name == "LUI"
    assert members[-1].name == "INVALID_INSTR"


def test_fp_amo_vector_names_present():
    names = {n.name for n in enums.RiscvInstrName}
    for required in (
        "FLW", "FSW", "FMADD_S", "FADD_S", "FCVT_W_S", "FMV_W_X",  # RV32F
        "FLD", "FSD", "FCVT_D_S",                                   # RV32D
        "LR_W", "SC_W", "AMOSWAP_W", "AMOMAXU_W",                   # RV32A
        "LR_D", "AMOMAXU_D",                                        # RV64A
        "C_ADDI4SPN", "C_LI", "C_JALR", "C_EBREAK",                 # RV32C
        "VSETVLI", "VADD", "VLE_V", "VAMOSWAPE_V",                  # V
        "SH1ADD", "CLZ", "CLMUL", "BCLR",                           # Zb*
        "MRET", "SRET", "URET", "WFI", "SFENCE_VMA",                # privileged
    ):
        assert required in names


# ---------------------------------------------------------------------------
# CSR addresses — hand-pinned constants from the RISC-V spec.
# ---------------------------------------------------------------------------


def test_csr_addresses():
    assert enums.PrivilegedReg.MSTATUS == 0x300
    assert enums.PrivilegedReg.MISA == 0x301
    assert enums.PrivilegedReg.MIE == 0x304
    assert enums.PrivilegedReg.MTVEC == 0x305
    assert enums.PrivilegedReg.MSCRATCH == 0x340
    assert enums.PrivilegedReg.MEPC == 0x341
    assert enums.PrivilegedReg.MCAUSE == 0x342
    assert enums.PrivilegedReg.MTVAL == 0x343
    assert enums.PrivilegedReg.MIP == 0x344
    assert enums.PrivilegedReg.MHARTID == 0xF14
    assert enums.PrivilegedReg.SATP == 0x180
    assert enums.PrivilegedReg.FCSR == 0x003
    assert enums.PrivilegedReg.VSTART == 0x008
    assert enums.PrivilegedReg.VL == 0xC20
    assert enums.PrivilegedReg.VTYPE == 0xC21
    assert enums.PrivilegedReg.DCSR == 0x7B0
    assert enums.PrivilegedReg.DPC == 0x7B1


def test_csr_unique_when_same_address_avoided():
    # Some CSRs share an address (PMPADDR16 at 0x4C0 etc.); that is faithful to
    # the SV enum. Just make sure all names are distinct.
    names = [r.name for r in enums.PrivilegedReg]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Parameters / masks
# ---------------------------------------------------------------------------


def test_fixed_parameters():
    assert enums.MAX_INSTR_STR_LEN == 13
    assert enums.LABEL_STR_LEN == 18
    assert enums.MAX_CALLSTACK_DEPTH == 20
    assert enums.MAX_SUB_PROGRAM_CNT == 20
    assert enums.MAX_CALL_PER_FUNC == 5
    assert enums.MAX_USED_VADDR_BITS == 30


def test_bit_masks():
    assert enums.MPRV_BIT_MASK == 1 << 17
    assert enums.SUM_BIT_MASK == 1 << 18
    assert enums.MPP_BIT_MASK == 0b11 << 11


def test_default_include_csr_write():
    assert enums.DEFAULT_INCLUDE_CSR_WRITE == (enums.PrivilegedReg.MSCRATCH,)
