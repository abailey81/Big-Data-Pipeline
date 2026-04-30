"""Sector-neutral z-score tests."""

import numpy as np
import pandas as pd
import pytest

from engine.zscore import ZScoreEngine


def test_zscore_sector_mean_zero(base_config, synthetic_raw_factors, synthetic_gics_map):
    ze = ZScoreEngine(base_config)
    z = ze.zscore_cross_section(synthetic_raw_factors, synthetic_gics_map)
    # Each factor within each sector should have mean ~ 0
    df_with_sec = z.join(pd.Series(synthetic_gics_map, name="sector"))
    means = df_with_sec.groupby("sector").mean()
    # At least the overall structure: means are tiny
    assert means.drop(columns="sector", errors="ignore").abs().max().max() < 0.3


def test_zscore_small_sectors_neutral(base_config, synthetic_raw_factors):
    gics_tiny = {s: f"sec_{i}" for i, s in enumerate(synthetic_raw_factors.index)}
    ze = ZScoreEngine(base_config)
    # Override config to require ≥5 stocks/sector
    base_config.factors.min_sector_size = 5
    z = ze.zscore_cross_section(synthetic_raw_factors, gics_tiny)
    # All z-scores should be zero (neutral)
    assert np.allclose(z.fillna(0.0).values, 0.0)


def test_composite_weighted_sum(base_config, synthetic_raw_factors, synthetic_gics_map):
    ze = ZScoreEngine(base_config)
    z = ze.zscore_cross_section(synthetic_raw_factors, synthetic_gics_map)
    comp = ze.composite(z)
    # Should have values for each symbol
    assert len(comp) == len(synthetic_raw_factors)
    # Mean close to 0 (composite of mean-0 signals)
    assert abs(comp.mean()) < 0.5


def test_factor_ic_basic(base_config, synthetic_raw_factors, synthetic_gics_map):
    ze = ZScoreEngine(base_config)
    z = ze.zscore_cross_section(synthetic_raw_factors, synthetic_gics_map)
    fwd = pd.Series(np.random.RandomState(0).randn(len(z)), index=z.index)
    ic = ze.factor_ic(z, fwd)
    assert set(ic["factor"]) == {"momentum", "value", "quality", "sentiment"}
    assert ic["ic_spearman"].between(-1, 1).all()
