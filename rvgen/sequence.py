"""Instruction sequence — port of ``src/riscv_instr_sequence.sv``.

A :class:`InstrSequence` wraps a :class:`RandInstrStream` plus the
post-processing needed to:

- inject directed instruction streams into random positions,
- assign local numeric labels (``0:``, ``1:``, ...) for branch targets,
- resolve forward branch targets (picking ``imm_str = "<N>f"``),
- compute byte offsets for the ``imm`` field of BRANCH instructions,
- strip unused labels.

For the main program the result is then formatted to ``.S`` lines via
:meth:`generate_instr_stream`, which handles the 18-char label column.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from rvgen.config import Config
from rvgen.isa.base import Instr
from rvgen.isa.enums import (
    LABEL_STR_LEN,
    RiscvInstrCategory,
)
from rvgen.isa.filtering import AvailableInstrs
from rvgen.isa.utils import format_string
from rvgen.stream import InstrStream, RandInstrStream


@dataclass
class InstrSequence:
    """Program/sub-program sequence (SV: ``riscv_instr_sequence``)."""

    cfg: Config
    avail: AvailableInstrs
    label_name: str = "main"
    is_main_program: bool = True
    is_debug_program: bool = False
    instr_cnt: int = 0
    directed_instr: list[InstrStream] = field(default_factory=list)
    illegal_instr_pct: int = 0
    hint_instr_pct: int = 0
    instr_stream: RandInstrStream = field(default=None)  # type: ignore[assignment]
    instr_string_list: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.instr_stream is None:
            self.instr_stream = RandInstrStream(cfg=self.cfg, avail=self.avail)

    # ---- Core generation ----

    def gen_instr(self, rng: random.Random, *, no_branch: bool = False) -> None:
        """Populate :attr:`instr_stream.instr_list` with random instructions.

        SV: ``gen_instr(is_main_program, no_branch)`` (riscv_instr_sequence.sv:73).
        """
        self.instr_stream.cfg = self.cfg
        self.instr_stream.avail = self.avail
        self.instr_stream.initialize_instr_list(self.instr_cnt)
        self.instr_stream.gen_instr(
            rng,
            no_branch=no_branch,
            no_load_store=True,
            is_debug_program=self.is_debug_program,
        )
        # Stack enter/exit are for sub-programs only; directed-stream libs
        # (push/pop) land in step 7 — this is Phase 1 step 4 minimal scope.

    # ---- Post-processing (label + branch target resolution) ----

    def post_process_instr(self, rng: random.Random) -> None:
        """Assign labels, resolve forward branch targets, strip unused labels.

        Port of SV ``post_process_instr`` (riscv_instr_sequence.sv:133).
        """
        # 1) Inject directed streams at random non-atomic positions.
        for directed in self.directed_instr:
            self.instr_stream.insert_instr_stream(directed.instr_list, rng=rng)

        # 2) Walk the stream, assigning `idx` and allocating numeric labels.
        label_idx = 0
        for i, instr in enumerate(self.instr_stream.instr_list):
            instr.idx = label_idx
            if instr.has_label and not instr.atomic:
                # illegal/hint post-hoc tagging lives here in SV; Phase 1 skips.
                instr.label = str(label_idx)
                instr.is_local_numeric_label = True
                label_idx += 1

        # 3) Resolve forward branch targets.
        branch_idx = [rng.randint(1, self.cfg.max_branch_step) for _ in range(30)]
        branch_cnt = 0
        branch_target: dict[int, int] = {}

        for i, instr in enumerate(self.instr_stream.instr_list):
            if (
                instr.category == RiscvInstrCategory.BRANCH
                and not instr.branch_assigned
                and not instr.is_illegal_instr
            ):
                target = instr.idx + branch_idx[branch_cnt]
                if target >= label_idx:
                    target = label_idx - 1
                branch_cnt = (branch_cnt + 1) % len(branch_idx)
                if branch_cnt == 0:
                    rng.shuffle(branch_idx)

                instr.imm_str = f"{target}f"

                # Byte-offset computation for encoding (BRANCH imm field).
                byte_offset = 0
                target_label = str(target)
                for j in range(i + 1, len(self.instr_stream.instr_list)):
                    prev_compressed = self.instr_stream.instr_list[j - 1].is_compressed
                    byte_offset += 2 if prev_compressed else 4
                    if self.instr_stream.instr_list[j].label == target_label:
                        instr.imm = byte_offset
                        break
                instr.branch_assigned = True
                branch_target[target] = 1

        # 4) Remove unused local labels (not chosen as any branch target).
        for instr in self.instr_stream.instr_list:
            if instr.has_label and instr.is_local_numeric_label:
                try:
                    idx = int(instr.label)
                except ValueError:
                    continue
                if idx not in branch_target:
                    instr.has_label = False

    # ---- String rendering ----

    def generate_instr_stream(self, *, no_label: bool = False) -> None:
        """Render the final ``.S`` lines with 18-char label columns.

        Port of SV ``generate_instr_stream`` (riscv_instr_sequence.sv:255).
        """
        self.instr_string_list = []
        for i, instr in enumerate(self.instr_stream.instr_list):
            if i == 0:
                if no_label:
                    prefix = " " * LABEL_STR_LEN
                else:
                    prefix = format_string(f"{self.label_name}:", LABEL_STR_LEN)
                instr.has_label = True
            else:
                if instr.has_label:
                    prefix = format_string(f"{instr.label}:", LABEL_STR_LEN)
                else:
                    prefix = " " * LABEL_STR_LEN
            self.instr_string_list.append(prefix + instr.convert2asm())

    # ---- Debug helpers ----

    def as_text(self) -> str:
        return "\n".join(self.instr_string_list) + "\n"
