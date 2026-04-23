"""Tests for rvgen.privileged.trap — trap handler emission.

Focus areas:
- Correct labels emitted per mode (M / S).
- DIRECT vs VECTORED layout selection based on enable_interrupt.
- Exception-cause dispatch table includes every architectural cause.
- Sub-handlers share one body via stacked labels (size optimisation).
- Nested-interrupt guard present iff enable_nested_interrupt is True.
- Timer-disarm MTIMECMP=-1 store present iff enable_timer_irq is True.
"""

from __future__ import annotations

from rvgen.config import make_config
from rvgen.isa.enums import MtvecMode, PrivilegedMode
from rvgen.privileged.trap import gen_trap_handler
from rvgen.targets import get_target


def _emit_rv32imc(**overrides) -> str:
    cfg = make_config(get_target("rv32imc"), **overrides)
    return "\n".join(gen_trap_handler(cfg))


def _emit_rv64gc(**overrides) -> str:
    cfg = make_config(get_target("rv64gc"), **overrides)
    return "\n".join(gen_trap_handler(cfg))


# ---------- Basic layout ----------


def test_trap_handler_has_mtvec_entry():
    asm = _emit_rv32imc()
    assert "mtvec_handler:" in asm


def test_trap_handler_has_exception_dispatch_label():
    asm = _emit_rv32imc()
    assert "mmode_exception_handler:" in asm


def test_trap_handler_has_intr_handler_label():
    asm = _emit_rv32imc()
    assert "mmode_intr_handler:" in asm


def test_trap_handler_bare_mode_emits_nothing():
    cfg = make_config(get_target("rv32imc"), bare_program_mode=True)
    assert gen_trap_handler(cfg) == []


# ---------- VECTORED gating on enable_interrupt ----------


def test_vectored_table_present_when_interrupt_enabled():
    asm = _emit_rv32imc(enable_interrupt=True, mtvec_mode=MtvecMode.VECTORED)
    # Jump-table sentinel labels.
    assert "mmode_intr_vector_1:" in asm
    assert "mmode_intr_vector_15:" in asm
    # The table entries are bare `j ...` instructions; presence of the
    # 15th confirms the full 16-entry table emitted (entry 0 is the
    # exception handler jump, entries 1..15 are interrupt slots).


def test_vectored_table_absent_when_interrupt_disabled():
    # Even with mtvec_mode=VECTORED, skipping the table when interrupts
    # are off saves ~850 bytes of .text and keeps random stores away
    # from the handler region.
    asm = _emit_rv32imc(enable_interrupt=False, mtvec_mode=MtvecMode.VECTORED)
    assert "mmode_intr_vector_1" not in asm
    assert "mmode_intr_vector_15" not in asm


# ---------- Exception dispatch table ----------


def test_exception_dispatch_covers_all_causes():
    asm = _emit_rv32imc()
    # Every architectural cause gets a beq → target pair.
    for label in (
        "ebreak_handler",
        "ecall_handler",
        "illegal_instr_handler",
        "instr_fault_handler",
        "load_fault_handler",
        "store_fault_handler",
        "pt_fault_handler",
    ):
        assert f"{label}:" in asm, f"missing handler: {label}"


def test_ecall_handler_sets_gp_before_write_tohost():
    # Spike's tohost protocol needs a non-zero gp value to exit. Random
    # ecalls arrive with gp unset — the handler must force gp=1 before
    # jumping to write_tohost, else spike spins forever.
    asm = _emit_rv32imc()
    i_gp = asm.find("li gp, 1")
    i_wt = asm.find("write_tohost")
    assert 0 < i_gp < i_wt


def test_sub_handlers_share_one_body():
    # Size guard: the 6 fault/illegal/ebreak sub-handlers collapse into
    # one body with stacked labels. If someone accidentally restores the
    # per-label duplicated bodies, this test catches the regression.
    asm = _emit_rv32imc()
    # addi <gpr>, <gpr>, 4 is emitted exactly once in the shared body.
    assert asm.count("csrw 0x341, t0") == 1, (
        "sub-handlers should share one MEPC-bump body"
    )


# ---------- Nested interrupts ----------


def test_nested_interrupt_guard_present():
    asm = _emit_rv32imc(enable_interrupt=True, enable_nested_interrupt=True)
    # Scratch-CSR sticky-lock: csrwi scratch, 1 gates nested entry.
    assert "csrwi 0x340, 0x1" in asm
    # Re-enable MSTATUS.MIE inside the handler.
    assert "csrsi 0x300, 0x8" in asm


def test_nested_interrupt_guard_absent_when_disabled():
    asm = _emit_rv32imc(enable_interrupt=True, enable_nested_interrupt=False)
    assert "csrwi 0x340, 0x1" not in asm
    assert "csrsi 0x300, 0x8" not in asm


# ---------- Timer-IRQ disarm ----------


def test_timer_disarm_present_when_timer_irq_enabled():
    asm = _emit_rv32imc(enable_interrupt=True, enable_timer_irq=True)
    # MTIMECMP base on spike CLINT.
    assert "0x2004000" in asm
    # -1 sentinel that deasserts MTIP.
    assert "li t1, -1" in asm


def test_timer_disarm_absent_when_timer_irq_disabled():
    asm = _emit_rv32imc(enable_interrupt=True, enable_timer_irq=False)
    assert "0x2004000" not in asm


# ---------- S-mode handler emission gated on delegation ----------


def test_smode_handler_absent_when_no_delegation():
    # Default cfg has no_delegation=True. S-mode handler would be dead
    # code — skip it.
    asm = _emit_rv64gc()
    assert "stvec_handler:" not in asm
    assert "smode_exception_handler:" not in asm


def test_smode_handler_present_when_delegation_on_and_init_s():
    # S-mode handler only emitted when we'll actually be in S-mode during
    # the test (init_mode <= SUPERVISOR). Matches SV's gate on
    # `mode >= cfg.init_privileged_mode`.
    asm = _emit_rv64gc(
        no_delegation=False,
        init_privileged_mode=PrivilegedMode.SUPERVISOR_MODE,
    )
    assert "stvec_handler:" in asm
    assert "smode_exception_handler:" in asm
    assert "sret" in asm
