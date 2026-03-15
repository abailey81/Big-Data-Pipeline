"""
Tests for company static data loading and reading.

Covers:
  - modules.input.get_company_static.get_equity_static
  - modules.input.get_company_static.get_ticker_list
  - modules.input.get_company_static.load_company_static_csv
"""

import csv
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from modules.input.get_company_static import get_equity_static, get_ticker_list, load_company_static_csv

# ---------------------------------------------------------------------------
# load_company_static_csv
# ---------------------------------------------------------------------------


class TestLoadCompanyStaticCsv:

    def _make_csv(self, rows):
        """Create a temporary CSV file with given rows."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
        writer = csv.DictWriter(
            tmp, fieldnames=["Symbol", "Security", "GICS Sector", "GICS Industry", "Country", "Region"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        tmp.close()
        return tmp.name

    def test_parses_single_row(self):
        path = self._make_csv(
            [
                {
                    "Symbol": "AAPL  ",
                    "Security": "Apple Inc.",
                    "GICS Sector": "Information Technology",
                    "GICS Industry": "Technology Hardware",
                    "Country": "United States",
                    "Region": "North America",
                }
            ]
        )
        try:
            records = load_company_static_csv(path)
            assert len(records) == 1
            r = records[0]
            assert r["symbol"] == "AAPL"  # trailing whitespace stripped
            assert r["security"] == "Apple Inc."
            assert r["gics_sector"] == "Information Technology"
            assert r["country"] == "Uni"  # truncated to 3 chars
            assert r["region"] == "North America"
        finally:
            os.unlink(path)

    def test_parses_multiple_rows(self):
        path = self._make_csv(
            [
                {
                    "Symbol": "AAPL",
                    "Security": "Apple Inc.",
                    "GICS Sector": "IT",
                    "GICS Industry": "HW",
                    "Country": "US",
                    "Region": "NA",
                },
                {
                    "Symbol": "MSFT",
                    "Security": "Microsoft Corp",
                    "GICS Sector": "IT",
                    "GICS Industry": "SW",
                    "Country": "US",
                    "Region": "NA",
                },
            ]
        )
        try:
            records = load_company_static_csv(path)
            assert len(records) == 2
            assert records[0]["symbol"] == "AAPL"
            assert records[1]["symbol"] == "MSFT"
        finally:
            os.unlink(path)

    def test_strips_whitespace(self):
        path = self._make_csv(
            [
                {
                    "Symbol": "  BP.L  ",
                    "Security": "  BP plc  ",
                    "GICS Sector": "  Energy  ",
                    "GICS Industry": "  Oil  ",
                    "Country": "  GBR  ",
                    "Region": "  Europe  ",
                }
            ]
        )
        try:
            records = load_company_static_csv(path)
            r = records[0]
            assert r["symbol"] == "BP.L"
            assert r["security"] == "BP plc"
            assert r["gics_sector"] == "Energy"
            assert r["country"] == "GBR"
            assert r["region"] == "Europe"
        finally:
            os.unlink(path)

    def test_empty_csv(self):
        path = self._make_csv([])
        try:
            records = load_company_static_csv(path)
            assert records == []
        finally:
            os.unlink(path)

    def test_real_csv_file(self):
        """Integration test: parse the actual company_static.csv shipped with the project."""
        csv_path = os.path.join(os.path.dirname(__file__), "..", "static", "schema", "company_static.csv")
        if os.path.exists(csv_path):
            records = load_company_static_csv(csv_path)
            assert len(records) > 400  # ~505 tickers expected
            non_empty = [r for r in records if r["symbol"]]
            assert len(non_empty) > 400
            assert all(len(r["country"]) <= 3 for r in records)


# ---------------------------------------------------------------------------
# get_equity_static (mocked DB)
# ---------------------------------------------------------------------------


class TestGetEquityStatic:

    @patch("modules.input.get_company_static.get_postgres_data")
    def test_returns_data(self, mock_get_pg):
        mock_get_pg.return_value = [
            ("AAPL", "Apple Inc", "IT", "HW", "US", "NA"),
            ("MSFT", "Microsoft", "IT", "SW", "US", "NA"),
        ]
        result = get_equity_static(database="testdb", username="u", password="p", host="h", port="5432")
        assert len(result) == 2
        mock_get_pg.assert_called_once()

    @patch("modules.input.get_company_static.get_postgres_data")
    def test_empty_result(self, mock_get_pg):
        mock_get_pg.return_value = []
        result = get_equity_static()
        assert result == []


# ---------------------------------------------------------------------------
# get_ticker_list (mocked DB)
# ---------------------------------------------------------------------------


class TestGetTickerList:

    @patch("modules.input.get_company_static.get_postgres_data")
    def test_returns_stripped_tickers(self, mock_get_pg):
        mock_get_pg.return_value = [("AAPL  ",), ("  MSFT",), ("GOOG",)]
        result = get_ticker_list(database="testdb", username="u", password="p", host="h", port="5432")
        assert result == ["AAPL", "MSFT", "GOOG"]

    @patch("modules.input.get_company_static.get_postgres_data")
    def test_empty_table(self, mock_get_pg):
        mock_get_pg.return_value = []
        result = get_ticker_list()
        assert result == []
