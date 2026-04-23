# Contributing

Thanks for considering a contribution! chipforge-inst-gen is open-source
and maintained by a small team — every bug report, feature suggestion,
or PR is appreciated.

## Ways to contribute

- **Report a bug** — [open an issue](../../issues/new?template=bug_report.md)
  with a reproducer.
- **Request a feature** — [feature issue template](../../issues/new?template=feature_request.md).
- **Improve docs** — PRs to `docs/`, `README.md`, or the docstrings land
  without ceremony.
- **Add an instruction extension** — the pattern is short
  (`chipforge_inst_gen/isa/<ext>.py` registers opcodes into the global
  factory; see `isa/bitmanip.py` or `isa/crypto.py` as templates).
- **Add a directed stream** — subclass `DirectedInstrStream` and call
  `register_stream("riscv_your_name", cls)`. See
  [`docs/examples/custom-stream.py`](docs/examples/custom-stream.py).
- **Add a target** — a `TargetCfg` entry in `targets/__init__.py` +
  a `(isa, mabi)` pair in `cli.py::_TARGET_ISA_MABI`.
- **Add a covergroup** — see
  [`docs/coverage.md#11-extending-the-model`](docs/coverage.md#11--extending-the-model).

## Development workflow

```bash
git clone <fork-url> chipforge-inst-gen
cd chipforge-inst-gen
pip install -e ".[test]"
python -m pytest tests/ -q
```

Before pushing a PR:

1. **All unit tests green** — `python -m pytest tests/ -q`. We block on
   green CI.
2. **No regression in the 51-case scalar sweep** — run a reduced
   version (2–3 targets × 2 tests × 2 seeds) locally; full sweep runs
   in CI.
3. **Coverage not regressed** — if your change touches the generator,
   run `scripts/regression.py` for an appropriate matrix and attach the
   coverage delta (via
   `python -m chipforge_inst_gen.coverage.tools diff`) in the PR
   description.

## Commit style

- **Imperative present tense** — "Add", "Fix", "Refactor" (not "Added"
  or "Adds").
- **One logical change per commit.** `feat(coverage):`, `fix(streams):`,
  `docs:`, `test:` prefixes are encouraged (Conventional Commits) but
  not required.
- **Co-authored credits welcome** — include a `Co-Authored-By:` trailer
  where appropriate.

Example:

```
feat(streams): add riscv_zero_stride_vector_stream

Ports SV's ZeroStrideVectorStream — emits VL*.V with stride==0 which
exercises the vector unit's scalar-broadcast path (the same value is
loaded into every element).

- New class in streams/vector.py (90 LOC).
- Registered as "riscv_zero_stride_vector_stream".
- Unit test in tests/unit/test_streams.py.
- Reference in docs/testlist.md.

Tests: 333/333 green.
```

## PR checklist

- [ ] Tests added / updated for the new behaviour.
- [ ] `python -m pytest tests/ -q` passes locally.
- [ ] `python -m chipforge_inst_gen.coverage.tools lint-goals` clean on any
      new / modified goals YAML.
- [ ] Docstring on any new public function.
- [ ] README / `docs/` updated if the user-facing surface changed.
- [ ] No new runtime dependencies (or explicit justification).
- [ ] Commit messages follow the style above.

## Code style

- **Python 3.11+** only (pattern matching, `StrEnum`).
- **PEP-8** by convention. We don't enforce auto-formatters — prefer
  readability over strict 79-column wraps.
- **Type hints** on public APIs. Not required on tests.
- **No new mandatory dependencies** without discussion — the hard dep
  set is intentionally small (PyYAML only; pytest for dev).
- **One Python module per concept** — avoid 2000-line files.

## Testing conventions

- Unit tests under `tests/unit/`. Fast (<2s total) and deterministic.
- Integration tests that call spike / GCC are in `scripts/` (shell) and
  expected to run on demand, not as part of `pytest`.
- For anything randomized, pass a fixed seed in the test — `random.Random(42)`.
- Use `tmp_path` (pytest fixture) for any file I/O.

## Security / sensitive issues

File a security-sensitive issue privately — email the maintainer
directly rather than opening a public issue. We'll coordinate a CVE /
disclosure timeline if needed.

## Licence

By contributing, you agree your contribution is licensed under
[Apache-2.0](LICENSE), same as the rest of the project.
