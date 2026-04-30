"""
Tests for the DataExporter output module.

Covers:
  - query_by_symbol with date filters
  - query_by_year
  - to_csv_string
  - to_json_string
  - get_table_summary
  - get_company_summary
  - error handling for unknown tables
"""

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from modules.output.data_exporter import DataExporter


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def exporter(mock_db):
    return DataExporter(mock_db)


class TestQueryBySymbol:

    def test_returns_rows_for_symbol(self, exporter, mock_db):
        mock_db.read_query.return_value = [
            ("AAPL", date(2024, 1, 2), 150.0),
            ("AAPL", date(2024, 1, 3), 151.0),
        ]
        rows = exporter.query_by_symbol("daily_prices", "AAPL")
        assert len(rows) == 2
        mock_db.read_query.assert_called_once()

    def test_with_date_range(self, exporter, mock_db):
        mock_db.read_query.return_value = [("AAPL", date(2024, 1, 2), 150.0)]
        rows = exporter.query_by_symbol(
            "daily_prices", "AAPL", start_date="2024-01-01", end_date="2024-01-05"
        )
        assert len(rows) == 1
        # Dates are now bound parameters (SQL injection safe)
        query = mock_db.read_query.call_args[0][0]
        params = mock_db.read_query.call_args[0][1]
        assert ":start_dt" in query
        assert ":end_dt" in query
        assert params["start_dt"] == "2024-01-01"
        assert params["end_dt"] == "2024-01-05"

    def test_unknown_table_raises(self, exporter):
        with pytest.raises(ValueError, match="Unknown table"):
            exporter.query_by_symbol("nonexistent_table", "AAPL")

    def test_table_without_symbol_key_raises(self, exporter):
        with pytest.raises(ValueError, match="does not support symbol filtering"):
            exporter.query_by_symbol("vix_data", "AAPL")

    def test_empty_results(self, exporter, mock_db):
        mock_db.read_query.return_value = None
        rows = exporter.query_by_symbol("daily_prices", "UNKNOWN")
        assert rows == []


class TestQueryByYear:

    def test_returns_year_data(self, exporter, mock_db):
        mock_db.read_query.return_value = [
            (date(2024, 3, 1), 14.5),
        ]
        rows = exporter.query_by_year("vix_data", 2024)
        assert len(rows) == 1
        # Year is now a bound parameter (SQL injection safe)
        query = mock_db.read_query.call_args[0][0]
        params = mock_db.read_query.call_args[0][1]
        assert ":yr" in query
        assert params["yr"] == 2024

    def test_unknown_table_raises(self, exporter):
        with pytest.raises(ValueError, match="Unknown table"):
            exporter.query_by_year("fake_table", 2024)

    def test_empty_results(self, exporter, mock_db):
        mock_db.read_query.return_value = None
        rows = exporter.query_by_year("daily_prices", 1999)
        assert rows == []


class TestToCsvString:

    def test_basic_csv(self, exporter):
        rows = [("AAPL", "2024-01-02", 150.0), ("AAPL", "2024-01-03", 151.0)]
        csv_str = exporter.to_csv_string(rows)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2

    def test_csv_with_headers(self, exporter):
        rows = [("AAPL", 150.0)]
        csv_str = exporter.to_csv_string(rows, headers=["symbol", "price"])
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2
        assert "symbol" in lines[0]

    def test_empty_rows(self, exporter):
        csv_str = exporter.to_csv_string([])
        assert csv_str.strip() == ""


class TestToJsonString:

    def test_basic_json(self, exporter):
        rows = [("AAPL", 150.0)]
        headers = ["symbol", "price"]
        json_str = exporter.to_json_string(rows, headers)
        parsed = json.loads(json_str)
        assert len(parsed) == 1
        assert parsed[0]["symbol"] == "AAPL"
        assert parsed[0]["price"] == 150.0

    def test_date_serialization(self, exporter):
        rows = [(date(2024, 1, 2), 150.0)]
        headers = ["date", "price"]
        json_str = exporter.to_json_string(rows, headers)
        parsed = json.loads(json_str)
        assert parsed[0]["date"] == "2024-01-02"

    def test_empty_rows(self, exporter):
        json_str = exporter.to_json_string([], ["col1"])
        assert json.loads(json_str) == []

    def test_more_cols_than_headers(self, exporter):
        rows = [("AAPL", 150.0, "extra")]
        headers = ["symbol", "price"]
        json_str = exporter.to_json_string(rows, headers)
        parsed = json.loads(json_str)
        assert parsed[0]["col_2"] == "extra"


class TestGetTableSummary:

    def test_summary_with_symbol_table(self, exporter, mock_db):
        mock_db.read_query.side_effect = [
            [(1500,)],  # count
            [(date(2020, 1, 1), date(2024, 12, 31))],  # date range
            [(678,)],  # symbol count
        ]
        summary = exporter.get_table_summary("daily_prices")
        assert summary["table"] == "daily_prices"
        assert summary["row_count"] == 1500
        assert summary["symbol_count"] == 678
        assert "date_range" in summary

    def test_summary_without_symbol_table(self, exporter, mock_db):
        mock_db.read_query.side_effect = [
            [(500,)],  # count
            [(date(2020, 1, 1), date(2024, 12, 31))],  # date range
        ]
        summary = exporter.get_table_summary("vix_data")
        assert summary["row_count"] == 500
        assert "symbol_count" not in summary

    def test_unknown_table_raises(self, exporter):
        with pytest.raises(ValueError):
            exporter.get_table_summary("nonexistent")


class TestGetCompanySummary:

    def test_company_with_data(self, exporter, mock_db):
        mock_db.read_query.return_value = [(252, date(2024, 1, 2), date(2024, 12, 31))]
        summary = exporter.get_company_summary("AAPL")
        assert summary["symbol"] == "AAPL"
        assert len(summary["tables"]) > 0

    def test_company_with_no_data(self, exporter, mock_db):
        mock_db.read_query.return_value = [(0, None, None)]
        summary = exporter.get_company_summary("DEAD")
        assert summary["symbol"] == "DEAD"
        assert len(summary["tables"]) == 0


class TestExportableTablesList:

    def test_all_expected_tables_present(self):
        expected = [
            "daily_prices",
            "fundamentals",
            "company_ratios",
            "fx_rates",
            "vix_data",
            "risk_free_rate",
            "benchmark_index",
            "esg_scores",
            "news_sentiment",
        ]
        for table in expected:
            assert table in DataExporter.EXPORTABLE_TABLES

    def test_each_table_has_date_col(self):
        for table, meta in DataExporter.EXPORTABLE_TABLES.items():
            assert "date_col" in meta
            assert meta["date_col"] is not None
