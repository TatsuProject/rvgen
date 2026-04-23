"""riscv-gcc invocation + objcopy — port of ``run.py::gcc_compile``.

Invokes ``riscv64-unknown-elf-gcc`` (or whatever is pointed to by the
``RISCV_GCC`` env var) on every generated ``.S`` file and optionally runs
objcopy to produce raw binaries.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rvgen.testlist import TestEntry


_LOG = logging.getLogger("rvgen.gcc")


def _find_gcc() -> str:
    """Resolve the GCC binary, preferring $RISCV_GCC → $RISCV_TOOLCHAIN/bin → PATH."""
    env = os.environ.get("RISCV_GCC")
    if env:
        return env
    tc = os.environ.get("RISCV_TOOLCHAIN")
    if tc:
        for name in ("riscv64-unknown-elf-gcc", "riscv64-unknown-linux-gnu-gcc"):
            path = Path(tc) / "bin" / name
            if path.exists():
                return str(path)
    for name in ("riscv64-unknown-elf-gcc", "riscv-none-elf-gcc"):
        path = shutil.which(name)
        if path:
            return path
    # Last-resort: well-known install dirs from this user's setup.
    for candidate in (
        "/home/qamar/tools/riscv-elf/bin/riscv64-unknown-elf-gcc",
        "/opt/riscv/bin/riscv64-unknown-elf-gcc",
    ):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "riscv-gcc not found. Set $RISCV_GCC or $RISCV_TOOLCHAIN."
    )


def _find_objcopy() -> str:
    env = os.environ.get("RISCV_OBJCOPY")
    if env:
        return env
    # Sibling of gcc.
    gcc = _find_gcc()
    sibling = gcc.replace("-gcc", "-objcopy")
    if Path(sibling).exists():
        return sibling
    for name in ("riscv64-unknown-elf-objcopy", "riscv-none-elf-objcopy"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError("riscv-objcopy not found. Set $RISCV_OBJCOPY.")


_ISA_STRIP_C = re.compile(r"(rv(?:32|64)\w*?)c")


def _strip_compressed_from_isa(isa: str) -> str:
    """Port of run.py gcc_compile behavior: strip ``c`` when disable_compressed_instr.

    riscv-dv's ``run.py`` at line 437 strips the trailing ``c`` from the ISA
    string before passing to GCC when ``+disable_compressed_instr`` appears
    in ``gen_opts``. We keep the rule local since it's a GCC-compile concern.
    """
    m = _ISA_STRIP_C.match(isa)
    if m:
        return isa[:m.end(1)] + isa[m.end():]
    return isa


@dataclass
class GccResult:
    test_id: str
    asm_path: Path
    elf_path: Path
    bin_path: Path
    returncode: int
    log: str


def gcc_compile(
    tests: Iterable[TestEntry],
    *,
    output_dir: Path,
    riscv_dv_root: Path,
    isa: str,
    mabi: str,
    extra_gcc_opts: str = "",
    include_dirs: Iterable[Path] = (),
    link_script: Path | None = None,
) -> list[GccResult]:
    """Compile every iteration of every test into ``.o`` (+ ``.bin``).

    Parameters mirror ``run.py::gcc_compile``; additional ``include_dirs``
    lets the caller add ``-I`` flags for shim headers.
    """
    gcc = _find_gcc()
    objcopy = _find_objcopy()
    asm_dir = output_dir / "asm_test"
    results: list[GccResult] = []

    # Ensure user_define.h + user_init.s exist (riscv-dv provides them but
    # they're expected to be empty if the test doesn't use them).
    for shim in ("user_define.h", "user_init.s"):
        p = output_dir / shim
        if not p.exists():
            p.write_text("")

    include_args: list[str] = [f"-I{output_dir}"]
    include_args += [f"-I{Path(d)}" for d in include_dirs]

    for te in tests:
        if te.no_gcc:
            continue
        for it in range(te.iterations):
            test_id = f"{te.test}_{it}"
            asm_path = asm_dir / f"{test_id}.S"
            elf_path = asm_dir / f"{test_id}.o"
            bin_path = asm_dir / f"{test_id}.bin"

            test_isa = isa
            if "+disable_compressed_instr" in te.gen_opts:
                test_isa = _strip_compressed_from_isa(test_isa)

            cmd = [
                gcc,
                f"-march={test_isa}",
                f"-mabi={mabi}",
                "-static", "-mcmodel=medany",
                "-fvisibility=hidden",
                "-nostdlib", "-nostartfiles",
                *include_args,
            ]
            if link_script is not None:
                cmd.append(f"-T{link_script}")
            if te.gcc_opts:
                cmd += te.gcc_opts.split()
            if extra_gcc_opts:
                cmd += extra_gcc_opts.split()
            cmd += [str(asm_path), "-o", str(elf_path)]
            _LOG.info("Compiling %s", asm_path)
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                _LOG.error("GCC failed for %s:\n%s", asm_path, proc.stderr)
                results.append(GccResult(
                    test_id=test_id,
                    asm_path=asm_path,
                    elf_path=elf_path,
                    bin_path=bin_path,
                    returncode=proc.returncode,
                    log=proc.stderr,
                ))
                continue

            # objcopy -O binary elf → bin
            subprocess.run(
                [objcopy, "-O", "binary", str(elf_path), str(bin_path)],
                capture_output=True, text=True, check=False,
            )

            results.append(GccResult(
                test_id=test_id,
                asm_path=asm_path,
                elf_path=elf_path,
                bin_path=bin_path,
                returncode=0,
                log="",
            ))
    return results


def default_link_script(output_dir: Path) -> Path:
    """Write a default link script compatible with spike's HTIF layout."""
    path = output_dir / "link.ld"
    path.write_text(
        """\
OUTPUT_ARCH( "riscv" )
ENTRY(_start)
SECTIONS {
  . = 0x80000000;
  .text : { *(.text*) }
  . = ALIGN(0x1000);
  .tohost : { *(.tohost) }
  . = ALIGN(0x1000);
  .data : { *(.data) *(.region_0) *(.region_1) *(.amo_0) }
  .user_stack : { *(.user_stack*) }
  .kernel_stack : { *(.kernel_stack*) }
  .bss : { *(.bss) }
}
"""
    )
    return path
