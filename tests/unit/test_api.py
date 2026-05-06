"""Tests for the Library API (rvgen.api / rvgen.Generator / rvgen.Pipeline)."""

from __future__ import annotations

import os

import pytest

from rvgen import Generator, Pipeline, PipelineResult, GeneratedAsm


def test_generator_imports_from_top_level_package():
    # The whole point of the public API: `from rvgen import Generator`.
    import rvgen
    assert rvgen.Generator is Generator
    assert rvgen.Pipeline is Pipeline


def test_generator_basic_run():
    g = Generator(target="rv32imc", test="riscv_rand_instr_test",
                  start_seed=100, iterations=2)
    asm = g.generate()
    assert len(asm) == 2
    assert all(isinstance(a, GeneratedAsm) for a in asm)
    assert asm[0].test_id == "riscv_rand_instr_test_0"
    assert asm[1].test_id == "riscv_rand_instr_test_1"
    assert asm[0].seed == 100
    assert asm[1].seed == 101


def test_generator_returns_text_with_trailing_newline():
    g = Generator(target="rv32imc", iterations=1)
    asm = g.generate()
    assert asm[0].text.endswith("\n")


def test_generator_writes_to_path(tmp_path):
    g = Generator(target="rv32imc", iterations=1)
    asm = g.generate()
    out_path = tmp_path / "test.S"
    written = asm[0].write(out_path)
    assert written == out_path
    assert out_path.exists()
    assert out_path.read_text().startswith('.include "user_define.h"')


def test_generator_fixed_seed_overrides_iterations():
    g = Generator(target="rv32imc", iterations=5, seed=42)
    asm = g.generate()
    # `seed` forces single iteration regardless of `iterations`.
    assert len(asm) == 1
    assert asm[0].seed == 42


def test_generator_gen_opts_propagate():
    # +no_fence=1 disables FENCE emission. The static covergroup
    # bin doesn't get hit in the random stream.
    g = Generator(target="rv32imc", iterations=1, gen_opts="+no_fence=1")
    asm = g.generate()
    text = asm[0].text
    # FENCE should not appear in the random body.
    # (FENCE in the boot/handler asm is fine; just ensure none in main.)
    main_idx = text.find("main:")
    test_done_idx = text.find("test_done:")
    main_section = text[main_idx:test_done_idx] if main_idx >= 0 else text
    assert "fence" not in main_section.lower() or "sfence" in main_section


def test_generator_main_program_instr_cnt_override():
    g = Generator(target="rv32imc", iterations=1, main_program_instr_cnt=50)
    asm = g.generate()
    text = asm[0].text
    # The main: section should have ~50 instrs. Roughly counted by lines
    # with leading spaces (instr lines).
    main_idx = text.find("main:")
    test_done_idx = text.find("test_done:")
    if main_idx >= 0 and test_done_idx > main_idx:
        body = text[main_idx:test_done_idx]
        line_count = body.count("\n")
        # Allow plenty of slack for hazard pads + directed streams.
        assert line_count < 200


def test_generator_seed_sequence_is_deterministic():
    g1 = Generator(target="rv32imc", iterations=2, start_seed=42)
    g2 = Generator(target="rv32imc", iterations=2, start_seed=42)
    a1 = g1.generate()
    a2 = g2.generate()
    assert a1[0].text == a2[0].text
    assert a1[1].text == a2[1].text


def test_pipeline_gen_only_writes_files(tmp_path):
    p = Pipeline(target="rv32imc", test="riscv_arithmetic_basic_test",
                 start_seed=100, iterations=1,
                 output_dir=str(tmp_path))
    result = p.run(steps=["gen"])
    assert isinstance(result, PipelineResult)
    assert len(result.asm) == 1
    assert (tmp_path / "asm_test" / "riscv_arithmetic_basic_test_0.S").exists()
    # gcc/iss not requested → empty.
    assert result.gcc_results == []
    assert result.iss_results == []


def test_pipeline_full_run_through_iss(tmp_path):
    # Requires the RISC-V GNU toolchain + Spike on $PATH. On CI runners
    # without those, skip rather than fail — the unit-test layer already
    # covers the Python pipeline; gcc/iss is an integration concern.
    import shutil
    if not (shutil.which("riscv64-unknown-elf-gcc") or
            os.environ.get("RISCV_GCC") or os.environ.get("RISCV_TOOLCHAIN")):
        pytest.skip("riscv-gcc not available — integration test")
    if not shutil.which("spike"):
        pytest.skip("spike not available — integration test")

    p = Pipeline(target="rv32imc", test="riscv_arithmetic_basic_test",
                 start_seed=100, iterations=1,
                 output_dir=str(tmp_path))
    result = p.run(steps=["gen", "gcc_compile", "iss_sim"])
    # gen produced 1 asm.
    assert len(result.asm) == 1
    # gcc compiled it cleanly.
    assert len(result.gcc_results) == 1
    assert all(r.returncode == 0 for r in result.gcc_results)
    # iss ran.
    assert len(result.iss_results) == 1
    assert all(r.returncode == 0 for r in result.iss_results)


def test_pipeline_target_unknown_raises():
    p = Pipeline(target="rv99nonsense", iterations=1)
    with pytest.raises(KeyError):
        p.run(steps=["gen"])
