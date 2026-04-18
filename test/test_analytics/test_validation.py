import pandas as pd

from analytics.validation import (
    validate_factors,
    validate_regime,
    validate_returns,
    validate_weights,
)


def test_validate_weights_flags_negative_weight() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-01-31", "2026-01-31"],
            "symbol": ["AAA", "BBB"],
            "weight": [0.60, -0.10],
        }
    )
    issues = validate_weights(df)
    assert not issues.empty


def test_validate_returns_flags_nan() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-01-31", "2026-03-31"],
            "gross_return": [0.01, None],
            "net_return": [0.009, 0.005],
            "exposure": [1.0, 0.8],
        }
    )
    issues = validate_returns(df)
    assert not issues.empty


def test_validate_factors_flags_bad_zero_run() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31"],
            "symbol": ["AAA", "AAA", "AAA", "AAA"],
            "gics_sector": ["Tech", "Tech", "Tech", "Tech"],
            "momentum_z": [0.0, 0.0, 0.0, 0.0],
            "composite_z": [0.0, 0.0, 0.0, 0.0],
        }
    )
    issues = validate_factors(df)
    assert not issues.empty


def test_validate_regime_flags_invalid_regime() -> None:
    df = pd.DataFrame(
        {
            "date": ["2026-01-31"],
            "vix_percentile": [1.2],
            "regime": ["weird"],
            "signal_200dma": [1],
            "signal_12m": [0],
            "signal_vix": [1],
            "trigger_count": [1],  # should be 2, so this is inconsistent on purpose
            "exposure": [0.8],
        }
    )
    issues = validate_regime(df)
    assert not issues.empty