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
from typing import ClassVar

from rvgen.config import Config
from rvgen.isa.base import Instr
from rvgen.isa.filtering import AvailableInstrs


@dataclass
class DirectedInstrStream:
    """Base class for atomic directed instruction streams."""

    # Knobs on ``cfg`` that, when truthy, drop this stream entirely.
    # The splicer (asm_program_gen.py) consults this BEFORE building so
    # ``+no_branch_jump=1 +directed_instr_1=riscv_loop_instr,N`` honors the
    # user's knob instead of silently emitting branches.
    # ClassVar tells dataclass this is a class attribute, not a field;
    # subclasses override with a plain class-level assignment.
    BANNED_BY: ClassVar[tuple[str, ...]] = ()

    # CSR symbolic names this stream writes as a side effect (e.g. the
    # vstart-corner stream writes VSTART; the vsetvli-stress stream
    # writes VTYPE + VL via vsetvli's implicit semantics).
    # ``cfg.include_write_csr`` controls the RANDOM CSR walker; directed
    # streams are EXEMPT (the user explicitly requested them). The
    # splicer logs INFO when a stream's WRITES_CSRS isn't a subset of
    # the configured whitelist, so the verif engineer can see why
    # write counters they thought were locked down moved.
    WRITES_CSRS: ClassVar[tuple[str, ...]] = ()

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

    @classmethod
    def is_banned_by(cls, cfg: Config) -> str | None:
        """Return the first ``cfg.no_*`` knob name that vetoes this stream,
        or None if the stream is allowed under the current config.

        Used by the splicer to drop a directed-stream request without
        building it — the user's knob wins over the directed_instr
        plusarg.
        """
        for knob in cls.BANNED_BY:
            if getattr(cfg, knob, False):
                return knob
        return None

    @classmethod
    def csrs_outside_whitelist(cls, cfg: Config) -> tuple[str, ...]:
        """Return CSRs this stream writes that are NOT in
        ``cfg.include_write_csr``. Empty tuple means "stream's CSR
        writes are all whitelisted" (or the stream writes no CSRs).

        Used by the splicer for an INFO log only — directed streams
        are EXEMPT from the whitelist (the user explicitly requested
        them). The log is for visibility, not for gating.
        """
        if not cls.WRITES_CSRS:
            return ()
        whitelist = {s.upper() for s in getattr(cfg, "include_write_csr", ()) or ()}
        return tuple(c for c in cls.WRITES_CSRS if c.upper() not in whitelist)

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
