"""Seed-generation helper — port of ``run.py::SeedGen`` (scripts/lib.py).

Semantics:
- ``--seed <n>``       → fixed seed, iterations forced to 1.
- ``--start_seed <n>`` → increments by ``iteration`` each call.
- ``--seed_yaml <f>``  → a previously-dumped ``seed.yaml`` (``{test_id_batch: seed}``) is
                         replayed verbatim; on miss this raises a clear error.
- (none)               → a fresh random 31-bit seed per call (``random.getrandbits(31)``).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SeedGen:
    """Stateful seed provider mirroring riscv-dv's ``SeedGen``."""

    start_seed: int | None = None
    fixed_seed: int | None = None
    rerun_seeds: dict[str, int] | None = None

    _rng: random.Random = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Validation: seed / start_seed / seed_yaml are mutually exclusive.
        set_count = sum(
            1 for x in (self.start_seed, self.fixed_seed, self.rerun_seeds) if x is not None
        )
        if set_count > 1:
            raise ValueError(
                "seed, start_seed, and seed_yaml are mutually exclusive."
            )
        self._rng = random.Random()

    @classmethod
    def from_yaml(cls, path: Path | str) -> "SeedGen":
        """Load a previously-saved ``seed.yaml`` map."""
        with Path(path).open("r") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping, got {type(data).__name__}")
        seeds = {k: int(v) for k, v in data.items()}
        return cls(rerun_seeds=seeds)

    def get(self, test_id: str, iteration: int = 0) -> int:
        """Return the seed for ``test_id`` at ``iteration`` (0-based).

        The test_id convention matches riscv-dv: ``"<test_name>_<batch_index>"``.
        """
        if self.rerun_seeds is not None:
            try:
                return self.rerun_seeds[test_id]
            except KeyError as e:
                raise KeyError(
                    f"seed_yaml has no entry for {test_id!r}"
                ) from e
        if self.fixed_seed is not None:
            if iteration != 0:
                raise ValueError(
                    "Fixed --seed is incompatible with --iterations > 1 "
                    "(riscv-dv forces iterations=1)."
                )
            return self.fixed_seed
        if self.start_seed is not None:
            return self.start_seed + iteration
        # Default: random 31-bit seed per call.
        return self._rng.getrandbits(31)

    def dump(self, path: Path | str, observed: dict[str, int]) -> None:
        """Write ``observed`` (test_id → seed) to ``path`` as YAML."""
        with Path(path).open("w") as fh:
            yaml.safe_dump({k: int(v) for k, v in observed.items()}, fh, default_flow_style=False)
