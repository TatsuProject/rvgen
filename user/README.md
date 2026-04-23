# User area

This directory is a **framework-owned opt-in** for user customisation.
Nothing here is required — but if you need to add your own target, a
custom testlist, a directed stream, or a coverage goal set, this is
where it goes so you never have to edit `rvgen/` source.

```
user/
├── README.md                    # this file
├── targets/                     # YAML target configs (riscv_core_setting.sv analogue)
│   └── <your_core>.yaml         # one file per target — see targets/README.md
├── testlists/                   # YAML testlists (gen_opts per test)
│   └── <your_regression>.yaml
├── streams/                     # Python — custom directed instruction streams
│   └── <your_stream>.py         # see rvgen/streams/ for the API shape
└── coverage/                    # YAML coverage-goal overlays (CGF format)
    └── <your_goals>.yaml
```

## How rvgen finds the user area

Resolution order (first match wins):

1. `--user_dir <path>` on the CLI.
2. `$RVGEN_USER_DIR` environment variable.
3. `./user` relative to the current working directory (only if the
   directory exists — so this default is harmless when unused).

To point rvgen at a user area outside the rvgen checkout:

```bash
export RVGEN_USER_DIR=/absolute/path/to/my/user/area
python -m rvgen --target <my_core> --test riscv_rand_instr_test ...
```

Or per-invocation:

```bash
python -m rvgen --user_dir /path/to/my/user/area --target <my_core> ...
```

## What each subdirectory does

| Dir | What lives here | How rvgen consumes it |
|---|---|---|
| `targets/` | `<name>.yaml` — one per target | Discovered by `rvgen.targets.discover_user_targets`. Name comes from the YAML's `name` field, not the filename. Made available via `--target <name>`. |
| `testlists/` | `<name>.yaml` — riscv-dv-format testlist | Load with `--testlist user/testlists/<name>.yaml`. No auto-discovery — explicit path is safer for CI. |
| `streams/` | Python module(s) | Import-time side effect: the module should `@register_stream` its classes via `rvgen.streams`. Put your stream modules under `user/streams/` and ensure they're on `PYTHONPATH` before the generator runs. |
| `coverage/` | `<name>.yaml` — CGF-format goal overlay | Load with `--cov_goals user/coverage/<name>.yaml`. Overlays compose on top of the framework baseline. |

## Minimum viable target YAML

`user/targets/chipforge-mcu.yaml` is included as a worked example — it
takes the built-in `rv32imc_zkn` configuration and adjusts the CLINT
memory map to whatever the chipforge MCU SoC actually implements.
Copy and edit for your own core.

## What *not* to put here

- **Don't** store framework fixes — send those as PRs against `rvgen/`.
- **Don't** put credentials or anything private; this directory is in
  the repo and tracked by default.
- **Don't** override a built-in target name — built-ins win. Rename
  your target if the name clashes.
