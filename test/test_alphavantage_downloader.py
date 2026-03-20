"""
Tests for Alpha Vantage fundamentals downloader.

Covers:
  - modules.input.alphavantage_downloader.AlphaVantageFundamentalsDownloader
  - Symbol conversion (Yahoo Finance -> Alpha Vantage format)
  - Key rotation and exhaustion logic
  - HTTP fetch with rate-limit / error handling
  - Record extraction from income, balance sheet, and cash flow statements
  - Derived field computation (total_debt fallback, book_value, diluted_eps,
    free_cash_flow)
  - Deduplication logic
  - Full download() orchestration
"""

import json
import urllib.error
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from modules.input.alphavantage_downloader import (
    BALANCE_FIELD_MAP,
    CASHFLOW_FIELD_MAP,
    INCOME_FIELD_MAP,
    YF_TO_AV_SUFFIX,
    AlphaVantageFundamentalsDownloader,
)


# ── Fixtures ────────────────────────────────────────────────────────────

def _make_downloader(keys=None, api_delay=0, max_retries=2):
    """Create a downloader with injected API keys (no env vars needed)."""
    # Reset class-level state before each test helper call
    AlphaVantageFundamentalsDownloader._current_key_idx = 0
    AlphaVantageFundamentalsDownloader._exhausted_keys = set()

    with patch.dict("os.environ", {}, clear=True):
        dl = AlphaVantageFundamentalsDownloader(
            api_delay=api_delay,
            max_retries=max_retries,
        )
    # Inject keys directly
    if keys is not None:
        dl._api_keys = list(keys)
    return dl


def _mock_urlopen_response(data_dict):
    """Build a mock context-manager response for urllib.request.urlopen."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data_dict).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── Realistic AV API response fixtures ──────────────────────────────────

SAMPLE_INCOME_RESPONSE = {
    "symbol": "AAPL",
    "quarterlyReports": [
        {
            "fiscalDateEnding": "2024-03-31",
            "reportedCurrency": "USD",
            "totalRevenue": "94836000000",
            "netIncome": "23636000000",
            "grossProfit": "42818000000",
            "operatingIncome": "27900000000",
            "ebitda": "32500000000",
            "eps": "1.53",
        },
        {
            "fiscalDateEnding": "2023-12-31",
            "reportedCurrency": "USD",
            "totalRevenue": "119575000000",
            "netIncome": "33916000000",
            "grossProfit": "54855000000",
            "operatingIncome": "40373000000",
            "ebitda": "45000000000",
            "eps": "2.18",
        },
    ],
    "annualReports": [
        {
            "fiscalDateEnding": "2023-09-30",
            "reportedCurrency": "USD",
            "totalRevenue": "383285000000",
            "netIncome": "96995000000",
            "grossProfit": "169148000000",
            "operatingIncome": "114301000000",
            "ebitda": "130000000000",
            "eps": "6.16",
        },
    ],
}

SAMPLE_BALANCE_RESPONSE = {
    "symbol": "AAPL",
    "quarterlyReports": [
        {
            "fiscalDateEnding": "2024-03-31",
            "reportedCurrency": "USD",
            "totalShareholderEquity": "74100000000",
            "totalAssets": "352583000000",
            "totalLiabilities": "278483000000",
            "shortLongTermDebtTotal": "104590000000",
            "commonStockSharesOutstanding": "15441900000",
        },
    ],
    "annualReports": [
        {
            "fiscalDateEnding": "2023-09-30",
            "reportedCurrency": "USD",
            "totalShareholderEquity": "62146000000",
            "totalAssets": "352583000000",
            "totalLiabilities": "290437000000",
            "shortLongTermDebtTotal": "111088000000",
            "commonStockSharesOutstanding": "15550061000",
        },
    ],
}

SAMPLE_CASHFLOW_RESPONSE = {
    "symbol": "AAPL",
    "quarterlyReports": [
        {
            "fiscalDateEnding": "2024-03-31",
            "reportedCurrency": "USD",
            "operatingCashflow": "26390000000",
            "capitalExpenditures": "2160000000",
        },
    ],
    "annualReports": [
        {
            "fiscalDateEnding": "2023-09-30",
            "reportedCurrency": "USD",
            "operatingCashflow": "110543000000",
            "capitalExpenditures": "11000000000",
        },
    ],
}


# ── Initialization tests ────────────────────────────────────────────────


class TestAlphaVantageInit:

    def test_init_source_name(self):
        dl = _make_downloader(keys=["key1"])
        assert dl.source_name == "alphavantage_fundamentals"

    def test_init_loads_keys(self):
        dl = _make_downloader(keys=["k1", "k2", "k3"])
        assert dl._api_keys == ["k1", "k2", "k3"]

    def test_init_default_api_delay(self):
        with patch.dict("os.environ", {}, clear=True):
            dl = AlphaVantageFundamentalsDownloader()
        assert dl.api_delay == 3.1

    def test_init_custom_params(self):
        dl = _make_downloader(keys=["k1"])
        assert dl.max_retries == 2
        assert dl.api_delay == 0

    def test_init_no_keys_warns(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("modules.input.alphavantage_downloader.pipeline_logger") as mock_log:
                dl = AlphaVantageFundamentalsDownloader()
            mock_log.warning.assert_called()
        assert dl._api_keys == []

    def test_init_reads_keys_from_env(self):
        AlphaVantageFundamentalsDownloader._current_key_idx = 0
        AlphaVantageFundamentalsDownloader._exhausted_keys = set()
        env = {"ALPHA_VANTAGE_KEY_1": "aaa", "ALPHA_VANTAGE_KEY_2": "bbb"}
        with patch.dict("os.environ", env, clear=True):
            dl = AlphaVantageFundamentalsDownloader(api_delay=0)
        assert dl._api_keys == ["aaa", "bbb"]

    def test_init_skips_empty_env_keys(self):
        AlphaVantageFundamentalsDownloader._current_key_idx = 0
        AlphaVantageFundamentalsDownloader._exhausted_keys = set()
        env = {"ALPHA_VANTAGE_KEY_1": "aaa", "ALPHA_VANTAGE_KEY_2": "  ", "ALPHA_VANTAGE_KEY_3": "ccc"}
        with patch.dict("os.environ", env, clear=True):
            dl = AlphaVantageFundamentalsDownloader(api_delay=0)
        assert dl._api_keys == ["aaa", "ccc"]

    def test_stats_initial_values(self):
        dl = _make_downloader(keys=["k1"])
        s = dl.stats
        assert s["downloads"] == 0
        assert s["successes"] == 0
        assert s["failures"] == 0


# ── Symbol conversion tests ─────────────────────────────────────────────


class TestConvertToAvSymbol:

    def test_us_ticker_unchanged(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("AAPL") == "AAPL"

    def test_london_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("VOD.L") == "VOD.LON"

    def test_paris_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("TTE.PA") == "TTE.PAR"

    def test_germany_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("SAP.DE") == "SAP.DEU"

    def test_amsterdam_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("ASML.AS") == "ASML.AMS"

    def test_toronto_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("RY.TO") == "RY.TRT"

    def test_swiss_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("NESN.SW") == "NESN.SWX"

    def test_milan_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("UCG.MI") == "UCG.MIL"

    def test_madrid_suffix(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("SAN.MC") == "SAN.MCE"

    def test_strips_whitespace(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("  VOD.L  ") == "VOD.LON"

    def test_unknown_suffix_passed_through(self):
        assert AlphaVantageFundamentalsDownloader._convert_to_av_symbol("XYZ.HK") == "XYZ.HK"

    def test_all_mappings_covered(self):
        for yf_suffix, av_suffix in YF_TO_AV_SUFFIX.items():
            ticker = f"TEST{yf_suffix}"
            result = AlphaVantageFundamentalsDownloader._convert_to_av_symbol(ticker)
            assert result == f"TEST{av_suffix}"


# ── Key rotation tests ──────────────────────────────────────────────────


class TestKeyRotation:

    def test_get_current_key_returns_first(self):
        dl = _make_downloader(keys=["k1", "k2", "k3"])
        key, idx = dl._get_current_key()
        assert key == "k1"
        assert idx == 0

    def test_get_current_key_no_keys(self):
        dl = _make_downloader(keys=[])
        key, idx = dl._get_current_key()
        assert key is None
        assert idx == -1

    def test_mark_key_exhausted_advances(self):
        dl = _make_downloader(keys=["k1", "k2", "k3"])
        dl._mark_key_exhausted(0)
        key, idx = dl._get_current_key()
        assert key == "k2"
        assert idx == 1

    def test_mark_key_exhausted_twice_advances_to_third(self):
        dl = _make_downloader(keys=["k1", "k2", "k3"])
        dl._mark_key_exhausted(0)
        dl._mark_key_exhausted(1)
        key, idx = dl._get_current_key()
        assert key == "k3"
        assert idx == 2

    def test_all_keys_exhausted_returns_none(self):
        dl = _make_downloader(keys=["k1", "k2"])
        dl._mark_key_exhausted(0)
        dl._mark_key_exhausted(1)
        key, idx = dl._get_current_key()
        assert key is None
        assert idx == -1

    def test_mark_wrong_index_does_not_advance(self):
        dl = _make_downloader(keys=["k1", "k2"])
        # Caller thinks key 1 is active, but key 0 is still current
        dl._mark_key_exhausted(1)
        key, idx = dl._get_current_key()
        assert key == "k1"
        assert idx == 0


# ── _fetch_json tests ───────────────────────────────────────────────────


class TestFetchJson:

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_success(self, mock_urlopen):
        dl = _make_downloader(keys=["mykey"])
        payload = {"quarterlyReports": [{"fiscalDateEnding": "2024-03-31"}]}
        mock_urlopen.return_value = _mock_urlopen_response(payload)

        result = dl._fetch_json("INCOME_STATEMENT", "AAPL")
        assert result == payload

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_constructs_correct_url(self, mock_urlopen):
        dl = _make_downloader(keys=["testkey123"])
        mock_urlopen.return_value = _mock_urlopen_response({"data": []})

        dl._fetch_json("BALANCE_SHEET", "VOD.LON")
        req = mock_urlopen.call_args[0][0]
        assert "function=BALANCE_SHEET" in req.full_url
        assert "symbol=VOD.LON" in req.full_url
        assert "apikey=testkey123" in req.full_url

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_no_keys_returns_none(self, mock_urlopen):
        dl = _make_downloader(keys=[])
        result = dl._fetch_json("INCOME_STATEMENT", "AAPL")
        assert result is None
        mock_urlopen.assert_not_called()

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_error_message_returns_none(self, mock_urlopen):
        dl = _make_downloader(keys=["k1"])
        mock_urlopen.return_value = _mock_urlopen_response(
            {"Error Message": "Invalid API call"}
        )
        result = dl._fetch_json("INCOME_STATEMENT", "BADSYMBOL")
        assert result is None

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_rate_limit_marks_key_exhausted(self, mock_urlopen):
        dl = _make_downloader(keys=["k1", "k2"])
        rate_limit_resp = _mock_urlopen_response(
            {"Note": "API call frequency exceeded. Please wait..."}
        )
        success_resp = _mock_urlopen_response({"quarterlyReports": []})
        mock_urlopen.side_effect = [rate_limit_resp, success_resp]

        result = dl._fetch_json("INCOME_STATEMENT", "AAPL")
        # Should have retried with k2 and succeeded
        assert result == {"quarterlyReports": []}
        assert 0 in AlphaVantageFundamentalsDownloader._exhausted_keys

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_rate_limit_all_keys_exhausted(self, mock_urlopen):
        dl = _make_downloader(keys=["k1"])
        mock_urlopen.return_value = _mock_urlopen_response(
            {"Note": "API call frequency exceeded"}
        )
        result = dl._fetch_json("INCOME_STATEMENT", "AAPL")
        assert result is None

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_rate_limit_retry_also_limited(self, mock_urlopen):
        dl = _make_downloader(keys=["k1", "k2"])
        rate_resp = _mock_urlopen_response({"Note": "rate limited"})
        mock_urlopen.return_value = rate_resp

        result = dl._fetch_json("INCOME_STATEMENT", "AAPL")
        assert result is None
        # Both keys should now be exhausted
        assert 0 in AlphaVantageFundamentalsDownloader._exhausted_keys
        assert 1 in AlphaVantageFundamentalsDownloader._exhausted_keys

    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_fetch_json_sets_user_agent(self, mock_urlopen):
        dl = _make_downloader(keys=["k1"])
        mock_urlopen.return_value = _mock_urlopen_response({"data": []})

        dl._fetch_json("INCOME_STATEMENT", "AAPL")
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("User-agent") == "SystematicEquityPipeline/1.0"


# ── Record extraction tests ─────────────────────────────────────────────


class TestExtractRecordsFromReports:

    def test_income_statement_fields(self):
        reports = SAMPLE_INCOME_RESPONSE["quarterlyReports"]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        field_names = {r["field_name"] for r in records}
        assert "total_revenue" in field_names
        assert "net_income" in field_names
        assert "gross_profit" in field_names
        assert "operating_income" in field_names
        assert "ebitda" in field_names
        assert "basic_eps" in field_names

    def test_balance_sheet_fields(self):
        reports = SAMPLE_BALANCE_RESPONSE["quarterlyReports"]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, BALANCE_FIELD_MAP, "AAPL", "quarterly"
        )
        field_names = {r["field_name"] for r in records}
        assert "stockholders_equity" in field_names
        assert "total_assets" in field_names
        assert "total_liabilities" in field_names
        assert "total_debt" in field_names

    def test_cashflow_fields(self):
        reports = SAMPLE_CASHFLOW_RESPONSE["quarterlyReports"]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, CASHFLOW_FIELD_MAP, "AAPL", "quarterly"
        )
        field_names = {r["field_name"] for r in records}
        assert "operating_cash_flow" in field_names
        assert "capital_expenditure" in field_names

    def test_record_has_all_eav_fields(self):
        reports = SAMPLE_INCOME_RESPONSE["quarterlyReports"][:1]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        for rec in records:
            assert "symbol" in rec
            assert "report_date" in rec
            assert "field_name" in rec
            assert "field_value" in rec
            assert "period_type" in rec
            assert "currency" in rec

    def test_symbol_and_period_type_set_correctly(self):
        reports = SAMPLE_INCOME_RESPONSE["annualReports"]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "MY_SYMBOL", "annual"
        )
        for rec in records:
            assert rec["symbol"] == "MY_SYMBOL"
            assert rec["period_type"] == "annual"

    def test_currency_from_report(self):
        reports = [
            {
                "fiscalDateEnding": "2024-06-30",
                "reportedCurrency": "GBP",
                "totalRevenue": "5000000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "VOD.L", "quarterly"
        )
        assert records[0]["currency"] == "GBP"

    def test_currency_defaults_to_usd(self):
        reports = [
            {
                "fiscalDateEnding": "2024-06-30",
                "totalRevenue": "1000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert records[0]["currency"] == "USD"

    def test_skips_none_string_values(self):
        reports = [
            {
                "fiscalDateEnding": "2024-06-30",
                "reportedCurrency": "USD",
                "totalRevenue": "None",
                "netIncome": "500000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        field_names = [r["field_name"] for r in records]
        assert "total_revenue" not in field_names
        assert "net_income" in field_names

    def test_skips_none_python_values(self):
        reports = [
            {
                "fiscalDateEnding": "2024-06-30",
                "reportedCurrency": "USD",
                "totalRevenue": None,
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_skips_non_numeric_values(self):
        reports = [
            {
                "fiscalDateEnding": "2024-06-30",
                "reportedCurrency": "USD",
                "totalRevenue": "N/A",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_skips_report_without_fiscal_date(self):
        reports = [
            {
                "reportedCurrency": "USD",
                "totalRevenue": "1000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_skips_report_with_bad_date(self):
        reports = [
            {
                "fiscalDateEnding": "not-a-date",
                "reportedCurrency": "USD",
                "totalRevenue": "1000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_report_date_parsed_correctly(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalRevenue": "1000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert records[0]["report_date"] == date(2024, 3, 31)

    def test_field_value_is_float(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalRevenue": "94836000000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            reports, INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert records[0]["field_value"] == 94836000000.0
        assert isinstance(records[0]["field_value"], float)

    def test_empty_reports_list(self):
        records = AlphaVantageFundamentalsDownloader._extract_records_from_reports(
            [], INCOME_FIELD_MAP, "AAPL", "quarterly"
        )
        assert records == []


# ── Derived field: total_debt fallback ──────────────────────────────────


class TestBalanceSheetExtras:

    def test_total_debt_fallback_from_components(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "shortLongTermDebtTotal": "None",
                "shortTermDebt": "10000000",
                "longTermDebt": "90000000",
                "totalShareholderEquity": "None",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        debt_recs = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt_recs) == 1
        assert debt_recs[0]["field_value"] == 100000000.0

    def test_total_debt_fallback_only_short_term(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "shortLongTermDebtTotal": "None",
                "shortTermDebt": "5000000",
                "longTermDebt": "None",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        debt_recs = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt_recs) == 1
        assert debt_recs[0]["field_value"] == 5000000.0

    def test_total_debt_fallback_only_long_term(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "shortLongTermDebtTotal": None,
                "shortTermDebt": None,
                "longTermDebt": "80000000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        debt_recs = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt_recs) == 1
        assert debt_recs[0]["field_value"] == 80000000.0

    def test_no_fallback_when_direct_debt_present(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "shortLongTermDebtTotal": "100000000",
                "shortTermDebt": "10000000",
                "longTermDebt": "90000000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        debt_recs = [r for r in records if r["field_name"] == "total_debt"]
        # Direct debt present - no fallback record produced (that comes from the main field map)
        assert len(debt_recs) == 0

    def test_no_debt_components_no_record(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "shortLongTermDebtTotal": "None",
                "shortTermDebt": "None",
                "longTermDebt": "None",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        debt_recs = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt_recs) == 0


# ── Derived field: book_value ────────────────────────────────────────────


class TestBookValue:

    def test_book_value_computed(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalShareholderEquity": "74100000000",
                "commonStockSharesOutstanding": "15441900000",
                "shortLongTermDebtTotal": "100000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        bv_recs = [r for r in records if r["field_name"] == "book_value"]
        assert len(bv_recs) == 1
        expected = 74100000000.0 / 15441900000.0
        assert abs(bv_recs[0]["field_value"] - expected) < 0.001

    def test_book_value_zero_shares_skipped(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalShareholderEquity": "74100000000",
                "commonStockSharesOutstanding": "0",
                "shortLongTermDebtTotal": "100000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        bv_recs = [r for r in records if r["field_name"] == "book_value"]
        assert len(bv_recs) == 0

    def test_book_value_missing_equity_skipped(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "totalShareholderEquity": "None",
                "commonStockSharesOutstanding": "15000000",
                "shortLongTermDebtTotal": "100000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_balance_sheet_extras(
            reports, "AAPL", "quarterly"
        )
        bv_recs = [r for r in records if r["field_name"] == "book_value"]
        assert len(bv_recs) == 0


# ── Derived field: diluted_eps ───────────────────────────────────────────


class TestDilutedEps:

    def test_diluted_eps_extracted(self):
        reports = SAMPLE_INCOME_RESPONSE["quarterlyReports"]
        records = AlphaVantageFundamentalsDownloader._extract_diluted_eps(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 2
        for rec in records:
            assert rec["field_name"] == "diluted_eps"
            assert isinstance(rec["field_value"], float)

    def test_diluted_eps_correct_values(self):
        reports = SAMPLE_INCOME_RESPONSE["quarterlyReports"]
        records = AlphaVantageFundamentalsDownloader._extract_diluted_eps(
            reports, "AAPL", "quarterly"
        )
        values = [r["field_value"] for r in records]
        assert 1.53 in values
        assert 2.18 in values

    def test_diluted_eps_skips_none(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "eps": "None",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_diluted_eps(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_diluted_eps_skips_missing(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._extract_diluted_eps(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 0


# ── Derived field: free_cash_flow ────────────────────────────────────────


class TestFreeCashFlow:

    def test_free_cash_flow_computed(self):
        reports = SAMPLE_CASHFLOW_RESPONSE["quarterlyReports"]
        records = AlphaVantageFundamentalsDownloader._compute_free_cash_flow(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 1
        # free_cash_flow = operatingCashflow - abs(capitalExpenditures)
        expected = 26390000000.0 - abs(2160000000.0)
        assert records[0]["field_value"] == expected
        assert records[0]["field_name"] == "free_cash_flow"

    def test_free_cash_flow_negative_capex(self):
        """Capital expenditure reported as negative should be abs()-ed."""
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingCashflow": "10000",
                "capitalExpenditures": "-3000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._compute_free_cash_flow(
            reports, "AAPL", "quarterly"
        )
        assert records[0]["field_value"] == 10000.0 - 3000.0

    def test_free_cash_flow_missing_capex_skipped(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingCashflow": "10000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._compute_free_cash_flow(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_free_cash_flow_missing_ocf_skipped(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "capitalExpenditures": "3000",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._compute_free_cash_flow(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 0

    def test_free_cash_flow_none_values_skipped(self):
        reports = [
            {
                "fiscalDateEnding": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingCashflow": "None",
                "capitalExpenditures": "None",
            }
        ]
        records = AlphaVantageFundamentalsDownloader._compute_free_cash_flow(
            reports, "AAPL", "quarterly"
        )
        assert len(records) == 0


# ── Deduplication tests ──────────────────────────────────────────────────


class TestDeduplication:

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_deduplication_removes_duplicate_records(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)

        # Both income endpoints return same quarterly report (will create
        # the same basic_eps + diluted_eps records)
        income_data = {
            "quarterlyReports": [
                {
                    "fiscalDateEnding": "2024-03-31",
                    "reportedCurrency": "USD",
                    "totalRevenue": "1000",
                    "eps": "1.5",
                },
            ],
            "annualReports": [],
        }
        balance_data = {"quarterlyReports": [], "annualReports": []}
        cashflow_data = {"quarterlyReports": [], "annualReports": []}

        responses = [
            _mock_urlopen_response(income_data),
            _mock_urlopen_response(balance_data),
            _mock_urlopen_response(cashflow_data),
        ]
        mock_urlopen.side_effect = responses

        result = dl.download("AAPL", "AAPL")
        # basic_eps and diluted_eps both come from eps field for same date+period
        # They should both appear since they have different field_names
        field_names = [r["field_name"] for r in result]
        # No duplicate (field_name, report_date, period_type) combos
        seen = set()
        for r in result:
            key = (r["field_name"], r["report_date"], r["period_type"])
            assert key not in seen, f"Duplicate found: {key}"
            seen.add(key)


# ── Full download() orchestration tests ──────────────────────────────────


class TestDownloadOrchestration:

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_returns_records_for_all_statements(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)

        responses = [
            _mock_urlopen_response(SAMPLE_INCOME_RESPONSE),
            _mock_urlopen_response(SAMPLE_BALANCE_RESPONSE),
            _mock_urlopen_response(SAMPLE_CASHFLOW_RESPONSE),
        ]
        mock_urlopen.side_effect = responses

        result = dl.download("AAPL", "AAPL")
        assert result is not None
        assert len(result) > 0

        field_names = {r["field_name"] for r in result}
        # Income fields
        assert "total_revenue" in field_names
        assert "net_income" in field_names
        assert "basic_eps" in field_names
        assert "diluted_eps" in field_names
        # Balance fields
        assert "total_assets" in field_names
        assert "stockholders_equity" in field_names
        assert "book_value" in field_names
        # Cash flow fields
        assert "operating_cash_flow" in field_names
        assert "free_cash_flow" in field_names

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_with_non_us_ticker_converts_symbol(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)

        empty_resp = {"quarterlyReports": [], "annualReports": []}
        mock_urlopen.return_value = _mock_urlopen_response(empty_resp)

        dl.download("VOD.L", "VOD.L")

        # Check that the constructed URL used the AV-format symbol
        calls = mock_urlopen.call_args_list
        for call in calls:
            req = call[0][0]
            assert "VOD.LON" in req.full_url

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_no_keys_returns_empty(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=[], api_delay=0)
        result = dl.download("AAPL", "AAPL")
        assert result == []
        assert dl._failure_count == 1
        mock_urlopen.assert_not_called()

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_increments_download_count(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)
        empty_resp = {"quarterlyReports": [], "annualReports": []}
        mock_urlopen.return_value = _mock_urlopen_response(empty_resp)

        dl.download("AAPL", "AAPL")
        assert dl._download_count == 1

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_success_increments_success_count(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)

        responses = [
            _mock_urlopen_response(SAMPLE_INCOME_RESPONSE),
            _mock_urlopen_response(SAMPLE_BALANCE_RESPONSE),
            _mock_urlopen_response(SAMPLE_CASHFLOW_RESPONSE),
        ]
        mock_urlopen.side_effect = responses

        dl.download("AAPL", "AAPL")
        assert dl._success_count == 1

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_empty_results_increments_failure(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)
        empty_resp = {"quarterlyReports": [], "annualReports": []}
        mock_urlopen.return_value = _mock_urlopen_response(empty_resp)

        dl.download("AAPL", "AAPL")
        assert dl._failure_count == 1

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_http_error_retries(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=2)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.HTTPError("url", 500, "Server Error", {}, None)
            return _mock_urlopen_response(SAMPLE_INCOME_RESPONSE)

        mock_urlopen.side_effect = side_effect
        # Will retry income, then succeed on second attempt, then balance + cashflow
        # We just verify it doesn't crash
        dl.download("AAPL", "AAPL")

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_network_error_retries(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=2)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise ConnectionError("Network unreachable")
            return _mock_urlopen_response(
                {"quarterlyReports": [], "annualReports": []}
            )

        mock_urlopen.side_effect = side_effect
        result = dl.download("AAPL", "AAPL")
        # Should not crash; retried after the network error
        assert result is not None

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_circuit_open_returns_empty(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0)
        # Force circuit breaker open
        import time as _t
        dl.circuit_breaker._state = 1  # OPEN
        dl.circuit_breaker._opened_at = _t.time()  # just opened

        result = dl.download("AAPL", "AAPL")
        assert result == []
        assert dl._failure_count == 1
        mock_urlopen.assert_not_called()

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_includes_both_quarterly_and_annual(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)

        responses = [
            _mock_urlopen_response(SAMPLE_INCOME_RESPONSE),
            _mock_urlopen_response(SAMPLE_BALANCE_RESPONSE),
            _mock_urlopen_response(SAMPLE_CASHFLOW_RESPONSE),
        ]
        mock_urlopen.side_effect = responses

        result = dl.download("AAPL", "AAPL")
        period_types = {r["period_type"] for r in result}
        assert "quarterly" in period_types
        assert "annual" in period_types

    @patch("modules.input.alphavantage_downloader.time.sleep")
    @patch("modules.input.alphavantage_downloader.urllib.request.urlopen")
    def test_download_makes_three_api_calls(self, mock_urlopen, mock_sleep):
        dl = _make_downloader(keys=["k1"], api_delay=0, max_retries=1)
        empty_resp = {"quarterlyReports": [], "annualReports": []}
        mock_urlopen.return_value = _mock_urlopen_response(empty_resp)

        dl.download("AAPL", "AAPL")
        assert mock_urlopen.call_count == 3

        # Verify the three endpoints
        functions_called = []
        for call in mock_urlopen.call_args_list:
            req = call[0][0]
            url = req.full_url
            for fn in ["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"]:
                if fn in url:
                    functions_called.append(fn)
        assert "INCOME_STATEMENT" in functions_called
        assert "BALANCE_SHEET" in functions_called
        assert "CASH_FLOW" in functions_called


# ── _execute_download raises ─────────────────────────────────────────────


class TestExecuteDownload:

    def test_execute_download_raises_not_implemented(self):
        dl = _make_downloader(keys=["k1"])
        with pytest.raises(NotImplementedError):
            dl._execute_download()
