# User directed streams

Drop Python modules here to register custom directed instruction
streams. See `docs/examples/custom-stream.py` in the repo for an
annotated template, and `rvgen/streams/` for the API shape
(base class + `@register_stream` decorator).

## Activating a custom stream — just drop the file

`rvgen` auto-imports every `*.py` under `<user_dir>/streams/` on CLI
startup, so the **only thing you need to do** is:

1. Save your module here (e.g. `user/streams/my_burst.py`).
2. Decorate the stream class with `@register_stream("my_burst_stream")`
   (or rely on the class-name → snake-case auto-name in the decorator).
3. Reference it from a testlist's `gen_opts`:

```yaml
- test: my_stream_smoke
  iterations: 1
  gen_opts: >
    +directed_instr_1=my_burst_stream,10
```

Or from the CLI directly:

```bash
rvgen --target rv32imc --test riscv_rand_instr_test \
      --gen_opts "+directed_instr_1=my_burst_stream,10" \
      --steps gen --output out/
```

That's it — no `import` shell-incantation, no `PYTHONPATH` games. The
auto-import is best-effort; a broken module logs a `WARNING` and the
CLI keeps running with the rest.

## Where the auto-import looks

`<user_dir>` is resolved in this order:

1. `--user_dir <path>` on the CLI
2. `$RVGEN_USER_DIR` environment variable
3. `./user` next to the current working directory (so the
   repository's `user/` is picked up automatically)

## Stream naming

The stream's registered name is what `+directed_instr_N=<name>,<cnt>`
references. Use a namespace prefix (e.g. `my_*`, `chipforge_*`,
`<your_org>_*`) to avoid collision with future framework streams.

## Files in this directory are NOT auto-collected for git

Files starting with `_` are skipped by the auto-importer, and
`README.md` (this file) is never imported.
