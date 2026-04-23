"""AMO (atomic memory operation) base class — port of ``src/isa/riscv_amo_instr.sv``.

RV32A / RV64A instructions have:

- ``aq`` / ``rl`` bits, mutually exclusive at randomize-time.
- A mnemonic suffix (``.w`` / ``.d``) derived from their group.
- An optional ``.aq`` / ``.rl`` append based on the two bits.

Encoding layout (AMO opcode 0x2F): ``{func5[31:27], aq, rl, rs2, rs1, func3, rd, opcode}``.
"""

from __future__ import annotations

from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    MAX_INSTR_STR_LEN,
    RiscvInstrGroup,
    RiscvInstrName,
)
from rvgen.isa.utils import format_string


# AMO func5 values (bits[31:27] of the encoded instruction).
_AMO_FUNC5: dict[RiscvInstrName, int] = {
    RiscvInstrName.LR_W:      0b00010,
    RiscvInstrName.SC_W:      0b00011,
    RiscvInstrName.AMOSWAP_W: 0b00001,
    RiscvInstrName.AMOADD_W:  0b00000,
    RiscvInstrName.AMOXOR_W:  0b00100,
    RiscvInstrName.AMOAND_W:  0b01100,
    RiscvInstrName.AMOOR_W:   0b01000,
    RiscvInstrName.AMOMIN_W:  0b10000,
    RiscvInstrName.AMOMAX_W:  0b10100,
    RiscvInstrName.AMOMINU_W: 0b11000,
    RiscvInstrName.AMOMAXU_W: 0b11100,
    RiscvInstrName.LR_D:      0b00010,
    RiscvInstrName.SC_D:      0b00011,
    RiscvInstrName.AMOSWAP_D: 0b00001,
    RiscvInstrName.AMOADD_D:  0b00000,
    RiscvInstrName.AMOXOR_D:  0b00100,
    RiscvInstrName.AMOAND_D:  0b01100,
    RiscvInstrName.AMOOR_D:   0b01000,
    RiscvInstrName.AMOMIN_D:  0b10000,
    RiscvInstrName.AMOMAX_D:  0b10100,
    RiscvInstrName.AMOMINU_D: 0b11000,
    RiscvInstrName.AMOMAXU_D: 0b11100,
}


_W_INSTRS = frozenset(n for n, _ in _AMO_FUNC5.items() if n.name.endswith("_W"))
_D_INSTRS = frozenset(n for n, _ in _AMO_FUNC5.items() if n.name.endswith("_D"))
_LR_INSTRS = frozenset({RiscvInstrName.LR_W, RiscvInstrName.LR_D})


class AmoInstr(Instr):
    """RV32A / RV64A atomic instruction base class."""

    __slots__ = ("aq", "rl")

    def __init__(self) -> None:
        super().__init__()
        self.aq: bool = False
        self.rl: bool = False

    def randomize_imm(self, rng, xlen: int) -> None:
        """AMO has no immediate. Also coin-flip ``aq`` / ``rl`` (mutually exclusive)."""
        super().randomize_imm(rng, xlen)  # no-op for AMO (imm_len=0 after set_imm_len)
        r = rng.randint(0, 2)  # 0 = neither, 1 = aq, 2 = rl
        self.aq = r == 1
        self.rl = r == 2

    def set_imm_len(self) -> None:
        # AMO ops use R_FORMAT (no immediate).
        self.imm_len = 0
        self.imm_mask = 0xFFFFFFFF

    def set_rand_mode(self) -> None:
        # AMO uses R_FORMAT: rd, rs1, rs2. has_imm=0.
        self.has_imm = False

    # ---- Mnemonic / operand rendering ----

    def get_instr_name(self) -> str:
        """Build the AMO mnemonic with optional ``.aq`` / ``.rl`` suffix.

        Port of SV ``riscv_amo_instr.get_instr_name`` (riscv_amo_instr.sv:35).
        """
        name = self.instr_name.name
        # SV strips the trailing "_W"/"_D" and appends ".w"/".d".
        if name.endswith("_W"):
            base = name[:-2] + ".w"
        elif name.endswith("_D"):
            base = name[:-2] + ".d"
        else:
            base = name
        if self.aq:
            return f"{base.lower()}.aq"
        if self.rl:
            return f"{base.lower()}.rl"
        return base.lower()

    def convert2asm(self, prefix: str = "") -> str:
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        if self.instr_name in _LR_INSTRS:
            asm_str = f"{mnemonic}{self.rd.name}, ({self.rs1.name})"
        else:
            asm_str = f"{mnemonic}{self.rd.name}, {self.rs2.name}, ({self.rs1.name})"
        if self.comment:
            asm_str = f"{asm_str} #{self.comment}"
        return asm_str.lower()

    # ---- Binary encoding (AMO opcode = 0x2F, func3 per width) ----

    def get_opcode(self) -> int:
        return 0b0101111

    def get_func3(self) -> int:
        return 0b010 if self.instr_name in _W_INSTRS else 0b011  # W=.010, D=.011

    def get_func5(self) -> int:
        return _AMO_FUNC5[self.instr_name]

    def convert2bin(self, prefix: str = "") -> str:
        opcode = self.get_opcode()
        func3 = self.get_func3()
        func5 = self.get_func5()
        aq = 1 if self.aq else 0
        rl = 1 if self.rl else 0
        rd = int(self.rd) & 0x1F
        rs1 = int(self.rs1) & 0x1F
        rs2 = int(self.rs2) & 0x1F
        # LR_W / LR_D: rs2 field must be 0.
        if self.instr_name in _LR_INSTRS:
            rs2 = 0
        word = (
            (func5 << 27)
            | (aq << 26)
            | (rl << 25)
            | (rs2 << 20)
            | (rs1 << 15)
            | (func3 << 12)
            | (rd << 7)
            | opcode
        )
        return f"{prefix}{word & 0xFFFFFFFF:08x}"
