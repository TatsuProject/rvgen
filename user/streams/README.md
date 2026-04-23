# User directed streams

Drop Python modules here to register custom directed instruction
streams. See `docs/examples/custom-stream.py` in the repo for an
annotated template, and `rvgen/streams/` for the API shape
(base class + `@register_stream` decorator).

## Activating a custom stream

Two options, pick one:

### Option A — module on PYTHONPATH
```bash
export PYTHONPATH=$PWD/user/streams:$PYTHONPATH
python -c "import my_stream"        # triggers @register_stream side effect
python -m rvgen --target <t> --test <name> \
    --gen_opts "+directed_instr_1=my_stream,10" ...
```

### Option B — import from a custom testlist
```yaml
# user/testlists/with_my_stream.yaml
- test: my_stream_smoke
  gen_test: riscv_rand_instr_test
  gen_opts: >
    +directed_instr_1=my_stream,10
  # Hint: your test entry can `import user.streams.my_stream`
  # elsewhere in setup code to force registration.
```

## Stream naming

The stream's registered name is what `+directed_instr_N=<name>,<cnt>`
references. Use a namespace prefix (e.g. `my_stream`, `chipforge_xxx`)
to avoid collision with future framework streams.
