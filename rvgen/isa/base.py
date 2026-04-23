"""Base instruction class, ported from ``src/isa/riscv_instr.sv``.

This is the spine of the ISA layer. Every concrete instruction subclass
inherits from :class:`Instr` (or one of its format-specific specializations
in sibling modules) and carries class-level ``instr_name/format/category/
group/imm_type`` attributes that mirror the SV ``DEFINE_INSTR`` expansion.

The behavior (``set_rand_mode``, ``set_imm_len``, ``extend_imm``,
``convert2asm``, ``convert2bin``, ``get_opcode/func3/func7``) is a direct
port of the SV base class — when something is ambiguous, always prefer what
the SV source does.
"""

from __future__ import annotations

import copy
from typing import ClassVar

from rvgen.isa.enums import (
    DATA_WIDTH,
    MAX_INSTR_STR_LEN,
    ImmType,
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.utils import format_string, sign_extend


# Shorthand aliases (module-local, not re-exported).
_FMT = RiscvInstrFormat
_CAT = RiscvInstrCategory
_IMM = ImmType


class Instr:
    """Base instruction class — port of ``riscv_instr`` (riscv_instr.sv:17).

    Subclasses must set the five class-level attributes before ``__init__``
    runs. The :func:`register_instr` / :func:`define_instr` helpers in
    :mod:`rvgen.isa.factory` do this automatically when
    constructing a new subclass.
    """

    # -- Class-level attributes (one per subclass, SV's DEFINE_INSTR fields)
    instr_name: ClassVar[RiscvInstrName]
    format: ClassVar[RiscvInstrFormat]
    category: ClassVar[RiscvInstrCategory]
    group: ClassVar[RiscvInstrGroup]
    imm_type: ClassVar[ImmType] = ImmType.IMM

    # -- Instance slots (flat for speed; mirrors SV field layout)
    __slots__ = (
        # Operands
        "rs1", "rs2", "rd", "csr", "imm",
        # has_* flags (toggled by set_rand_mode)
        "has_rs1", "has_rs2", "has_rd", "has_imm",
        # Immediate bookkeeping
        "imm_len", "imm_mask", "imm_str",
        # Instruction-stream metadata
        "atomic", "branch_assigned", "is_branch_target",
        "has_label", "label", "is_local_numeric_label",
        "is_illegal_instr", "is_hint_instr",
        "is_compressed", "is_floating_point",
        "process_load_store",
        "comment", "idx",
        # GPR hazard tracking (set by riscv_instr_stream.check_hazard_condition)
        "gpr_hazard",
    )

    # -------- Construction / defaults --------

    def __init__(self) -> None:
        # Operands default to zero; downstream generators overwrite these.
        self.rs1: RiscvReg = RiscvReg.ZERO
        self.rs2: RiscvReg = RiscvReg.ZERO
        self.rd: RiscvReg = RiscvReg.ZERO
        self.csr: int = 0
        self.imm: int = 0

        self.has_rs1: bool = True
        self.has_rs2: bool = True
        self.has_rd: bool = True
        self.has_imm: bool = True

        self.imm_len: int = 0
        self.imm_mask: int = 0xFFFFFFFF
        self.imm_str: str = ""

        self.atomic: bool = False
        self.branch_assigned: bool = False
        self.is_branch_target: bool = False
        self.has_label: bool = True
        self.label: str = ""
        self.is_local_numeric_label: bool = False
        self.is_illegal_instr: bool = False
        self.is_hint_instr: bool = False
        self.is_compressed: bool = False
        self.is_floating_point: bool = False
        self.process_load_store: bool = True
        self.comment: str = ""
        self.idx: int = -1
        self.gpr_hazard: int = 0  # HazardE.NO_HAZARD

        # SV DEFINE_INSTR macro runs set_imm_len() and set_rand_mode() in the
        # constructor *after* the class-level attributes are assigned. We
        # replicate that order.
        self.set_imm_len()
        self.set_rand_mode()

    # -------- Rand-mode / imm-len (SV: set_rand_mode, set_imm_len) --------

    def set_rand_mode(self) -> None:
        """Disable randomization for unused operands (riscv_instr.sv:283)."""
        fmt = self.format
        if fmt == _FMT.R_FORMAT:
            self.has_imm = False
        elif fmt == _FMT.I_FORMAT:
            self.has_rs2 = False
        elif fmt in (_FMT.S_FORMAT, _FMT.B_FORMAT):
            self.has_rd = False
        elif fmt in (_FMT.U_FORMAT, _FMT.J_FORMAT):
            self.has_rs1 = False
            self.has_rs2 = False

    def set_imm_len(self) -> None:
        """Compute ``imm_len`` and ``imm_mask`` from format/imm_type (riscv_instr.sv:305).

        - ``U_FORMAT``, ``J_FORMAT`` → 20-bit immediate.
        - ``I_FORMAT``, ``S_FORMAT``, ``B_FORMAT``:
            - ``UIMM`` (shift amount) → 5-bit immediate.
            - else → 12-bit immediate.
        """
        if self.format in (_FMT.U_FORMAT, _FMT.J_FORMAT):
            self.imm_len = 20
        elif self.format in (_FMT.I_FORMAT, _FMT.S_FORMAT, _FMT.B_FORMAT):
            self.imm_len = 5 if self.imm_type == _IMM.UIMM else 12
        # SV: imm_mask = imm_mask << imm_len;
        # Note: imm_mask starts as 0xFFFFFFFF (DATA_WIDTH ones); after the
        # shift the low imm_len bits are zero and the upper bits are set.
        self.imm_mask = (0xFFFFFFFF << self.imm_len) & 0xFFFFFFFF

    # -------- Immediate extension & stringification --------

    def randomize_imm(self, rng, xlen: int) -> None:
        """Pick a random immediate honoring the SV ``imm_c`` constraint.

        Port of the shift-shamt constraint at ``riscv_instr.sv:71-82``:

        - SLLIW/SRLIW/SRAIW: ``imm[11:5] == 0`` (5-bit unsigned shamt).
        - SLLI/SRLI/SRAI:    RV32 → ``imm[11:5] == 0``; RV64 → ``imm[11:6] == 0``.

        For every other I/S/B/U/J format the full ``imm_len`` width is used.
        """
        name = self.instr_name
        if name in (RiscvInstrName.SLLIW, RiscvInstrName.SRLIW, RiscvInstrName.SRAIW):
            self.imm = rng.getrandbits(5)
            return
        if name in (RiscvInstrName.SLLI, RiscvInstrName.SRLI, RiscvInstrName.SRAI):
            shamt_bits = 5 if xlen == 32 else 6
            self.imm = rng.getrandbits(shamt_bits)
            return
        self.imm = rng.getrandbits(self.imm_len)

    def extend_imm(self) -> None:
        """Sign-extend the low ``imm_len`` bits of ``imm`` to 32 bits (riscv_instr.sv:318).

        Verbatim port of the SV two-step (left-shift to MSB, read sign, logical
        right-shift, OR in ``imm_mask`` if signed).
        """
        imm = (self.imm << (DATA_WIDTH - self.imm_len)) & 0xFFFFFFFF
        sign = (imm >> (DATA_WIDTH - 1)) & 1
        imm >>= DATA_WIDTH - self.imm_len
        if sign and self.format != _FMT.U_FORMAT and self.imm_type not in (_IMM.UIMM, _IMM.NZUIMM):
            imm = (self.imm_mask | imm) & 0xFFFFFFFF
        self.imm = imm

    def update_imm_str(self) -> None:
        """Default: signed-decimal string of ``imm`` (riscv_instr.sv:620)."""
        self.imm_str = str(sign_extend(self.imm, DATA_WIDTH))

    def get_imm(self) -> str:
        """Return the immediate rendering (``imm_str``).

        Overridden by branch-target resolution which sets ``imm_str`` to a
        label reference like ``"3f"``. (riscv_instr.sv:586.)
        """
        return self.imm_str

    # -------- Post-randomize hook --------

    def post_randomize(self) -> None:
        """Runs :meth:`extend_imm` then :meth:`update_imm_str` (riscv_instr.sv:329).

        In riscv-dv this is called automatically after SV randomize(); our
        generator calls it manually after picking ``imm`` etc.
        """
        self.extend_imm()
        self.update_imm_str()

    # -------- Naming --------

    def get_instr_name(self) -> str:
        """Return the assembly mnemonic: enum name with ``_`` → ``.`` (riscv_instr.sv:569).

        Example: ``FENCE_I`` → ``FENCE.I`` (lower-cased later in
        :meth:`convert2asm`).
        """
        return self.instr_name.name.replace("_", ".")

    # -------- Encoding helpers (overridable per-subclass) --------

    def get_opcode(self) -> int:
        """Return the 7-bit opcode (riscv_instr.sv:381)."""
        name = self.instr_name
        # Ordered exactly as SV — first-match wins (SV cases are exclusive).
        if name == RiscvInstrName.LUI:
            return 0b0110111
        if name == RiscvInstrName.AUIPC:
            return 0b0010111
        if name == RiscvInstrName.JAL:
            return 0b1101111
        if name == RiscvInstrName.JALR:
            return 0b1100111
        if name in _BRANCH_OPCODES:
            return 0b1100011
        if name in _LOAD_OPCODES:
            return 0b0000011
        if name in _STORE_OPCODES:
            return 0b0100011
        if name in _OP_IMM_OPCODES:
            return 0b0010011
        if name in _OP_OPCODES:
            return 0b0110011
        if name in _OP_IMM_32_OPCODES:
            return 0b0011011
        if name in (RiscvInstrName.FENCE, RiscvInstrName.FENCE_I):
            return 0b0001111
        if name in _OP_32_OPCODES:
            return 0b0111011
        if name in _SYSTEM_OPCODES:
            return 0b1110011
        raise ValueError(f"Unsupported instruction {name.name} in get_opcode")

    def get_func3(self) -> int:
        """Return the 3-bit func3 field (riscv_instr.sv:403)."""
        try:
            return _FUNC3[self.instr_name]
        except KeyError as e:
            raise ValueError(
                f"Unsupported instruction {self.instr_name.name} in get_func3"
            ) from e

    def get_func7(self) -> int:
        """Return the 7-bit func7 field (riscv_instr.sv:474)."""
        try:
            return _FUNC7[self.instr_name]
        except KeyError as e:
            raise ValueError(
                f"Unsupported instruction {self.instr_name.name} in get_func7"
            ) from e

    # -------- Assembly / binary output --------

    def convert2asm(self, prefix: str = "") -> str:
        """Emit the assembly mnemonic + operands string (riscv_instr.sv:335).

        Returns the assembly text *without* the 18-char label column (caller
        prepends that via :func:`~rvgen.isa.utils.indent_line`).
        """
        mnemonic = format_string(self.get_instr_name(), MAX_INSTR_STR_LEN)
        asm_str = mnemonic
        name = self.instr_name

        if self.category != _CAT.SYSTEM:
            fmt = self.format
            if fmt in (_FMT.J_FORMAT, _FMT.U_FORMAT):
                asm_str = f"{mnemonic}{self.rd.name}, {self.get_imm()}"
            elif fmt == _FMT.I_FORMAT:
                if name == RiscvInstrName.NOP:
                    asm_str = "nop"
                elif name == RiscvInstrName.WFI:
                    asm_str = "wfi"
                elif name == RiscvInstrName.FENCE:
                    # SV TODO: fence combinations — emit bare "fence".
                    asm_str = "fence"
                elif name == RiscvInstrName.FENCE_I:
                    asm_str = "fence.i"
                elif self.category == _CAT.LOAD:
                    # Pseudo-form: "lw rd, imm(rs1)".
                    asm_str = f"{mnemonic}{self.rd.name}, {self.get_imm()}({self.rs1.name})"
                else:
                    asm_str = f"{mnemonic}{self.rd.name}, {self.rs1.name}, {self.get_imm()}"
            elif fmt in (_FMT.S_FORMAT, _FMT.B_FORMAT):
                if self.category == _CAT.STORE:
                    asm_str = f"{mnemonic}{self.rs2.name}, {self.get_imm()}({self.rs1.name})"
                else:
                    asm_str = f"{mnemonic}{self.rs1.name}, {self.rs2.name}, {self.get_imm()}"
            elif fmt == _FMT.R_FORMAT:
                if name == RiscvInstrName.SFENCE_VMA:
                    # SV TODO: support all sfence variants.
                    asm_str = "sfence.vma x0, x0"
                else:
                    asm_str = (
                        f"{mnemonic}{self.rd.name}, {self.rs1.name}, {self.rs2.name}"
                    )
            else:
                raise ValueError(
                    f"Unsupported format {self.format.name} for {name.name} in convert2asm"
                )
        else:
            # category == SYSTEM — only EBREAK gets a special asm_str.
            if name == RiscvInstrName.EBREAK:
                asm_str = ".4byte 0x00100073 # ebreak"
            # Otherwise asm_str remains the padded mnemonic (ECALL, URET,
            # SRET, MRET, DRET).

        if self.comment:
            asm_str = f"{asm_str} #{self.comment}"
        return asm_str.lower()

    def convert2bin(self, prefix: str = "") -> str:
        """Emit the 32-bit machine-code hex string (riscv_instr.sv:525).

        Returns an 8-char lowercase hex string (no ``0x`` prefix) optionally
        prefixed by ``prefix``. Mirrors SV ``%8h`` with the bit concatenation
        for each format.
        """
        name = self.instr_name
        word = _encode(self)
        return f"{prefix}{word:08x}"


# ---------------------------------------------------------------------------
# Opcode / func3 / func7 dispatch tables
#
# These mirror the exclusive SV ``case(instr_name) inside ...`` blocks.
# Ordering is preserved for readability, but lookup is by instruction name.
# ---------------------------------------------------------------------------


_BRANCH_OPCODES = frozenset({
    RiscvInstrName.BEQ, RiscvInstrName.BNE, RiscvInstrName.BLT,
    RiscvInstrName.BGE, RiscvInstrName.BLTU, RiscvInstrName.BGEU,
})

_LOAD_OPCODES = frozenset({
    RiscvInstrName.LB, RiscvInstrName.LH, RiscvInstrName.LW,
    RiscvInstrName.LBU, RiscvInstrName.LHU,
    RiscvInstrName.LWU, RiscvInstrName.LD,
})

_STORE_OPCODES = frozenset({
    RiscvInstrName.SB, RiscvInstrName.SH, RiscvInstrName.SW, RiscvInstrName.SD,
})

_OP_IMM_OPCODES = frozenset({
    RiscvInstrName.ADDI, RiscvInstrName.SLTI, RiscvInstrName.SLTIU,
    RiscvInstrName.XORI, RiscvInstrName.ORI, RiscvInstrName.ANDI,
    RiscvInstrName.SLLI, RiscvInstrName.SRLI, RiscvInstrName.SRAI,
    RiscvInstrName.NOP,
})

_OP_OPCODES = frozenset({
    RiscvInstrName.ADD, RiscvInstrName.SUB, RiscvInstrName.SLL,
    RiscvInstrName.SLT, RiscvInstrName.SLTU, RiscvInstrName.XOR,
    RiscvInstrName.SRL, RiscvInstrName.SRA, RiscvInstrName.OR,
    RiscvInstrName.AND,
    RiscvInstrName.MUL, RiscvInstrName.MULH, RiscvInstrName.MULHSU,
    RiscvInstrName.MULHU, RiscvInstrName.DIV, RiscvInstrName.DIVU,
    RiscvInstrName.REM, RiscvInstrName.REMU,
})

_OP_IMM_32_OPCODES = frozenset({
    RiscvInstrName.ADDIW, RiscvInstrName.SLLIW,
    RiscvInstrName.SRLIW, RiscvInstrName.SRAIW,
})

_OP_32_OPCODES = frozenset({
    RiscvInstrName.ADDW, RiscvInstrName.SUBW, RiscvInstrName.SLLW,
    RiscvInstrName.SRLW, RiscvInstrName.SRAW,
    RiscvInstrName.MULW, RiscvInstrName.DIVW, RiscvInstrName.DIVUW,
    RiscvInstrName.REMW, RiscvInstrName.REMUW,
})

_SYSTEM_OPCODES = frozenset({
    RiscvInstrName.ECALL, RiscvInstrName.EBREAK,
    RiscvInstrName.URET, RiscvInstrName.SRET, RiscvInstrName.MRET,
    RiscvInstrName.DRET, RiscvInstrName.WFI, RiscvInstrName.SFENCE_VMA,
})

# I-format shift instructions whose encoding includes func7 in bits[31:25].
# SRAI / SRAIW set func7 = 0b0100000; SLLI / SRLI / SLLIW / SRLIW use 0.
_I_FORMAT_SHIFTS = frozenset({
    RiscvInstrName.SLLI, RiscvInstrName.SRLI, RiscvInstrName.SRAI,
    RiscvInstrName.SLLIW, RiscvInstrName.SRLIW, RiscvInstrName.SRAIW,
})


# SV get_func3 cases flattened into a dict.
_FUNC3: dict[RiscvInstrName, int] = {
    RiscvInstrName.JALR: 0b000,
    RiscvInstrName.BEQ: 0b000, RiscvInstrName.BNE: 0b001,
    RiscvInstrName.BLT: 0b100, RiscvInstrName.BGE: 0b101,
    RiscvInstrName.BLTU: 0b110, RiscvInstrName.BGEU: 0b111,
    RiscvInstrName.LB: 0b000, RiscvInstrName.LH: 0b001, RiscvInstrName.LW: 0b010,
    RiscvInstrName.LBU: 0b100, RiscvInstrName.LHU: 0b101,
    RiscvInstrName.SB: 0b000, RiscvInstrName.SH: 0b001, RiscvInstrName.SW: 0b010,
    RiscvInstrName.ADDI: 0b000, RiscvInstrName.NOP: 0b000,
    RiscvInstrName.SLTI: 0b010, RiscvInstrName.SLTIU: 0b011,
    RiscvInstrName.XORI: 0b100, RiscvInstrName.ORI: 0b110, RiscvInstrName.ANDI: 0b111,
    RiscvInstrName.SLLI: 0b001, RiscvInstrName.SRLI: 0b101, RiscvInstrName.SRAI: 0b101,
    RiscvInstrName.ADD: 0b000, RiscvInstrName.SUB: 0b000,
    RiscvInstrName.SLL: 0b001, RiscvInstrName.SLT: 0b010, RiscvInstrName.SLTU: 0b011,
    RiscvInstrName.XOR: 0b100, RiscvInstrName.SRL: 0b101, RiscvInstrName.SRA: 0b101,
    RiscvInstrName.OR: 0b110, RiscvInstrName.AND: 0b111,
    RiscvInstrName.FENCE: 0b000, RiscvInstrName.FENCE_I: 0b001,
    RiscvInstrName.ECALL: 0b000, RiscvInstrName.EBREAK: 0b000,
    # RV64I
    RiscvInstrName.LWU: 0b110, RiscvInstrName.LD: 0b011, RiscvInstrName.SD: 0b011,
    RiscvInstrName.ADDIW: 0b000, RiscvInstrName.SLLIW: 0b001,
    RiscvInstrName.SRLIW: 0b101, RiscvInstrName.SRAIW: 0b101,
    RiscvInstrName.ADDW: 0b000, RiscvInstrName.SUBW: 0b000,
    RiscvInstrName.SLLW: 0b001, RiscvInstrName.SRLW: 0b101, RiscvInstrName.SRAW: 0b101,
    # RV32M / RV64M
    RiscvInstrName.MUL: 0b000, RiscvInstrName.MULH: 0b001,
    RiscvInstrName.MULHSU: 0b010, RiscvInstrName.MULHU: 0b011,
    RiscvInstrName.DIV: 0b100, RiscvInstrName.DIVU: 0b101,
    RiscvInstrName.REM: 0b110, RiscvInstrName.REMU: 0b111,
    RiscvInstrName.MULW: 0b000, RiscvInstrName.DIVW: 0b100,
    RiscvInstrName.DIVUW: 0b101, RiscvInstrName.REMW: 0b110, RiscvInstrName.REMUW: 0b111,
    # Privileged / system shims
    RiscvInstrName.URET: 0b000, RiscvInstrName.SRET: 0b000, RiscvInstrName.MRET: 0b000,
    RiscvInstrName.DRET: 0b000, RiscvInstrName.WFI: 0b000, RiscvInstrName.SFENCE_VMA: 0b000,
}

_FUNC7: dict[RiscvInstrName, int] = {
    RiscvInstrName.SLLI: 0b0000000, RiscvInstrName.SRLI: 0b0000000,
    RiscvInstrName.SRAI: 0b0100000,
    RiscvInstrName.ADD: 0b0000000, RiscvInstrName.SUB: 0b0100000,
    RiscvInstrName.SLL: 0b0000000, RiscvInstrName.SLT: 0b0000000,
    RiscvInstrName.SLTU: 0b0000000, RiscvInstrName.XOR: 0b0000000,
    RiscvInstrName.SRL: 0b0000000, RiscvInstrName.SRA: 0b0100000,
    RiscvInstrName.OR: 0b0000000, RiscvInstrName.AND: 0b0000000,
    RiscvInstrName.FENCE: 0b0000000, RiscvInstrName.FENCE_I: 0b0000000,
    RiscvInstrName.SLLIW: 0b0000000, RiscvInstrName.SRLIW: 0b0000000,
    RiscvInstrName.SRAIW: 0b0100000,
    RiscvInstrName.ADDW: 0b0000000, RiscvInstrName.SUBW: 0b0100000,
    RiscvInstrName.SLLW: 0b0000000, RiscvInstrName.SRLW: 0b0000000,
    RiscvInstrName.SRAW: 0b0100000,
    # M
    RiscvInstrName.MUL: 0b0000001, RiscvInstrName.MULH: 0b0000001,
    RiscvInstrName.MULHSU: 0b0000001, RiscvInstrName.MULHU: 0b0000001,
    RiscvInstrName.DIV: 0b0000001, RiscvInstrName.DIVU: 0b0000001,
    RiscvInstrName.REM: 0b0000001, RiscvInstrName.REMU: 0b0000001,
    RiscvInstrName.MULW: 0b0000001, RiscvInstrName.DIVW: 0b0000001,
    RiscvInstrName.DIVUW: 0b0000001, RiscvInstrName.REMW: 0b0000001,
    RiscvInstrName.REMUW: 0b0000001,
    # Privileged
    RiscvInstrName.ECALL: 0b0000000, RiscvInstrName.EBREAK: 0b0000000,
    RiscvInstrName.URET: 0b0000000, RiscvInstrName.SRET: 0b0001000,
    RiscvInstrName.MRET: 0b0011000, RiscvInstrName.DRET: 0b0111101,
    RiscvInstrName.WFI: 0b0001000, RiscvInstrName.SFENCE_VMA: 0b0001001,
}


# ---------------------------------------------------------------------------
# Binary encoder
# ---------------------------------------------------------------------------


def _bits(value: int, hi: int, lo: int) -> int:
    """Return bits ``value[hi:lo]`` as an integer (right-justified)."""
    width = hi - lo + 1
    return (value >> lo) & ((1 << width) - 1)


def _encode(instr: Instr) -> int:
    """Assemble the 32-bit machine encoding (riscv_instr.sv:525)."""
    fmt = instr.format
    name = instr.instr_name
    opcode = instr.get_opcode()
    imm = instr.imm & 0xFFFFFFFF
    rd = int(instr.rd) & 0x1F
    rs1 = int(instr.rs1) & 0x1F
    rs2 = int(instr.rs2) & 0x1F

    if fmt == _FMT.J_FORMAT:
        # {imm[20], imm[10:1], imm[11], imm[19:12], rd, opcode}
        bits = (
            (_bits(imm, 20, 20) << 31)
            | (_bits(imm, 10, 1) << 21)
            | (_bits(imm, 11, 11) << 20)
            | (_bits(imm, 19, 12) << 12)
            | (rd << 7)
            | opcode
        )
        return bits & 0xFFFFFFFF

    if fmt == _FMT.U_FORMAT:
        # LUI / AUIPC place the 20-bit immediate at bits[31:12] of the
        # instruction. riscv-dv stores ``imm`` as the raw value (matching
        # the assembler-visible "lui a0, 0x12345" literal), so the encoder
        # shifts that value into position. NB: SV's ``{imm[31:12], rd, opc}``
        # is only safe when imm is already placed in bits[31:12] — which it
        # isn't in riscv-dv's own code. We emit the spec-correct encoding
        # that matches what GCC produces from the .S text.
        imm20 = imm & 0xFFFFF
        bits = (imm20 << 12) | (rd << 7) | opcode
        return bits & 0xFFFFFFFF

    if fmt == _FMT.I_FORMAT:
        func3 = instr.get_func3()
        if name in (RiscvInstrName.FENCE, RiscvInstrName.FENCE_I):
            # {17'b0, func3, 5'b0, opcode}
            return ((func3 & 0b111) << 12) | opcode
        if name == RiscvInstrName.ECALL:
            # {func7, 18'b0, opcode}  (ECALL has a specific 25-bit encoding)
            return (instr.get_func7() << 25) | opcode
        if name in (RiscvInstrName.URET, RiscvInstrName.SRET, RiscvInstrName.MRET):
            # {func7, 5'b00010, 13'b0, opcode}
            return (instr.get_func7() << 25) | (0b00010 << 20) | opcode
        if name == RiscvInstrName.DRET:
            # {func7, 5'b10010, 13'b0, opcode}
            return (instr.get_func7() << 25) | (0b10010 << 20) | opcode
        if name == RiscvInstrName.EBREAK:
            # {func7, 5'd1, 13'b0, opcode}
            return (instr.get_func7() << 25) | (0b00001 << 20) | opcode
        if name == RiscvInstrName.WFI:
            # {func7, 5'b00101, 13'b0, opcode}
            return (instr.get_func7() << 25) | (0b00101 << 20) | opcode
        if name in _I_FORMAT_SHIFTS:
            # Shift immediates carry a func7 in bits[31:25]: SRAI/SRAIW use
            # 0b0100000; SLLI/SRLI/SLLIW/SRLIW use 0b0000000. RV64 shamt is
            # 6 bits, so _bits(imm, 5, 0) covers both RV32 and RV64.
            shamt = imm & 0x3F  # up to 6 bits (RV32 ignores bit 5)
            func7 = instr.get_func7()
            return (
                (func7 << 25)
                | (shamt << 20)
                | (rs1 << 15)
                | (func3 << 12)
                | (rd << 7)
                | opcode
            )
        # General I-format: {imm[11:0], rs1, func3, rd, opcode}
        imm12 = imm & 0xFFF
        return (imm12 << 20) | (rs1 << 15) | (func3 << 12) | (rd << 7) | opcode

    if fmt == _FMT.S_FORMAT:
        func3 = instr.get_func3()
        # {imm[11:5], rs2, rs1, func3, imm[4:0], opcode}
        return (
            (_bits(imm, 11, 5) << 25)
            | (rs2 << 20)
            | (rs1 << 15)
            | (func3 << 12)
            | (_bits(imm, 4, 0) << 7)
            | opcode
        )

    if fmt == _FMT.B_FORMAT:
        func3 = instr.get_func3()
        # {imm[12], imm[10:5], rs2, rs1, func3, imm[4:1], imm[11], opcode}
        return (
            (_bits(imm, 12, 12) << 31)
            | (_bits(imm, 10, 5) << 25)
            | (rs2 << 20)
            | (rs1 << 15)
            | (func3 << 12)
            | (_bits(imm, 4, 1) << 8)
            | (_bits(imm, 11, 11) << 7)
            | opcode
        )

    if fmt == _FMT.R_FORMAT:
        func3 = instr.get_func3()
        func7 = instr.get_func7()
        if name == RiscvInstrName.SFENCE_VMA:
            return (func7 << 25) | opcode
        # {func7, rs2, rs1, func3, rd, opcode}
        return (
            (func7 << 25)
            | (rs2 << 20)
            | (rs1 << 15)
            | (func3 << 12)
            | (rd << 7)
            | opcode
        )

    raise ValueError(f"Unsupported format {fmt.name} in convert2bin")


# ---------------------------------------------------------------------------
# Copy (SV "shallow copy via new instr_template[name]")
# ---------------------------------------------------------------------------


def copy_instr(instr: Instr) -> Instr:
    """Return a shallow copy of ``instr`` (SV: ``new instr_template[name]``).

    Mirrors ``do_copy`` in SV (riscv_instr.sv:596) — we copy operand fields
    plus the immediate bookkeeping. Other metadata (label, has_label, atomic,
    is_branch_target, …) is reset to defaults so callers can't accidentally
    inherit stale generator-stream state.
    """
    cls = type(instr)
    fresh = cls()
    # The base ctor already initialized class-level attrs via set_imm_len /
    # set_rand_mode, so only propagate fields the SV do_copy copies.
    fresh.rs1 = instr.rs1
    fresh.rs2 = instr.rs2
    fresh.rd = instr.rd
    fresh.csr = instr.csr
    fresh.imm = instr.imm
    fresh.imm_len = instr.imm_len
    fresh.imm_mask = instr.imm_mask
    fresh.imm_str = instr.imm_str
    fresh.is_compressed = instr.is_compressed
    fresh.has_rs1 = instr.has_rs1
    fresh.has_rs2 = instr.has_rs2
    fresh.has_rd = instr.has_rd
    fresh.has_imm = instr.has_imm
    return fresh
