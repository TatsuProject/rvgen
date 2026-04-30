"""rvgen — pure-Python RISC-V random instruction generator.

Phase 1 goal: structural + ISS-equivalent parity with riscv-dv.
Phase 2 goal: extensions beyond riscv-dv (RVV, Zk*, Zfh, Zc*, declarative YAML).

Library API entry points (preferred for embedding rvgen in another tool)::

    from rvgen import Generator, Pipeline, GeneratedAsm

    g = Generator(target="rv64gcv", test="riscv_rand_instr_test", iterations=4)
    asm = g.generate()  # list[GeneratedAsm]

    p = Pipeline(target="rv64gc", test="riscv_arithmetic_basic_test")
    result = p.run(steps=["gen", "gcc_compile", "iss_sim"])
"""

__version__ = "0.1.0"

from rvgen.api import GeneratedAsm, Generator, Pipeline, PipelineResult

__all__ = ["GeneratedAsm", "Generator", "Pipeline", "PipelineResult", "__version__"]
