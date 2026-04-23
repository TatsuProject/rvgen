# Releasing rvgen to PyPI

Short, reproducible checklist for cutting a release. Everything runs locally;
nothing here needs CI. Only the maintainer needs a PyPI API token.

## One-time setup

```bash
pip install --upgrade build twine
```

Create two PyPI API tokens:

- TestPyPI — https://test.pypi.org/manage/account/token/
- PyPI     — https://pypi.org/manage/account/token/

Save them in `~/.pypirc`:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-<token>

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-<token>
```

## Pre-flight

1. **All tests pass:** `python -m pytest tests/ -q`.
2. **Canonical regression sweep:** see CLAUDE.md §0 — the 51/51 Spike and
   18/18 rv64gcv rows must still be green.
3. **Version bumped in two places:**
   - `pyproject.toml` — `project.version`
   - `rvgen/__init__.py` — `__version__`
   These must match. Semver:
   - patch: bug fix / doc tweak.
   - minor: new extension, new target, new covergroup, new CLI flag.
   - major: CLI surface change, behaviour change, drop of a target.
4. **CHANGELOG.md updated:** move items from `[Unreleased]` into the new
   version heading; add release date; update the footer diff links.
5. **CITATION.cff version + date-released match.**

## Build

```bash
rm -rf dist build rvgen.egg-info
python -m build
```

Produces:

```
dist/rvgen-<version>-py3-none-any.whl
dist/rvgen-<version>.tar.gz
```

Verify the package:

```bash
python -m twine check dist/*
unzip -l dist/rvgen-*.whl | grep goals   # 12 goal YAMLs should be present
```

Smoke-test in a clean venv (catches missing package-data and import errors
that the repo's `pip install -e` hides):

```bash
python -m venv /tmp/rvgen-smoke
/tmp/rvgen-smoke/bin/pip install dist/rvgen-*.whl
cd /tmp
/tmp/rvgen-smoke/bin/rvgen --target rv32imc --test riscv_arithmetic_basic_test \
    --steps gen --output /tmp/rvgen-smoke-out --start_seed 42 -i 1
```

The `.S` file must appear under `/tmp/rvgen-smoke-out/asm_test/`.

## Upload to TestPyPI (dry run)

```bash
python -m twine upload --repository testpypi dist/*
```

Then install from TestPyPI and re-smoke-test:

```bash
python -m venv /tmp/rvgen-testpypi
/tmp/rvgen-testpypi/bin/pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    rvgen
/tmp/rvgen-testpypi/bin/rvgen --help
```

## Upload to PyPI

```bash
python -m twine upload dist/*
```

## Tag the release

```bash
git tag -a v<version> -m "rvgen v<version>"
git push origin v<version>
```

On GitHub, turn the tag into a release and paste the matching CHANGELOG.md
section as the release notes. Attach `dist/*.whl` and `dist/*.tar.gz`.

## Post-release

- Bump to the next development version in `pyproject.toml` and
  `rvgen/__init__.py` (e.g. `0.1.1.dev0`).
- Open a fresh `[Unreleased]` section in `CHANGELOG.md`.
