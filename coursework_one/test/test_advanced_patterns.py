"""
Advanced test patterns: parametrized tests, boundary conditions,
error injection, and cross-component integration.

Demonstrates sophisticated testing techniques for the Systematic Equity Pipeline:
  - pytest.mark.parametrize for exhaustive input coverage
  - Boundary value analysis for edge cases
  - Error injection and fault tolerance verification
  - Data consistency invariants
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Parametrized ticker utility tests ──────────────────────────────────


class TestTickerUtilsParametrized:
    """Exhaustive parametrized testing of ticker processing pipeline."""

    @pytest.mark.parametrize(
        "raw_input,expected_clean",
        [
            ("AAPL", "AAPL"),
            ("AAPL   ", "AAPL"),
            ("  MSFT  ", "MSFT"),
            ("VOD.L    ", "VOD.L"),
            ("NOVN.S      ", "NOVN.S"),
            ("BRK.B ", "BRK.B"),
            ("RY.TO  ", "RY.TO"),
            ("TTE.PA", "TTE.PA"),
        ],
    )
    def test_clean_ticker_whitespace(self, raw_input, expected_clean):
        from modules.processing.ticker_utils import clean_ticker

        assert clean_ticker(raw_input) == expected_clean

    @pytest.mark.parametrize(
        "symbol,expected_suffix",
        [
            ("AAPL", ""),
            ("VOD.L", ".L"),
            ("TTE.PA", ".PA"),
            ("ASML.AS", ".AS"),
            ("SAP.DE", ".DE"),
            ("NOVN.S", ".S"),
            ("RY.TO", ".TO"),
            ("BRK.B", ".B"),
        ],
    )
    def test_exchange_suffix_extraction(self, symbol, expected_suffix):
        from modules.processing.ticker_utils import get_exchange_suffix

        assert get_exchange_suffix(symbol) == expected_suffix

    @pytest.mark.parametrize(
        "symbol,expected_currency",
        [
            ("AAPL", "USD"),
            ("MSFT", "USD"),
            ("VOD.L", "GBP"),
            ("SHEL.L", "GBP"),
            ("TTE.PA", "EUR"),
            ("ASML.AS", "EUR"),
            ("SAP.DE", "EUR"),
            ("RY.TO", "CAD"),
            ("NOVN.S", "CHF"),
            ("UNKNOWN.XX", "USD"),  # Falls back to USD
        ],
    )
    def test_currency_inference_parametrized(self, symbol, expected_currency):
        from modules.processing.ticker_utils import infer_currency

        assert infer_currency(symbol) == expected_currency

    @pytest.mark.parametrize(
        "input_symbol,expected_yf",
        [
            ("NOVN.S", "NOVN.SW"),
            ("NESN.S", "NESN.SW"),
            ("RO.S", "RO.SW"),
            ("AAPL", "AAPL"),
            ("VOD.L", "VOD.L"),
            ("NOVN.SW", "NOVN.SW"),  # Already remapped
        ],
    )
    def test_swiss_remap_parametrized(self, input_symbol, expected_yf):
        from modules.processing.ticker_utils import remap_swiss_ticker

        assert remap_swiss_ticker(input_symbol) == expected_yf

    @pytest.mark.parametrize(
        "raw,db_sym,yf_sym,ccy",
        [
            ("AAPL   ", "AAPL", "AAPL", "USD"),
            ("VOD.L  ", "VOD.L", "VOD.L", "GBP"),
            ("NOVN.S  ", "NOVN.S", "NOVN.SW", "CHF"),
            ("RY.TO ", "RY.TO", "RY.TO", "CAD"),
            ("TTE.PA", "TTE.PA", "TTE.PA", "EUR"),
        ],
    )
    def test_prepare_yfinance_ticker_full_pipeline(self, raw, db_sym, yf_sym, ccy):
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        result_db, result_yf, result_ccy = prepare_yfinance_ticker(raw)
        assert result_db == db_sym
        assert result_yf == yf_sym
        assert result_ccy == ccy


# ── Parametrized safe conversion tests ─────────────────────────────────


class TestSafeConversionParametrized:
    """Boundary values and edge cases for type coercion."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            (42.5, 42.5),
            (0.0, 0.0),
            (-1.5, -1.5),
            (1e15, 1e15),
            (float("nan"), None),
            (float("inf"), None),
            (float("-inf"), None),
            (None, None),
            ("123.45", 123.45),
            ("not_a_number", None),
            (np.nan, None),
            (np.float64(42.5), 42.5),
            (np.int64(100), 100.0),
            (True, 1.0),
            (False, 0.0),
        ],
    )
    def test_safe_float_parametrized(self, input_val, expected):
        from modules.processing.data_cleaner import _safe_float

        result = _safe_float(input_val)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected)

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            (42, 42),
            (42.9, 42),
            (0, 0),
            (-5, -5),
            (float("nan"), None),
            (None, None),
            (np.int64(100), 100),
        ],
    )
    def test_safe_int_parametrized(self, input_val, expected):
        from modules.processing.data_cleaner import _safe_int

        result = _safe_int(input_val)
        assert result == expected


# ── Pydantic model boundary tests ─────────────────────────────────────


class TestDailyPriceBoundaryValues:
    """Boundary value analysis for DailyPrice Pydantic model."""

    @pytest.mark.parametrize(
        "open_p,high_p,low_p,close_p",
        [
            (0.01, 0.02, 0.005, 0.015),  # Penny stock
            (1e5, 1.1e5, 9e4, 1.05e5),  # High-value stock (BRK.A)
            (None, None, None, None),  # All prices None
            (100.0, None, None, 100.0),  # Only open and close
        ],
    )
    def test_price_model_accepts_extreme_values(self, open_p, high_p, low_p, close_p):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(
            symbol="TEST",
            cob_date=date(2024, 1, 2),
            open_price=open_p,
            high_price=high_p,
            low_price=low_p,
            close_price=close_p,
            currency="USD",
        )
        assert p.open_price == open_p

    @pytest.mark.parametrize("volume", [0, 1, 1000000, 2**31, None])
    def test_volume_boundary_values(self, volume):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(symbol="TEST", cob_date=date(2024, 1, 2), volume=volume, currency="USD")
        assert p.volume == volume

    @pytest.mark.parametrize(
        "high,low,expected_high,expected_low",
        [
            (152.0, 148.0, 152.0, 148.0),  # Normal: no swap
            (148.0, 152.0, 152.0, 148.0),  # Inverted: auto-swap
            (150.0, 150.0, 150.0, 150.0),  # Equal: no swap
            (None, 148.0, None, 148.0),  # High is None: no swap
            (152.0, None, 152.0, None),  # Low is None: no swap
        ],
    )
    def test_high_low_swap_logic(self, high, low, expected_high, expected_low):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(
            symbol="TEST", cob_date=date(2024, 1, 2), high_price=high, low_price=low, currency="USD"
        )
        assert p.high_price == expected_high
        assert p.low_price == expected_low


class TestFundamentalRecordBoundary:
    """Boundary tests for fundamental records."""

    @pytest.mark.parametrize(
        "field_name,field_value",
        [
            ("net_income", 1e12),  # Apple-scale income
            ("net_income", -5e9),  # Large loss
            ("basic_eps", 0.001),  # Micro EPS
            ("total_assets", 0.0),  # Edge: zero assets
            ("book_value_per_share", None),  # NULL allowed
        ],
    )
    def test_fundamental_accepts_financial_values(self, field_name, field_value):
        from modules.data_models.models import FundamentalRecord

        r = FundamentalRecord(
            symbol="AAPL", report_date=date(2024, 9, 30), field_name=field_name, field_value=field_value
        )
        assert r.field_value == field_value


# ── Error injection tests ──────────────────────────────────────────────


class TestErrorInjection:
    """Verify pipeline resilience to injected failures."""

    def test_price_cleaner_survives_corrupt_dataframe(self):
        from modules.processing.data_cleaner import clean_price_dataframe

        df = pd.DataFrame(
            {
                "Open": ["corrupt", None, 150.0],
                "High": [None, "NaN", 152.0],
                "Low": [149.0, None, None],
                "Close": [None, None, 150.0],
                "Volume": ["bad", -1, 1000000],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        )
        records = clean_price_dataframe(df, "CORRUPT", "USD")
        # Should not crash — produces whatever it can
        assert isinstance(records, list)
        assert len(records) == 3  # All rows attempted

    def test_fx_cleaner_handles_all_nan_rates(self):
        from modules.processing.data_cleaner import clean_fx_dataframe

        df = pd.DataFrame(
            {
                "Open": [np.nan, np.nan],
                "High": [np.nan, np.nan],
                "Low": [np.nan, np.nan],
                "Close": [np.nan, np.nan],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        records = clean_fx_dataframe(df, "GBPUSD=X")
        # Rows with NULL close are skipped — they indicate crumb-poisoned
        # downloads and should not be persisted to the database.
        assert len(records) == 0

    def test_fundamentals_cleaner_handles_missing_fields(self):
        from modules.processing.data_cleaner import clean_fundamentals_data

        # Balance sheet with none of the expected field names
        bs = pd.DataFrame(
            {
                pd.Timestamp("2024-09-30"): [1000, 2000],
            },
            index=["Unknown Field 1", "Unknown Field 2"],
        )
        result = clean_fundamentals_data(
            {"balance_sheet": bs, "income_stmt": pd.DataFrame(), "info": {}}, "TEST", "USD"
        )
        # Should still produce records (with None values)
        assert isinstance(result, list)

    def test_vix_cleaner_handles_missing_adj_close(self):
        from modules.processing.data_cleaner import clean_vix_dataframe

        # VIX data without Adj Close column
        df = pd.DataFrame(
            {
                "Open": [13.5],
                "High": [14.2],
                "Low": [13.0],
                "Close": [13.8],
                "Volume": [0],
            },
            index=pd.to_datetime(["2024-01-02"]),
        )
        records = clean_vix_dataframe(df)
        assert len(records) == 1
        assert records[0]["close_price"] == 13.8


# ── Frequency lookback parametrized tests ──────────────────────────────


class TestDateRangeParametrized:
    """Parametrized tests for frequency-based lookback calculation."""

    @pytest.mark.parametrize(
        "frequency,expected_days",
        [
            ("daily", 5),
            ("weekly", 14),
            ("monthly", 35),
            ("quarterly", 95),
        ],
    )
    def test_frequency_lookback_days(self, frequency, expected_days, sample_conf, mock_parsed_args):
        from modules.orchestration.state import get_date_range as _get_date_range

        args = mock_parsed_args(frequency=frequency, date_run="2024-06-15")
        start, end = _get_date_range(sample_conf, args)
        expected_start = (datetime(2024, 6, 15) - timedelta(days=expected_days)).strftime("%Y-%m-%d")
        assert start == expected_start
        assert end == "2024-06-15"


# ── Data quality checker parametrized tests ────────────────────────────


class TestDataQualityParametrized:
    """Parametrized data quality checks."""

    @pytest.mark.parametrize(
        "close,expected_null_count",
        [
            (150.0, 0),
            (None, 1),
        ],
    )
    def test_null_close_detection(self, close, expected_null_count):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("prices")
        records = [{"close_price": close, "high_price": 152.0, "low_price": 149.0, "volume": 100}]
        report = dq.check_price_records(records)
        assert report["null_close"] == expected_null_count

    @pytest.mark.parametrize(
        "rate,expected_non_positive",
        [
            (1.268, 0),
            (0.0, 1),
            (-0.5, 1),
        ],
    )
    def test_fx_rate_validation(self, rate, expected_non_positive):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("fx")
        records = [{"close_rate": rate}]
        report = dq.check_fx_records(records)
        assert report["non_positive_rate"] == expected_non_positive


# ── Progress tracker tests ─────────────────────────────────────────────


class TestProgressTracker:
    """Test progress tracker with rich dependency mocked."""

    def test_tracker_initialises(self):
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run", total_tickers=678)
        assert tracker.run_id == "test-run"
        assert tracker.total_tickers == 678

    def test_banner_does_not_raise(self):
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run", total_tickers=10)
        tracker.print_banner()  # Should not raise

    def test_source_progress_yields_update(self):
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run")
        with tracker.source_progress("prices", 3) as update:
            update("AAPL", "SUCCESS")
            update("MSFT", "FAILED")
            update("DEAD", "SKIPPED")
        outcomes = tracker._source_outcomes.get("prices", {})
        assert outcomes["success"] == 1
        assert outcomes["failed"] == 1
        assert outcomes["skipped"] == 1

    def test_phase_start_does_not_raise(self):
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run")
        tracker.print_phase_start("prices")

    def test_phase_complete_does_not_raise(self):
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run")
        tracker.print_phase_complete("prices", 10.5, 50000)

    def test_print_summary_does_not_raise(self):
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run")
        tracker.print_summary(
            {
                "run_id": "test-run",
                "total_elapsed_seconds": 42.5,
                "sources": {
                    "prices": {
                        "elapsed_seconds": 30.0,
                        "total_rows": 50000,
                        "success": 600,
                        "failed": 10,
                        "skipped": 68,
                    }
                },
            }
        )

    def test_circuit_breaker_status_does_not_raise(self):
        from modules.utils.circuit_breaker import CircuitBreaker
        from modules.utils.progress_tracker import PipelineProgressTracker

        tracker = PipelineProgressTracker("test-run")
        cbs = [
            CircuitBreaker("prices"),
            CircuitBreaker("fx"),
        ]
        tracker.print_circuit_breaker_status(cbs)
