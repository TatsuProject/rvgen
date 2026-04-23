# User target configs

Drop one YAML file per target into this directory. Filename doesn't
matter — the **target's name is whatever the YAML's `name` field says**.
That way you can rename freely without touching imports.

See `../README.md` for how rvgen discovers this directory
(`--user_dir` / `$RVGEN_USER_DIR` / `./user`).

## Schema

```yaml
# Required
name: my_core                            # used as `--target my_core`
xlen: 32                                 # 32 or 64
supported_isa: [RV32I, RV32M, RV32C]     # enum names from RiscvInstrGroup
supported_privileged_mode: [MACHINE_MODE]  # enum names from PrivilegedMode

# Optional — defaults shown
satp_mode: BARE
support_sfence: false
support_unaligned_load_store: true
num_harts: 1

# Implementation-defined CLINT memory map. Defaults match SiFive CLINT
# (Spike / QEMU virt). Override when your DUT puts the timer elsewhere.
clint:
  base: 0x02000000
  mtime_offset: 0xBFF8
  mtimecmp_offset: 0x4000
  msip_offset: 0x0

# Toolchain strings for --iss spike + GCC compile. If empty, the CLI
# falls back to its built-in table for built-in target names. YAML
# targets should populate these explicitly.
isa_string: rv32imc_zicsr_zifencei
mabi: ilp32

# Individual instructions the core does NOT implement, even though
# their extension group is listed in supported_isa. Enum names from
# RiscvInstrName.
unsupported_instr:
  - MUL
  - MULH

# Which architectural CSRs / interrupts / exceptions the core
# implements. Accepts either an explicit list of enum names or a
# preset name (MMODE_CSRS, UMODE_CSRS, SMODE_CSRS, MMODE_INTERRUPTS,
# MMODE_EXCEPTIONS, USM_INTERRUPTS, USM_EXCEPTIONS — defined in
# rvgen/targets/presets.py).
implemented_csr: MMODE_CSRS
implemented_interrupt: MMODE_INTERRUPTS
implemented_exception: MMODE_EXCEPTIONS

# Optional — custom (non-architectural) CSR addresses, as integers.
custom_csr: [0x800, 0x801]
```

## Example

See `chipforge-mcu.yaml` in this directory — a real-world custom
CLINT memory map on an otherwise-standard rv32imc core.
