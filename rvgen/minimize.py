"""Failure minimizer — shrink a failing ``.S`` to a minimal reproducer.

Verification engineers regularly hit a failure on a 3000-line random
test and spend hours hand-bisecting which 5 instructions actually
cause the bug. This module automates that step: given a failing test
and a "fail predicate" (a callable that returns True when a candidate
.S still reproduces the failure), it ddmin-shrinks the test to the
smallest subset of main-section instructions that still fails.

The resulting reproducer typically lands in the 3-20 instruction range,
making triage minutes-of-work instead of hours-of-work.

Algorithm — ddmin (Andreas Zeller's delta-debugging minimization):

1. Split the main-section instruction list into ``n`` equal subsets.
2. For each subset, try the test with *only that subset* as the main
   sequence. If any reproduces the failure, recurse on it with n=2.
3. Otherwise, try the test with *each subset removed*. If any
   reproduces, recurse on the remaining instructions with n−1
   subsets.
4. If neither works, double n (up to len(main)) and repeat. Bottom
   out when n > len(main) — return the current (now minimal) seq.

This is O(n log n) candidate runs in the average case. Each candidate
runs gcc + ISS, so on a typical RISC-V toolchain the wall-clock cost
is minutes — well within "leave it running over coffee" territory.

Usage::

    from rvgen.minimize import minimize_asm, default_iss_predicate

    pred = default_iss_predicate(target="rv32imc")
    minimal_lines = minimize_asm(failing_path, fail_predicate=pred)
    Path("minimized.S").write_text("\\n".join(minimal_lines))

Limitations:

* Only minimizes the ``main:`` section. Boot, trap-handler, and data
  sections stay intact (they're usually not the bug source).
* ddmin assumes the predicate is *monotonic*: if a subset reproduces,
  every larger subset containing it also reproduces. Most real-world
  failures satisfy this; some (timing-sensitive races) don't.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .S parsing — split into preamble / main-body / trailer.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AsmStructure:
    """Parsed view of a generated ``.S`` file.

    The minimizer only mutates ``main_body``; ``preamble`` (everything
    up to the ``main:`` label) and ``trailer`` (everything from
    ``test_done:`` onward) stay byte-identical so boot + termination
    paths are preserved.
    """

    preamble: list[str]    # ".include"... up to and including "main:"
    main_body: list[str]   # instruction lines under main:
    trailer: list[str]     # "test_done:" onward (handlers, data sections, etc.)


_LABEL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")


_LABEL_LEAD_RE = re.compile(r"^\s*((?:[hH]\d+_)?(?:main|test_done))\s*:")


def parse_asm(lines: list[str]) -> AsmStructure:
    """Split ``lines`` at the ``main:`` and ``test_done:`` markers.

    Both markers are unconditionally emitted by AsmProgramGen; users
    don't typically need to override the boundaries. The label may
    appear:

    - on its own line (``main:`` then a separate instruction line), or
    - as a label-prefix with the first instruction on the same line
      (``main:             c.xor a2, a2``).

    Both are handled. When the first body instruction shares the
    ``main:`` line, we keep the ``main:`` token in the preamble and
    push the trailing instruction into ``main_body[0]``.
    """
    main_idx = -1
    main_inline_instr: str | None = None
    trailer_idx = -1

    for i, line in enumerate(lines):
        m = _LABEL_LEAD_RE.match(line)
        if not m:
            continue
        # Strip a leading h<N>_ hart prefix (only the first underscore-
        # separated chunk if it matches h<digits>); the rest of the
        # label name is the token we care about. ``test_done`` keeps
        # its internal underscore.
        raw = m.group(1)
        if re.match(r"^[hH]\d+_", raw):
            token = raw.split("_", 1)[1]
        else:
            token = raw
        if token == "main" and main_idx < 0:
            main_idx = i
            # Capture any text after the colon (excluding the leading
            # whitespace + label) as the first body instruction.
            after = line[m.end():].rstrip()
            if after.strip():
                main_inline_instr = after
        elif token == "test_done" and trailer_idx < 0:
            trailer_idx = i
            break

    if main_idx < 0:
        raise ValueError("No `main:` label found in .S — can't minimize.")
    if trailer_idx < 0:
        # Fallback: trailer starts at end-of-file. Some bare-mode tests
        # don't emit test_done:.
        trailer_idx = len(lines)

    # Preamble keeps the ``main:`` line itself; if there's an inline
    # instruction after the colon, replace it with just the bare label
    # in the preamble and prepend the instruction into main_body.
    if main_inline_instr is not None:
        preamble = list(lines[:main_idx]) + [
            re.sub(r":(\s*[^\s].*)$", ":", lines[main_idx])
        ]
        main_body = [main_inline_instr] + list(lines[main_idx + 1:trailer_idx])
    else:
        preamble = list(lines[:main_idx + 1])
        main_body = list(lines[main_idx + 1:trailer_idx])

    return AsmStructure(
        preamble=preamble,
        main_body=main_body,
        trailer=list(lines[trailer_idx:]),
    )


def assemble(struct: AsmStructure, body_subset: list[str]) -> list[str]:
    """Rebuild a full .S from the parsed structure + a (subset of) main body."""
    return list(struct.preamble) + list(body_subset) + list(struct.trailer)


# ---------------------------------------------------------------------------
# Candidate evaluation — gcc + ISS (or a user-supplied predicate).
# ---------------------------------------------------------------------------


def default_iss_predicate(
    *,
    target: str,
    isa: str | None = None,
    mabi: str | None = None,
    iss: str = "spike",
    priv: str = "m",
    iss_timeout_s: int = 30,
    expected_outcome: str = "fail",
) -> Callable[[list[str]], bool]:
    """Return a fail-predicate that runs gcc + ISS on the candidate.

    Returns True iff the candidate's ISS run matches
    ``expected_outcome``. Two outcomes are recognised:

    - ``"fail"`` (default): predicate True on any non-zero ISS rc
      (compile failures count as fails too — sometimes the bug *is*
      a compile failure).
    - ``"hang"``: predicate True only on ISS timeout (rc=124).
    - ``"pass"``: predicate True only on rc=0 — useful for
      maximization rather than minimization, but the same delta
      machinery applies.

    The closure stamps a fresh tempdir per candidate so concurrent
    minimizers don't trample each other's outputs.
    """
    from rvgen.gcc import default_link_script, gcc_compile
    from rvgen.iss import run_iss
    from rvgen.targets import get_target
    from rvgen.testlist import TestEntry
    from rvgen.cli import _infer_isa, _infer_mabi

    target_cfg = get_target(target)
    final_isa = isa or _infer_isa(target_cfg)
    final_mabi = mabi or _infer_mabi(target_cfg)

    def _predicate(lines: list[str]) -> bool:
        # Each candidate gets a scratch dir.
        with tempfile.TemporaryDirectory(prefix="rvgen-min-") as tmp:
            tmp_path = Path(tmp)
            asm_dir = tmp_path / "asm_test"
            asm_dir.mkdir()
            (asm_dir / "candidate_0.S").write_text("\n".join(lines) + "\n")
            tests = [TestEntry(test="candidate", iterations=1,
                               gen_opts="", gcc_opts="")]
            link_script = default_link_script(tmp_path)
            try:
                gcc_results = gcc_compile(
                    tests, output_dir=tmp_path,
                    riscv_dv_root=Path("."),
                    isa=final_isa, mabi=final_mabi,
                    extra_gcc_opts="",
                    link_script=link_script,
                )
            except Exception:
                return expected_outcome == "fail"

            if not gcc_results or any(r.returncode != 0 for r in gcc_results):
                return expected_outcome == "fail"

            try:
                iss_results = run_iss(
                    iss, [r for r in gcc_results if r.returncode == 0],
                    output_dir=tmp_path, isa=final_isa, priv=priv,
                    timeout_s=iss_timeout_s, enable_trace=False,
                )
            except Exception:
                return expected_outcome == "fail"

            if not iss_results:
                return False
            rc = iss_results[0].returncode
            if expected_outcome == "fail":
                return rc != 0
            if expected_outcome == "hang":
                return rc == 124
            if expected_outcome == "pass":
                return rc == 0
            raise ValueError(f"Unknown expected_outcome {expected_outcome!r}")

    return _predicate


# ---------------------------------------------------------------------------
# ddmin core — Andreas Zeller's delta-debug minimization.
# ---------------------------------------------------------------------------


def ddmin(
    items: list,
    fail_predicate: Callable[[list], bool],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list:
    """Return the smallest subset of ``items`` that ``fail_predicate`` accepts.

    Contract: ``fail_predicate(items)`` must currently return True
    (otherwise there's nothing to shrink). The returned subset is
    guaranteed to also satisfy the predicate, and removing any single
    element would make it stop satisfying.

    Reference: Zeller & Hildebrandt, "Simplifying and Isolating
    Failure-Inducing Input" (2002).
    """
    if not fail_predicate(items):
        # Predicate doesn't fire on the input — nothing to minimize.
        return items

    n = 2
    candidate = list(items)
    iterations = 0

    while len(candidate) >= 2:
        iterations += 1
        if on_progress is not None:
            on_progress(iterations, len(candidate))

        # Cap n at the candidate length — finer granularity doesn't make sense.
        n = min(n, len(candidate))
        chunk_size = max(1, len(candidate) // n)

        # 1. Try each subset alone.
        progressed = False
        for i in range(n):
            start = i * chunk_size
            end = (i + 1) * chunk_size if i < n - 1 else len(candidate)
            subset = candidate[start:end]
            if fail_predicate(subset):
                candidate = subset
                n = 2
                progressed = True
                break
        if progressed:
            continue

        # 2. Try removing each subset (i.e. keep complement).
        for i in range(n):
            start = i * chunk_size
            end = (i + 1) * chunk_size if i < n - 1 else len(candidate)
            complement = candidate[:start] + candidate[end:]
            if complement and fail_predicate(complement):
                candidate = complement
                n = max(n - 1, 2)
                progressed = True
                break
        if progressed:
            continue

        # 3. Increase granularity (more / smaller subsets). Bail when we
        # can't go finer than 1-element subsets without making progress —
        # the candidate is already minimal.
        if n >= len(candidate):
            break
        n = min(n * 2, len(candidate))

    return candidate


# ---------------------------------------------------------------------------
# Top-level helper
# ---------------------------------------------------------------------------


def minimize_asm(
    asm_path: Path | str,
    fail_predicate: Callable[[list[str]], bool],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Shrink ``asm_path`` to the smallest body-subset that still fails.

    Returns the full .S as a list of lines (preamble + minimized body
    + trailer). Caller writes it back to disk.
    """
    raw = Path(asm_path).read_text().splitlines()
    struct = parse_asm(raw)
    _LOG.info(
        "minimize: starting body=%d lines (preamble=%d, trailer=%d)",
        len(struct.main_body), len(struct.preamble), len(struct.trailer),
    )

    def _candidate_predicate(body: list[str]) -> bool:
        return fail_predicate(assemble(struct, body))

    minimal_body = ddmin(struct.main_body, _candidate_predicate,
                         on_progress=on_progress)
    _LOG.info(
        "minimize: shrunk body from %d to %d lines",
        len(struct.main_body), len(minimal_body),
    )
    return assemble(struct, minimal_body)


# ---------------------------------------------------------------------------
# CLI entry point — `python -m rvgen.minimize`
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    """``python -m rvgen.minimize`` — shrink a failing .S to a minimal repro.

    Usage::

        python -m rvgen.minimize \\
            --asm fail.S --target rv32imc \\
            --output minimized.S \\
            [--priv m] [--iss spike] [--iss_timeout 30] \\
            [--expected fail|hang|pass]
    """
    import argparse
    import logging
    import sys

    p = argparse.ArgumentParser(
        prog="python -m rvgen.minimize",
        description="Shrink a failing .S to the smallest reproducer.",
    )
    p.add_argument("--asm", required=True, help="Path to the failing .S file.")
    p.add_argument("--target", required=True, help="rvgen target name.")
    p.add_argument("--output", required=True, help="Where to write the minimized .S.")
    p.add_argument("--isa", default=None, help="Override -march for gcc.")
    p.add_argument("--mabi", default=None, help="Override -mabi for gcc.")
    p.add_argument("--priv", default="m", help="Spike --priv value (m/su/msu).")
    p.add_argument("--iss", default="spike")
    p.add_argument("--iss_timeout", type=int, default=30,
                   help="Per-candidate ISS timeout in seconds.")
    p.add_argument("--expected", default="fail",
                   choices=("fail", "hang", "pass"),
                   help="Outcome the predicate looks for. 'fail' (default) "
                        "minimizes a failing test; 'hang' minimizes a "
                        "timeout; 'pass' minimizes a passing test.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    pred = default_iss_predicate(
        target=args.target, isa=args.isa, mabi=args.mabi,
        iss=args.iss, priv=args.priv,
        iss_timeout_s=args.iss_timeout,
        expected_outcome=args.expected,
    )

    def progress(it, size):
        print(f"  iter {it}: candidate has {size} body lines",
              file=sys.stderr)

    minimal = minimize_asm(args.asm, pred, on_progress=progress)
    Path(args.output).write_text("\n".join(minimal) + "\n")
    print(f"Wrote minimized .S to {args.output} ({len(minimal)} lines)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
