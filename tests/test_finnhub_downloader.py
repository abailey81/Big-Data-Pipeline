"""
Tests for Finnhub fundamentals downloader.

Covers:
  - modules.input.finnhub_downloader helper functions
  - modules.input.finnhub_downloader.FinnhubFundamentalsDownloader
  - modules.input.finnhub_downloader.extract_finnhub_fundamentals
"""

import json
import urllib.error
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from modules.input.finnhub_downloader import (
    BALANCE_FIELD_MAP,
    CASHFLOW_FIELD_MAP,
    INCOME_FIELD_MAP,
    SUFFIX_TO_EXCHANGE,
    FinnhubFundamentalsDownloader,
    _finnhub_ticker,
    extract_finnhub_fundamentals,
    is_non_us_ticker,
)

# ── Helper function tests ────────────────────────────────────────────


class TestFinnhubTicker:

    def test_strips_whitespace(self):
        assert _finnhub_ticker("HSBA.L   ") == "HSBA.L"

    def test_already_clean(self):
        assert _finnhub_ticker("TTE.PA") == "TTE.PA"

    def test_empty_string(self):
        assert _finnhub_ticker("") == ""


class TestIsNonUsTicker:

    def test_london_is_non_us(self):
        assert is_non_us_ticker("HSBA.L") is True

    def test_us_ticker_is_not_non_us(self):
        assert is_non_us_ticker("AAPL") is False

    def test_ticker_with_whitespace(self):
        assert is_non_us_ticker("  HSBA.L  ") is True

    def test_us_with_whitespace(self):
        assert is_non_us_ticker("  AAPL  ") is False


class TestSuffixMapping:

    def test_all_expected_suffixes_present(self):
        expected = [".L", ".PA", ".AS", ".DE", ".MC", ".MI", ".TO", ".SW", ".S"]
        for suffix in expected:
            assert suffix in SUFFIX_TO_EXCHANGE

    def test_swiss_alternate_maps_to_sw(self):
        assert SUFFIX_TO_EXCHANGE[".S"] == "SW"


# ── FinnhubFundamentalsDownloader tests ───────────────────────────────


class TestFinnhubDownloaderInit:

    def test_init_sets_api_key(self):
        dl = FinnhubFundamentalsDownloader(api_key="test_key")
        assert dl.api_key == "test_key"

    def test_init_sets_source_name(self):
        dl = FinnhubFundamentalsDownloader(api_key="test_key")
        assert dl.source_name == "finnhub_fundamentals"

    def test_init_default_retries(self):
        dl = FinnhubFundamentalsDownloader(api_key="k")
        assert dl.max_retries == 3

    def test_stats_initial(self):
        dl = FinnhubFundamentalsDownloader(api_key="k")
        s = dl.stats
        assert s["downloads"] == 0
        assert s["successes"] == 0
        assert s["failures"] == 0


class TestFinnhubDownloaderFetchJson:

    @patch("modules.input.finnhub_downloader.urllib.request.urlopen")
    def test_fetch_json_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"data": []}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        dl = FinnhubFundamentalsDownloader(api_key="mykey")
        result = dl._fetch_json("https://finnhub.io/api/v1/test?param=1")
        assert result == {"data": []}
        # Verify token appended with &
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "token=mykey" in req.full_url

    @patch("modules.input.finnhub_downloader.urllib.request.urlopen")
    def test_fetch_json_appends_token_with_question_mark(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b"{}"
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        dl = FinnhubFundamentalsDownloader(api_key="k")
        dl._fetch_json("https://finnhub.io/api/v1/test")
        req = mock_urlopen.call_args[0][0]
        assert "?token=k" in req.full_url


class TestFinnhubDownloaderDownload:

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_download_success_both_frequencies(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fin.side_effect = [
            [{"endDate": "2024-01-01", "report": {}}],  # quarterly
            [{"endDate": "2024-01-01", "report": {}}],  # annual
        ]
        result = dl.download("HSBA.L")
        assert result is not None
        assert "quarterly" in result
        assert "annual" in result
        assert dl._success_count == 1

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_download_both_empty(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fin.return_value = None
        result = dl.download("HSBA.L")
        assert result is not None
        assert result.get("quarterly") == []
        assert result.get("annual") == []
        assert dl._failure_count == 1

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_download_circuit_open_skips(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0)
        dl.circuit_breaker._state = 1  # OPEN
        dl.circuit_breaker._opened_at = 0  # opened long ago but recovery_timeout=30
        import time as _t

        dl.circuit_breaker._opened_at = _t.time()  # just opened
        result = dl.download("HSBA.L")
        # Circuit is OPEN — should skip
        assert result is None or dl._failure_count >= 1

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_download_403_returns_empty_list(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fin.side_effect = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        result = dl.download("HSBA.L")
        assert result is not None
        assert result.get("quarterly") == []
        assert result.get("annual") == []

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_download_429_retries(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0, max_retries=2)
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise urllib.error.HTTPError("url", 429, "Too Many", {}, None)
            return [{"endDate": "2024-01-01", "report": {}}]

        mock_fin.side_effect = side_effect
        result = dl.download("HSBA.L")
        assert result is not None

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_execute_download_delegates(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fin.return_value = [{"report": {}}]
        result = dl._execute_download("HSBA.L", freq="annual")
        mock_fin.assert_called_once_with("HSBA.L", "annual")

    @patch("modules.input.finnhub_downloader.time.sleep")
    @patch.object(FinnhubFundamentalsDownloader, "_download_financials")
    def test_execute_download_default_quarterly(self, mock_fin, mock_sleep):
        dl = FinnhubFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fin.return_value = None
        dl._execute_download("HSBA.L")
        mock_fin.assert_called_once_with("HSBA.L", "quarterly")


# ── extract_finnhub_fundamentals tests ────────────────────────────────


SAMPLE_FINNHUB_REPORTS = {
    "quarterly": [
        {
            "endDate": "2024-03-31",
            "report": {
                "currency": "GBP",
                "ic": [
                    {"concept": "revenue", "value": 5000000},
                    {"concept": "netIncome", "value": 1200000},
                    {"concept": "eps", "value": 1.5},
                ],
                "bs": [
                    {"concept": "totalAssets", "value": 80000000},
                    {"concept": "totalLiabilities", "value": 40000000},
                    {"concept": "totalEquity", "value": 40000000},
                ],
                "cf": [
                    {"concept": "operatingCashflow", "value": 2000000},
                    {"concept": "capitalExpenditures", "value": -500000},
                    {"concept": "freeCashFlow", "value": 1500000},
                ],
            },
        },
        {
            "endDate": "2023-12-31",
            "report": {
                "currency": "GBP",
                "ic": [
                    {"concept": "revenue", "value": 4800000},
                ],
                "bs": [
                    {"concept": "totalAssets", "value": 78000000},
                ],
                "cf": [],
            },
        },
    ],
    "annual": [
        {
            "endDate": "2023-12-31",
            "report": {
                "currency": "GBP",
                "ic": [
                    {"concept": "revenue", "value": 19000000},
                    {"concept": "netIncome", "value": 4500000},
                ],
                "bs": [
                    {"concept": "totalAssets", "value": 78000000},
                ],
                "cf": [],
            },
        },
    ],
}


class TestExtractFinnhubFundamentals:

    def test_extracts_quarterly_records(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        quarterly = [r for r in records if r["period_type"] == "quarterly"]
        assert len(quarterly) > 0

    def test_extracts_annual_records(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        annual = [r for r in records if r["period_type"] == "annual"]
        assert len(annual) > 0

    def test_maps_revenue_correctly(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        revenue_recs = [r for r in records if r["field_name"] == "total_revenue"]
        assert len(revenue_recs) >= 2  # quarterly + annual
        for r in revenue_recs:
            assert r["field_value"] > 0

    def test_maps_total_assets(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        assets = [r for r in records if r["field_name"] == "total_assets"]
        assert len(assets) >= 2

    def test_maps_cash_flow_fields(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        cf_fields = {
            r["field_name"]
            for r in records
            if r["field_name"] in ("operating_cash_flow", "capital_expenditure", "free_cash_flow")
        }
        assert "operating_cash_flow" in cf_fields
        assert "capital_expenditure" in cf_fields
        assert "free_cash_flow" in cf_fields

    def test_symbol_set_correctly(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        for r in records:
            assert r["symbol"] == "HSBA.L"

    def test_currency_from_report(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L")
        for r in records:
            assert r["currency"] == "GBP"

    def test_currency_override(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L", currency="EUR")
        for r in records:
            assert r["currency"] == "EUR"

    def test_start_date_filter(self):
        records = extract_finnhub_fundamentals(SAMPLE_FINNHUB_REPORTS, "HSBA.L", start_date="2024-01-01")
        for r in records:
            assert r["report_date"] >= date(2024, 1, 1)

    def test_empty_reports_returns_empty(self):
        assert extract_finnhub_fundamentals(None, "HSBA.L") == []
        assert extract_finnhub_fundamentals({}, "HSBA.L") == []

    def test_deduplicates_by_field_date_period(self):
        reports = {
            "quarterly": [
                {
                    "endDate": "2024-03-31",
                    "report": {
                        "ic": [
                            {"concept": "revenue", "value": 100},
                            {"concept": "totalRevenue", "value": 200},  # same canonical
                        ],
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        revenue = [r for r in records if r["field_name"] == "total_revenue"]
        assert len(revenue) == 1  # deduped

    def test_skips_none_values(self):
        reports = {
            "quarterly": [
                {
                    "endDate": "2024-03-31",
                    "report": {
                        "ic": [
                            {"concept": "revenue", "value": None},
                        ],
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 0

    def test_skips_bad_date(self):
        reports = {
            "quarterly": [
                {
                    "endDate": "not-a-date",
                    "report": {
                        "ic": [{"concept": "revenue", "value": 100}],
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 0

    def test_skips_missing_end_date(self):
        reports = {
            "quarterly": [
                {
                    "report": {
                        "ic": [{"concept": "revenue", "value": 100}],
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 0

    def test_skips_empty_report_data(self):
        reports = {
            "quarterly": [
                {
                    "endDate": "2024-03-31",
                    "report": {},
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 0

    def test_non_numeric_value_skipped(self):
        reports = {
            "quarterly": [
                {
                    "endDate": "2024-03-31",
                    "report": {
                        "ic": [{"concept": "revenue", "value": "N/A"}],
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 0

    def test_uses_period_field_as_fallback_date(self):
        reports = {
            "quarterly": [
                {
                    "period": "2024-06-30",
                    "report": {
                        "ic": [{"concept": "revenue", "value": 100}],
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 1
        assert records[0]["report_date"] == date(2024, 6, 30)

    def test_stmt_not_list_skipped(self):
        reports = {
            "quarterly": [
                {
                    "endDate": "2024-03-31",
                    "report": {
                        "ic": "not a list",
                        "bs": [],
                        "cf": [],
                    },
                },
            ],
        }
        records = extract_finnhub_fundamentals(reports, "TEST")
        assert len(records) == 0
