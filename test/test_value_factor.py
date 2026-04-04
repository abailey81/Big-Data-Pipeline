import pandas as pd
import pytest

from modules.processing.value_factor import (
    ValueFactorConfig,
    build_value_factor,
    prepare_latest_fundamentals,
    prepare_latest_prices,
)


def make_sample_fundamentals():
    rows = [
        # AAA
        ("AAA", "2024-03-31", "book_value_per_share", 20),
        ("AAA", "2024-03-31", "diluted_eps", 2),
        ("AAA", "2024-03-31", "free_cash_flow", 1000),
        ("AAA", "2024-03-31", "shares_outstanding", 100),

        # BBB: diluted_eps missing -> use basic_eps
        ("BBB", "2024-03-31", "book_value_per_share", 10),
        ("BBB", "2024-03-31", "basic_eps", 1.5),
        ("BBB", "2024-03-31", "free_cash_flow", 500),
        ("BBB", "2024-03-31", "shares_outstanding", 100),

        # CCC: free_cash_flow missing -> build from ocf - abs(capex)
        ("CCC", "2024-03-31", "book_value_per_share", 8),
        ("CCC", "2024-03-31", "diluted_eps", 1.0),
        ("CCC", "2024-03-31", "operating_cash_flow", 700),
        ("CCC", "2024-03-31", "capital_expenditure", -200),
        ("CCC", "2024-03-31", "shares_outstanding", 100),

        # DDD
        ("DDD", "2024-03-31", "book_value_per_share", 12),
        ("DDD", "2024-03-31", "diluted_eps", 1.2),
        ("DDD", "2024-03-31", "free_cash_flow", 600),
        ("DDD", "2024-03-31", "shares_outstanding", 100),

        # EEE
        ("EEE", "2024-03-31", "book_value_per_share", 15),
        ("EEE", "2024-03-31", "diluted_eps", 1.8),
        ("EEE", "2024-03-31", "free_cash_flow", 900),
        ("EEE", "2024-03-31", "shares_outstanding", 100),

        # Look-ahead test symbol
        ("ZZZ", "2024-03-31", "book_value_per_share", 10),
        ("ZZZ", "2024-03-31", "diluted_eps", 1.0),
        ("ZZZ", "2024-06-30", "book_value_per_share", 999),
        ("ZZZ", "2024-06-30", "diluted_eps", 999),
    ]
    return pd.DataFrame(rows, columns=["symbol", "report_date", "field_name", "field_value"])


def make_sample_prices():
    rows = [
        ("AAA", "2024-04-01", 10, 10),
        ("BBB", "2024-04-01", 20, 20),
        ("CCC", "2024-04-01", 25, 25),
        ("DDD", "2024-04-01", 30, 30),
        ("EEE", "2024-04-01", 18, 18),
        ("ZZZ", "2024-04-01", 10, 10),
    ]
    return pd.DataFrame(rows, columns=["symbol", "cob_date", "adj_close_price", "close_price"])


def make_sample_sectors():
    rows = [
        ("AAA", "Tech"),
        ("BBB", "Tech"),
        ("CCC", "Tech"),
        ("DDD", "Tech"),
        ("EEE", "Tech"),
        ("ZZZ", "Health"),
    ]
    return pd.DataFrame(rows, columns=["symbol", "gics_sector"])


def test_prepare_latest_fundamentals_no_lookahead():
    fund = make_sample_fundamentals()
    latest = prepare_latest_fundamentals(fund, "2024-05-01")

    row = latest[latest["symbol"] == "ZZZ"].iloc[0]
    assert row["book_value_per_share"] == 10
    assert row["diluted_eps"] == 1.0


def test_prepare_latest_prices():
    prices = make_sample_prices()
    latest = prepare_latest_prices(prices, "2024-05-01")

    assert len(latest) == 6
    assert "price" in latest.columns
    assert latest.loc[latest["symbol"] == "AAA", "price"].iloc[0] == 10


def test_build_value_factor_basic_calculation():
    fund = make_sample_fundamentals()
    prices = make_sample_prices()
    sectors = make_sample_sectors()

    result = build_value_factor(fund, prices, sectors, "2024-05-01")

    aaa = result[result["symbol"] == "AAA"].iloc[0]
    assert aaa["book_to_price_raw"] == pytest.approx(20 / 10)
    assert aaa["earnings_to_price_raw"] == pytest.approx(2 / 10)
    assert aaa["cashflow_to_price_raw"] == pytest.approx(1000 / (10 * 100))


def test_eps_fallback_from_basic_eps():
    fund = make_sample_fundamentals()
    prices = make_sample_prices()
    sectors = make_sample_sectors()

    result = build_value_factor(fund, prices, sectors, "2024-05-01")

    bbb = result[result["symbol"] == "BBB"].iloc[0]
    assert bbb["earnings_to_price_raw"] == pytest.approx(1.5 / 20)


def test_fcf_fallback_from_ocf_minus_capex():
    fund = make_sample_fundamentals()
    prices = make_sample_prices()
    sectors = make_sample_sectors()

    result = build_value_factor(fund, prices, sectors, "2024-05-01")

    ccc = result[result["symbol"] == "CCC"].iloc[0]
    expected_fcf = 700 - abs(-200)
    expected_cfp = expected_fcf / (25 * 100)
    assert ccc["cashflow_to_price_raw"] == pytest.approx(expected_cfp)


def test_small_sector_gets_neutral_score():
    fund = make_sample_fundamentals()
    prices = make_sample_prices()
    sectors = make_sample_sectors()

    result = build_value_factor(
        fund,
        prices,
        sectors,
        "2024-05-01",
        config=ValueFactorConfig(min_sector_size=5, neutral_score=0.0),
    )

    zzz = result[result["symbol"] == "ZZZ"].iloc[0]
    assert zzz["book_to_price_z"] == 0.0
    assert zzz["earnings_to_price_z"] == 0.0