"""Tests for the Sprint-1 coverage-gap closures.

Six new covergroups land in this sprint, all targeting verification
features SOTA tools cover (riscv-isac / riscvISACOV / ImperasDV) but
rvgen previously did not:

1. ``CG_FP_FFLAGS``  — accrued FP exception flags from FCSR/FFLAGS writes.
2. ``CG_TRAP_CAUSE`` — per-cause decode of mcause / scause writes.
3. ``CG_OP_COMB``    — special-register usage + triple-equality.
4. ``CG_EA_ALIGN``   — runtime effective-address alignment.
5. ``CG_CSR_READ``   — CSR-read coverage (csr_value_cg only sees writes).
6. ``CG_FP_DATASET`` — FP corner values per IEEE-754 (sp_dataset / dp_dataset).
"""

from __future__ import annotations

import pytest

from rvgen.coverage.collectors import (
    CG_CSR_READ,
    CG_EA_ALIGN,
    CG_FP_DATASET,
    CG_FP_FFLAGS,
    CG_OP_COMB,
    CG_TRAP_CAUSE,
    _ea_align_bin,
    _fp_dataset_bin,
    _fp_fflags_bins,
    _op_comb_bins,
    _trap_cause_bin,
    new_db,
    sample_instr,
)
from rvgen.isa.enums import RiscvInstrName as N, RiscvReg as R
from rvgen.isa.factory import get_instr


# ---------------------------------------------------------------------------
# CG_FP_FFLAGS — fcsr/fflags decode
# ---------------------------------------------------------------------------


def test_fflags_no_flags():
    assert _fp_fflags_bins(0) == ("no_flags",)


def test_fflags_each_single_bit():
    # Bit 0=NX, 1=UF, 2=OF, 3=DZ, 4=NV.
    assert _fp_fflags_bins(0x01) == ("nx_set",)
    assert _fp_fflags_bins(0x02) == ("uf_set",)
    assert _fp_fflags_bins(0x04) == ("of_set",)
    assert _fp_fflags_bins(0x08) == ("dz_set",)
    assert _fp_fflags_bins(0x10) == ("nv_set",)


def test_fflags_multiple_bits_emit_aggregate():
    bins = _fp_fflags_bins(0x05)  # NX | OF
    assert "nx_set" in bins
    assert "of_set" in bins
    assert "multiple_flags" in bins


def test_fflags_ignores_high_fcsr_bits():
    # FCSR[7:5] holds frm; should not affect fflags decode.
    assert _fp_fflags_bins(0xE0) == ("no_flags",)


# ---------------------------------------------------------------------------
# CG_TRAP_CAUSE — mcause/scause decode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cause,expected", [
    (0,  "exception_00_instr_addr_misaligned"),
    (2,  "exception_02_illegal_instruction"),
    (8,  "exception_08_ecall_u"),
    (11, "exception_11_ecall_m"),
    (12, "exception_12_instr_page_fault"),
    (13, "exception_13_load_page_fault"),
    (15, "exception_15_store_amo_page_fault"),
    (20, "exception_20_instr_guest_page_fault"),
])
def test_trap_cause_exception(cause, expected):
    assert _trap_cause_bin(cause, 64) == expected


@pytest.mark.parametrize("code,expected_suffix", [
    (3, "m_software"),
    (7, "m_timer"),
    (11, "m_external"),
    (12, "counter_overflow"),
])
def test_trap_cause_interrupt(code, expected_suffix):
    cause = (1 << 63) | code
    assert _trap_cause_bin(cause, 64).endswith(expected_suffix)
    assert _trap_cause_bin(cause, 64).startswith("interrupt_")


def test_trap_cause_unknown_code_kept_distinct():
    # Code 30 isn't in the spec; should still produce a distinct bin
    # rather than collapse to a single "unknown" sink.
    bin_a = _trap_cause_bin(30, 64)
    bin_b = _trap_cause_bin(31, 64)
    assert bin_a != bin_b
    assert "unknown" in bin_a


def test_trap_cause_xlen32_msb_handling():
    # On RV32 the interrupt bit is bit 31, not bit 63.
    cause = (1 << 31) | 5
    assert _trap_cause_bin(cause, 32).startswith("interrupt_")


# ---------------------------------------------------------------------------
# CG_OP_COMB — operand-combination static sampler
# ---------------------------------------------------------------------------


def test_op_comb_triple_equality():
    i = get_instr(N.ADD)
    i.rs1 = i.rs2 = i.rd = R.A0
    bins = _op_comb_bins(i)
    assert "rd_eq_rs1_eq_rs2" in bins


def test_op_comb_triple_equality_excludes_zero():
    # add x0, x0, x0 is a hint, not a meaningful triple-equality bin.
    i = get_instr(N.ADD)
    i.rs1 = i.rs2 = i.rd = R.ZERO
    bins = _op_comb_bins(i)
    assert "rd_eq_rs1_eq_rs2" not in bins


def test_op_comb_special_register_usage():
    i = get_instr(N.ADDI)
    i.rs1 = R.SP
    i.rd = R.RA
    i.rs2 = R.ZERO  # ADDI has no rs2 but base sets it
    i.has_rs2 = False
    bins = _op_comb_bins(i)
    assert "rs1_is_sp" in bins
    assert "rd_is_ra" in bins
    assert not any(b.startswith("rs2_") for b in bins)


def test_op_comb_zero_dst_pattern():
    # `add x0, ra, sp` discards result — pipeline-interesting.
    i = get_instr(N.ADD)
    i.rd = R.ZERO
    i.rs1 = R.RA
    i.rs2 = R.SP
    bins = _op_comb_bins(i)
    assert "rd_is_zero" in bins
    assert "rs1_is_ra" in bins
    assert "rs2_is_sp" in bins


def test_op_comb_sample_instr_populates_db():
    db = new_db()
    i = get_instr(N.SUB)
    i.rd = i.rs1 = i.rs2 = R.A1
    sample_instr(db, i)
    assert db[CG_OP_COMB].get("rd_eq_rs1_eq_rs2") == 1


# ---------------------------------------------------------------------------
# CG_EA_ALIGN — runtime EA alignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("addr,expected", [
    (0x1000, "align_64"),  # page-aligned
    (0x100, "align_64"),   # 256B-aligned -> capped at align_64
    (0x80, "align_64"),    # 128B-aligned -> capped
    (0x40, "align_64"),    # 64B-aligned
    (0x20, "align_32"),
    (0x10, "align_16"),
    (0x8,  "align_8"),
    (0x4,  "align_4"),
    (0x2,  "align_2"),
    (0x1,  "align_1"),
    (0x1003, "align_1"),
])
def test_ea_align_bins(addr, expected):
    assert _ea_align_bin(addr) == expected


# ---------------------------------------------------------------------------
# CG_CSR_READ — static, sampled inside sample_instr for read-only CSR ops
# ---------------------------------------------------------------------------


def test_csr_read_sampled_when_rs1_is_zero():
    # csrrs rd, mscratch, x0 == csrr (read-only).
    db = new_db()
    i = get_instr(N.CSRRS)
    i.rd = R.A0
    i.rs1 = R.ZERO
    i.csr = 0x340  # MSCRATCH
    sample_instr(db, i)
    assert "MSCRATCH" in db[CG_CSR_READ]


def test_csr_write_does_not_pollute_csr_read():
    db = new_db()
    i = get_instr(N.CSRRW)
    i.rd = R.A0
    i.rs1 = R.A1
    i.csr = 0x340
    sample_instr(db, i)
    assert db[CG_CSR_READ] == {}


# ---------------------------------------------------------------------------
# CG_FP_DATASET — IEEE-754 corner classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("v,width,expected", [
    # Single precision
    (0x00000000, 32, "pos_zero"),
    (0x80000000, 32, "neg_zero"),
    (0x7F800000, 32, "pos_inf"),
    (0xFF800000, 32, "neg_inf"),
    (0x7FC00000, 32, "qnan"),
    (0x7F800001, 32, "snan"),
    (0x00800000, 32, "pos_normal_min"),
    (0x80800000, 32, "neg_normal_min"),
    (0x7F7FFFFF, 32, "pos_normal_max"),
    (0xFF7FFFFF, 32, "neg_normal_max"),
    (0x00000001, 32, "pos_subnormal"),
    (0x80000001, 32, "neg_subnormal"),
    (0x3F800000, 32, "pos_one"),
    (0xBF800000, 32, "neg_one"),
    (0x40490FDB, 32, "generic"),  # ~pi
    # Double precision
    (0x0000000000000000, 64, "pos_zero"),
    (0x7FF0000000000000, 64, "pos_inf"),
    (0xFFF0000000000000, 64, "neg_inf"),
    (0x7FF8000000000000, 64, "qnan"),
    (0x3FF0000000000000, 64, "pos_one"),
    # Half precision (Zfh)
    (0x0000, 16, "pos_zero"),
    (0x3C00, 16, "pos_one"),
    (0x7C00, 16, "pos_inf"),
    (0x7E00, 16, "qnan"),
    (0x0001, 16, "pos_subnormal"),
])
def test_fp_dataset_bins(v, width, expected):
    assert _fp_dataset_bin(v, width) == expected


def test_fp_dataset_unsupported_width():
    # Width != 16/32/64 should fall through to generic.
    assert _fp_dataset_bin(0xDEADBEEF, 8) == "generic"


# ---------------------------------------------------------------------------
# End-to-end runtime ingest: a synthetic spike --log-commits trace
# exercises the FFLAGS / TRAP_CAUSE / EA_ALIGN / FP_DATASET wiring.
# ---------------------------------------------------------------------------


def test_runtime_ingest_decodes_all_sprint1_covergroups(tmp_path):
    from rvgen.coverage.runtime import sample_trace_file

    trace = tmp_path / "spike.log"
    trace.write_text(
        # Plain trace lines (TRACE_RE) interleaved with commit lines (COMMIT_RE).
        "core   0: >>>>  h0_start\n"
        # FCSR write — both NX and OF set, frm=RNE in high bits.
        "core   0: 3 0x80000010 (0x12345678) c003_fcsr 0x00000005\n"
        # MCAUSE write — exception 13 (load page fault).
        "core   0: 3 0x80000020 (0x87654321) c342_mcause 0x000000000000000d\n"
        # MCAUSE write — interrupt 7 (M-timer).
        "core   0: 3 0x80000024 (0x87654322) c342_mcause 0x8000000000000007\n"
        # FP register write — +1.0f as a 32-bit single (NaN-boxed in 64-bit FPR).
        "core   0: 3 0x80000030 (0x00000053) f5  0xffffffff3f800000\n"
        # FP register write — qnan as 64-bit double (no NaN-boxing).
        "core   0: 3 0x80000038 (0x00000053) f6  0x7ff8000000000000\n"
        # Memory access — store-data event with dword-aligned EA.
        "core   0: 3 0x80000040 (0x00b62023) mem 0x80001008\n"
        # Memory access — byte-misaligned EA.
        "core   0: 3 0x80000044 (0x00b58023) mem 0x80001003\n"
    )

    db = {}
    sample_trace_file(db, trace)

    # FFLAGS — NX + OF + multiple_flags aggregate.
    assert db[CG_FP_FFLAGS]["nx_set"] >= 1
    assert db[CG_FP_FFLAGS]["of_set"] >= 1
    assert db[CG_FP_FFLAGS]["multiple_flags"] >= 1
    # TRAP_CAUSE — both an exception and an interrupt sample.
    assert db[CG_TRAP_CAUSE]["exception_13_load_page_fault"] >= 1
    assert db[CG_TRAP_CAUSE]["interrupt_07_m_timer"] >= 1
    # FP_DATASET — pos_one (NaN-boxed single) and qnan (64-bit double).
    assert db[CG_FP_DATASET]["pos_one"] >= 1
    assert db[CG_FP_DATASET]["qnan"] >= 1
    # EA_ALIGN — one 8-byte-aligned, one byte-aligned.
    assert db[CG_EA_ALIGN]["align_8"] >= 1
    assert db[CG_EA_ALIGN]["align_1"] >= 1
