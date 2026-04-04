"""
Tests for quality factor construction in the multi-factor equity strategy.

Covers:
    - prepare_latest_fundamentals (latest available fundamentals as of rebalance date)
    - prepare_eps_history (earnings stability using trailing EPS history with diluted/basic EPS fallback)
    - compute_quality_subsignals (ROE, inverse D/E, and equity fallback logic)
    - build_quality_factor (sector-neutral winsorisation, z-scoring, and composite quality score)
    - small-sector handling (neutral score when sector size is below threshold)
    - look-ahead bias protection (exclude future report_date observations)
    - final quality_score aggregation as the mean of component z-scores
"""

import numpy as np
import pandas as pd

from modules.processing.quality_factor import (
    QualityFactorConfig,
    build_quality_factor,
    compute_quality_subsignals,
    prepare_eps_history,
    prepare_latest_fundamentals,
)


def make_sample_fundamentals():
    rows = [
        # AAA older data
        ("AAA", "2023-12-31", "net_income", 80),
        ("AAA", "2023-12-31", "stockholders_equity", 400),

        # AAA latest point-in-time fields
        ("AAA", "2024-03-31", "net_income", 100),
        ("AAA", "2024-03-31", "stockholders_equity", 500),
        ("AAA", "2024-03-31", "total_assets", 1000),
        ("AAA", "2024-03-31", "total_liabilities", 500),
        ("AAA", "2024-03-31", "total_debt", 200),

        # AAA EPS history (12 quarters)
        ("AAA", "2021-06-30", "diluted_eps", 1.00),
        ("AAA", "2021-09-30", "diluted_eps", 1.10),
        ("AAA", "2021-12-31", "diluted_eps", 1.15),
        ("AAA", "2022-03-31", "diluted_eps", 1.20),
        ("AAA", "2022-06-30", "diluted_eps", 1.25),
        ("AAA", "2022-09-30", "diluted_eps", 1.30),
        ("AAA", "2022-12-31", "diluted_eps", 1.35),
        ("AAA", "2023-03-31", "diluted_eps", 1.40),
        ("AAA", "2023-06-30", "diluted_eps", 1.45),
        ("AAA", "2023-09-30", "diluted_eps", 1.50),
        ("AAA", "2023-12-31", "diluted_eps", 1.55),
        ("AAA", "2024-03-31", "diluted_eps", 1.60),

        # BBB latest point-in-time fields; missing stockholders_equity to force fallback
        ("BBB", "2024-03-31", "net_income", 60),
        ("BBB", "2024-03-31", "total_assets", 600),
        ("BBB", "2024-03-31", "total_liabilities", 200),
        ("BBB", "2024-03-31", "total_debt", 100),

        # BBB EPS history using basic_eps fallback
        ("BBB", "2021-06-30", "basic_eps", 0.80),
        ("BBB", "2021-09-30", "basic_eps", 0.82),
        ("BBB", "2021-12-31", "basic_eps", 0.84),
        ("BBB", "2022-03-31", "basic_eps", 0.86),
        ("BBB", "2022-06-30", "basic_eps", 0.88),
        ("BBB", "2022-09-30", "basic_eps", 0.90),
        ("BBB", "2022-12-31", "basic_eps", 0.92),
        ("BBB", "2023-03-31", "basic_eps", 0.95),
        ("BBB", "2023-06-30", "basic_eps", 0.98),
        ("BBB", "2023-09-30", "basic_eps", 1.00),
        ("BBB", "2023-12-31", "basic_eps", 1.03),
        ("BBB", "2024-03-31", "basic_eps", 1.05),

        # CCC small-sector stock
        ("CCC", "2024-03-31", "net_income", 20),
        ("CCC", "2024-03-31", "stockholders_equity", 100),
        ("CCC", "2024-03-31", "total_assets", 250),
        ("CCC", "2024-03-31", "total_liabilities", 150),
        ("CCC", "2024-03-31", "total_debt", 50),

        ("CCC", "2021-06-30", "diluted_eps", 0.40),
        ("CCC", "2021-09-30", "diluted_eps", 0.42),
        ("CCC", "2021-12-31", "diluted_eps", 0.41),
        ("CCC", "2022-03-31", "diluted_eps", 0.43),
        ("CCC", "2022-06-30", "diluted_eps", 0.44),
        ("CCC", "2022-09-30", "diluted_eps", 0.45),
        ("CCC", "2022-12-31", "diluted_eps", 0.46),
        ("CCC", "2023-03-31", "diluted_eps", 0.47),
        ("CCC", "2023-06-30", "diluted_eps", 0.49),
        ("CCC", "2023-09-30", "diluted_eps", 0.50),
        ("CCC", "2023-12-31", "diluted_eps", 0.52),
        ("CCC", "2024-03-31", "diluted_eps", 0.53),
    ]

    return pd.DataFrame(
        rows,
        columns=["symbol", "report_date", "field_name", "field_value"],
    )


def make_sample_sectors():
    return pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC"],
            "gics_sector": ["Tech", "Tech", "Utilities"],
        }
    )


def test_prepare_latest_fundamentals_selects_latest_asof():
    fundamentals = make_sample_fundamentals()

    result = prepare_latest_fundamentals(fundamentals, "2024-05-01")

    aaa = result[result["symbol"] == "AAA"].iloc[0]
    assert aaa["net_income"] == 100
    assert aaa["stockholders_equity"] == 500


def test_prepare_latest_fundamentals_does_not_use_future_data():
    fundamentals = make_sample_fundamentals().copy()
    future_row = pd.DataFrame(
        [["AAA", "2024-06-30", "net_income", 999]],
        columns=["symbol", "report_date", "field_name", "field_value"],
    )
    fundamentals = pd.concat([fundamentals, future_row], ignore_index=True)

    result = prepare_latest_fundamentals(fundamentals, "2024-05-01")
    aaa = result[result["symbol"] == "AAA"].iloc[0]

    assert aaa["net_income"] == 100


def test_prepare_eps_history_uses_basic_eps_fallback():
    fundamentals = make_sample_fundamentals()

    eps_hist = prepare_eps_history(fundamentals, "2024-05-01")
    bbb = eps_hist[eps_hist["symbol"] == "BBB"].iloc[0]

    assert pd.notna(bbb["earnings_stability_raw"])


def test_prepare_eps_history_returns_nan_when_history_too_short():
    fundamentals = pd.DataFrame(
        [
            ("ZZZ", "2023-09-30", "diluted_eps", 1.0),
            ("ZZZ", "2023-12-31", "diluted_eps", 1.1),
            ("ZZZ", "2024-03-31", "diluted_eps", 1.2),
        ],
        columns=["symbol", "report_date", "field_name", "field_value"],
    )

    eps_hist = prepare_eps_history(fundamentals, "2024-05-01")
    zzz = eps_hist[eps_hist["symbol"] == "ZZZ"].iloc[0]

    assert pd.isna(zzz["earnings_stability_raw"])


def test_compute_quality_subsignals_uses_equity_fallback():
    raw = pd.DataFrame(
        {
            "symbol": ["BBB"],
            "gics_sector": ["Tech"],
            "net_income": [60],
            "stockholders_equity": [None],
            "total_assets": [600],
            "total_liabilities": [200],
            "total_debt": [100],
            "earnings_stability_raw": [10.0],
        }
    )

    result = compute_quality_subsignals(raw)
    row = result.iloc[0]

    # equity fallback = 600 - 200 = 400
    assert row["roe_raw"] == 60 / 400
    assert row["inverse_de_raw"] == 400 / 100
    assert row["earnings_stability_raw"] == 10.0


def test_inverse_de_zero_debt_produces_extreme_raw_value():
    raw = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "gics_sector": ["Tech"],
            "net_income": [100],
            "stockholders_equity": [500],
            "total_assets": [1000],
            "total_liabilities": [500],
            "total_debt": [0],
            "earnings_stability_raw": [5.0],
        }
    )

    result = compute_quality_subsignals(raw)
    row = result.iloc[0]

    assert np.isinf(row["inverse_de_raw"])


def test_build_quality_factor_returns_expected_columns():
    fundamentals = make_sample_fundamentals()
    sectors = make_sample_sectors()

    result = build_quality_factor(
        fundamentals_df=fundamentals,
        sectors_df=sectors,
        rebalance_date="2024-05-01",
    )

    expected_cols = {
        "symbol",
        "gics_sector",
        "rebalance_date",
        "roe_raw",
        "earnings_stability_raw",
        "inverse_de_raw",
        "roe_z",
        "earnings_stability_z",
        "inverse_de_z",
        "quality_score",
    }

    assert expected_cols.issubset(set(result.columns))
    assert len(result) == 3


def test_small_sector_gets_neutral_score():
    fundamentals = make_sample_fundamentals()
    sectors = make_sample_sectors()

    result = build_quality_factor(
        fundamentals_df=fundamentals,
        sectors_df=sectors,
        rebalance_date="2024-05-01",
        config=QualityFactorConfig(min_sector_size=5, neutral_score=0.0),
    )

    ccc = result[result["symbol"] == "CCC"].iloc[0]
    assert ccc["roe_z"] == 0.0
    assert ccc["earnings_stability_z"] == 0.0
    assert ccc["inverse_de_z"] == 0.0


def test_rebalance_date_is_written_correctly():
    fundamentals = make_sample_fundamentals()
    sectors = make_sample_sectors()

    result = build_quality_factor(
        fundamentals_df=fundamentals,
        sectors_df=sectors,
        rebalance_date="2024-05-01",
    )

    assert str(result["rebalance_date"].iloc[0]) == "2024-05-01"


def test_quality_score_is_mean_of_subscores():
    fundamentals = make_sample_fundamentals()
    sectors = make_sample_sectors()

    result = build_quality_factor(
        fundamentals_df=fundamentals,
        sectors_df=sectors,
        rebalance_date="2024-05-01",
        config=QualityFactorConfig(min_sector_size=2, neutral_score=0.0),
    )

    aaa = result[result["symbol"] == "AAA"].iloc[0]
    expected = (
        aaa["roe_z"]
        + aaa["earnings_stability_z"]
        + aaa["inverse_de_z"]
    ) / 3.0

    assert aaa["quality_score"] == expected