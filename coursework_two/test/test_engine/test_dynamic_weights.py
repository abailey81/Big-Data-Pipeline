"""Dynamic weighting + regime classification tests."""

import numpy as np
import pandas as pd
import pytest

from engine.dynamic_weights import (
    DynamicGridWeights,
    StaticWeights,
    classify_regime_percentile,
    factor_dispersion,
)
from engine.types import Regime


def test_regime_percentile_low():
    vix = pd.Series(list(range(1, 101)))   # monotone [1, 100]
    # Last value is highest → high regime
    r, pct = classify_regime_percentile(vix, low_pct=0.3, high_pct=0.8)
    assert r == Regime.HIGH
    # Reverse: last is lowest
    vix2 = pd.Series(list(range(100, 0, -1)))
    r2, _ = classify_regime_percentile(vix2, low_pct=0.3, high_pct=0.8)
    assert r2 == Regime.LOW


def test_factor_dispersion_sign():
    syms = [f"S{i}" for i in range(20)]
    z = pd.DataFrame({
        "momentum": list(range(20)),
        "value": [-i for i in range(20)],
    }, index=syms)
    d = factor_dispersion(z, long_q=0.25, short_q=0.25)
    # momentum: top 5 avg = 17, bot 5 avg = 2, diff ≈ 15 > 0
    assert d["momentum"] > 10
    # value: top 5 avg = -2, bot 5 avg = -17, diff = 15
    assert d["value"] > 10


def test_static_weights_sum_one(base_config, synthetic_raw_factors, synthetic_gics_map):
    sw = StaticWeights(base_config)
    vix_series = pd.Series(np.linspace(15, 25, 260))
    from engine.zscore import ZScoreEngine
    ze = ZScoreEngine(base_config)
    z = ze.zscore_cross_section(synthetic_raw_factors, synthetic_gics_map)
    w, regime, pct, disp = sw.compute(z, vix_series)
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_dynamic_weights_sum_one(base_config, synthetic_raw_factors, synthetic_gics_map):
    dg = DynamicGridWeights(base_config)
    vix_series = pd.Series(np.linspace(15, 25, 260))
    from engine.zscore import ZScoreEngine
    ze = ZScoreEngine(base_config)
    z = ze.zscore_cross_section(synthetic_raw_factors, synthetic_gics_map)
    w, regime, pct, disp = dg.compute(z, vix_series)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(v >= 0 for v in w.values())
