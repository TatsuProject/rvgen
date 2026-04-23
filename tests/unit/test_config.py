"""Tests for rvgen.config."""

from __future__ import annotations

from rvgen.config import Config, make_config
from rvgen.isa.enums import (
    DataPattern,
    MtvecMode,
    PrivilegedMode,
    RiscvReg,
)
from rvgen.targets import get_target


def test_default_config_fields():
    cfg = Config()
    assert cfg.num_of_tests == 1
    assert cfg.num_of_sub_program == 5
    assert cfg.instr_cnt == 200
    assert cfg.no_ebreak is True
    assert cfg.no_ecall is True
    assert cfg.no_dret is True
    assert cfg.no_wfi is True
    assert cfg.enable_interrupt is False
    assert cfg.init_privileged_mode == PrivilegedMode.MACHINE_MODE
    assert cfg.mtvec_mode == MtvecMode.VECTORED


def test_reserved_regs_computed():
    cfg = Config()
    # Default sp=SP, tp=TP, scratch_reg=T5 → reserved_regs = (TP, SP, T5)
    assert cfg.reserved_regs == (RiscvReg.TP, RiscvReg.SP, RiscvReg.T5)


def test_main_program_instr_cnt_defaults_to_instr_cnt():
    cfg = Config(instr_cnt=500)
    assert cfg.main_program_instr_cnt == 500


def test_apply_plusargs_scalar_int():
    cfg = Config()
    cfg.apply_plusargs("+instr_cnt=5000")
    assert cfg.instr_cnt == 5000
    assert cfg.main_program_instr_cnt == 5000


def test_apply_plusargs_bool():
    cfg = Config()
    cfg.apply_plusargs("+no_fence=1")
    assert cfg.no_fence is True


def test_apply_plusargs_multiple():
    cfg = Config()
    cfg.apply_plusargs(
        "+instr_cnt=5000 +num_of_sub_program=0 +no_fence=1 +no_data_page=1 "
        "+no_branch_jump=1 +no_csr_instr=1"
    )
    assert cfg.instr_cnt == 5000
    assert cfg.num_of_sub_program == 0
    assert cfg.no_fence is True
    assert cfg.no_data_page is True
    assert cfg.no_branch_jump is True
    assert cfg.no_csr_instr is True


def test_apply_plusargs_boot_mode():
    for letter, mode in (
        ("m", PrivilegedMode.MACHINE_MODE),
        ("s", PrivilegedMode.SUPERVISOR_MODE),
        ("u", PrivilegedMode.USER_MODE),
    ):
        cfg = Config()
        cfg.apply_plusargs(f"+boot_mode={letter}")
        assert cfg.init_privileged_mode == mode


def test_apply_plusargs_directed_instr():
    cfg = Config()
    cfg.apply_plusargs(
        "+directed_instr_0=riscv_int_numeric_corner_stream,4 "
        "+directed_instr_1=riscv_loop_instr,20"
    )
    assert cfg.directed_instr[0] == ("riscv_int_numeric_corner_stream", 4)
    assert cfg.directed_instr[1] == ("riscv_loop_instr", 20)


def test_apply_plusargs_hex_values():
    cfg = Config()
    cfg.apply_plusargs("+signature_addr=0x80001000")
    assert cfg.signature_addr == 0x80001000


def test_make_config_auto_disables_compressed_when_unsupported():
    # rv32i doesn't include RV32C, so disable_compressed_instr should be auto-set.
    t = get_target("rv32i")
    cfg = make_config(t)
    assert cfg.disable_compressed_instr is True


def test_make_config_leaves_compressed_enabled_when_supported():
    t = get_target("rv32imc")
    cfg = make_config(t)
    assert cfg.disable_compressed_instr is False


def test_make_config_num_of_harts_from_target():
    cfg = make_config(get_target("multi_harts"))
    assert cfg.num_of_harts == 2


def test_make_config_applies_gen_opts():
    t = get_target("rv32imc")
    cfg = make_config(t, gen_opts="+instr_cnt=5000 +boot_mode=m +no_fence=1")
    assert cfg.instr_cnt == 5000
    assert cfg.init_privileged_mode == PrivilegedMode.MACHINE_MODE
    assert cfg.no_fence is True


def test_make_config_overrides_applied_last():
    t = get_target("rv32imc")
    cfg = make_config(t, gen_opts="+instr_cnt=5000", instr_cnt=200)
    assert cfg.instr_cnt == 200


def test_as_dict_snapshot():
    cfg = Config(instr_cnt=10)
    snap = cfg.as_dict()
    assert snap["instr_cnt"] == 10
    # Target is None in bare Config; should round-trip.
    assert snap["target"] is None
