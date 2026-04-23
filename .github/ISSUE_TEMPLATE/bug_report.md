---
name: Bug report
about: Something wrong with generated output, coverage, or the CLI.
labels: bug
---

### Describe the bug

A clear, concise description of what went wrong.

### Reproducer

The exact CLI invocation, or Python snippet, that triggers the bug:

```
python -m chipforge_inst_gen ...
```

### Expected vs actual

- **Expected**: (what you thought would happen)
- **Actual**: (what happened — paste error output, traceback, or the
  specific .S-file line that's wrong)

### Environment

- chipforge-inst-gen version (git SHA or tag):
- Python version (`python --version`):
- OS:
- RISC-V toolchain (`$RISCV_GCC --version | head -1`):
- ISS version (`$SPIKE_PATH --help | head -1`):

### Additional context

Attach the failing `.S` file + `coverage.json` if relevant. Redact
anything sensitive (signature addresses, custom CSR mappings, etc.).
