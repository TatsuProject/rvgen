## What changes and why

One or two sentences on the user-visible change. Link to the issue if
there is one.

## Type of change

- [ ] Bug fix
- [ ] New feature (non-breaking)
- [ ] Breaking change
- [ ] Documentation / refactor
- [ ] Tests / CI only

## Checklist

- [ ] `python -m pytest tests/ -q` passes locally.
- [ ] `python -m rvgen.coverage.tools lint-goals ...` clean
      on any new / modified goals YAML.
- [ ] Docstring on any new public function.
- [ ] README / `docs/` updated if the user-facing surface changed.
- [ ] Commit messages use imperative tense.

## Coverage impact (if applicable)

If this change touches the generator or streams, attach a
`coverage.tools diff` between a clean baseline and this PR:

```
python -m rvgen --target rv32imc --test riscv_rand_instr_test \
    --steps gen,cov --output /tmp/before --start_seed 100 -i 1
git stash  # or check out main
python -m rvgen --target rv32imc --test riscv_rand_instr_test \
    --steps gen,cov --output /tmp/after --start_seed 100 -i 1
python -m rvgen.coverage.tools diff \
    /tmp/before/coverage.json /tmp/after/coverage.json
```

Paste the diff output here:

```
<paste>
```

## Reviewer notes

Anything non-obvious about the approach, or parts that deserve a second
look.
