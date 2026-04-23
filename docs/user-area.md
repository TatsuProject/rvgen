# User area

rvgen ships with 27 built-in target configurations (the rv32i/rv32imc/
rv64gc/… targets from the riscv-dv `target/` tree, plus a handful of
Zve* embedded-vector additions). For bringup of a custom core you
don't need to edit the framework — drop your configuration into the
**user area** and it's picked up automatically.

## Layout

```
user/
├── README.md                       # top-level guide
├── targets/                        # YAML target configs
│   ├── README.md                   # schema reference
│   └── chipforge-mcu.yaml          # worked example
├── testlists/                      # YAML testlists
│   └── README.md
├── streams/                        # custom directed streams (Python)
│   └── README.md
└── coverage/                       # CGF-format coverage-goal overlays
    └── README.md
```

See `user/README.md` and each subdirectory's `README.md` for schemas
and examples.

## Resolving the user area

Precedence (first match wins):

1. `--user_dir <path>` on the CLI.
2. `$RVGEN_USER_DIR` environment variable.
3. `./user` relative to the current working directory (only if it
   exists — so this default is harmless when unused).

Tip: if you want rvgen to operate on a user area outside the repo
(e.g. in a private sibling checkout), point at it via the env var:

```bash
export RVGEN_USER_DIR=$HOME/my-chip-project/rvgen-site
python -m rvgen --target my_core --test riscv_rand_instr_test ...
```

## Adding a new target

Three options, in decreasing order of rvgen-owned-ness:

### Option 1 — user YAML (preferred for external cores)

1. Write `user/targets/<your_core>.yaml` following the schema in
   `user/targets/README.md`.
2. `python -m rvgen --target <your_core> ...` — the name comes from
   your YAML's `name` field, not the filename.

The `chipforge-mcu.yaml` example shows a complete YAML with a
custom CLINT memory map, a restricted crypto ISA, and an
`unsupported_instr` denylist for SHA-512 split-pair opcodes the
physical core doesn't implement.

### Option 2 — standalone YAML via `--target_config`

If you don't want to place the YAML in the user area:

```bash
python -m rvgen --target_config /any/path/to/my_core.yaml \
    --test riscv_rand_instr_test ...
```

Same schema as user/targets/*.yaml.

### Option 3 — Python target (framework-internal only)

Only for targets upstreamed back to rvgen. Add a `TargetCfg` entry
to `rvgen/targets/builtin.py` and submit a PR.

## Listing known targets

```bash
python -m rvgen --help_targets
```

Reports built-in targets and any user-area targets discoverable from
the current user directory.

## Implementation-defined knobs

The four fields most likely to differ between your SoC and a generic
riscv-dv target:

| Field | What it controls | Default |
|---|---|---|
| `clint.base` | Base address of the CLINT controller | `0x02000000` (SiFive CLINT / Spike / QEMU virt) |
| `clint.mtimecmp_offset` | MTIMECMP[0] = base + offset | `0x4000` |
| `clint.mtime_offset` | MTIME = base + offset | `0xBFF8` |
| `clint.msip_offset` | MSIP[0] = base + offset | `0x0` |

These land verbatim in the asm emitted by
`rvgen.privileged.interrupts.gen_arm_timer_irq` /
`gen_clear_timer_irq` / `gen_arm_software_irq` /
`gen_clear_software_irq`. If your SoC's timer is at a different
memory location, setting these is the difference between a test
that fires a real timer IRQ and a test that stores to random
addresses.

The SiFive CLINT layout works on Spike, QEMU virt, and most
off-the-shelf RISC-V FPGA cores. Override only when you know your
SoC differs.
