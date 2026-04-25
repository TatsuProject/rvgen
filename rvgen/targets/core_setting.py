"""Per-target processor configuration record.

This module is the Python analogue of riscv-dv's SystemVerilog
``target/<name>/riscv_core_setting.sv`` file — a flat, declarative
record of everything the generator needs to know about the target core:

- XLEN, supported ISA groups, implemented CSRs.
- Privilege modes, SATP mode, delegation capability.
- Vector configuration (VLEN / ELEN / SELEN / MAX_LMUL / num_vec_gpr).
- CLINT memory-map (base, MTIME / MTIMECMP / MSIP offsets) — rvgen-
  specific, because we emit real CLINT writes (unlike riscv-dv which
  delegates interrupt stimulus to the RTL testbench).
- Toolchain strings (GCC ``-march`` ISA string + ABI).
- Per-target instruction denylist (``unsupported_instr``) for cores
  that don't implement the full extension.

Built-in targets live in :mod:`rvgen.targets.builtin`.
User-declared targets live as YAML under ``user/targets/`` and are
parsed by :mod:`rvgen.targets.loader`.
"""

from __future__ import annotations

from dataclasses import dataclass

from rvgen.isa.enums import (
    ExceptionCause,
    InterruptCause,
    MtvecMode,
    PrivilegedMode,
    PrivilegedReg,
    RiscvInstrGroup,
    RiscvInstrName,
    SatpMode,
)


@dataclass(frozen=True, slots=True)
class TargetCfg:
    """Fully-populated per-target configuration.

    Most fields mirror the SV ``parameter`` / ``bit`` in
    ``target/<name>/riscv_core_setting.sv``. A few fields are rvgen-
    specific because we generate code riscv-dv delegates to the RTL
    testbench — notably the CLINT memory-map fields below, which
    control where ``gen_arm_timer_irq`` / ``gen_clear_timer_irq`` etc.
    target their loads and stores. SiFive-CLINT defaults (as used by
    Spike, QEMU virt, and most FPGA cores) are pre-filled; override
    per-target when the SoC memory map differs.
    """

    name: str
    xlen: int
    supported_isa: tuple[RiscvInstrGroup, ...]
    supported_privileged_mode: tuple[PrivilegedMode, ...]
    satp_mode: SatpMode = SatpMode.BARE
    supported_interrupt_mode: tuple[MtvecMode, ...] = (
        MtvecMode.DIRECT, MtvecMode.VECTORED,
    )
    max_interrupt_vector_num: int = 16
    num_harts: int = 1
    num_gpr: int = 32
    num_float_gpr: int = 32
    num_vec_gpr: int = 32
    vlen: int = 512
    elen: int = 32
    selen: int = 8
    max_lmul: int = 8
    vector_extension_enable: bool = False
    support_pmp: bool = False
    support_epmp: bool = False
    support_debug_mode: bool = False
    support_umode_trap: bool = False
    support_sfence: bool = False
    support_unaligned_load_store: bool = True
    unsupported_instr: tuple[RiscvInstrName, ...] = ()
    implemented_csr: tuple[PrivilegedReg, ...] = ()
    custom_csr: tuple[int, ...] = ()
    implemented_interrupt: tuple[InterruptCause, ...] = ()
    implemented_exception: tuple[ExceptionCause, ...] = ()

    # --- CLINT memory map (implementation-defined per SoC) ---
    # Defaults match SiFive CLINT, which is what Spike, QEMU virt, and
    # most FPGA RISC-V cores expose. Override per-target when targeting
    # a SoC with a different timer/IPI layout.
    clint_base: int = 0x02000000
    msip_offset: int = 0x0          # MSIP[hart] = clint_base + msip_offset + 4*hart
    mtimecmp_offset: int = 0x4000   # MTIMECMP[hart] = clint_base + mtimecmp_offset + 8*hart
    mtime_offset: int = 0xBFF8      # MTIME = clint_base + mtime_offset (shared across harts)

    # --- Toolchain strings (GCC ``-march`` + ABI) ---
    # When empty, the CLI falls back to the built-in lookup table for
    # the target name. YAML-defined targets should populate these
    # directly so no code change is needed to build their tests.
    isa_string: str = ""
    mabi: str = ""

    # --- DUT memory layout (used by load/store stream offset clamping) ---
    # ``data_section_size_bytes`` is the total available space the linker
    # places ``.data`` / ``region_*`` / stacks into. When set, the per-
    # region default of 3000 B is scaled down so MMU stress streams don't
    # generate addresses past the end of the DUT's DMEM (which on small
    # cores like Challenge_0014 / chipforge-mcu is 32 KiB total — minus
    # the user/kernel stacks, AMO region and any tohost padding).
    # Default ``None`` keeps SV-parity (two 3000-byte regions = 6 KiB).
    data_section_size_bytes: int | None = None
