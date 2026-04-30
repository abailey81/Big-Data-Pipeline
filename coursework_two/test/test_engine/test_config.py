"""Config loading + validation tests."""

import pytest
from engine.config import load_config, Config


def test_load_default_config(base_config):
    assert isinstance(base_config, Config)
    assert base_config.database.port == 5439
    assert base_config.database.schema_ == "systematic_equity"


def test_base_weights_sum_to_one(base_config):
    bw = base_config.factors.base_weights
    assert abs(bw.momentum + bw.value + bw.quality + bw.sentiment - 1.0) < 1e-6


def test_config_hash_deterministic(base_config):
    h1 = base_config.config_hash()
    h2 = base_config.config_hash()
    assert h1 == h2
    assert len(h1) == 16


def test_config_hash_changes_with_parameter(base_config):
    h1 = base_config.config_hash()
    base_config.dynamic_weights.gamma = 0.999
    h2 = base_config.config_hash()
    assert h1 != h2


def test_gamma_grid_populated(base_config):
    assert len(base_config.backtest.gamma_grid) >= 3
    assert len(base_config.backtest.lambda_magnitude_grid) >= 3


def test_vix_thresholds_sensible(base_config):
    dw = base_config.dynamic_weights
    assert 0 < dw.vix_low_pct < dw.vix_high_pct < 1
