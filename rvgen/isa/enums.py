"""Enums, register types, and parameters ported from riscv-dv's riscv_instr_pkg.sv.

Every enum here mirrors the declaration order and (where specified) explicit
values of the SystemVerilog source at
``~/Desktop/verif_env_tatsu/riscv-dv/src/riscv_instr_pkg.sv``.

If a line below looks wrong, check the SV source first — do not "fix" the enum
order by Python stylistic preference. Phase-1 parity with riscv-dv depends on
this ordering being byte-identical in meaning.
"""

from __future__ import annotations

from enum import IntEnum


# ---------------------------------------------------------------------------
# Formatting / program-generation parameters (riscv_instr_pkg.sv:1319-1331)
# ---------------------------------------------------------------------------

IMM25_WIDTH = 25
IMM12_WIDTH = 12
INSTR_WIDTH = 32
DATA_WIDTH = 32

#: Mnemonic column width used by convert2asm (format_string pad target).
MAX_INSTR_STR_LEN = 13
#: Label column width (total chars incl. trailing spaces). Blank label lines
#: emit LABEL_STR_LEN spaces.
LABEL_STR_LEN = 18

MAX_CALLSTACK_DEPTH = 20
MAX_SUB_PROGRAM_CNT = 20
MAX_CALL_PER_FUNC = 5

MAX_USED_VADDR_BITS = 30
SINGLE_PRECISION_FRACTION_BITS = 23
DOUBLE_PRECISION_FRACTION_BITS = 52

# xSTATUS bit masks (riscv_instr_pkg.sv:1315-1317).
MPRV_BIT_MASK = 0x1 << 17
SUM_BIT_MASK = 0x1 << 18
MPP_BIT_MASK = 0x3 << 11


# ---------------------------------------------------------------------------
# Basic enums (SV lines ~40-144)
# ---------------------------------------------------------------------------


class VregInitMethod(IntEnum):
    """How vector registers are initialized at boot (riscv_instr_pkg.sv:40-44)."""

    SAME_VALUES_ALL_ELEMS = 0
    RANDOM_VALUES_VMV = 1
    RANDOM_VALUES_LOAD = 2


class SatpMode(IntEnum):
    """Virtual memory translation mode (riscv_instr_pkg.sv:46-52)."""

    BARE = 0b0000
    SV32 = 0b0001
    SV39 = 0b1000
    SV48 = 0b1001
    SV57 = 0b1010
    SV64 = 0b1011


class FRoundingMode(IntEnum):
    """FP static rounding mode (riscv_instr_pkg.sv:55-61)."""

    RNE = 0b000
    RTZ = 0b001
    RDN = 0b010
    RUP = 0b011
    RMM = 0b100


class MtvecMode(IntEnum):
    """MTVEC mode (riscv_instr_pkg.sv:63-66)."""

    DIRECT = 0b00
    VECTORED = 0b01


class ImmType(IntEnum):
    """Immediate type (riscv_instr_pkg.sv:68-73)."""

    IMM = 0
    UIMM = 1
    NZUIMM = 2
    NZIMM = 3


class PrivilegedMode(IntEnum):
    """Privileged-mode values for MSTATUS.MPP / SPP (riscv_instr_pkg.sv:76-81)."""

    USER_MODE = 0b00
    SUPERVISOR_MODE = 0b01
    RESERVED_MODE = 0b10
    MACHINE_MODE = 0b11


class RiscvInstrGroup(IntEnum):
    """ISA extension groups (riscv_instr_pkg.sv:83-112)."""

    RV32I = 0
    RV64I = 1
    RV32M = 2
    RV64M = 3
    RV32A = 4
    RV64A = 5
    RV32F = 6
    RV32FC = 7
    RV64F = 8
    RV32D = 9
    RV32DC = 10
    RV64D = 11
    RV32C = 12
    RV64C = 13
    RV128I = 14
    RV128C = 15
    RVV = 16
    RV32B = 17
    RV32ZBA = 18
    RV32ZBB = 19
    RV32ZBC = 20
    RV32ZBS = 21
    RV64B = 22
    RV64ZBA = 23
    RV64ZBB = 24
    RV64ZBC = 25
    RV64ZBS = 26
    RV32X = 27
    RV64X = 28
    # --- Ratified crypto (Zk) extensions ---
    # Bit-manip for crypto (Zbkb is essentially a superset of Zbb's rotations +
    # some new ops like BREV8/ZIP/UNZIP; Zbkc mirrors Zbc's carry-less multiply;
    # Zbkx is XPERM.B/XPERM.N for the byte/nibble permutations).
    RV32ZBKB = 29
    RV32ZBKC = 30
    RV32ZBKX = 31
    RV64ZBKB = 32
    RV64ZBKC = 33
    RV64ZBKX = 34
    # AES — Zknd (decrypt), Zkne (encrypt). RV32-flavour uses 32-bit word ops,
    # RV64-flavour uses 64-bit qword ops.
    RV32ZKND = 35
    RV32ZKNE = 36
    RV64ZKND = 37
    RV64ZKNE = 38
    # SHA — Zknh (SHA-2 helpers: sigma/sum for 256 on both widths; 512 on RV64
    # or split H/L pair on RV32).
    RV32ZKNH = 39
    RV64ZKNH = 40
    # SM3 / SM4 — Chinese national crypto (Zksh / Zksed).
    RV32ZKSH = 41
    RV32ZKSED = 42
    RV64ZKSH = 43
    RV64ZKSED = 44
    # --- Embedded vector (Zve*) — RVV v1.0 subset profiles. ---
    # Vector instructions themselves stay in RiscvInstrGroup.RVV (so the registry
    # class doesn't care which subset the host selects); targets list *one* of
    # these to advertise partial vector support.
    #
    # Zve32x  : ELEN=32, SEW∈{8,16,32}, integer+fixed-point only.
    # Zve32f  : Zve32x + FP32 vector ops (requires scalar F).
    # Zve64x  : Zve32x + SEW=64 integer.
    # Zve64f  : Zve64x + FP32 vector.
    # Zve64d  : Zve64f + FP64 vector (requires scalar D).
    ZVE32X = 45
    ZVE32F = 46
    ZVE64X = 47
    ZVE64F = 48
    ZVE64D = 49


# ---------------------------------------------------------------------------
# Instruction name enum (riscv_instr_pkg.sv:115-654)
# ---------------------------------------------------------------------------

# NOTE: ordering and names match the SV source exactly. Do not reorder.
_INSTR_NAMES = [
    # RV32I
    "LUI", "AUIPC", "JAL", "JALR",
    "BEQ", "BNE", "BLT", "BGE", "BLTU", "BGEU",
    "LB", "LH", "LW", "LBU", "LHU",
    "SB", "SH", "SW",
    "ADDI", "SLTI", "SLTIU", "XORI", "ORI", "ANDI",
    "SLLI", "SRLI", "SRAI",
    "ADD", "SUB", "SLL", "SLT", "SLTU", "XOR", "SRL", "SRA", "OR", "AND",
    "NOP", "FENCE", "FENCE_I",
    "ECALL", "EBREAK",
    "CSRRW", "CSRRS", "CSRRC", "CSRRWI", "CSRRSI", "CSRRCI",
    # RV32ZBA
    "SH1ADD", "SH2ADD", "SH3ADD",
    # RV32ZBB
    "ANDN", "CLZ", "CPOP", "CTZ", "MAX", "MAXU", "MIN", "MINU",
    "ORC_B", "ORN", "REV8", "ROL", "ROR", "RORI",
    "SEXT_B", "SEXT_H", "XNOR", "ZEXT_H",
    # RV32ZBC
    "CLMUL", "CLMULH", "CLMULR",
    # RV32ZBS
    "BCLR", "BCLRI", "BEXT", "BEXTI", "BINV", "BINVI", "BSET", "BSETI",
    # RV32B draft (Zba/Zbb/Zbc/Zbs split; remaining bitmanip)
    "GORC", "GORCI", "CMIX", "CMOV",
    "PACK", "PACKU", "PACKH",
    "XPERM_N", "XPERM_B", "XPERM_H",
    "SLO", "SRO", "SLOI", "SROI",
    "GREV", "GREVI",
    "FSL", "FSR", "FSRI",
    "CRC32_B", "CRC32_H", "CRC32_W",
    "CRC32C_B", "CRC32C_H", "CRC32C_W",
    "SHFL", "UNSHFL", "SHFLI", "UNSHFLI",
    "BCOMPRESS", "BDECOMPRESS", "BFP",
    # RV64ZBA
    "ADD_UW", "SH1ADD_UW", "SH2ADD_UW", "SH3ADD_UW", "SLLI_UW",
    # RV64ZBB
    "CLZW", "CPOPW", "CTZW", "ROLW", "RORW", "RORIW",
    # RV64B draft
    "BMATOR", "BMATXOR", "BMATFLIP",
    "CRC32_D", "CRC32C_D",
    "SHFLW", "UNSHFLW",
    "BCOMPRESSW", "BDECOMPRESSW", "BFPW",
    "SLOW", "SROW", "SLOIW", "SROIW",
    "GREVW", "GREVIW",
    "FSLW", "FSRW", "FSRIW",
    "GORCW", "GORCIW",
    "PACKW", "PACKUW",
    "XPERM_W",
    # RV32M
    "MUL", "MULH", "MULHSU", "MULHU",
    "DIV", "DIVU", "REM", "REMU",
    # RV64M
    "MULW", "DIVW", "DIVUW", "REMW", "REMUW",
    # RV32F
    "FLW", "FSW",
    "FMADD_S", "FMSUB_S", "FNMSUB_S", "FNMADD_S",
    "FADD_S", "FSUB_S", "FMUL_S", "FDIV_S", "FSQRT_S",
    "FSGNJ_S", "FSGNJN_S", "FSGNJX_S", "FMIN_S", "FMAX_S",
    "FCVT_W_S", "FCVT_WU_S", "FMV_X_W",
    "FEQ_S", "FLT_S", "FLE_S", "FCLASS_S",
    "FCVT_S_W", "FCVT_S_WU", "FMV_W_X",
    # RV64F
    "FCVT_L_S", "FCVT_LU_S", "FCVT_S_L", "FCVT_S_LU",
    # RV32D
    "FLD", "FSD",
    "FMADD_D", "FMSUB_D", "FNMSUB_D", "FNMADD_D",
    "FADD_D", "FSUB_D", "FMUL_D", "FDIV_D", "FSQRT_D",
    "FSGNJ_D", "FSGNJN_D", "FSGNJX_D", "FMIN_D", "FMAX_D",
    "FCVT_S_D", "FCVT_D_S",
    "FEQ_D", "FLT_D", "FLE_D", "FCLASS_D",
    "FCVT_W_D", "FCVT_WU_D", "FCVT_D_W", "FCVT_D_WU",
    # RV64D
    "FCVT_L_D", "FCVT_LU_D", "FMV_X_D",
    "FCVT_D_L", "FCVT_D_LU", "FMV_D_X",
    # RV64I
    "LWU", "LD", "SD",
    "ADDIW", "SLLIW", "SRLIW", "SRAIW",
    "ADDW", "SUBW", "SLLW", "SRLW", "SRAW",
    # RV32C
    "C_LW", "C_SW", "C_LWSP", "C_SWSP",
    "C_ADDI4SPN", "C_ADDI", "C_LI", "C_ADDI16SP", "C_LUI",
    "C_SRLI", "C_SRAI", "C_ANDI",
    "C_SUB", "C_XOR", "C_OR", "C_AND",
    "C_BEQZ", "C_BNEZ",
    "C_SLLI", "C_MV",
    "C_EBREAK", "C_ADD", "C_NOP",
    "C_J", "C_JAL", "C_JR", "C_JALR",
    # RV64C
    "C_ADDIW", "C_SUBW", "C_ADDW",
    "C_LD", "C_SD", "C_LDSP", "C_SDSP",
    # RV128C
    "C_SRLI64", "C_SRAI64", "C_SLLI64",
    "C_LQ", "C_SQ", "C_LQSP", "C_SQSP",
    # RV32FC
    "C_FLW", "C_FSW", "C_FLWSP", "C_FSWSP",
    # RV32DC
    "C_FLD", "C_FSD", "C_FLDSP", "C_FSDSP",
    # RV32A
    "LR_W", "SC_W",
    "AMOSWAP_W", "AMOADD_W", "AMOAND_W", "AMOOR_W", "AMOXOR_W",
    "AMOMIN_W", "AMOMAX_W", "AMOMINU_W", "AMOMAXU_W",
    # RV64A
    "LR_D", "SC_D",
    "AMOSWAP_D", "AMOADD_D", "AMOAND_D", "AMOOR_D", "AMOXOR_D",
    "AMOMIN_D", "AMOMAX_D", "AMOMINU_D", "AMOMAXU_D",
    # Vector config
    "VSETVL", "VSETVLI",
    # Vector arithmetic
    "VADD", "VSUB", "VRSUB",
    "VWADDU", "VWSUBU", "VWADD", "VWSUB",
    "VADC", "VMADC", "VSBC", "VMSBC",
    "VAND", "VOR", "VXOR",
    "VSLL", "VSRL", "VSRA", "VNSRL", "VNSRA",
    "VMSEQ", "VMSNE", "VMSLTU", "VMSLT", "VMSLEU", "VMSLE", "VMSGTU", "VMSGT",
    "VMINU", "VMIN", "VMAXU", "VMAX",
    "VMUL", "VMULH", "VMULHU", "VMULHSU",
    "VDIVU", "VDIV", "VREMU", "VREM",
    "VWMUL", "VWMULU", "VWMULSU",
    "VMACC", "VNMSAC", "VMADD", "VNMSUB",
    "VWMACCU", "VWMACC", "VWMACCSU", "VWMACCUS",
    "VMERGE", "VMV",
    "VSADDU", "VSADD", "VSSUBU", "VSSUB",
    "VAADDU", "VAADD", "VASUBU", "VASUB",
    "VSSRL", "VSSRA", "VNCLIPU", "VNCLIP",
    # Vector FP
    "VFADD", "VFSUB", "VFRSUB",
    "VFMUL", "VFDIV", "VFRDIV",
    "VFWMUL",
    "VFMACC", "VFNMACC", "VFMSAC", "VFNMSAC",
    "VFMADD", "VFNMADD", "VFMSUB", "VFNMSUB",
    "VFWMACC", "VFWNMACC", "VFWMSAC", "VFWNMSAC",
    "VFSQRT_V",
    "VFMIN", "VFMAX",
    "VFSGNJ", "VFSGNJN", "VFSGNJX",
    "VMFEQ", "VMFNE", "VMFLT", "VMFLE", "VMFGT", "VMFGE",
    "VFCLASS_V", "VFMERGE", "VFMV",
    "VFCVT_XU_F_V", "VFCVT_X_F_V", "VFCVT_F_XU_V", "VFCVT_F_X_V",
    "VFWCVT_XU_F_V", "VFWCVT_X_F_V", "VFWCVT_F_XU_V", "VFWCVT_F_X_V", "VFWCVT_F_F_V",
    "VFNCVT_XU_F_W", "VFNCVT_X_F_W", "VFNCVT_F_XU_W", "VFNCVT_F_X_W",
    "VFNCVT_F_F_W", "VFNCVT_ROD_F_F_W",
    # Vector reduction
    "VREDSUM_VS", "VREDMAXU_VS", "VREDMAX_VS", "VREDMINU_VS", "VREDMIN_VS",
    "VREDAND_VS", "VREDOR_VS", "VREDXOR_VS",
    "VWREDSUMU_VS", "VWREDSUM_VS",
    "VFREDOSUM_VS", "VFREDSUM_VS", "VFREDMAX_VS",
    "VFWREDOSUM_VS", "VFWREDSUM_VS",
    # Vector mask
    "VMAND_MM", "VMNAND_MM", "VMANDNOT_MM", "VMXOR_MM",
    "VMOR_MM", "VMNOR_MM", "VMORNOT_MM", "VMXNOR_MM",
    "VPOPC_M", "VFIRST_M", "VMSBF_M", "VMSIF_M", "VMSOF_M",
    "VIOTA_M", "VID_V",
    # Vector permutation
    "VMV_X_S", "VMV_S_X", "VFMV_F_S", "VFMV_S_F",
    "VSLIDEUP", "VSLIDEDOWN", "VSLIDE1UP", "VSLIDE1DOWN",
    "VRGATHER", "VCOMPRESS",
    "VMV1R_V", "VMV2R_V", "VMV4R_V", "VMV8R_V",
    # Vector load/store
    "VLE_V", "VSE_V", "VLSE_V", "VSSE_V",
    "VLXEI_V", "VSXEI_V", "VSUXEI_V",
    "VLEFF_V",
    # Segmented load/store
    "VLSEGE_V", "VSSEGE_V", "VLSEGEFF_V",
    "VLSSEGE_V", "VSSSEGE_V",
    "VLXSEGEI_V", "VSXSEGEI_V", "VSUXSEGEI_V",
    # Vector AMO (EEW)
    "VAMOSWAPE_V", "VAMOADDE_V", "VAMOXORE_V", "VAMOANDE_V", "VAMOORE_V",
    "VAMOMINE_V", "VAMOMAXE_V", "VAMOMINUE_V", "VAMOMAXUE_V",
    # Supervisor / privileged
    "DRET", "MRET", "URET", "SRET", "WFI", "SFENCE_VMA",

    # ---- Ratified crypto extensions (RISC-V K) ----
    # Zbkb (bit-manip for crypto — ratified crypto-specific bitmanip).
    # ROR/ROL/RORI/ANDN/ORN/XNOR/PACK/PACKH/REV8 overlap with Zbb/draft-B and
    # are already registered; below are Zbkb-only new ops.
    "BREV8",     # reverse bits within each byte — RV32/RV64 Zbkb
    "ZIP",       # RV32-only Zbkb byte-interleave for GCM
    "UNZIP",     # RV32-only Zbkb byte-deinterleave
    # Zbkx (byte/nibble permutation) — XPERM.B / XPERM.N already in draft-B as
    # XPERM_B / XPERM_N. No new opcodes.
    # Zbkc (carry-less multiply) — CLMUL / CLMULH already in Zbc. No new opcodes.

    # AES — Zkne encrypt (RV32 32-bit variant + RV64 64-bit).
    "AES32ESI",   # RV32 Zkne — single round (no mixcolumns)
    "AES32ESMI",  # RV32 Zkne — single round + mixcolumns
    "AES64ES",    # RV64 Zkne — mid-round
    "AES64ESM",   # RV64 Zkne — final round
    "AES64KS1I",  # RV64 Zkne+Zknd — key schedule step 1
    "AES64KS2",   # RV64 Zkne+Zknd — key schedule step 2

    # AES — Zknd decrypt.
    "AES32DSI",   # RV32 Zknd — single round
    "AES32DSMI",  # RV32 Zknd — single round + inverse mixcolumns
    "AES64DS",    # RV64 Zknd — mid-round
    "AES64DSM",   # RV64 Zknd — final round
    "AES64IM",    # RV64 Zknd — inverse mixcolumns (decryption key schedule)

    # SHA-2 — Zknh (SHA-256 round helpers on both widths, SHA-512 on RV64 or
    # H/L split on RV32).
    "SHA256SIG0", "SHA256SIG1", "SHA256SUM0", "SHA256SUM1",
    # RV32 Zknh SHA-512 H/L pair.
    "SHA512SIG0L", "SHA512SIG0H", "SHA512SIG1L", "SHA512SIG1H",
    "SHA512SUM0R", "SHA512SUM1R",
    # RV64 Zknh SHA-512 (single-instruction form).
    "SHA512SIG0", "SHA512SIG1", "SHA512SUM0", "SHA512SUM1",

    # SM3 — Zksh.
    "SM3P0", "SM3P1",

    # SM4 — Zksed.
    "SM4ED", "SM4KS",

    # Terminator
    "INVALID_INSTR",
]

RiscvInstrName = IntEnum("RiscvInstrName", {name: i for i, name in enumerate(_INSTR_NAMES)})
RiscvInstrName.__doc__ = (
    "All instruction mnemonics known to riscv-dv (riscv_instr_pkg.sv:115-654).\n\n"
    "Values match the SV declaration order. Members are referenced by name "
    "throughout the generator; the integer value is only used as a stable key."
)


# ---------------------------------------------------------------------------
# Register enums (riscv_instr_pkg.sv:662-676)
# ---------------------------------------------------------------------------


class RiscvReg(IntEnum):
    """General-purpose register x0..x31 (riscv_instr_pkg.sv:662-666)."""

    ZERO = 0
    RA = 1
    SP = 2
    GP = 3
    TP = 4
    T0 = 5
    T1 = 6
    T2 = 7
    S0 = 8
    S1 = 9
    A0 = 10
    A1 = 11
    A2 = 12
    A3 = 13
    A4 = 14
    A5 = 15
    A6 = 16
    A7 = 17
    S2 = 18
    S3 = 19
    S4 = 20
    S5 = 21
    S6 = 22
    S7 = 23
    S8 = 24
    S9 = 25
    S10 = 26
    S11 = 27
    T3 = 28
    T4 = 29
    T5 = 30
    T6 = 31

    @property
    def abi(self) -> str:
        """ABI mnemonic in lowercase (what convert2asm emits)."""
        return self.name.lower()

    @property
    def x(self) -> str:
        """Numeric `xN` name (rarely used — golden files prefer ABI)."""
        return f"x{self.value}"


class RiscvFpr(IntEnum):
    """Floating-point register f0..f31 (riscv_instr_pkg.sv:668-671)."""

    FT0 = 0
    FT1 = 1
    FT2 = 2
    FT3 = 3
    FT4 = 4
    FT5 = 5
    FT6 = 6
    FT7 = 7
    FS0 = 8
    FS1 = 9
    FA0 = 10
    FA1 = 11
    FA2 = 12
    FA3 = 13
    FA4 = 14
    FA5 = 15
    FA6 = 16
    FA7 = 17
    FS2 = 18
    FS3 = 19
    FS4 = 20
    FS5 = 21
    FS6 = 22
    FS7 = 23
    FS8 = 24
    FS9 = 25
    FS10 = 26
    FS11 = 27
    FT8 = 28
    FT9 = 29
    FT10 = 30
    FT11 = 31

    @property
    def abi(self) -> str:
        return self.name.lower()

    @property
    def f(self) -> str:
        return f"f{self.value}"


class RiscvVreg(IntEnum):
    """Vector register v0..v31 (riscv_instr_pkg.sv:673-676)."""

    V0 = 0
    V1 = 1
    V2 = 2
    V3 = 3
    V4 = 4
    V5 = 5
    V6 = 6
    V7 = 7
    V8 = 8
    V9 = 9
    V10 = 10
    V11 = 11
    V12 = 12
    V13 = 13
    V14 = 14
    V15 = 15
    V16 = 16
    V17 = 17
    V18 = 18
    V19 = 19
    V20 = 20
    V21 = 21
    V22 = 22
    V23 = 23
    V24 = 24
    V25 = 25
    V26 = 26
    V27 = 27
    V28 = 28
    V29 = 29
    V30 = 30
    V31 = 31

    @property
    def abi(self) -> str:
        return self.name.lower()


# ABI integer register groupings used throughout riscv-dv.
#: x0..x15 subset valid as 3-bit compressed register fields.
COMPRESSED_GPR = (
    RiscvReg.S0, RiscvReg.S1,
    RiscvReg.A0, RiscvReg.A1, RiscvReg.A2, RiscvReg.A3, RiscvReg.A4, RiscvReg.A5,
)

#: Full x0..x31 in declaration order (riscv_instr_pkg.sv:1489-1491).
ALL_GPR = tuple(RiscvReg)


# ---------------------------------------------------------------------------
# Instruction format and category (riscv_instr_pkg.sv:678-745)
# ---------------------------------------------------------------------------


class RiscvInstrFormat(IntEnum):
    """Instruction encoding format (riscv_instr_pkg.sv:678-707)."""

    J_FORMAT = 0
    U_FORMAT = 1
    I_FORMAT = 2
    B_FORMAT = 3
    R_FORMAT = 4
    S_FORMAT = 5
    R4_FORMAT = 6
    # Compressed
    CI_FORMAT = 7
    CB_FORMAT = 8
    CJ_FORMAT = 9
    CR_FORMAT = 10
    CA_FORMAT = 11
    CL_FORMAT = 12
    CS_FORMAT = 13
    CSS_FORMAT = 14
    CIW_FORMAT = 15
    # Vector
    VSET_FORMAT = 16
    VA_FORMAT = 17
    VS2_FORMAT = 18
    VL_FORMAT = 19
    VS_FORMAT = 20
    VLX_FORMAT = 21
    VSX_FORMAT = 22
    VLS_FORMAT = 23
    VSS_FORMAT = 24
    VAMO_FORMAT = 25


class VaVariant(IntEnum):
    """Vector arithmetic variant selector (riscv_instr_pkg.sv:711-725)."""

    VV = 0
    VI = 1
    VX = 2
    VF = 3
    WV = 4
    WI = 5
    WX = 6
    VVM = 7
    VIM = 8
    VXM = 9
    VFM = 10
    VS = 11
    VM = 12


class RiscvInstrCategory(IntEnum):
    """Instruction semantic category (riscv_instr_pkg.sv:727-745).

    ``AMO`` must remain the last member — riscv-dv uses it as a sentinel.
    """

    LOAD = 0
    STORE = 1
    SHIFT = 2
    ARITHMETIC = 3
    LOGICAL = 4
    COMPARE = 5
    BRANCH = 6
    JUMP = 7
    SYNCH = 8
    SYSTEM = 9
    COUNTER = 10
    CSR = 11
    CHANGELEVEL = 12
    TRAP = 13
    INTERRUPT = 14
    AMO = 15


ALL_CATEGORIES = tuple(RiscvInstrCategory)


# ---------------------------------------------------------------------------
# Privileged / CSR-field enums (riscv_instr_pkg.sv:1103-1128)
# ---------------------------------------------------------------------------


class PrivilegedRegFld(IntEnum):
    """CSR field-type tag (riscv_instr_pkg.sv:1103-1110)."""

    RSVD = 0
    MXL = 1
    EXTENSION = 2
    MODE = 3
    ASID = 4
    PPN = 5


class PrivilegedLevel(IntEnum):
    """CSR privilege level encoded in address bits[9:8] (riscv_instr_pkg.sv:1112-1116)."""

    U_LEVEL = 0b00
    S_LEVEL = 0b01
    M_LEVEL = 0b11


class RegFieldAccess(IntEnum):
    """WARL/WLRL/WPRI (riscv_instr_pkg.sv:1118-1122)."""

    WPRI = 0
    WLRL = 1
    WARL = 2


class PseudoInstrName(IntEnum):
    """Pseudo-instructions (riscv_instr_pkg.sv:1125-1128)."""

    LI = 0
    LA = 1


class DataPattern(IntEnum):
    """Data-section initialization pattern (riscv_instr_pkg.sv:1131-1135)."""

    RAND_DATA = 0
    ALL_ZERO = 1
    INCR_VAL = 2


class PtePermission(IntEnum):
    """Leaf PTE xwr permissions (riscv_instr_pkg.sv:1137-1144)."""

    NEXT_LEVEL_PAGE = 0b000
    READ_ONLY_PAGE = 0b001
    READ_WRITE_PAGE = 0b011
    EXECUTE_ONLY_PAGE = 0b100
    READ_EXECUTE_PAGE = 0b101
    R_W_EXECUTE_PAGE = 0b111


class InterruptCause(IntEnum):
    """Interrupt exception codes (riscv_instr_pkg.sv:1146-1156)."""

    U_SOFTWARE_INTR = 0x0
    S_SOFTWARE_INTR = 0x1
    M_SOFTWARE_INTR = 0x3
    U_TIMER_INTR = 0x4
    S_TIMER_INTR = 0x5
    M_TIMER_INTR = 0x7
    U_EXTERNAL_INTR = 0x8
    S_EXTERNAL_INTR = 0x9
    M_EXTERNAL_INTR = 0xB


class ExceptionCause(IntEnum):
    """Synchronous exception codes (riscv_instr_pkg.sv:1158-1173)."""

    INSTRUCTION_ADDRESS_MISALIGNED = 0x0
    INSTRUCTION_ACCESS_FAULT = 0x1
    ILLEGAL_INSTRUCTION = 0x2
    BREAKPOINT = 0x3
    LOAD_ADDRESS_MISALIGNED = 0x4
    LOAD_ACCESS_FAULT = 0x5
    STORE_AMO_ADDRESS_MISALIGNED = 0x6
    STORE_AMO_ACCESS_FAULT = 0x7
    ECALL_UMODE = 0x8
    ECALL_SMODE = 0x9
    ECALL_MMODE = 0xB
    INSTRUCTION_PAGE_FAULT = 0xC
    LOAD_PAGE_FAULT = 0xD
    STORE_AMO_PAGE_FAULT = 0xF


class MisaExt(IntEnum):
    """Bit position of each ISA extension letter in MISA (riscv_instr_pkg.sv:1175-1202)."""

    MISA_EXT_A = 0
    MISA_EXT_B = 1
    MISA_EXT_C = 2
    MISA_EXT_D = 3
    MISA_EXT_E = 4
    MISA_EXT_F = 5
    MISA_EXT_G = 6
    MISA_EXT_H = 7
    MISA_EXT_I = 8
    MISA_EXT_J = 9
    MISA_EXT_K = 10
    MISA_EXT_L = 11
    MISA_EXT_M = 12
    MISA_EXT_N = 13
    MISA_EXT_O = 14
    MISA_EXT_P = 15
    MISA_EXT_Q = 16
    MISA_EXT_R = 17
    MISA_EXT_S = 18
    MISA_EXT_T = 19
    MISA_EXT_U = 20
    MISA_EXT_V = 21
    MISA_EXT_W = 22
    MISA_EXT_X = 23
    MISA_EXT_Y = 24
    MISA_EXT_Z = 25


class HazardE(IntEnum):
    """GPR hazard classification (riscv_instr_pkg.sv:1204-1209)."""

    NO_HAZARD = 0
    RAW_HAZARD = 1
    WAR_HAZARD = 2
    WAW_HAZARD = 3


class PmpAddrMode(IntEnum):
    """PMP address-matching mode (riscv_instr_pkg.sv:1223-1228)."""

    OFF = 0b00
    TOR = 0b01
    NA4 = 0b10
    NAPOT = 0b11


class VxrmMode(IntEnum):
    """Vector fixed-point rounding mode (riscv_instr_pkg.sv:1289-1294)."""

    RoundToNearestUp = 0
    RoundToNearestEven = 1
    RoundDown = 2
    RoundToOdd = 3


class BExtGroup(IntEnum):
    """Bitmanip subgroup tag (riscv_instr_pkg.sv:1296-1308)."""

    ZBA = 0
    ZBB = 1
    ZBS = 2
    ZBP = 3
    ZBE = 4
    ZBF = 5
    ZBC = 6
    ZBR = 7
    ZBM = 8
    ZBT = 9
    ZB_TMP = 10  # uncategorized


# ---------------------------------------------------------------------------
# Privileged CSR addresses (riscv_instr_pkg.sv:749-1101)
#
# Full set of privileged_reg_t — 12-bit CSR addresses. Kept here (rather than
# csrs.py) because the SV source defines the whole list inside riscv_instr_pkg;
# downstream code does ``cfg.signature_addr`` / ``implemented_csr[]`` membership
# checks that treat these as plain enum values.
# ---------------------------------------------------------------------------


class PrivilegedReg(IntEnum):
    """Every CSR address riscv-dv knows about (riscv_instr_pkg.sv:749-1101)."""

    # Unprivileged / user
    USTATUS = 0x000
    UIE = 0x004
    UTVEC = 0x005
    USCRATCH = 0x040
    UEPC = 0x041
    UCAUSE = 0x042
    UTVAL = 0x043
    UIP = 0x044
    FFLAGS = 0x001
    FRM = 0x002
    FCSR = 0x003
    # Unprivileged counters
    CYCLE = 0xC00
    TIME = 0xC01
    INSTRET = 0xC02
    HPMCOUNTER3 = 0xC03
    HPMCOUNTER4 = 0xC04
    HPMCOUNTER5 = 0xC05
    HPMCOUNTER6 = 0xC06
    HPMCOUNTER7 = 0xC07
    HPMCOUNTER8 = 0xC08
    HPMCOUNTER9 = 0xC09
    HPMCOUNTER10 = 0xC0A
    HPMCOUNTER11 = 0xC0B
    HPMCOUNTER12 = 0xC0C
    HPMCOUNTER13 = 0xC0D
    HPMCOUNTER14 = 0xC0E
    HPMCOUNTER15 = 0xC0F
    HPMCOUNTER16 = 0xC10
    HPMCOUNTER17 = 0xC11
    HPMCOUNTER18 = 0xC12
    HPMCOUNTER19 = 0xC13
    HPMCOUNTER20 = 0xC14
    HPMCOUNTER21 = 0xC15
    HPMCOUNTER22 = 0xC16
    HPMCOUNTER23 = 0xC17
    HPMCOUNTER24 = 0xC18
    HPMCOUNTER25 = 0xC19
    HPMCOUNTER26 = 0xC1A
    HPMCOUNTER27 = 0xC1B
    HPMCOUNTER28 = 0xC1C
    HPMCOUNTER29 = 0xC1D
    HPMCOUNTER30 = 0xC1E
    HPMCOUNTER31 = 0xC1F
    CYCLEH = 0xC80
    TIMEH = 0xC81
    INSTRETH = 0xC82
    HPMCOUNTER3H = 0xC83
    HPMCOUNTER4H = 0xC84
    HPMCOUNTER5H = 0xC85
    HPMCOUNTER6H = 0xC86
    HPMCOUNTER7H = 0xC87
    HPMCOUNTER8H = 0xC88
    HPMCOUNTER9H = 0xC89
    HPMCOUNTER10H = 0xC8A
    HPMCOUNTER11H = 0xC8B
    HPMCOUNTER12H = 0xC8C
    HPMCOUNTER13H = 0xC8D
    HPMCOUNTER14H = 0xC8E
    HPMCOUNTER15H = 0xC8F
    HPMCOUNTER16H = 0xC90
    HPMCOUNTER17H = 0xC91
    HPMCOUNTER18H = 0xC92
    HPMCOUNTER19H = 0xC93
    HPMCOUNTER20H = 0xC94
    HPMCOUNTER21H = 0xC95
    HPMCOUNTER22H = 0xC96
    HPMCOUNTER23H = 0xC97
    HPMCOUNTER24H = 0xC98
    HPMCOUNTER25H = 0xC99
    HPMCOUNTER26H = 0xC9A
    HPMCOUNTER27H = 0xC9B
    HPMCOUNTER28H = 0xC9C
    HPMCOUNTER29H = 0xC9D
    HPMCOUNTER30H = 0xC9E
    HPMCOUNTER31H = 0xC9F
    # Supervisor trap setup
    SSTATUS = 0x100
    SEDELEG = 0x102
    SIDELEG = 0x103
    SIE = 0x104
    STVEC = 0x105
    SCOUNTEREN = 0x106
    SENVCFG = 0x10A
    SSCRATCH = 0x140
    SEPC = 0x141
    SCAUSE = 0x142
    STVAL = 0x143
    SIP = 0x144
    SATP = 0x180
    SCONTEXT = 0x5A8
    # Hypervisor
    HSTATUS = 0x600
    HEDELEG = 0x602
    HIDELEG = 0x603
    HIE = 0x604
    HCOUNTEREN = 0x606
    HGEIE = 0x607
    HTVAL = 0x643
    HIP = 0x644
    HVIP = 0x645
    HTINST = 0x64A
    HGEIP = 0xE12
    HENVCFG = 0x60A
    HENVCFGH = 0x61A
    HGATP = 0x680
    HCONTEXT = 0x6A8
    HTIMEDELTA = 0x605
    HTIMEDELTAH = 0x615
    # Virtual supervisor
    VSSTATUS = 0x200
    VSIE = 0x204
    VSTVEC = 0x205
    VSSCRATCH = 0x240
    VSEPC = 0x241
    VSCAUSE = 0x242
    VSTVAL = 0x243
    VSIP = 0x244
    VSATP = 0x280
    # Machine information
    MVENDORID = 0xF11
    MARCHID = 0xF12
    MIMPID = 0xF13
    MHARTID = 0xF14
    MCONFIGPTR = 0xF15
    # Machine trap setup
    MSTATUS = 0x300
    MISA = 0x301
    MEDELEG = 0x302
    MIDELEG = 0x303
    MIE = 0x304
    MTVEC = 0x305
    MCOUNTEREN = 0x306
    MSTATUSH = 0x310
    # Machine trap handling
    MSCRATCH = 0x340
    MEPC = 0x341
    MCAUSE = 0x342
    MTVAL = 0x343
    MIP = 0x344
    # Machine configuration
    MENVCFG = 0x30A
    MENVCFGH = 0x31A
    MSECCFG = 0x747
    MSECCFGH = 0x757
    # PMP configuration
    PMPCFG0 = 0x3A0
    PMPCFG1 = 0x3A1
    PMPCFG2 = 0x3A2
    PMPCFG3 = 0x3A3
    PMPCFG4 = 0x3A4
    PMPCFG5 = 0x3A5
    PMPCFG6 = 0x3A6
    PMPCFG7 = 0x3A7
    PMPCFG8 = 0x3A8
    PMPCFG9 = 0x3A9
    PMPCFG10 = 0x3AA
    PMPCFG11 = 0x3AB
    PMPCFG12 = 0x3AC
    PMPCFG13 = 0x3AD
    PMPCFG14 = 0x3AE
    PMPCFG15 = 0x3AF
    # PMP address (NB: SV file addresses mix pmpaddr16..31 between 0x3C1..0x3CF
    # and 0x4C0 — we reproduce that exactly so downstream code that iterates
    # implemented_csr[] sees the same gaps.)
    PMPADDR0 = 0x3B0
    PMPADDR1 = 0x3B1
    PMPADDR2 = 0x3B2
    PMPADDR3 = 0x3B3
    PMPADDR4 = 0x3B4
    PMPADDR5 = 0x3B5
    PMPADDR6 = 0x3B6
    PMPADDR7 = 0x3B7
    PMPADDR8 = 0x3B8
    PMPADDR9 = 0x3B9
    PMPADDR10 = 0x3BA
    PMPADDR11 = 0x3BB
    PMPADDR12 = 0x3BC
    PMPADDR13 = 0x3BD
    PMPADDR14 = 0x3BE
    PMPADDR15 = 0x3BF
    PMPADDR16 = 0x4C0
    PMPADDR17 = 0x3C1
    PMPADDR18 = 0x3C2
    PMPADDR19 = 0x3C3
    PMPADDR20 = 0x3C4
    PMPADDR21 = 0x3C5
    PMPADDR22 = 0x3C6
    PMPADDR23 = 0x3C7
    PMPADDR24 = 0x3C8
    PMPADDR25 = 0x3C9
    PMPADDR26 = 0x3CA
    PMPADDR27 = 0x3CB
    PMPADDR28 = 0x3CC
    PMPADDR29 = 0x3CD
    PMPADDR30 = 0x3CE
    PMPADDR31 = 0x3CF
    PMPADDR32 = 0x4D0
    PMPADDR33 = 0x3D1
    PMPADDR34 = 0x3D2
    PMPADDR35 = 0x3D3
    PMPADDR36 = 0x3D4
    PMPADDR37 = 0x3D5
    PMPADDR38 = 0x3D6
    PMPADDR39 = 0x3D7
    PMPADDR40 = 0x3D8
    PMPADDR41 = 0x3D9
    PMPADDR42 = 0x3DA
    PMPADDR43 = 0x3DB
    PMPADDR44 = 0x3DC
    PMPADDR45 = 0x3DD
    PMPADDR46 = 0x3DE
    PMPADDR47 = 0x3DF
    PMPADDR48 = 0x4E0
    PMPADDR49 = 0x3E1
    PMPADDR50 = 0x3E2
    PMPADDR51 = 0x3E3
    PMPADDR52 = 0x3E4
    PMPADDR53 = 0x3E5
    PMPADDR54 = 0x3E6
    PMPADDR55 = 0x3E7
    PMPADDR56 = 0x3E8
    PMPADDR57 = 0x3E9
    PMPADDR58 = 0x3EA
    PMPADDR59 = 0x3EB
    PMPADDR60 = 0x3EC
    PMPADDR61 = 0x3ED
    PMPADDR62 = 0x3EE
    PMPADDR63 = 0x3EF
    # Machine counters
    MCYCLE = 0xB00
    MINSTRET = 0xB02
    MHPMCOUNTER3 = 0xB03
    MHPMCOUNTER4 = 0xB04
    MHPMCOUNTER5 = 0xB05
    MHPMCOUNTER6 = 0xB06
    MHPMCOUNTER7 = 0xB07
    MHPMCOUNTER8 = 0xB08
    MHPMCOUNTER9 = 0xB09
    MHPMCOUNTER10 = 0xB0A
    MHPMCOUNTER11 = 0xB0B
    MHPMCOUNTER12 = 0xB0C
    MHPMCOUNTER13 = 0xB0D
    MHPMCOUNTER14 = 0xB0E
    MHPMCOUNTER15 = 0xB0F
    MHPMCOUNTER16 = 0xB10
    MHPMCOUNTER17 = 0xB11
    MHPMCOUNTER18 = 0xB12
    MHPMCOUNTER19 = 0xB13
    MHPMCOUNTER20 = 0xB14
    MHPMCOUNTER21 = 0xB15
    MHPMCOUNTER22 = 0xB16
    MHPMCOUNTER23 = 0xB17
    MHPMCOUNTER24 = 0xB18
    MHPMCOUNTER25 = 0xB19
    MHPMCOUNTER26 = 0xB1A
    MHPMCOUNTER27 = 0xB1B
    MHPMCOUNTER28 = 0xB1C
    MHPMCOUNTER29 = 0xB1D
    MHPMCOUNTER30 = 0xB1E
    MHPMCOUNTER31 = 0xB1F
    MCYCLEH = 0xB80
    MINSTRETH = 0xB82
    MHPMCOUNTER3H = 0xB83
    MHPMCOUNTER4H = 0xB84
    MHPMCOUNTER5H = 0xB85
    MHPMCOUNTER6H = 0xB86
    MHPMCOUNTER7H = 0xB87
    MHPMCOUNTER8H = 0xB88
    MHPMCOUNTER9H = 0xB89
    MHPMCOUNTER10H = 0xB8A
    MHPMCOUNTER11H = 0xB8B
    MHPMCOUNTER12H = 0xB8C
    MHPMCOUNTER13H = 0xB8D
    MHPMCOUNTER14H = 0xB8E
    MHPMCOUNTER15H = 0xB8F
    MHPMCOUNTER16H = 0xB90
    MHPMCOUNTER17H = 0xB91
    MHPMCOUNTER18H = 0xB92
    MHPMCOUNTER19H = 0xB93
    MHPMCOUNTER20H = 0xB94
    MHPMCOUNTER21H = 0xB95
    MHPMCOUNTER22H = 0xB96
    MHPMCOUNTER23H = 0xB97
    MHPMCOUNTER24H = 0xB98
    MHPMCOUNTER25H = 0xB99
    MHPMCOUNTER26H = 0xB9A
    MHPMCOUNTER27H = 0xB9B
    MHPMCOUNTER28H = 0xB9C
    MHPMCOUNTER29H = 0xB9D
    MHPMCOUNTER30H = 0xB9E
    MHPMCOUNTER31H = 0xB9F
    MCOUNTINHIBIT = 0x320
    MHPMEVENT3 = 0x323
    MHPMEVENT4 = 0x324
    MHPMEVENT5 = 0x325
    MHPMEVENT6 = 0x326
    MHPMEVENT7 = 0x327
    MHPMEVENT8 = 0x328
    MHPMEVENT9 = 0x329
    MHPMEVENT10 = 0x32A
    MHPMEVENT11 = 0x32B
    MHPMEVENT12 = 0x32C
    MHPMEVENT13 = 0x32D
    MHPMEVENT14 = 0x32E
    MHPMEVENT15 = 0x32F
    MHPMEVENT16 = 0x330
    MHPMEVENT17 = 0x331
    MHPMEVENT18 = 0x332
    MHPMEVENT19 = 0x333
    MHPMEVENT20 = 0x334
    MHPMEVENT21 = 0x335
    MHPMEVENT22 = 0x336
    MHPMEVENT23 = 0x337
    MHPMEVENT24 = 0x338
    MHPMEVENT25 = 0x339
    MHPMEVENT26 = 0x33A
    MHPMEVENT27 = 0x33B
    MHPMEVENT28 = 0x33C
    MHPMEVENT29 = 0x33D
    MHPMEVENT30 = 0x33E
    MHPMEVENT31 = 0x33F
    # Debug trigger
    TSELECT = 0x7A0
    TDATA1 = 0x7A1
    TDATA2 = 0x7A2
    TDATA3 = 0x7A3
    TINFO = 0x7A4
    TCONTROL = 0x7A5
    MCONTEXT = 0x7A8
    MSCONTEXT = 0x7AA
    # Debug mode
    DCSR = 0x7B0
    DPC = 0x7B1
    DSCRATCH0 = 0x7B2
    DSCRATCH1 = 0x7B3
    # Vector
    VSTART = 0x008
    VXSTAT = 0x009
    VXRM = 0x00A
    VL = 0xC20
    VTYPE = 0xC21
    VLENB = 0xC22


#: Default writable CSR set (riscv_instr_pkg.sv:1211).
DEFAULT_INCLUDE_CSR_WRITE: tuple[PrivilegedReg, ...] = (PrivilegedReg.MSCRATCH,)
