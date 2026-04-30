"""Library API — embed rvgen programmatically (no subprocess CLI).

Use this when you want to drive rvgen from another Python program
(e.g. a custom verification harness, a Jupyter notebook, a CI script
that needs the asm strings in-memory). Two layers:

* :class:`Generator` — high-level: pick a target + test, get back
  a list of :class:`GeneratedAsm` (one per iteration). Generation
  only — no GCC, no ISS. Use this for piping the asm into other
  tools.
* :class:`Pipeline` — full gen→gcc→iss flow, equivalent to the CLI
  but with results returned as Python objects rather than written
  to disk only.

Both layers are thin — the heavy lifting lives in
:mod:`rvgen.cli`'s helpers, :mod:`rvgen.asm_program_gen`,
:mod:`rvgen.gcc`, and :mod:`rvgen.iss`. We just wrap them in
ergonomic dataclasses so callers don't have to glue argparse
arguments together.

Example::

    from rvgen import Generator
    g = Generator(target="rv64gcv_crypto", test="riscv_rand_instr_test",
                  start_seed=100, iterations=2)
    for asm in g.generate():
        print(asm.test_id, len(asm.lines), "lines, seed", asm.seed)
        print(asm.text[:200])

For end-to-end runs::

    from rvgen import Pipeline
    p = Pipeline(target="rv32imafdc_zfh", test="riscv_rand_instr_test",
                 start_seed=100, iterations=1)
    result = p.run(steps=["gen", "gcc_compile", "iss_sim"])
    for r in result.iss_results:
        print(r.test_id, "rc=", r.returncode)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from rvgen.config import make_config
from rvgen.targets import get_target


# Re-export the most-used types so callers can `from rvgen import X`.
__all__ = [
    "Generator",
    "GeneratedAsm",
    "Pipeline",
    "PipelineResult",
]


@dataclass(slots=True)
class GeneratedAsm:
    """One generated test program — the in-memory equivalent of a `.S` file.

    Attributes
    ----------
    test_id : str
        ``f"{test_name}_{iteration_idx}"`` — matches the CLI's filename.
    test_name : str
        The original testlist entry name (e.g. ``"riscv_rand_instr_test"``).
    iteration : int
        Zero-based iteration index within this test.
    seed : int
        Seed used for this iteration.
    lines : list[str]
        The raw asm lines (no trailing newline).
    """

    test_id: str
    test_name: str
    iteration: int
    seed: int
    lines: list[str]

    @property
    def text(self) -> str:
        """Return the full ``.S`` text with line endings."""
        return "\n".join(self.lines) + "\n"

    def write(self, path: Path | str) -> Path:
        """Write the asm text to a file. Returns the resolved path."""
        p = Path(path)
        p.write_text(self.text)
        return p


@dataclass
class Generator:
    """Generate asm programs without invoking GCC / ISS.

    Constructor arguments mirror the CLI flags that control generation:

    * ``target`` — built-in target name or YAML target name.
    * ``test`` — test name from the testlist (e.g. ``riscv_rand_instr_test``).
    * ``start_seed`` — seed for iteration 0; iteration N uses seed+N.
    * ``seed`` — fixed seed (overrides start_seed; forces iterations=1).
    * ``iterations`` — how many .S files to generate.
    * ``gen_opts`` — gen_opts plusarg string (``+no_fence=1`` etc.).
    * ``main_program_instr_cnt`` — length of the main random sequence.
    """

    target: str
    test: str = "riscv_rand_instr_test"
    start_seed: int = 100
    iterations: int = 1
    seed: int | None = None
    gen_opts: str = ""
    main_program_instr_cnt: int | None = None

    def generate(self) -> list[GeneratedAsm]:
        """Run the generator. Returns one :class:`GeneratedAsm` per iteration."""
        from rvgen.asm_program_gen import AsmProgramGen
        from rvgen.isa import enums  # noqa: F401 — ensure ISA modules imported
        from rvgen.isa.filtering import create_instr_list

        target_cfg = get_target(self.target)
        out: list[GeneratedAsm] = []

        if self.seed is not None:
            seeds = [self.seed]
        else:
            seeds = [self.start_seed + i for i in range(self.iterations)]

        for it, seed in enumerate(seeds):
            cfg = make_config(target_cfg, gen_opts=self.gen_opts)
            cfg.seed = seed
            if self.main_program_instr_cnt is not None:
                cfg.main_program_instr_cnt = self.main_program_instr_cnt

            avail = create_instr_list(cfg)
            rng = random.Random(seed)
            gen = AsmProgramGen(cfg=cfg, avail=avail, rng=rng)
            lines = gen.gen_program()
            out.append(GeneratedAsm(
                test_id=f"{self.test}_{it}",
                test_name=self.test,
                iteration=it,
                seed=seed,
                lines=lines,
            ))
        return out


@dataclass
class PipelineResult:
    """Bundle returned from :meth:`Pipeline.run`.

    Each list is keyed by iteration index; entries that didn't run for
    a given step (because that step wasn't requested) are absent.
    """

    asm: list[GeneratedAsm] = field(default_factory=list)
    gcc_results: list = field(default_factory=list)
    iss_results: list = field(default_factory=list)
    output_dir: Path | None = None


@dataclass
class Pipeline:
    """End-to-end gen → gcc → iss runner.

    Equivalent to the CLI (``python -m rvgen ...``) but returns
    Python objects directly. Output directory is created
    (``./out_<date>`` by default; pass ``output_dir`` to override).
    """

    target: str
    test: str = "riscv_rand_instr_test"
    start_seed: int = 100
    iterations: int = 1
    seed: int | None = None
    gen_opts: str = ""
    output_dir: Path | str | None = None
    isa: str | None = None
    mabi: str | None = None
    iss: str = "spike"
    priv: str = "m"
    iss_timeout_s: int = 30
    iss_trace: bool = False
    main_program_instr_cnt: int | None = None

    def run(self, steps: Iterable[str] = ("gen", "gcc_compile", "iss_sim")) -> PipelineResult:
        """Execute ``steps`` in order. Returns a :class:`PipelineResult`."""
        from rvgen.cli import _infer_isa, _infer_mabi
        from rvgen.gcc import default_link_script, gcc_compile
        from rvgen.iss import run_iss
        from rvgen.testlist import TestEntry

        steps = set(steps)
        target_cfg = get_target(self.target)
        if self.output_dir is None:
            from datetime import date
            output_dir = Path(f"out_{date.today():%Y%m%d}")
        else:
            output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        asm_dir = output_dir / "asm_test"
        asm_dir.mkdir(exist_ok=True)

        result = PipelineResult(output_dir=output_dir)

        # ---- gen ----
        if "gen" in steps:
            gen = Generator(
                target=self.target, test=self.test,
                start_seed=self.start_seed, iterations=self.iterations,
                seed=self.seed, gen_opts=self.gen_opts,
                main_program_instr_cnt=self.main_program_instr_cnt,
            )
            result.asm = gen.generate()
            for a in result.asm:
                a.write(asm_dir / f"{a.test_id}.S")

        # gcc_compile and iss_sim need TestEntry objects (one per
        # iteration). Build them from the asm we just generated.
        if "gcc_compile" in steps or "iss_sim" in steps:
            tests = [
                TestEntry(test=self.test, iterations=self.iterations,
                          gen_opts=self.gen_opts, gcc_opts="")
            ]

            isa = self.isa or _infer_isa(target_cfg)
            mabi = self.mabi or _infer_mabi(target_cfg)

            if "gcc_compile" in steps:
                link_script = default_link_script(output_dir)
                result.gcc_results = gcc_compile(
                    tests,
                    output_dir=output_dir,
                    riscv_dv_root=Path("."),  # not used when we already have .S
                    isa=isa, mabi=mabi,
                    extra_gcc_opts="",
                    link_script=link_script,
                )

            if "iss_sim" in steps:
                ok = [r for r in result.gcc_results if r.returncode == 0]
                if ok:
                    result.iss_results = run_iss(
                        self.iss, ok,
                        output_dir=output_dir,
                        isa=isa,
                        priv=self.priv,
                        timeout_s=self.iss_timeout_s,
                        enable_trace=self.iss_trace,
                    )

        return result
