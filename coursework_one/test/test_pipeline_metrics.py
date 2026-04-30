"""
Tests for modules.utils.pipeline_metrics and modules.processing.data_quality.

Covers:
  - PipelineMetrics timing, outcome tracking, and summary reporting
  - DataQualityChecker for prices, FX, and fundamentals records
  - Cross-field Pydantic model validation (high/low swap)
"""

import time
from datetime import date

import pytest

from modules.processing.data_quality import DataQualityChecker
from modules.utils.pipeline_metrics import PipelineMetrics

# ── PipelineMetrics tests ────────────────────────────────────────────────


class TestPipelineMetrics:

    def test_track_records_elapsed_time(self):
        m = PipelineMetrics("run-1")
        with m.track("prices"):
            time.sleep(0.05)
        assert "prices" in m._timings
        assert m._timings["prices"] >= 0.04

    def test_record_outcome_increments_success(self):
        m = PipelineMetrics("run-2")
        m.record_outcome("prices", "AAPL", "SUCCESS", 252)
        m.record_outcome("prices", "MSFT", "SUCCESS", 250)
        assert m._counts["prices"]["success"] == 2
        assert m._counts["prices"]["total_rows"] == 502

    def test_record_outcome_increments_failed(self):
        m = PipelineMetrics("run-3")
        m.record_outcome("fx", "GBPUSD=X", "FAILED")
        assert m._counts["fx"]["failed"] == 1
        assert m._counts["fx"]["total_rows"] == 0

    def test_record_outcome_increments_skipped(self):
        m = PipelineMetrics("run-4")
        m.record_outcome("fundamentals", "DEAD.L", "SKIPPED")
        assert m._counts["fundamentals"]["skipped"] == 1

    def test_to_dict_structure(self):
        m = PipelineMetrics("run-5")
        with m.track("vix"):
            pass
        m.record_outcome("vix", "^VIX", "SUCCESS", 1260)
        d = m.to_dict()
        assert d["run_id"] == "run-5"
        assert "total_elapsed_seconds" in d
        assert "vix" in d["sources"]
        assert d["sources"]["vix"]["total_rows"] == 1260

    def test_log_summary_does_not_raise(self):
        m = PipelineMetrics("run-6")
        with m.track("prices"):
            pass
        m.record_outcome("prices", "AAPL", "SUCCESS", 100)
        m.record_outcome("prices", "FAIL", "FAILED")
        m.log_summary()  # Should not raise

    def test_multiple_sources(self):
        m = PipelineMetrics("run-7")
        with m.track("prices"):
            pass
        with m.track("fx"):
            pass
        m.record_outcome("prices", "AAPL", "SUCCESS", 252)
        m.record_outcome("fx", "GBPUSD=X", "SUCCESS", 1260)
        d = m.to_dict()
        assert len(d["sources"]) == 2


# ── DataQualityChecker tests ─────────────────────────────────────────────


class TestDataQualityCheckerPrices:

    def test_empty_records(self):
        dq = DataQualityChecker("prices")
        report = dq.check_price_records([])
        assert report["total"] == 0
        assert report["issues"] == []

    def test_clean_records_no_issues(self):
        dq = DataQualityChecker("prices")
        records = [
            {"close_price": 150.0, "high_price": 152.0, "low_price": 149.0, "volume": 1000000},
        ]
        report = dq.check_price_records(records)
        assert report["issues"] == []
        assert report["null_close"] == 0

    def test_null_close_flagged(self):
        dq = DataQualityChecker("prices")
        records = [
            {"close_price": None, "high_price": 152.0, "low_price": 149.0, "volume": 100},
        ]
        report = dq.check_price_records(records)
        assert report["null_close"] == 1
        assert len(report["issues"]) == 1

    def test_high_low_inverted_flagged(self):
        dq = DataQualityChecker("prices")
        records = [
            {"close_price": 150.0, "high_price": 148.0, "low_price": 152.0, "volume": 100},
        ]
        report = dq.check_price_records(records)
        assert report["high_low_inverted"] == 1

    def test_negative_volume_flagged(self):
        dq = DataQualityChecker("prices")
        records = [
            {"close_price": 150.0, "high_price": 152.0, "low_price": 149.0, "volume": -100},
        ]
        report = dq.check_price_records(records)
        assert report["negative_volume"] == 1


class TestDataQualityCheckerFx:

    def test_clean_fx_records(self):
        dq = DataQualityChecker("fx")
        records = [{"close_rate": 1.268, "open_rate": 1.265}]
        report = dq.check_fx_records(records)
        assert report["issues"] == []

    def test_non_positive_rate_flagged(self):
        dq = DataQualityChecker("fx")
        records = [{"close_rate": 0.0}]
        report = dq.check_fx_records(records)
        assert report["non_positive_rate"] == 1


class TestDataQualityCheckerFundamentals:

    def test_high_null_rate_flagged(self):
        dq = DataQualityChecker("fundamentals")
        records = [
            {"field_name": "net_income", "field_value": None},
            {"field_name": "ebitda", "field_value": None},
            {"field_name": "total_debt", "field_value": None},
        ]
        report = dq.check_fundamentals_records(records)
        assert report["null_pct"] == 100.0
        assert len(report["issues"]) == 1

    def test_field_distribution_counted(self):
        dq = DataQualityChecker("fundamentals")
        records = [
            {"field_name": "net_income", "field_value": 5000},
            {"field_name": "net_income", "field_value": 4800},
            {"field_name": "ebitda", "field_value": 8000},
        ]
        report = dq.check_fundamentals_records(records)
        assert report["field_distribution"]["net_income"] == 2
        assert report["field_distribution"]["ebitda"] == 1


# ── Pydantic cross-field validation tests ─────────────────────────────────


class TestDailyPriceCrossValidation:

    def test_high_low_swap_when_inverted(self):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(
            symbol="TEST", cob_date=date(2024, 1, 2), high_price=148.0, low_price=152.0, currency="USD"
        )
        assert p.high_price == 152.0
        assert p.low_price == 148.0

    def test_normal_high_low_unchanged(self):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(
            symbol="TEST", cob_date=date(2024, 1, 2), high_price=155.0, low_price=148.0, currency="USD"
        )
        assert p.high_price == 155.0
        assert p.low_price == 148.0

    def test_none_high_low_no_error(self):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(
            symbol="TEST", cob_date=date(2024, 1, 2), high_price=None, low_price=148.0, currency="USD"
        )
        assert p.high_price is None
        assert p.low_price == 148.0
