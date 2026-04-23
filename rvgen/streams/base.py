"""Base class for directed instruction streams.

Port of SV ``riscv_directed_instr_stream`` (src/riscv_directed_instr_lib.sv:20).

Each subclass implements :meth:`build` which populates ``instr_list`` with
atomic instructions. :meth:`finalize` tags every instruction as atomic and
attaches ``Start <name>`` / ``End <name>`` comments to the first/last instr
(to help debug and to keep the sequence intact across insertion).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from rvgen.config import Config
from rvgen.isa.base import Instr
from rvgen.isa.filtering import AvailableInstrs


@dataclass
class DirectedInstrStream:
    """Base class for atomic directed instruction streams."""

    cfg: Config
    avail: AvailableInstrs
    rng: random.Random
    #: Logical stream name (defaults to class ``__name__``). Used for the
    #: start/end comments that SV's post_randomize attaches.
    stream_name: str = ""
    #: Optional unique label (e.g. ``main_0``) — first instruction carries it.
    label: str = ""
    #: Hart index — only meaningful for multi-hart streams.
    hart: int = 0
    #: The built instruction list.
    instr_list: list[Instr] = field(default_factory=list)

    def build(self) -> None:
        """Populate :attr:`instr_list`. Subclasses override."""
        raise NotImplementedError

    def finalize(self) -> None:
        """Mark all instructions atomic + add start/end comments (SV:29).

        Preserves ``has_label`` on instructions that the stream builder
        explicitly labeled (e.g. loop body target for a backward branch).
        """
        if not self.instr_list:
            return
        name = self.stream_name or type(self).__name__
        for instr in self.instr_list:
            # Only clear labels that weren't set by the builder.
            if not instr.label:
                instr.has_label = False
            instr.atomic = True
        self.instr_list[0].comment = f"Start {name}"
        self.instr_list[-1].comment = f"End {name}"
        # Only overwrite instr_list[0]'s label if the builder didn't give it
        # a specific one (e.g., JalInstr assigns per-jump labels already).
        if self.label and not self.instr_list[0].label:
            self.instr_list[0].label = self.label
            self.instr_list[0].has_label = True

    def generate(self) -> list[Instr]:
        """Build and return the finalized instruction list."""
        self.build()
        self.finalize()
        return self.instr_list
