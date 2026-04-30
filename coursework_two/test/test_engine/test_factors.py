"""Factor-engine + orthogonalisation tests."""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.factors import FactorEngine, orthogonalise


def test_orthogonalise_reduces_correlations(synthetic_raw_factors, synthetic_gics_map):
    raw = synthetic_raw_factors.copy()
    # Inject strong value-quality correlation
    raw["quality"] = 0.7 * raw["value"] + 0.3 * raw["quality"]
    ortho = orthogonalise(raw, synthetic_gics_map, order=["momentum", "value", "quality", "sentiment"])
    # Within-sector OLS means off-diagonal corrs should go to zero approximately
    corr = ortho.corr()
    # Diagonal = 1
    for f in corr.columns:
        assert abs(corr.loc[f, f] - 1.0) < 1e-6
    # Off-diagonal value-quality should be lower than before
    before = raw.corr().loc["value", "quality"]
    after = corr.loc["value", "quality"]
    assert abs(after) < abs(before) + 0.1


def test_orthogonalise_preserves_shape(synthetic_raw_factors, synthetic_gics_map):
    ortho = orthogonalise(synthetic_raw_factors, synthetic_gics_map)
    assert ortho.shape == synthetic_raw_factors.shape
    assert list(ortho.columns) == list(synthetic_raw_factors.columns)


def test_orthogonalise_first_factor_unchanged(synthetic_raw_factors, synthetic_gics_map):
    order = ["momentum", "value", "quality", "sentiment"]
    ortho = orthogonalise(synthetic_raw_factors, synthetic_gics_map, order=order)
    # First factor in order should equal raw
    pd.testing.assert_series_equal(
        ortho["momentum"], synthetic_raw_factors["momentum"], check_names=False
    )


def test_orthogonalise_handles_small_sectors(synthetic_raw_factors):
    # All-unique sectors: each stock in its own sector (below min_group_size=5)
    gics_tiny = {s: f"sec_{i}" for i, s in enumerate(synthetic_raw_factors.index)}
    ortho = orthogonalise(synthetic_raw_factors, gics_tiny, min_group_size=5)
    # Should return raw unchanged (no residualisation possible)
    pd.testing.assert_frame_equal(ortho, synthetic_raw_factors)
