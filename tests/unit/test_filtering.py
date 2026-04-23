"""Tests for rvgen.isa.filtering."""

from __future__ import annotations

import random

import pytest

from rvgen.config import make_config
from rvgen.isa import rv32i  # noqa: F401 — register RV32I
from rvgen.isa.enums import (
    PrivilegedMode,
    RiscvInstrCategory,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvReg,
)
from rvgen.isa.filtering import (
    AvailableInstrs,
    create_instr_list,
    get_rand_instr,
    randomize_gpr_operands,
)
from rvgen.targets import get_target


def test_create_instr_list_for_rv32i_filters_to_rv32i_only():
    cfg = make_config(get_target("rv32i"))
    avail = create_instr_list(cfg)
    assert set(avail.by_group.keys()) == {RiscvInstrGroup.RV32I}
    # Must contain the canonical integer ops.
    for n in (RiscvInstrName.ADD, RiscvInstrName.ADDI, RiscvInstrName.LW, RiscvInstrName.BEQ):
        assert n in avail.names
    # Must NOT contain RV32M ops since rv32i target doesn't include RV32M.
    assert RiscvInstrName.MUL not in avail.names


def test_create_instr_list_respects_unsupported_instr():
    # rv32im marks MUL/MULH/MULHSU/MULHU as unsupported.
    cfg = make_config(get_target("rv32im"))
    avail = create_instr_list(cfg)
    for n in (RiscvInstrName.MUL, RiscvInstrName.MULH,
              RiscvInstrName.MULHSU, RiscvInstrName.MULHU):
        assert n not in avail.names
    # DIV/REM should remain.
    assert RiscvInstrName.DIV in avail.names
    assert RiscvInstrName.REM in avail.names


def test_create_instr_list_no_fence_drops_fence():
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_fence=1")
    avail = create_instr_list(cfg)
    assert RiscvInstrName.FENCE not in avail.names
    assert RiscvInstrName.FENCE_I not in avail.names


def test_create_instr_list_disable_compressed_drops_c_ops():
    cfg = make_config(get_target("rv32imc"), gen_opts="+disable_compressed_instr=1")
    avail = create_instr_list(cfg)
    for n in (RiscvInstrName.C_ADDI, RiscvInstrName.C_LI, RiscvInstrName.C_LW):
        assert n not in avail.names


def test_basic_instr_includes_shift_arith_logical_compare():
    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    assert RiscvInstrName.ADDI in avail.basic_instr
    assert RiscvInstrName.SLLI in avail.basic_instr
    assert RiscvInstrName.AND in avail.basic_instr
    assert RiscvInstrName.SLT in avail.basic_instr


def test_basic_instr_honors_no_ebreak_no_ecall_no_wfi():
    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    assert RiscvInstrName.EBREAK not in avail.basic_instr
    assert RiscvInstrName.ECALL not in avail.basic_instr
    assert RiscvInstrName.WFI not in avail.basic_instr


def test_basic_instr_csr_included_when_enabled():
    cfg = make_config(get_target("rv32imc"), gen_opts="+no_csr_instr=0")
    avail = create_instr_list(cfg)
    # init_privileged_mode defaults to MACHINE_MODE, so CSR ops should join.
    assert RiscvInstrName.CSRRW in avail.basic_instr


def test_get_rand_instr_from_category():
    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    rng = random.Random(42)
    for _ in range(20):
        instr = get_rand_instr(rng, avail, include_category=[RiscvInstrCategory.BRANCH])
        assert instr.category == RiscvInstrCategory.BRANCH


def test_get_rand_instr_exclude_wins_over_include():
    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    rng = random.Random(1)
    for _ in range(20):
        instr = get_rand_instr(
            rng,
            avail,
            include_category=[RiscvInstrCategory.ARITHMETIC],
            exclude_instr=[RiscvInstrName.ADDI, RiscvInstrName.LUI, RiscvInstrName.AUIPC],
        )
        assert instr.instr_name not in (RiscvInstrName.ADDI, RiscvInstrName.LUI, RiscvInstrName.AUIPC)


def test_get_rand_instr_raises_when_filter_empty():
    cfg = make_config(get_target("rv32i"))
    avail = create_instr_list(cfg)
    rng = random.Random(0)
    with pytest.raises(RuntimeError, match="no candidates"):
        # Request only RVV from a pure RV32I target.
        get_rand_instr(rng, avail, include_group=[RiscvInstrGroup.RVV])


def test_randomize_gpr_operands_respects_reserved_rd():
    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    rng = random.Random(7)
    forbidden = {RiscvReg.TP, RiscvReg.SP, cfg.scratch_reg}
    for _ in range(50):
        instr = get_rand_instr(rng, avail, include_category=[RiscvInstrCategory.ARITHMETIC])
        randomize_gpr_operands(instr, rng, cfg)
        if instr.has_rd:
            assert instr.rd not in forbidden


def test_randomize_gpr_operands_uses_avail_regs():
    cfg = make_config(get_target("rv32imc"))
    avail = create_instr_list(cfg)
    rng = random.Random(13)
    allowed = (RiscvReg.A0, RiscvReg.A1, RiscvReg.A2, RiscvReg.A3, RiscvReg.A4, RiscvReg.A5)
    for _ in range(20):
        instr = get_rand_instr(rng, avail, include_category=[RiscvInstrCategory.ARITHMETIC])
        randomize_gpr_operands(instr, rng, cfg, avail_regs=allowed)
        if instr.has_rs1:
            assert instr.rs1 in allowed
        if instr.has_rs2:
            assert instr.rs2 in allowed
        if instr.has_rd:
            assert instr.rd in allowed


def test_fp_groups_gated_by_enable_floating_point():
    """RV32F/D/FC/DC must all disappear when enable_floating_point=False."""
    cfg = make_config(get_target("rv32imafdc"), gen_opts="+enable_floating_point=0")
    avail = create_instr_list(cfg)
    fp_groups = {
        RiscvInstrGroup.RV32F, RiscvInstrGroup.RV32D,
        RiscvInstrGroup.RV32FC, RiscvInstrGroup.RV32DC,
    }
    assert fp_groups.isdisjoint(avail.by_group.keys())
    # Flip on — FP + compressed-FP groups re-appear.
    cfg_on = make_config(get_target("rv32imafdc"), gen_opts="+enable_floating_point=1")
    avail_on = create_instr_list(cfg_on)
    assert fp_groups <= avail_on.by_group.keys()


def test_target_unsupported_instr_honored_for_fp():
    """unsupported_instr filters individual instrs even when their group survives."""
    from rvgen.targets import TargetCfg, get_target
    from dataclasses import replace

    base = get_target("rv32imafdc")
    # Synthetically mark a handful of FP ops as unsupported.
    blocked = (RiscvInstrName.FCVT_W_S, RiscvInstrName.FCVT_WU_S, RiscvInstrName.FLW)
    custom = replace(base, name="rv32imafdc_noconv",
                     unsupported_instr=base.unsupported_instr + blocked)
    cfg = make_config(custom, gen_opts="+enable_floating_point=1")
    avail = create_instr_list(cfg)
    for n in blocked:
        assert n not in avail.names, f"{n.name} should be filtered"
    # Sanity: other FP ops still present.
    assert RiscvInstrName.FADD_S in avail.names


def test_rv32imc_zkn_includes_crypto():
    """rv32imc_zkn target lists the ratified K sub-groups and no Zbb."""
    cfg = make_config(get_target("rv32imc_zkn"))
    avail = create_instr_list(cfg)
    for g in (RiscvInstrGroup.RV32ZBKB, RiscvInstrGroup.RV32ZKND,
              RiscvInstrGroup.RV32ZKNE, RiscvInstrGroup.RV32ZKNH):
        assert g in avail.by_group, f"missing {g.name}"
    # Zbb must NOT be present — MCU-style targets only use the Zbkb overlap.
    assert RiscvInstrGroup.RV32ZBB not in avail.by_group
    # Representative opcodes.
    for n in (RiscvInstrName.BREV8, RiscvInstrName.AES32ESI,
              RiscvInstrName.AES32DSI, RiscvInstrName.SHA256SIG0):
        assert n in avail.names
    # Zbb-unique ops must not leak through (min/max/minu/maxu/rev8/sext/zext).
    for n in (RiscvInstrName.MIN, RiscvInstrName.MAX,
              RiscvInstrName.MINU, RiscvInstrName.MAXU,
              RiscvInstrName.REV8, RiscvInstrName.SEXT_B,
              RiscvInstrName.SEXT_H, RiscvInstrName.ZEXT_H):
        assert n not in avail.names, f"Zbb-unique {n.name} leaked into rv32imc_zkn"


def test_xlen_gates_crypto_instr_width():
    """RV32-only crypto is blocked on RV64 and vice versa."""
    # RV32 target — no AES64 ops.
    cfg32 = make_config(get_target("rv32imc_zkn"))
    avail32 = create_instr_list(cfg32)
    assert RiscvInstrName.AES64ES not in avail32.names
    assert RiscvInstrName.AES64IM not in avail32.names
    assert RiscvInstrName.SHA512SIG0 not in avail32.names
    # ZIP/UNZIP/SHA512SIG0L etc. *are* available on RV32.
    assert RiscvInstrName.AES32ESI in avail32.names

    # RV64 target — no AES32 ops or RV32-only SHA-512 split pair.
    cfg64 = make_config(get_target("rv64imc_zkn"))
    avail64 = create_instr_list(cfg64)
    assert RiscvInstrName.AES64ES in avail64.names
    assert RiscvInstrName.SHA512SIG0 in avail64.names
    assert RiscvInstrName.AES32ESI not in avail64.names
    assert RiscvInstrName.SHA512SIG0L not in avail64.names
    assert RiscvInstrName.ZIP not in avail64.names


def test_rv32imcb_includes_ratified_bitmanip():
    """rv32imcb target exposes Zba/Zbb/Zbc/Zbs groups (not draft RV32B)."""
    cfg = make_config(get_target("rv32imcb"))
    avail = create_instr_list(cfg)
    assert RiscvInstrGroup.RV32ZBA in avail.by_group
    assert RiscvInstrGroup.RV32ZBB in avail.by_group
    assert RiscvInstrGroup.RV32ZBC in avail.by_group
    assert RiscvInstrGroup.RV32ZBS in avail.by_group
    # Draft-B (RV32B) stays filtered out — those mnemonics aren't in ratified
    # GCC and we'd fail to assemble them.
    assert RiscvInstrGroup.RV32B not in avail.by_group
    # Representative sample from each ratified sub-group is present.
    for n in (RiscvInstrName.SH1ADD, RiscvInstrName.CLZ, RiscvInstrName.CLMUL,
              RiscvInstrName.BCLRI):
        assert n in avail.names
