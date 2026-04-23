"""RV32I base integer instruction registrations.

Port of ``src/isa/rv32i_instr.sv``. Every line in that file maps to exactly
one :func:`define_instr` / :func:`define_csr_instr` call here. Order is
preserved to keep the ``instr_registry`` iteration stable with the SV source.
"""

from __future__ import annotations

from rvgen.isa.enums import (
    ImmType,
    RiscvInstrCategory as C,
    RiscvInstrFormat as F,
    RiscvInstrGroup as G,
    RiscvInstrName as N,
)
from rvgen.isa.factory import define_csr_instr, define_instr


# -- LOAD instructions (rv32i_instr.sv:17-22) --
define_instr(N.LB, F.I_FORMAT, C.LOAD, G.RV32I)
define_instr(N.LH, F.I_FORMAT, C.LOAD, G.RV32I)
define_instr(N.LW, F.I_FORMAT, C.LOAD, G.RV32I)
define_instr(N.LBU, F.I_FORMAT, C.LOAD, G.RV32I)
define_instr(N.LHU, F.I_FORMAT, C.LOAD, G.RV32I)

# -- STORE instructions (rv32i_instr.sv:23-26) --
define_instr(N.SB, F.S_FORMAT, C.STORE, G.RV32I)
define_instr(N.SH, F.S_FORMAT, C.STORE, G.RV32I)
define_instr(N.SW, F.S_FORMAT, C.STORE, G.RV32I)

# -- SHIFT instructions (rv32i_instr.sv:27-33) --
define_instr(N.SLL, F.R_FORMAT, C.SHIFT, G.RV32I)
define_instr(N.SLLI, F.I_FORMAT, C.SHIFT, G.RV32I)
define_instr(N.SRL, F.R_FORMAT, C.SHIFT, G.RV32I)
define_instr(N.SRLI, F.I_FORMAT, C.SHIFT, G.RV32I)
define_instr(N.SRA, F.R_FORMAT, C.SHIFT, G.RV32I)
define_instr(N.SRAI, F.I_FORMAT, C.SHIFT, G.RV32I)

# -- ARITHMETIC instructions (rv32i_instr.sv:34-40) --
define_instr(N.ADD, F.R_FORMAT, C.ARITHMETIC, G.RV32I)
define_instr(N.ADDI, F.I_FORMAT, C.ARITHMETIC, G.RV32I)
define_instr(N.NOP, F.I_FORMAT, C.ARITHMETIC, G.RV32I)
define_instr(N.SUB, F.R_FORMAT, C.ARITHMETIC, G.RV32I)
define_instr(N.LUI, F.U_FORMAT, C.ARITHMETIC, G.RV32I, ImmType.UIMM)
define_instr(N.AUIPC, F.U_FORMAT, C.ARITHMETIC, G.RV32I, ImmType.UIMM)

# -- LOGICAL instructions (rv32i_instr.sv:41-47) --
define_instr(N.XOR, F.R_FORMAT, C.LOGICAL, G.RV32I)
define_instr(N.XORI, F.I_FORMAT, C.LOGICAL, G.RV32I)
define_instr(N.OR, F.R_FORMAT, C.LOGICAL, G.RV32I)
define_instr(N.ORI, F.I_FORMAT, C.LOGICAL, G.RV32I)
define_instr(N.AND, F.R_FORMAT, C.LOGICAL, G.RV32I)
define_instr(N.ANDI, F.I_FORMAT, C.LOGICAL, G.RV32I)

# -- COMPARE instructions (rv32i_instr.sv:48-52) --
define_instr(N.SLT, F.R_FORMAT, C.COMPARE, G.RV32I)
define_instr(N.SLTI, F.I_FORMAT, C.COMPARE, G.RV32I)
define_instr(N.SLTU, F.R_FORMAT, C.COMPARE, G.RV32I)
define_instr(N.SLTIU, F.I_FORMAT, C.COMPARE, G.RV32I)

# -- BRANCH instructions (rv32i_instr.sv:53-59) --
define_instr(N.BEQ, F.B_FORMAT, C.BRANCH, G.RV32I)
define_instr(N.BNE, F.B_FORMAT, C.BRANCH, G.RV32I)
define_instr(N.BLT, F.B_FORMAT, C.BRANCH, G.RV32I)
define_instr(N.BGE, F.B_FORMAT, C.BRANCH, G.RV32I)
define_instr(N.BLTU, F.B_FORMAT, C.BRANCH, G.RV32I)
define_instr(N.BGEU, F.B_FORMAT, C.BRANCH, G.RV32I)

# -- JUMP instructions (rv32i_instr.sv:60-62) --
define_instr(N.JAL, F.J_FORMAT, C.JUMP, G.RV32I)
define_instr(N.JALR, F.I_FORMAT, C.JUMP, G.RV32I)

# -- SYNCH instructions (rv32i_instr.sv:63-66) --
define_instr(N.FENCE, F.I_FORMAT, C.SYNCH, G.RV32I)
define_instr(N.FENCE_I, F.I_FORMAT, C.SYNCH, G.RV32I)
define_instr(N.SFENCE_VMA, F.R_FORMAT, C.SYNCH, G.RV32I)

# -- SYSTEM instructions (rv32i_instr.sv:67-74) --
define_instr(N.ECALL, F.I_FORMAT, C.SYSTEM, G.RV32I)
define_instr(N.EBREAK, F.I_FORMAT, C.SYSTEM, G.RV32I)
define_instr(N.URET, F.I_FORMAT, C.SYSTEM, G.RV32I)
define_instr(N.SRET, F.I_FORMAT, C.SYSTEM, G.RV32I)
define_instr(N.MRET, F.I_FORMAT, C.SYSTEM, G.RV32I)
define_instr(N.DRET, F.I_FORMAT, C.SYSTEM, G.RV32I)
define_instr(N.WFI, F.I_FORMAT, C.INTERRUPT, G.RV32I)

# -- CSR instructions (rv32i_instr.sv:75-81) --
define_csr_instr(N.CSRRW, F.R_FORMAT, C.CSR, G.RV32I, ImmType.UIMM)
define_csr_instr(N.CSRRS, F.R_FORMAT, C.CSR, G.RV32I, ImmType.UIMM)
define_csr_instr(N.CSRRC, F.R_FORMAT, C.CSR, G.RV32I, ImmType.UIMM)
define_csr_instr(N.CSRRWI, F.I_FORMAT, C.CSR, G.RV32I, ImmType.UIMM)
define_csr_instr(N.CSRRSI, F.I_FORMAT, C.CSR, G.RV32I, ImmType.UIMM)
define_csr_instr(N.CSRRCI, F.I_FORMAT, C.CSR, G.RV32I, ImmType.UIMM)


# All 47 RV32I instructions registered. Re-exported as a constant so tests
# and higher layers can reason about completeness without iterating the
# whole registry.

RV32I_INSTR_NAMES = (
    N.LB, N.LH, N.LW, N.LBU, N.LHU,
    N.SB, N.SH, N.SW,
    N.SLL, N.SLLI, N.SRL, N.SRLI, N.SRA, N.SRAI,
    N.ADD, N.ADDI, N.NOP, N.SUB, N.LUI, N.AUIPC,
    N.XOR, N.XORI, N.OR, N.ORI, N.AND, N.ANDI,
    N.SLT, N.SLTI, N.SLTU, N.SLTIU,
    N.BEQ, N.BNE, N.BLT, N.BGE, N.BLTU, N.BGEU,
    N.JAL, N.JALR,
    N.FENCE, N.FENCE_I, N.SFENCE_VMA,
    N.ECALL, N.EBREAK, N.URET, N.SRET, N.MRET, N.DRET, N.WFI,
    N.CSRRW, N.CSRRS, N.CSRRC, N.CSRRWI, N.CSRRSI, N.CSRRCI,
)
