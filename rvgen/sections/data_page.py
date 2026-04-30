"""Data-page generator — port of ``src/riscv_data_page_gen.sv``.

Emits one or more ``.section`` blocks with the per-region data pattern
(RAND_DATA / ALL_ZERO / INCR_VAL) as ``.word`` directives, 32 bytes per
``.word`` line.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from rvgen.isa.enums import LABEL_STR_LEN, DataPattern
from rvgen.isa.utils import format_data, hart_prefix


@dataclass(frozen=True, slots=True)
class MemRegion:
    """Single data-page region (SV: ``mem_region_t``)."""

    name: str
    size_in_bytes: int
    xwr: int = 0b111  # X/W/R permission bits — informational for tests
    #: When True the region is emitted once (no per-hart prefix) and
    #: every hart references it by the same name. Used by multi-hart
    #: shared-memory race tests + AMO regions.
    shared: bool = False


#: Default user-mode memory regions (matches SV ``cfg.mem_region`` defaults).
DEFAULT_MEM_REGIONS: tuple[MemRegion, ...] = (
    MemRegion("region_0", 3000),
    MemRegion("region_1", 3000),
)

#: AMO region for atomic operation tests.
DEFAULT_AMO_REGION: tuple[MemRegion, ...] = (MemRegion("amo_0", 128, shared=True),)

#: Multi-hart shared-memory race region. Single un-prefixed section that
#: every hart hits; ideal for LR/SC rendezvous, fence-pair stress, and
#: testing memory ordering. Size matches DEFAULT_MEM_REGIONS so existing
#: load/store offset distributions still apply.
DEFAULT_SHARED_REGIONS: tuple[MemRegion, ...] = (
    MemRegion("shared_region_0", 3000, shared=True),
)

#: Supervisor/kernel data regions.
DEFAULT_S_MEM_REGIONS: tuple[MemRegion, ...] = (
    MemRegion("s_region_0", 32),
    MemRegion("s_region_1", 32),
)


_INDENT = " " * LABEL_STR_LEN


def _gen_bytes(
    pattern: DataPattern,
    idx: int,
    num_of_bytes: int,
    rng: random.Random,
) -> list[int]:
    """Port of SV ``gen_data`` (riscv_data_page_gen.sv:34)."""
    if pattern == DataPattern.RAND_DATA:
        return [rng.randint(0, 255) for _ in range(num_of_bytes)]
    if pattern == DataPattern.INCR_VAL:
        return [(idx + i) % 256 for i in range(num_of_bytes)]
    # ALL_ZERO (default)
    return [0] * num_of_bytes


def gen_data_page(
    regions: Sequence[MemRegion],
    pattern: DataPattern,
    *,
    hart: int = 0,
    num_harts: int = 1,
    amo: bool = False,
    rng: random.Random | None = None,
    use_push_data_section: bool = False,
) -> list[str]:
    """Emit the ``.section .hN_<region>`` blocks for the given regions.

    Port of SV ``gen_data_page`` (riscv_data_page_gen.sv:49). AMO regions are
    not hart-prefixed (shared across harts).
    """
    rng = rng or random.Random()
    lines: list[str] = []

    for region in regions:
        # Shared (and AMO) regions skip the per-hart prefix so all harts
        # reference the same single section. Per-hart prefix only applies
        # to private regions.
        use_prefix = not (amo or region.shared)
        label = (hart_prefix(hart, num_harts) + region.name) if use_prefix else region.name
        section_name = f".{label}"
        if use_push_data_section:
            lines.append(f".pushsection {section_name},\"aw\",@progbits;")
        else:
            lines.append(f".section {section_name},\"aw\",@progbits;")
        lines.append(f"{label}:")

        size = region.size_in_bytes
        i = 0
        while i < size:
            chunk_size = min(32, size - i)
            data = _gen_bytes(pattern, i, chunk_size, rng)
            lines.append(f"{_INDENT}.word {format_data(data)}")
            i += chunk_size

        if use_push_data_section:
            lines.append(".popsection;")

    return lines


def gen_stack_section(
    *,
    stack_len: int,
    hart: int = 0,
    num_harts: int = 1,
    xlen: int = 32,
    align: int = 2,
    kernel: bool = False,
) -> list[str]:
    """Emit a stack section (user or kernel) — SV: riscv_asm_program_gen.sv:589.

    Layout::

        .section .h<N>_<kind>,"aw",@progbits;
        .align <align>
        h<N>_<kind>_start:
        .rept <stack_len - 1>
        .8byte 0x0   (or .4byte for XLEN=32)
        .endr
        h<N>_<kind>_end:
        .8byte 0x0
    """
    prefix = hart_prefix(hart, num_harts)
    kind = "kernel_stack" if kernel else "user_stack"
    section = f".{prefix}{kind}"
    word_dir = ".8byte" if xlen == 64 else ".4byte"
    return [
        f".section {section},\"aw\",@progbits;",
        f".align {align}",
        f"{prefix}{kind}_start:",
        f".rept {stack_len - 1}",
        f"{word_dir} 0x0",
        ".endr",
        f"{prefix}{kind}_end:",
        f"{word_dir} 0x0",
    ]


def gen_tohost_fromhost() -> list[str]:
    """Emit the HTIF ``tohost``/``fromhost`` symbols into the dedicated
    ``.tohost`` output section.

    The linker script isolates ``.tohost`` on its own 4 KiB page between
    ``.text`` and ``.data``. Emitting into ``.data`` (like the SV reference
    does) puts ``tohost`` right next to ``.region_0`` — 72 bytes apart in
    practice — so a random store with a small negative offset from the
    region base silently overwrites ``tohost`` with garbage. Spike then
    interprets that garbage as an HTIF device pointer and aborts with
    ``Memory address 0x... is invalid`` (rc=255).

    Keeping them in ``.tohost`` puts them on their own page; random stores
    target ``.region_*`` inside ``.data`` whose ±2 KiB offsets can't reach
    across a page boundary.
    """
    return [
        ".section .tohost,\"aw\",@progbits",
        ".align 6; .global tohost; tohost: .dword 0;",
        ".align 6; .global fromhost; fromhost: .dword 0;",
    ]
