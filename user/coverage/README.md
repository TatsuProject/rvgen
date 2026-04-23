# User coverage goals

Drop CGF-format YAML goal overlays here. No auto-discovery — point
rvgen at them explicitly via `--cov_goals`:

```bash
python -m rvgen \
    --target <t> --test <name> \
    --steps gen,gcc_compile,iss_sim,cov --iss spike --iss_trace \
    --cov_goals rvgen/coverage/goals/baseline.yaml \
    --cov_goals user/coverage/my_extras.yaml \
    --output out/
```

Layers compose — later files override/augment earlier ones bin-by-bin.

## Goal schema

See `docs/coverage.md` for the full catalogue. Minimum:

```yaml
opcode_cg:
  required_bins:
    - ADD
    - SUB
    - XOR
category_cg:
  required_bins:
    - LOAD
    - STORE
```

Bin names match the ones emitted in `coverage.json`. To see what bins
exist for your target, run the `cov` step once and inspect the
output — each top-level key is a covergroup name, each key under
`bins` is a bin name.
