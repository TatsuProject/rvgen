"""Unit tests for the RVV vector extension port."""

from __future__ import annotations

import random

import pytest

from rvgen.config import make_config
from rvgen.isa import vector  # noqa: F401  (side-effect: register VectorInstr)
from rvgen.isa import rv32v   # noqa: F401  (side-effect: registrations)
from rvgen.isa.enums import (
    RiscvInstrCategory,
    RiscvInstrFormat,
    RiscvInstrGroup,
    RiscvInstrName,
    RiscvVreg,
    VaVariant,
)
from rvgen.isa.factory import INSTR_REGISTRY, get_instr
from rvgen.isa.filtering import create_instr_list
from rvgen.isa.vector import VectorInstr
from rvgen.targets import get_target
from rvgen.vector_config import VectorConfig, Vtype


# ---------------------------------------------------------------------------
# VectorConfig
# ---------------------------------------------------------------------------


def test_vector_config_default_bringup():
    cfg = VectorConfig(vtype=Vtype(vlmul=1, vsew=32, vediv=1), vlen=512, elen=32)
    assert cfg.vl == 512 // 32
    assert cfg.vstart == 0
    assert cfg.vtype.vediv == 1
    # legal_eew contains 8, 16, 32 (not 64+ since ELEN=32).
    assert 32 in cfg.legal_eew
    assert all(e <= 32 for e in cfg.legal_eew)


def test_vector_config_lmul_str_integer():
    cfg = VectorConfig(vtype=Vtype(vlmul=4, vsew=32), vlen=512, elen=32)
    assert cfg.lmul_str() == "m4"


def test_vector_config_lmul_str_fractional():
    cfg = VectorConfig(vtype=Vtype(vlmul=2, vsew=16, fractional_lmul=True),
                       vlen=512, elen=32)
    assert cfg.lmul_str() == "mf2"


def test_vector_config_rejects_vsew_above_elen():
    with pytest.raises(ValueError):
        VectorConfig(vtype=Vtype(vlmul=1, vsew=64), vlen=512, elen=32)


def test_vector_config_rejects_invalid_vlmul():
    with pytest.raises(ValueError):
        VectorConfig(vtype=Vtype(vlmul=3, vsew=32), vlen=512, elen=32)


def test_vector_config_vec_fp_forces_sew32():
    with pytest.raises(ValueError):
        VectorConfig(vtype=Vtype(vlmul=1, vsew=16), vlen=512, elen=32, vec_fp=True)


def test_vector_config_legal_eew_vsew16_lmul2():
    # vsew=16, vlmul=2. eew = vsew*emul/vlmul for integer emul ≥ 1:
    # emul=1 → 8, emul=2 → 16, emul=4 → 32.
    cfg = VectorConfig(vtype=Vtype(vlmul=2, vsew=16), vlen=512, elen=32)
    assert 16 in cfg.legal_eew
    # Fractional emul may also contribute integer values; just sanity-check
    # the bounds.
    assert all(8 <= e <= 32 for e in cfg.legal_eew)


# ---------------------------------------------------------------------------
# VectorInstr class shape
# ---------------------------------------------------------------------------


def test_vadd_registered_with_expected_variants():
    vadd = get_instr(RiscvInstrName.VADD)
    assert isinstance(vadd, VectorInstr)
    assert type(vadd).allowed_va_variants == (VaVariant.VV, VaVariant.VX, VaVariant.VI)
    assert vadd.format == RiscvInstrFormat.VA_FORMAT
    assert vadd.category == RiscvInstrCategory.ARITHMETIC
    assert vadd.group == RiscvInstrGroup.RVV


def test_vector_load_store_registered():
    assert RiscvInstrName.VLE_V in INSTR_REGISTRY
    assert RiscvInstrName.VSE_V in INSTR_REGISTRY
    assert RiscvInstrName.VLSE_V in INSTR_REGISTRY
    assert RiscvInstrName.VSSE_V in INSTR_REGISTRY
    assert RiscvInstrName.VLXEI_V in INSTR_REGISTRY


def test_vector_amo_registered_with_sub_extension():
    amo = get_instr(RiscvInstrName.VAMOSWAPE_V)
    assert isinstance(amo, VectorInstr)
    assert type(amo).sub_extension == "zvamo"


def test_widening_detection():
    vw = get_instr(RiscvInstrName.VWADD)
    assert vw.is_widening_instr
    vn = get_instr(RiscvInstrName.VNSRA)
    assert vn.is_narrowing_instr
    vcvt = get_instr(RiscvInstrName.VFCVT_F_X_V)
    assert vcvt.is_convert_instr


# ---------------------------------------------------------------------------
# randomize_vector_operands + convert2asm
# ---------------------------------------------------------------------------


@pytest.fixture
def vcfg():
    return VectorConfig(
        vtype=Vtype(vlmul=1, vsew=32, vediv=1),
        vlen=512, elen=32, selen=8, max_lmul=8,
    )


def test_vadd_vv_asm(vcfg):
    rng = random.Random(0)
    instr = get_instr(RiscvInstrName.VADD)
    instr.randomize_imm(rng, 32)
    instr.post_randomize()
    instr.randomize_vector_operands(rng, vcfg)
    asm = instr.convert2asm()
    # Example: "vadd.vv      v3, v5, v7, v0.t"  (suffix optional based on vm).
    assert asm.startswith("vadd.v")
    # Second token (vd) should be "v<0-31>".
    body = asm.split()
    assert body[1].startswith("v") and body[1].rstrip(",").lstrip("v").isdigit()


def test_vmv_variant_emission(vcfg):
    rng = random.Random(1)
    instr = get_instr(RiscvInstrName.VMV)
    instr.randomize_imm(rng, 32)
    instr.post_randomize()
    instr.randomize_vector_operands(rng, vcfg)
    asm = instr.convert2asm()
    # vm must be 1 for VMV — no ", v0.t" suffix.
    assert instr.vm == 1
    assert asm.startswith(("vmv.v.v", "vmv.v.x", "vmv.v.i"))
    assert "v0.t" not in asm


def test_vid_v_asm_is_fixed_form(vcfg):
    rng = random.Random(2)
    instr = get_instr(RiscvInstrName.VID_V)
    instr.randomize_vector_operands(rng, vcfg)
    asm = instr.convert2asm()
    assert asm.startswith("vid.v ")


def test_vector_load_has_eew_suffix(vcfg):
    rng = random.Random(3)
    instr = get_instr(RiscvInstrName.VLE_V)
    instr.randomize_vector_operands(rng, vcfg)
    asm = instr.convert2asm()
    # EEW is one of legal_eew, so mnemonic is vle<eew>.v.
    head = asm.split()[0]
    assert head.startswith("vle") and head.endswith(".v")
    eew = int(head[3:-2])
    assert eew in vcfg.legal_eew


def test_vector_store_has_eew_suffix(vcfg):
    rng = random.Random(4)
    instr = get_instr(RiscvInstrName.VSE_V)
    instr.randomize_vector_operands(rng, vcfg)
    head = instr.convert2asm().split()[0]
    assert head.startswith("vs") and head.endswith(".v")


def test_vleff_eew_suffix(vcfg):
    rng = random.Random(5)
    instr = get_instr(RiscvInstrName.VLEFF_V)
    instr.randomize_vector_operands(rng, vcfg)
    head = instr.convert2asm().split()[0]
    # vleNff.v
    assert head.startswith("vl") and head.endswith("ff.v")


def test_mask_register_logical_is_unmasked(vcfg):
    rng = random.Random(6)
    instr = get_instr(RiscvInstrName.VMAND_MM)
    instr.randomize_vector_operands(rng, vcfg)
    assert instr.vm == 1  # SV vector_mask_instr_c


def test_merge_is_always_masked(vcfg):
    rng = random.Random(7)
    # VMERGE is always dropped by the filter, but the instruction class still
    # exists — we can exercise convert2asm directly.
    instr = get_instr(RiscvInstrName.VMERGE)
    instr.randomize_vector_operands(rng, vcfg)
    assert instr.vm == 0  # SV vector_mask_enable_c
    asm = instr.convert2asm()
    assert asm.endswith(", v0") and not asm.endswith(", v0.t")


# ---------------------------------------------------------------------------
# Filter gating
# ---------------------------------------------------------------------------


def test_rv64gcv_target_has_vector_cfg_stamped():
    cfg = make_config(get_target("rv64gcv"))
    assert cfg.enable_vector_extension
    assert cfg.vector_cfg is not None
    assert cfg.vector_cfg.vlen == 512
    assert cfg.vector_cfg.elen == 32


def test_rv32imc_target_has_no_vector_cfg():
    cfg = make_config(get_target("rv32imc"))
    assert cfg.vector_cfg is None
    assert not cfg.enable_vector_extension


def test_vector_filter_drops_widening_when_flag_off():
    cfg = make_config(get_target("rv64gcv"))
    # Default: vec_narrowing_widening = False.
    assert not cfg.vector_cfg.vec_narrowing_widening
    avail = create_instr_list(cfg)
    assert RiscvInstrName.VWADD not in avail.names
    assert RiscvInstrName.VNSRA not in avail.names


def test_vector_filter_drops_fp_when_vec_fp_off():
    cfg = make_config(get_target("rv64gcv"))
    assert not cfg.vector_cfg.vec_fp
    avail = create_instr_list(cfg)
    assert RiscvInstrName.VFADD not in avail.names
    assert RiscvInstrName.VMFEQ not in avail.names


def test_vector_filter_keeps_basic_arith():
    cfg = make_config(get_target("rv64gcv"))
    avail = create_instr_list(cfg)
    for n in (RiscvInstrName.VADD, RiscvInstrName.VAND, RiscvInstrName.VMUL,
              RiscvInstrName.VSLL, RiscvInstrName.VMSEQ):
        assert n in avail.names, n.name


def test_vector_filter_drops_vsetvli_and_adc_sbc():
    cfg = make_config(get_target("rv64gcv"))
    avail = create_instr_list(cfg)
    assert RiscvInstrName.VSETVLI not in avail.names
    assert RiscvInstrName.VSETVL not in avail.names
    assert RiscvInstrName.VADC not in avail.names
    assert RiscvInstrName.VSBC not in avail.names


def test_vector_filter_drops_zvlsseg_when_off():
    cfg = make_config(get_target("rv64gcv"))
    assert not cfg.vector_cfg.enable_zvlsseg
    avail = create_instr_list(cfg)
    assert RiscvInstrName.VLSEGE_V not in avail.names


def test_vector_filter_drops_zvamo_phase1():
    cfg = make_config(get_target("rv64gcv"))
    avail = create_instr_list(cfg)
    # Zvamo is deferred to Phase 2 (no target wires it in).
    assert RiscvInstrName.VAMOSWAPE_V not in avail.names
