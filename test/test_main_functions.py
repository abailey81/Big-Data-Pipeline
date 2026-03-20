"""
Tests for Main.py helper functions.

Covers:
  - _get_date_range() with frequency-based lookback
  - _make_log_entry() with field construction and truncation
  - _get_db_client() with mocked DatabaseMethods
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from modules.orchestration.state import get_date_range as _get_date_range, make_log_entry as _make_log_entry

# ── _get_date_range tests ─────────────────────────────────────────────


class TestGetDateRange:

    def test_explicit_start_and_end_dates(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(start_date="2023-01-01", end_date="2024-12-31")
        start, end = _get_date_range(sample_conf, args)
        assert start == "2023-01-01"
        assert end == "2024-12-31"

    def test_explicit_start_overrides_frequency(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(start_date="2020-01-01", end_date="2024-12-31", frequency="daily")
        start, end = _get_date_range(sample_conf, args)
        assert start == "2020-01-01"  # Not overridden by daily lookback

    def test_date_run_as_fallback_end(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(end_date=None, date_run="2024-06-15")
        _, end = _get_date_range(sample_conf, args)
        assert end == "2024-06-15"

    def test_daily_frequency_lookback(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(frequency="daily", date_run="2024-06-15")
        start, end = _get_date_range(sample_conf, args)
        expected_start = (datetime(2024, 6, 15) - timedelta(days=5)).strftime("%Y-%m-%d")
        assert start == expected_start
        assert end == "2024-06-15"

    def test_weekly_frequency_lookback(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(frequency="weekly", date_run="2024-06-15")
        start, _ = _get_date_range(sample_conf, args)
        expected = (datetime(2024, 6, 15) - timedelta(days=14)).strftime("%Y-%m-%d")
        assert start == expected

    def test_monthly_frequency_lookback(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(frequency="monthly", date_run="2024-06-15")
        start, _ = _get_date_range(sample_conf, args)
        expected = (datetime(2024, 6, 15) - timedelta(days=35)).strftime("%Y-%m-%d")
        assert start == expected

    def test_quarterly_frequency_lookback(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(frequency="quarterly", date_run="2024-06-15")
        start, _ = _get_date_range(sample_conf, args)
        expected = (datetime(2024, 6, 15) - timedelta(days=95)).strftime("%Y-%m-%d")
        assert start == expected

    def test_unknown_frequency_uses_lookback_years(self, sample_conf, mock_parsed_args):
        args = mock_parsed_args(frequency=None, date_run="2024-06-15")
        start, _ = _get_date_range(sample_conf, args)
        expected = (datetime(2024, 6, 15) - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
        assert start == expected


# ── _make_log_entry tests ─────────────────────────────────────────────


class TestMakeLogEntry:

    def test_minimal_required_fields(self):
        entry = _make_log_entry("run-123", "prices", "AAPL", "SUCCESS")
        assert entry["run_id"] == "run-123"
        assert entry["data_source"] == "prices"
        assert entry["symbol"] == "AAPL"
        assert entry["status"] == "SUCCESS"
        assert entry["rows_affected"] == 0
        assert "error_message" not in entry
        assert "run_frequency" not in entry
        assert "date_range_start" not in entry
        assert "date_range_end" not in entry

    def test_all_fields_populated(self):
        entry = _make_log_entry(
            "run-456", "fx", "GBPUSD=X", "FAILED", 100, "connection lost", "daily", "2024-01-01", "2024-12-31"
        )
        assert entry["rows_affected"] == 100
        assert entry["error_message"] == "connection lost"
        assert entry["run_frequency"] == "daily"
        assert entry["date_range_start"] == "2024-01-01"
        assert entry["date_range_end"] == "2024-12-31"

    def test_error_truncation_at_500_chars(self):
        long_error = "x" * 600
        entry = _make_log_entry("run-789", "vix", "^VIX", "FAILED", 0, long_error)
        assert len(entry["error_message"]) == 500

    def test_optional_fields_excluded_when_none(self):
        entry = _make_log_entry(
            "run-0", "prices", "MSFT", "SUCCESS", 50, error=None, frequency=None, start=None, end=None
        )
        assert "error_message" not in entry
        assert "run_frequency" not in entry

    def test_rows_count_preserved(self):
        entry = _make_log_entry("run-x", "fundamentals", "VOD.L", "SUCCESS", 1234)
        assert entry["rows_affected"] == 1234


# ── _get_db_client tests ──────────────────────────────────────────────


class TestGetDbClient:

    @patch("modules.orchestration.state.DatabaseMethods")
    @patch("modules.orchestration.state.PostgresConfig")
    def test_creates_client_with_correct_params(self, mock_pg_cls, mock_db_cls, sample_conf):
        from modules.orchestration.state import get_db_client as _get_db_client

        mock_pg_instance = MagicMock()
        mock_pg_instance.username = "postgres"
        mock_pg_instance.password = "postgres"
        mock_pg_instance.host = "localhost"
        mock_pg_instance.port = "5438"
        mock_pg_instance.database = "fift"
        mock_pg_cls.return_value = mock_pg_instance

        _get_db_client(sample_conf)

        mock_db_cls.assert_called_once_with(
            "postgres",
            username="postgres",
            password="postgres",
            host="localhost",
            port="5438",
            database="fift",
        )
