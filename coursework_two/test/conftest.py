"""Shared pytest fixtures for CW2 test-suite.

Mirrors CW1 ``test/conftest.py`` style — factory fixtures for synthetic data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.config import Config, load_config


@pytest.fixture(scope="session")
def base_config() -> Config:
    """Load the canonical backtest_config.yaml once per test session."""
    return load_config(Path(__file__).parent.parent / "config" / "backtest_config.yaml")


@pytest.fixture
def synthetic_returns():
    """252 × 30 synthetic daily-return DataFrame."""
    rng = np.random.default_rng(42)
    T, N = 252, 30
    dates = pd.date_range("2024-01-01", periods=T, freq="B")
    cols = [f"S{i:02d}" for i in range(N)]
    # Slight cross-sectional heterogeneity
    vol = rng.uniform(0.01, 0.04, size=N)
    rets = rng.standard_normal((T, N)) * vol
    return pd.DataFrame(rets, index=dates, columns=cols)


@pytest.fixture
def synthetic_gics_map():
    sectors = ["Tech", "Financials", "Energy", "Health", "Consumer"]
    return {f"S{i:02d}": sectors[i % len(sectors)] for i in range(30)}


@pytest.fixture
def synthetic_raw_factors():
    rng = np.random.default_rng(0)
    N = 30
    syms = [f"S{i:02d}" for i in range(N)]
    return pd.DataFrame({
        "momentum": rng.standard_normal(N) * 0.2,
        "value": rng.standard_normal(N) * 0.5,
        "quality": rng.standard_normal(N) * 1.0,
        "sentiment": rng.standard_normal(N) * 0.3,
    }, index=syms)


@pytest.fixture
def synthetic_monthly_returns():
    rng = np.random.default_rng(0)
    dates = pd.date_range("2022-01-01", periods=36, freq="ME")
    return pd.Series(rng.normal(0.005, 0.03, len(dates)), index=dates, name="ret")
