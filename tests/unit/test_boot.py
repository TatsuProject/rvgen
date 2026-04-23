"""Tests for rvgen.privileged.boot — boot CSR sequence + mode transition."""

from __future__ import annotations

from rvgen.config import make_config
from rvgen.isa.enums import MtvecMode, PrivilegedMode
from rvgen.privileged.boot import (
    _mie_value,
    _mstatus_value,
    gen_pre_enter_privileged_mode,
)
from rvgen.targets import get_target


def _emit_rv32imc(**overrides) -> str:
    cfg = make_config(get_target("rv32imc"), **overrides)
    return "\n".join(gen_pre_enter_privileged_mode(cfg))


def _emit_rv64gc(**overrides) -> str:
    cfg = make_config(get_target("rv64gc"), **overrides)
    return "\n".join(gen_pre_enter_privileged_mode(cfg))


# ---------- Mode transition via MSTATUS.MPP ----------


def test_mpp_m_mode_default():
    cfg = make_config(get_target("rv32imc"))
    val = _mstatus_value(cfg)
    # MPP = 11 (M-mode) in bits [12:11].
    assert (val >> 11) & 0b11 == 0b11


def test_mpp_u_mode_boot():
    cfg = make_config(
        get_target("rv64gc"),
        init_privileged_mode=PrivilegedMode.USER_MODE,
    )
    val = _mstatus_value(cfg)
    assert (val >> 11) & 0b11 == 0b00


def test_mpp_s_mode_boot():
    cfg = make_config(
        get_target("rv64gc"),
        init_privileged_mode=PrivilegedMode.SUPERVISOR_MODE,
    )
    val = _mstatus_value(cfg)
    assert (val >> 11) & 0b11 == 0b01


def test_mpie_set_when_interrupts_enabled():
    cfg = make_config(get_target("rv32imc"), enable_interrupt=True)
    val = _mstatus_value(cfg)
    assert val & (1 << 7)  # MPIE


def test_mpie_clear_when_interrupts_disabled():
    cfg = make_config(get_target("rv32imc"))
    val = _mstatus_value(cfg)
    assert not (val & (1 << 7))


# ---------- MIE bits ----------


def test_mie_all_enabled_with_timer_irq():
    cfg = make_config(
        get_target("rv32imc"),
        enable_interrupt=True,
        enable_timer_irq=True,
    )
    val = _mie_value(cfg)
    assert val & (1 << 3)   # MSIE
    assert val & (1 << 7)   # MTIE
    assert val & (1 << 11)  # MEIE


def test_mie_timer_bit_gated_separately():
    cfg = make_config(
        get_target("rv32imc"),
        enable_interrupt=True,
        enable_timer_irq=False,
    )
    val = _mie_value(cfg)
    assert val & (1 << 3)        # MSIE still on
    assert not (val & (1 << 7))  # MTIE off
    assert val & (1 << 11)       # MEIE on


def test_mie_zero_when_interrupts_off():
    cfg = make_config(get_target("rv32imc"))
    assert _mie_value(cfg) == 0


# ---------- Boot sequence emit ----------


def test_boot_emits_mret():
    assert "mret" in _emit_rv32imc()


def test_boot_writes_mtvec():
    asm = _emit_rv32imc()
    # MTVEC csr = 0x305.
    assert "0x305" in asm


def test_boot_writes_mstatus():
    asm = _emit_rv32imc()
    assert "0x300" in asm


def test_boot_writes_mepc_to_init():
    asm = _emit_rv32imc()
    # MEPC = 0x341, and the label loaded into it is "init".
    assert "0x341" in asm
    assert "init" in asm


def test_mtvec_mode_bit_zero_when_interrupts_disabled():
    # enable_interrupt=False → DIRECT mode → MODE bit 0 → no ori on t0.
    asm = _emit_rv32imc(mtvec_mode=MtvecMode.VECTORED)
    # "ori t0, t0, 0" is legal but wasteful; we emit the bit as a plain
    # 0 literal, so "ori t0, t0, 0" will appear. The vector-mode form
    # uses 1 instead. Check we never emit the 1 form for this config.
    assert "ori t0, t0, 1" not in asm


def test_mtvec_mode_bit_one_when_interrupts_enabled_vectored():
    asm = _emit_rv32imc(enable_interrupt=True, mtvec_mode=MtvecMode.VECTORED)
    assert "ori t0, t0, 1" in asm


def test_boot_skips_stvec_when_no_delegation():
    asm = _emit_rv64gc()
    # STVEC = 0x105. Not written in no-delegation path.
    assert "csrw 0x105" not in asm


def test_boot_writes_stvec_with_delegation_on():
    asm = _emit_rv64gc(no_delegation=False)
    assert "csrw 0x105" in asm
    # MEDELEG + MIDELEG should also appear.
    assert "csrw 0x302" in asm  # MEDELEG
    assert "csrw 0x303" in asm  # MIDELEG
