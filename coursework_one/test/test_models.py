"""
Tests for Pydantic validation models.

Covers edge cases for validators in:
  - modules.data_models.models (DailyPrice, FundamentalRecord, FxRate,
    VixRecord, RiskFreeRateRecord, IngestionLogEntry)
"""

import math
from datetime import date

import pytest

from modules.data_models.models import (
    DailyPrice,
    FundamentalRecord,
    FxRate,
    IngestionLogEntry,
    RiskFreeRateRecord,
    VixRecord,
)

# ── DailyPrice validator tests ────────────────────────────────────────


class TestDailyPriceValidators:

    def test_coerce_nan_price_to_none(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), close_price=float("nan"))
        assert p.close_price is None

    def test_coerce_inf_price_to_none(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), high_price=float("inf"))
        assert p.high_price is None

    def test_coerce_neg_inf_price_to_none(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), low_price=float("-inf"))
        assert p.low_price is None

    def test_coerce_string_price_to_none(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), open_price="not_a_number")
        assert p.open_price is None

    def test_coerce_nan_volume_to_none(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), volume=float("nan"))
        assert p.volume is None

    def test_coerce_string_volume_to_none(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), volume="bad")
        assert p.volume is None

    def test_symbol_whitespace_stripped(self):
        p = DailyPrice(symbol="AAPL   ", cob_date=date(2024, 1, 1))
        assert p.symbol == "AAPL"

    def test_high_low_inversion_corrected(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), high_price=100.0, low_price=200.0)
        assert p.high_price == 200.0
        assert p.low_price == 100.0

    def test_high_low_normal_preserved(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1), high_price=200.0, low_price=100.0)
        assert p.high_price == 200.0
        assert p.low_price == 100.0

    def test_none_prices_accepted(self):
        p = DailyPrice(
            symbol="AAPL",
            cob_date=date(2024, 1, 1),
        )
        assert p.open_price is None
        assert p.high_price is None
        assert p.low_price is None
        assert p.close_price is None

    def test_default_currency_usd(self):
        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 1))
        assert p.currency == "USD"


# ── FundamentalRecord validator tests ─────────────────────────────────


class TestFundamentalRecordValidators:

    def test_coerce_nan_value_to_none(self):
        r = FundamentalRecord(
            symbol="AAPL", report_date=date(2024, 3, 31), field_name="total_revenue", field_value=float("nan")
        )
        assert r.field_value is None

    def test_coerce_inf_value_to_none(self):
        r = FundamentalRecord(
            symbol="AAPL", report_date=date(2024, 3, 31), field_name="net_income", field_value=float("inf")
        )
        assert r.field_value is None

    def test_coerce_string_value_to_none(self):
        r = FundamentalRecord(
            symbol="AAPL", report_date=date(2024, 3, 31), field_name="ebitda", field_value="N/A"
        )
        assert r.field_value is None

    def test_symbol_whitespace_stripped(self):
        r = FundamentalRecord(
            symbol="HSBA.L  ", report_date=date(2024, 3, 31), field_name="total_assets", field_value=1000.0
        )
        assert r.symbol == "HSBA.L"

    def test_default_period_type(self):
        r = FundamentalRecord(symbol="AAPL", report_date=date(2024, 3, 31), field_name="test")
        assert r.period_type == "quarterly"


# ── FxRate validator tests ────────────────────────────────────────────


class TestFxRateValidators:

    def test_coerce_nan_rate_to_none(self):
        r = FxRate(currency_pair="GBPUSD=X", cob_date=date(2024, 1, 1), close_rate=float("nan"))
        assert r.close_rate is None

    def test_coerce_string_rate_to_none(self):
        r = FxRate(currency_pair="GBPUSD=X", cob_date=date(2024, 1, 1), open_rate="bad")
        assert r.open_rate is None

    def test_valid_rates_preserved(self):
        r = FxRate(currency_pair="GBPUSD=X", cob_date=date(2024, 1, 1), open_rate=1.26, close_rate=1.27)
        assert r.open_rate == 1.26
        assert r.close_rate == 1.27


# ── VixRecord validator tests ─────────────────────────────────────────


class TestVixRecordValidators:

    def test_coerce_nan_price_to_none(self):
        v = VixRecord(cob_date=date(2024, 1, 1), close_price=float("nan"))
        assert v.close_price is None

    def test_coerce_string_volume_to_none(self):
        v = VixRecord(cob_date=date(2024, 1, 1), volume="bad")
        assert v.volume is None

    def test_nan_volume_to_none(self):
        v = VixRecord(cob_date=date(2024, 1, 1), volume=float("nan"))
        assert v.volume is None

    def test_coerce_string_price_to_none(self):
        v = VixRecord(cob_date=date(2024, 1, 1), open_price="bad")
        assert v.open_price is None


# ── RiskFreeRateRecord validator tests ────────────────────────────────


class TestRiskFreeRateRecordValidators:

    def test_coerce_dot_to_none(self):
        """FRED uses '.' for missing values."""
        r = RiskFreeRateRecord(cob_date=date(2024, 1, 1), rate_pct=".")
        assert r.rate_pct is None

    def test_coerce_nan_to_none(self):
        r = RiskFreeRateRecord(cob_date=date(2024, 1, 1), rate_pct=float("nan"))
        assert r.rate_pct is None

    def test_coerce_string_to_none(self):
        r = RiskFreeRateRecord(cob_date=date(2024, 1, 1), rate_pct="N/A")
        assert r.rate_pct is None

    def test_valid_rate_preserved(self):
        r = RiskFreeRateRecord(cob_date=date(2024, 1, 1), rate_pct=4.25)
        assert r.rate_pct == 4.25

    def test_default_series_id(self):
        r = RiskFreeRateRecord(cob_date=date(2024, 1, 1))
        assert r.series_id == "DGS3MO"


# ── IngestionLogEntry tests ───────────────────────────────────────────


class TestIngestionLogEntry:

    def test_valid_entry(self):
        entry = IngestionLogEntry(
            run_id="run-1", data_source="prices", symbol="AAPL", status="SUCCESS", rows_affected=100
        )
        assert entry.status == "SUCCESS"

    def test_invalid_status_raises(self):
        with pytest.raises(Exception):
            IngestionLogEntry(run_id="run-1", data_source="prices", status="INVALID")

    def test_default_rows_affected(self):
        entry = IngestionLogEntry(run_id="run-1", data_source="fx", status="FAILED")
        assert entry.rows_affected == 0

    def test_optional_fields_none(self):
        entry = IngestionLogEntry(run_id="run-1", data_source="vix", status="SKIPPED")
        assert entry.symbol is None
        assert entry.error_message is None
        assert entry.run_frequency is None
