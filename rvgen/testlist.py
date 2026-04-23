"""Testlist YAML loader — port of ``scripts/lib.py::process_regression_list``.

Handles the ``<riscv_dv_root>`` placeholder substitution and recursive
``import`` directives so per-target testlists can pull in
``yaml/base_testlist.yaml`` without duplication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml


@dataclass
class TestEntry:
    """A single testlist YAML entry.

    Only the keys the CLI consumes are modelled explicitly; unknown keys are
    preserved in :attr:`extras` for forward-compatibility.
    """

    __test__ = False  # pytest: don't treat as a test class

    test: str
    iterations: int = 0
    description: str = ""
    gen_test: str = ""
    gen_opts: str = ""
    rtl_test: str = ""
    sim_opts: str = ""
    compare_opts: str = ""
    gcc_opts: str = ""
    iss_opts: str = ""
    asm_test: str = ""
    c_test: str = ""
    no_iss: int = 0
    no_gcc: int = 0
    no_post_compare: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "TestEntry":
        """Construct from a YAML-loaded dict, preserving unknown keys."""
        known = {f: data.get(f) for f in (
            "test", "iterations", "description", "gen_test", "gen_opts",
            "rtl_test", "sim_opts", "compare_opts", "gcc_opts", "iss_opts",
            "asm_test", "c_test", "no_iss", "no_gcc", "no_post_compare",
        ) if f in data}
        extras = {k: v for k, v in data.items() if k not in known and k != "import"}
        kwargs = {
            "test": known.get("test", ""),
            "iterations": int(known.get("iterations", 0) or 0),
            "description": str(known.get("description", "") or "").strip(),
            "gen_test": str(known.get("gen_test", "") or ""),
            "gen_opts": str(known.get("gen_opts", "") or "").strip(),
            "rtl_test": str(known.get("rtl_test", "") or ""),
            "sim_opts": str(known.get("sim_opts", "") or "").strip(),
            "compare_opts": str(known.get("compare_opts", "") or "").strip(),
            "gcc_opts": str(known.get("gcc_opts", "") or "").strip(),
            "iss_opts": str(known.get("iss_opts", "") or "").strip(),
            "asm_test": str(known.get("asm_test", "") or ""),
            "c_test": str(known.get("c_test", "") or ""),
            "no_iss": int(known.get("no_iss", 0) or 0),
            "no_gcc": int(known.get("no_gcc", 0) or 0),
            "no_post_compare": int(known.get("no_post_compare", 0) or 0),
            "extras": extras,
        }
        return cls(**kwargs)


def _substitute_root(s: str, riscv_dv_root: Path | str) -> str:
    return s.replace("<riscv_dv_root>", str(riscv_dv_root))


def load_testlist(
    path: Path | str,
    *,
    riscv_dv_root: Path | str,
    test_filter: str | Iterable[str] = "all",
    iteration_override: int = 0,
) -> list[TestEntry]:
    """Load ``path`` recursively, flattening ``import`` directives.

    Parameters
    ----------
    path : Path or str
        Path to the top-level testlist YAML file.
    riscv_dv_root : Path or str
        Root directory for ``<riscv_dv_root>`` substitution (typically the
        riscv-dv repo root).
    test_filter : str or iterable of str
        ``"all"`` to accept every entry, else a comma-separated name string
        or an iterable of names.
    iteration_override : int
        If > 0, force every entry's ``iterations`` to this value (SV's
        ``--iterations`` flag on ``run.py``).
    """
    if isinstance(test_filter, str):
        filter_names = None if test_filter == "all" else set(test_filter.split(","))
    else:
        names = set(test_filter)
        filter_names = None if "all" in names else names

    matched: list[TestEntry] = []

    def _recurse(yaml_path: Path) -> None:
        if not yaml_path.exists():
            raise FileNotFoundError(f"Testlist YAML not found: {yaml_path}")
        with yaml_path.open("r") as fh:
            data = yaml.safe_load(fh) or []
        if not isinstance(data, list):
            raise ValueError(
                f"{yaml_path}: testlist YAML must be a list of entries, got {type(data).__name__}"
            )
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if "import" in entry:
                sub = Path(_substitute_root(entry["import"], riscv_dv_root))
                if not sub.is_absolute():
                    sub = (yaml_path.parent / sub).resolve()
                _recurse(sub)
                continue
            if "test" not in entry:
                continue
            te = TestEntry.from_dict(entry)
            if filter_names is not None and te.test not in filter_names:
                continue
            if iteration_override > 0 and te.iterations > 0:
                te.iterations = iteration_override
            if te.iterations > 0:
                matched.append(te)

    _recurse(Path(path))
    return matched
