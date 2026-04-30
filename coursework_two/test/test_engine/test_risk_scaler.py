"""Risk-scaler tests — HVaR, vol targeting, drawdown overlay."""

import numpy as np
import pandas as pd
import pytest

from engine.risk_scaler import (
    CompositeRiskScaler,
    historical_es_99,
    historical_var_99,
)


def test_var_99_positive():
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(0, 0.01, 500))
    var = historical_var_99(daily, 0.99)
    assert var > 0


def test_es_exceeds_var():
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(0, 0.02, 500))
    var = historical_var_99(daily, 0.99)
    es = historical_es_99(daily, 0.99)
    assert es >= var


def test_composite_scaler_applies(base_config):
    rs = CompositeRiskScaler(base_config)
    w = pd.Series({"A": 0.3, "B": -0.3, "C": 0.2})
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(0, 0.01, 500))
    scaled, diag = rs.apply(w, daily)
    # All fields present
    assert {"position_scale", "var_99", "es_99", "vol_target_scalar", "dd_control_scalar", "drawdown_12m"} <= diag.keys()
    # Composite scaler should be finite and positive
    assert 0 < diag["position_scale"] < 2.0


def test_dd_control_kicks_in_on_drawdown(base_config):
    rs = CompositeRiskScaler(base_config)
    # Simulate NAV history with 10% drawdown
    dates = pd.date_range("2023-01-01", periods=12, freq="ME")
    navs = [100.0] * 6 + [95, 92, 90, 88, 86, 85]
    for d, n in zip(dates, navs):
        rs.record_nav(d, n)
    scalar, dd = rs.dd_scalar()
    assert dd < -0.1
    assert scalar < 1.0   # should be de-risked


def test_vol_scalar_clipped(base_config):
    rs = CompositeRiskScaler(base_config)
    # Extremely low-vol returns → scalar should hit upper clip (1.5)
    tiny_ret = pd.Series(np.full(100, 1e-6))
    scalar = rs.vol_scalar(tiny_ret)
    assert scalar <= base_config.risk_scaler.vol_target_clip_upper + 1e-8
