"""Example: write your own directed instruction stream.

This file is not imported by the package by default — drop it into
``rvgen/streams/`` (or import it from your own code) to
make the stream available to testlist gen_opts.

After that, reference it from any testlist.yaml:

    +directed_instr_0=my_burst_accumulator_stream,5

Which will insert five copies of this stream's output, each at a
random non-atomic position in the main random sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

from rvgen.isa.enums import RiscvInstrName, RiscvReg
from rvgen.isa.factory import get_instr
from rvgen.streams import register_stream
from rvgen.streams.base import DirectedInstrStream


@dataclass
class MyBurstAccumulatorStream(DirectedInstrStream):
    """10 back-to-back ADDs with rd == rs1.

    This is the canonical 'accumulate into a register' pattern — the
    kind of code a software loop produces after unrolling. It stresses
    the register-file forwarding path and any OoO in-flight-WAW logic.

    Constraints:

    - ``rd`` is always the same register (pick once, reuse) so later
      ADDs depend on the previous ADD's result (RAW hazard chain).
    - ``rs2`` is randomized from the non-reserved pool each iteration.
    - x0 is never picked (would turn the ADD into a nop for rd=x0).
    """

    burst_len: int = 10

    def build(self) -> None:
        # Pick a single accumulator register once, avoiding reserved
        # regs and x0.
        reserved = set(self.cfg.reserved_regs) | {RiscvReg.ZERO}
        pool = [r for r in RiscvReg if r not in reserved]
        acc = self.rng.choice(pool)

        for _ in range(self.burst_len):
            instr = get_instr(RiscvInstrName.ADD)
            instr.rs1 = acc
            instr.rs2 = self.rng.choice(pool)
            instr.rd = acc  # rd == rs1 → in-place accumulate
            instr.post_randomize()
            self.instr_list.append(instr)


# Register with the SV-style class name so testlist gen_opts can
# reference it. Name is what users type in their gen_opts string.
register_stream("my_burst_accumulator_stream", MyBurstAccumulatorStream)
