"""Tests for Zvfh — vector half-precision FP wiring."""

from __future__ import annotations

import pytest

from rvgen.config import Config, make_config
from rvgen.targets.builtin import BUILTIN_TARGETS
from rvgen.vector_config import VectorConfig, Vtype


def test_vector_config_with_zvfh_accepts_sew_16():
    cfg = VectorConfig(
        vtype=Vtype(vlmul=1, vsew=16, vediv=1),
        vlen=512, elen=32, selen=8, max_lmul=8,
        vec_fp=True,
        enable_zvfh=True,
    )
    # __post_init__ runs — no exception.
    assert cfg.vtype.vsew == 16
    assert cfg.vec_fp is True
    assert cfg.enable_zvfh is True


def test_vector_config_without_zvfh_rejects_sew_16():
    with pytest.raises(ValueError):
        VectorConfig(
            vtype=Vtype(vlmul=1, vsew=16, vediv=1),
            vlen=512, elen=32, selen=8, max_lmul=8,
            vec_fp=True,
            enable_zvfh=False,
        )


def test_vector_config_zvfh_still_allows_sew_32():
    # SEW=32 is always legal regardless of Zvfh.
    cfg = VectorConfig(
        vtype=Vtype(vlmul=1, vsew=32, vediv=1),
        vlen=512, elen=32, selen=8, max_lmul=8,
        vec_fp=True,
        enable_zvfh=True,
    )
    assert cfg.vtype.vsew == 32


def test_rv64gcv_crypto_target_enables_zvfh():
    t = BUILTIN_TARGETS["rv64gcv_crypto"]
    assert t.enable_zvfh is True


def test_rv64gcv_target_does_not_enable_zvfh():
    t = BUILTIN_TARGETS["rv64gcv"]
    assert t.enable_zvfh is False


def test_make_config_passes_zvfh_to_vector_cfg():
    cfg = make_config(BUILTIN_TARGETS["rv64gcv_crypto"])
    assert cfg.vector_cfg is not None
    assert cfg.vector_cfg.enable_zvfh is True


def test_make_config_defaults_sew_16_for_zvfh_target():
    cfg = make_config(BUILTIN_TARGETS["rv64gcv_crypto"])
    assert cfg.vector_cfg.vtype.vsew == 16


def test_make_config_keeps_sew_32_for_non_zvfh_target():
    cfg = make_config(BUILTIN_TARGETS["rv64gcv"])
    assert cfg.vector_cfg.vtype.vsew == 32


def test_make_config_zvfh_does_not_force_zvfh_on_non_fp_target():
    # Embedded Zve32x has no FP-vector → Zvfh shouldn't auto-enable.
    cfg = make_config(BUILTIN_TARGETS["rv32imc_zve32x"])
    assert cfg.vector_cfg is not None
    assert cfg.vector_cfg.enable_zvfh is False
