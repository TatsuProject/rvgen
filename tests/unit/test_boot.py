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


def test_mstatus_fs_set_initial_when_fp_enabled():
    """SV ``floating_point_c``: MSTATUS.FS[14:13] = 0b01 (INITIAL) so FP
    instructions don't trap as illegal_instruction. Regression for the
    livelock that hit riscv_floating_point_mmu_stress_test on seeds that
    emitted an FP op before any explicit FS write.
    """
    cfg = make_config(get_target("rv32imafdc"), enable_floating_point=True)
    val = _mstatus_value(cfg)
    assert (val >> 13) & 0b11 == 0b01


def test_mstatus_fs_zero_when_fp_disabled():
    cfg = make_config(get_target("rv32imc"))
    val = _mstatus_value(cfg)
    assert (val >> 13) & 0b11 == 0b00


def test_mstatus_vs_set_initial_when_vector_enabled():
    """Same parity rule for the vector extension: MSTATUS.VS[10:9]=0b01."""
    cfg = make_config(get_target("rv64gcv"))
    val = _mstatus_value(cfg)
    assert (val >> 9) & 0b11 == 0b01


def test_trap_handler_bumps_mepc_by_instr_length():
    """Regression for the compressed-instruction livelock.

    The exception handler used to blindly do ``addi mepc, mepc, 4``. When
    the faulting instruction was a 2-byte compressed op (e.g. a c.fsw that
    traps as store_access_fault), +4 landed in the middle of the next
    4-byte instruction. Spike re-decoded those middle halfwords as a new
    compressed op — and if that halfword happened to encode c.lwsp tp (a
    valid write to a reserved register), it silently poisoned tp. A
    subsequent trap then livelocked in the handler prologue. Seed 2556 on
    riscv_floating_point_mmu_stress_test reproduced this reliably.

    The handler must read the halfword at MEPC, check bits[1:0] == 11
    (4-byte) vs anything else (2-byte), and bump MEPC accordingly.
    """
    from rvgen.privileged.trap import gen_trap_handler
    from rvgen.targets import get_target
    cfg = make_config(get_target("rv32imafdc"), enable_floating_point=True)
    asm = "\n".join(gen_trap_handler(cfg, hart=0))
    # Must read the halfword at MEPC to inspect instruction size.
    assert "lhu t1" in asm or "lhu  t1" in asm, (
        "Trap handler must load the halfword at MEPC to determine "
        "instruction length — a blind +4 bump walks into the middle of "
        "the next 4-byte instruction when the faulting op is compressed."
    )


def test_instr_fetch_faults_terminate():
    """Instruction access fault and instruction page fault cannot resume:
    MEPC points at unmapped memory, so we can't read the instruction to
    determine its length. The only safe path is to terminate (set gp=1
    and jump to write_tohost), not to retry at the same PC.
    """
    from rvgen.privileged.trap import gen_trap_handler
    from rvgen.targets import get_target
    cfg = make_config(get_target("rv32imafdc"), enable_floating_point=True)
    asm = "\n".join(gen_trap_handler(cfg, hart=0))
    assert "instr_fault_handler:" in asm
    # Must terminate (jump to write_tohost), not attempt resume via mret.
    handler_start = asm.find("instr_fault_handler:")
    ebreak_start = asm.find("ebreak_handler:")
    body = asm[handler_start:ebreak_start]
    assert "write_tohost" in body, (
        "instr_fault_handler must terminate via write_tohost — attempting "
        "to resume at an unmapped PC causes an infinite trap loop."
    )


def test_tohost_in_dedicated_section():
    """Regression for the tohost-corruption bug that caused rc=255 failures
    on riscv_floating_point_mmu_stress_test. tohost must land in its own
    ``.tohost`` section — if it sits in ``.data`` alongside region_0/1 with
    only 72 bytes between them, a random store with a small negative offset
    silently overwrites it and spike aborts.
    """
    from rvgen.sections.data_page import gen_tohost_fromhost
    lines = gen_tohost_fromhost()
    joined = "\n".join(lines)
    assert ".section .tohost" in joined, (
        "tohost/fromhost must emit into the .tohost section so the linker "
        "script can isolate them on their own page"
    )
    assert "tohost: .dword 0" in joined
    assert "fromhost: .dword 0" in joined


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
