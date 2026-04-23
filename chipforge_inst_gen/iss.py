"""Instruction-set simulator driver — port of ``run.py::iss_sim``.

Phase 1 supports spike only. Other ISSes (ovpsim, sail, whisper) can be
added later by extending the dispatch in :func:`run_iss`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from chipforge_inst_gen.gcc import GccResult


_LOG = logging.getLogger("chipforge_inst_gen.iss")


def _find_spike() -> str:
    """Resolve spike binary, preferring $SPIKE_PATH, $SPIKE, then PATH."""
    env = os.environ.get("SPIKE_PATH")
    if env:
        path = Path(env) / "spike" if Path(env).is_dir() else Path(env)
        if path.exists():
            return str(path)
    env = os.environ.get("SPIKE")
    if env and Path(env).exists():
        return env
    p = shutil.which("spike")
    if p:
        return p
    for candidate in (
        "/home/qamar/tools/spike-install/bin/spike",
        "/opt/riscv/bin/spike",
    ):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("spike not found. Set $SPIKE_PATH or $SPIKE.")


@dataclass
class IssResult:
    test_id: str
    elf_path: Path
    log_path: Path
    returncode: int
    log: str
    trace_path: Path | None = None


def run_iss(
    iss: str,
    results: Iterable[GccResult],
    *,
    output_dir: Path,
    isa: str,
    priv: str = "m",
    timeout_s: int = 30,
    extra_iss_opts: str = "",
    memory_size_bytes: int = 0x200000,
    memory_base: int = 0x80000000,
    enable_trace: bool = False,
) -> list[IssResult]:
    """Run each compiled ELF on the chosen ISS. Phase 1: spike only.

    ``enable_trace=True`` adds spike's ``-l`` flag, which produces an
    instruction trace suitable for runtime coverage ingestion. The trace
    is ~100x larger than the default stdout (every retired instruction
    logs a line) so callers should request it only when runtime coverage
    is wanted.
    """
    iss = iss.lower()
    if iss != "spike":
        raise NotImplementedError(
            f"ISS {iss!r} not yet supported. Phase 1 covers spike only."
        )

    spike = _find_spike()
    log_dir = output_dir / "spike_sim"
    log_dir.mkdir(parents=True, exist_ok=True)
    out: list[IssResult] = []

    for res in results:
        if res.returncode != 0 or not res.elf_path.exists():
            _LOG.warning("Skipping ISS for %s (gcc failure)", res.test_id)
            continue
        log_path = log_dir / f"{res.test_id}.log"
        trace_path = log_dir / f"{res.test_id}.trace" if enable_trace else None
        # ``--misaligned`` mirrors riscv-dv yaml/iss.yaml — spike otherwise
        # traps the ``addi tp, tp, -4`` / ``sd`` push prologue on RV64 (the
        # trap frame convention is 4-byte aligned, but SD is 8-byte aligned).
        cmd = [
            spike,
            f"--isa={isa}",
            f"--priv={priv}",
            "--misaligned",
            f"-m0x{memory_base:x}:0x{memory_size_bytes:x}",
        ]
        if enable_trace:
            # --log-commits adds a retirement-line per instruction showing
            # any GPR / CSR write that actually happened — lets the coverage
            # parser sample actual runtime values (CSR destinations,
            # destination register values).
            cmd += ["-l", "--log-commits", f"--log={trace_path}"]
        if extra_iss_opts:
            cmd += extra_iss_opts.split()
        cmd.append(str(res.elf_path))
        _LOG.info("Running spike: %s", res.elf_path)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s
            )
            returncode = proc.returncode
            log = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as e:
            returncode = 124
            log = f"TIMEOUT after {timeout_s}s\n{e.stderr or ''}"
        log_path.write_text(log)
        out.append(IssResult(
            test_id=res.test_id,
            elf_path=res.elf_path,
            log_path=log_path,
            returncode=returncode,
            log=log,
            trace_path=trace_path,
        ))
    return out
