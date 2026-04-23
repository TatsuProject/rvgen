"""Per-target processor configuration — the rvgen analogue of
riscv-dv's ``target/<name>/riscv_core_setting.sv``.

Public API:

- :class:`TargetCfg` — the config dataclass (in :mod:`.core_setting`).
- :func:`get_target` — look up a target by name, falling back to the
  user area when the name isn't in the built-in set.
- :func:`target_names` — the sorted list of every known target
  (built-in + whatever lives under ``<user_dir>/targets/``).
- :func:`load_target_yaml` — parse a YAML config directly (for ad-hoc
  tests or CLI ``--target_config`` usage).

Module layout:

- :mod:`rvgen.targets.core_setting` — the :class:`TargetCfg` dataclass.
- :mod:`rvgen.targets.presets` — reusable CSR / interrupt / exception
  tuples (``MMODE_CSRS``, ``USM_EXCEPTIONS``, …).
- :mod:`rvgen.targets.builtin` — the 27 framework-shipped targets.
- :mod:`rvgen.targets.loader` — YAML loader + user-area discovery.

User-authored targets live under the **user area** (see
``user/README.md``) — typically ``user/targets/<name>.yaml``. The
user-dir location is resolved from ``--user_dir``, then
``$RVGEN_USER_DIR``, then ``./user`` relative to the current working
directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from rvgen.targets.builtin import BUILTIN_TARGETS
from rvgen.targets.core_setting import TargetCfg
from rvgen.targets.loader import (
    discover_user_targets,
    load_target_yaml,
)

__all__ = [
    "TargetCfg",
    "BUILTIN_TARGETS",
    "get_target",
    "target_names",
    "iter_targets",
    "load_target_yaml",
    "resolve_user_dir",
    "set_user_dir",
]


# ---------------------------------------------------------------------------
# User-area resolution
# ---------------------------------------------------------------------------


# The "active" user directory. Initialised lazily; overridden by the
# CLI via :func:`set_user_dir` after argparse runs. Tests that want to
# bypass discovery can call ``set_user_dir(None)``.
_USER_DIR: Path | None = None


def resolve_user_dir() -> Path | None:
    """Return the effective user-area directory, or ``None`` if disabled.

    Precedence: the programmatically-set directory (via
    :func:`set_user_dir`) → ``$RVGEN_USER_DIR`` → ``./user`` relative to
    the current working directory (only if the directory exists).
    """
    if _USER_DIR is not None:
        return _USER_DIR
    env = os.environ.get("RVGEN_USER_DIR")
    if env:
        return Path(env)
    default = Path.cwd() / "user"
    if default.is_dir():
        return default
    return None


def set_user_dir(path: Path | str | None) -> None:
    """Override the user-area directory for the rest of this process.

    Pass ``None`` to clear the override and fall back to env / cwd.
    """
    global _USER_DIR
    _USER_DIR = Path(path) if path is not None else None


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_target(name: str) -> TargetCfg:
    """Look up a target by name.

    Built-in targets win over user-area targets with the same name, so
    users can shadow-rename without colliding. To truly override a
    built-in, delete it from :data:`BUILTIN_TARGETS` via a fork (rare).

    Raises ``KeyError`` with a helpful hint if the name is unknown.
    """
    if name in BUILTIN_TARGETS:
        return BUILTIN_TARGETS[name]
    user_dir = resolve_user_dir()
    if user_dir is not None:
        user = discover_user_targets(user_dir)
        if name in user:
            return load_target_yaml(user[name])
    raise KeyError(
        f"Unknown target {name!r}. Known built-in: {sorted(BUILTIN_TARGETS)}. "
        f"User area: {user_dir or '(none)'}"
    )


def target_names() -> tuple[str, ...]:
    """Sorted list of every known target — built-in + user area."""
    names = set(BUILTIN_TARGETS)
    user_dir = resolve_user_dir()
    if user_dir is not None:
        names |= set(discover_user_targets(user_dir))
    return tuple(sorted(names))


def iter_targets() -> Iterable[TargetCfg]:
    """Iterate built-in + user-area targets in sorted-name order."""
    for name in target_names():
        yield get_target(name)


# Deprecated alias — older code referenced ``_TARGETS``. New callers
# should use :data:`BUILTIN_TARGETS` or :func:`get_target`.
_TARGETS = BUILTIN_TARGETS
