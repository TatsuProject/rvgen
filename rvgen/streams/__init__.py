"""Directed instruction streams — port of ``src/riscv_directed_instr_lib.sv`` et al.

Each stream is a subclass of :class:`DirectedInstrStream` that appends a
sequence of atomic instructions to its ``instr_list``. Downstream code (the
:class:`~rvgen.sequence.InstrSequence`) inserts each directed
stream at a random non-atomic position before label / branch-target
resolution.

Streams are registered by their SV class name so testlist ``gen_opts`` like
``+directed_instr_0=riscv_int_numeric_corner_stream,4`` can look them up.
"""

from __future__ import annotations

from typing import Type

from rvgen.streams.base import DirectedInstrStream


# Global name → class registry. Populated at import time by each stream
# module below.
STREAM_REGISTRY: dict[str, Type[DirectedInstrStream]] = {}


def register_stream(name: str, cls: Type[DirectedInstrStream]) -> None:
    STREAM_REGISTRY[name] = cls


def get_stream(name: str) -> Type[DirectedInstrStream]:
    try:
        return STREAM_REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unknown directed stream {name!r}. Registered: {sorted(STREAM_REGISTRY)}"
        )


# Import (and register) concrete streams at package-load time.
# Import order matters: load_store.py registers the canonical
# load/store stream names and must run AFTER directed.py, whose
# historical aliases are superseded below.
from rvgen.streams import directed              # noqa: F401,E402
from rvgen.streams import loop                  # noqa: F401,E402
from rvgen.streams import amo_streams           # noqa: F401,E402
from rvgen.streams import load_store            # noqa: F401,E402
from rvgen.streams import vector_load_store     # noqa: F401,E402
from rvgen.streams import vsetvli_stress        # noqa: F401,E402
from rvgen.streams import vector_hazard         # noqa: F401,E402
from rvgen.streams import vstart_corner         # noqa: F401,E402
from rvgen.streams import h_ext                 # noqa: F401,E402
