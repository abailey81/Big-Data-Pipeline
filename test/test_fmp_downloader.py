"""
Tests for FMP fundamentals downloader.

Covers:
  - modules.input.fmp_downloader._fmp_ticker helper function
  - modules.input.fmp_downloader.FmpFundamentalsDownloader
  - modules.input.fmp_downloader._extract_fmp_records
  - Field mapping constants (INCOME, BALANCE, CASHFLOW)
  - Swiss ticker suffix variants
  - Error handling (network errors, empty responses, invalid JSON)
"""

import time as _time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from modules.input.fmp_downloader import (
    BALANCE_FIELD_MAP,
    CASHFLOW_FIELD_MAP,
    FMP_BASE,
    INCOME_FIELD_MAP,
    FmpFundamentalsDownloader,
    _extract_fmp_records,
    _fmp_ticker,
    _SWISS_SUFFIX_VARIANTS,
)

# ── Fixtures: realistic FMP API response payloads ─────────────────────


SAMPLE_INCOME = [
    {
        "date": "2024-03-31",
        "symbol": "NESN.SW",
        "reportedCurrency": "CHF",
        "revenue": 23000000000,
        "netIncome": 3500000000,
        "grossProfit": 11000000000,
        "operatingIncome": 5000000000,
        "ebitda": 7000000000,
        "eps": 4.25,
        "epsdiluted": 4.20,
    },
    {
        "date": "2023-12-31",
        "symbol": "NESN.SW",
        "reportedCurrency": "CHF",
        "revenue": 22000000000,
        "netIncome": 3200000000,
        "grossProfit": 10500000000,
        "operatingIncome": 4800000000,
        "ebitda": 6800000000,
        "eps": 4.10,
        "epsdiluted": 4.05,
    },
]

SAMPLE_BALANCE = [
    {
        "date": "2024-03-31",
        "symbol": "NESN.SW",
        "reportedCurrency": "CHF",
        "totalStockholdersEquity": 35000000000,
        "totalDebt": 28000000000,
        "totalAssets": 120000000000,
        "totalLiabilities": 85000000000,
        "bookValuePerShare": 12.50,
    },
]

SAMPLE_CASHFLOW = [
    {
        "date": "2024-03-31",
        "symbol": "NESN.SW",
        "reportedCurrency": "CHF",
        "operatingCashFlow": 8000000000,
        "capitalExpenditure": -2000000000,
        "freeCashFlow": 6000000000,
        "depreciationAndAmortization": 2000000000,
    },
]


# ── _fmp_ticker helper function tests ─────────────────────────────────


class TestFmpTicker:

    def test_us_ticker_returns_single_candidate(self):
        assert _fmp_ticker("AAPL") == ["AAPL"]

    def test_london_ticker_returns_single_candidate(self):
        assert _fmp_ticker("HSBA.L") == ["HSBA.L"]

    def test_paris_ticker_returns_single_candidate(self):
        assert _fmp_ticker("TTE.PA") == ["TTE.PA"]

    def test_swiss_sw_returns_multiple_candidates(self):
        candidates = _fmp_ticker("NESN.SW")
        assert len(candidates) == len(_SWISS_SUFFIX_VARIANTS)
        assert "NESN.SW" in candidates
        assert "NESN.ZU" in candidates
        assert "NESN.S" in candidates

    def test_swiss_s_returns_multiple_candidates(self):
        candidates = _fmp_ticker("UBSG.S")
        # .S is a Swiss suffix, so we get variants
        assert "UBSG.SW" in candidates
        assert "UBSG.ZU" in candidates
        assert "UBSG.S" in candidates

    def test_strips_whitespace(self):
        assert _fmp_ticker("  AAPL  ") == ["AAPL"]

    def test_swiss_with_whitespace(self):
        candidates = _fmp_ticker("  NESN.SW  ")
        assert "NESN.SW" in candidates
        assert "NESN.ZU" in candidates

    def test_empty_string(self):
        assert _fmp_ticker("") == [""]

    def test_german_ticker_single_candidate(self):
        assert _fmp_ticker("SIE.DE") == ["SIE.DE"]

    def test_case_insensitive_swiss_suffix(self):
        # Upper-case comparison should still match .sw
        candidates = _fmp_ticker("NESN.sw")
        assert len(candidates) >= 3


# ── FmpFundamentalsDownloader initialisation ──────────────────────────


class TestFmpDownloaderInit:

    def test_init_sets_api_key(self):
        dl = FmpFundamentalsDownloader(api_key="test_key_123")
        assert dl.api_key == "test_key_123"

    def test_init_reads_env_key(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "env_key_abc"}):
            dl = FmpFundamentalsDownloader()
            assert dl.api_key == "env_key_abc"

    def test_init_explicit_key_overrides_env(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "env_key"}):
            dl = FmpFundamentalsDownloader(api_key="explicit_key")
            assert dl.api_key == "explicit_key"

    def test_init_source_name(self):
        dl = FmpFundamentalsDownloader(api_key="k")
        assert dl.source_name == "fmp_fundamentals"

    def test_init_default_retries(self):
        dl = FmpFundamentalsDownloader(api_key="k")
        assert dl.max_retries == 3

    def test_init_custom_retries(self):
        dl = FmpFundamentalsDownloader(api_key="k", max_retries=5)
        assert dl.max_retries == 5

    def test_stats_initial(self):
        dl = FmpFundamentalsDownloader(api_key="k")
        s = dl.stats
        assert s["downloads"] == 0
        assert s["successes"] == 0
        assert s["failures"] == 0

    def test_session_headers(self):
        dl = FmpFundamentalsDownloader(api_key="k")
        assert dl._session.headers.get("Accept") == "application/json"


# ── _fetch_statement tests ────────────────────────────────────────────


class TestFetchStatement:

    def _make_dl(self, api_key="test_key"):
        return FmpFundamentalsDownloader(api_key=api_key)

    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_fetch_returns_list_on_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_INCOME
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        dl = self._make_dl()
        result = dl._fetch_statement("income-statement", "NESN.SW", "quarter")

        assert result == SAMPLE_INCOME
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "apikey" in call_kwargs[1]["params"]

    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_fetch_returns_none_on_error_dict(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Error Message": "Invalid API KEY"}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        dl = self._make_dl()
        result = dl._fetch_statement("income-statement", "INVALID", "quarter")

        assert result is None

    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_fetch_returns_none_on_unexpected_type(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = "unexpected string"
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        dl = self._make_dl()
        result = dl._fetch_statement("income-statement", "NESN.SW", "quarter")

        assert result is None

    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_fetch_raises_on_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=500)
        )
        mock_get.return_value = mock_resp

        dl = self._make_dl()
        with pytest.raises(requests.exceptions.HTTPError):
            dl._fetch_statement("income-statement", "NESN.SW", "quarter")

    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_fetch_empty_list_returned_as_is(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        dl = self._make_dl()
        result = dl._fetch_statement("income-statement", "NESN.SW", "quarter")

        assert result == []

    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_fetch_url_construction(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        dl = self._make_dl(api_key="mykey")
        dl._fetch_statement("balance-sheet-statement", "HSBA.L", "annual", limit=20)

        url_arg = mock_get.call_args[0][0]
        assert url_arg == f"{FMP_BASE}/balance-sheet-statement/HSBA.L"
        params = mock_get.call_args[1]["params"]
        assert params["period"] == "annual"
        assert params["limit"] == 20
        assert params["apikey"] == "mykey"


# ── _extract_fmp_records tests ────────────────────────────────────────


class TestExtractFmpRecords:

    def test_extracts_income_fields(self):
        stmts = {"income": SAMPLE_INCOME, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "NESN.SW", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "total_revenue" in field_names
        assert "net_income" in field_names
        assert "gross_profit" in field_names
        assert "operating_income" in field_names
        assert "basic_eps" in field_names
        assert "diluted_eps" in field_names

    def test_extracts_balance_fields(self):
        stmts = {"income": [], "balance": SAMPLE_BALANCE, "cashflow": []}
        records = _extract_fmp_records(stmts, "NESN.SW", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "stockholders_equity" in field_names
        assert "total_debt" in field_names
        assert "total_assets" in field_names
        assert "total_liabilities" in field_names
        assert "book_value" in field_names

    def test_extracts_cashflow_fields(self):
        stmts = {"income": [], "balance": [], "cashflow": SAMPLE_CASHFLOW}
        records = _extract_fmp_records(stmts, "NESN.SW", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "operating_cash_flow" in field_names
        assert "capital_expenditure" in field_names
        assert "free_cash_flow" in field_names

    def test_depreciation_removed_from_output(self):
        stmts = {"income": [], "balance": [], "cashflow": SAMPLE_CASHFLOW}
        records = _extract_fmp_records(stmts, "NESN.SW", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "_depreciation" not in field_names

    def test_symbol_set_correctly(self):
        stmts = {"income": SAMPLE_INCOME[:1], "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "HSBA.L", "quarterly")
        for r in records:
            assert r["symbol"] == "HSBA.L"

    def test_currency_from_reported_currency(self):
        stmts = {"income": SAMPLE_INCOME[:1], "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "NESN.SW", "quarterly")
        for r in records:
            assert r["currency"] == "CHF"

    def test_currency_defaults_to_usd(self):
        income = [{"date": "2024-03-31", "revenue": 100}]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "AAPL", "quarterly")
        for r in records:
            assert r["currency"] == "USD"

    def test_period_type_set_correctly(self):
        stmts = {"income": SAMPLE_INCOME[:1], "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "NESN.SW", "annual")
        for r in records:
            assert r["period_type"] == "annual"

    def test_report_date_parsed_correctly(self):
        stmts = {"income": SAMPLE_INCOME[:1], "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "NESN.SW", "quarterly")
        dates = {r["report_date"] for r in records}
        assert date(2024, 3, 31) in dates

    def test_empty_statements_returns_empty(self):
        assert _extract_fmp_records({}, "AAPL", "quarterly") == []
        assert _extract_fmp_records(None, "AAPL", "quarterly") == []

    def test_skips_none_values(self):
        income = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "revenue": None,
                "netIncome": 500,
            }
        ]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "AAPL", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "total_revenue" not in field_names
        assert "net_income" in field_names

    def test_skips_invalid_date(self):
        income = [
            {"date": "not-a-date", "reportedCurrency": "USD", "revenue": 100}
        ]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "AAPL", "quarterly")
        assert len(records) == 0

    def test_skips_missing_date(self):
        income = [{"reportedCurrency": "USD", "revenue": 100}]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "AAPL", "quarterly")
        assert len(records) == 0

    def test_uses_filing_date_as_fallback(self):
        income = [
            {"filingDate": "2024-06-15", "reportedCurrency": "USD", "revenue": 100}
        ]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "AAPL", "quarterly")
        assert len(records) == 1
        assert records[0]["report_date"] == date(2024, 6, 15)

    def test_start_date_filter(self):
        stmts = {"income": SAMPLE_INCOME, "balance": [], "cashflow": []}
        records = _extract_fmp_records(
            stmts, "NESN.SW", "quarterly", start_date="2024-01-01"
        )
        for r in records:
            assert r["report_date"] >= date(2024, 1, 1)

    def test_deduplicates_by_field_date_period(self):
        # Two income items with the same date — second should be deduped
        income = [
            {"date": "2024-03-31", "reportedCurrency": "CHF", "revenue": 100},
            {"date": "2024-03-31", "reportedCurrency": "CHF", "revenue": 200},
        ]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "TEST", "quarterly")
        revenue_recs = [r for r in records if r["field_name"] == "total_revenue"]
        assert len(revenue_recs) == 1
        assert revenue_recs[0]["field_value"] == 100.0  # first wins

    def test_non_numeric_value_skipped(self):
        income = [
            {"date": "2024-03-31", "reportedCurrency": "USD", "revenue": "N/A"}
        ]
        stmts = {"income": income, "balance": [], "cashflow": []}
        records = _extract_fmp_records(stmts, "TEST", "quarterly")
        assert len(records) == 0


# ── Post-processing: computed EBITDA and FCF ──────────────────────────


class TestExtractFmpRecordsPostProcessing:

    def test_computes_ebitda_when_missing(self):
        # Provide operating_income and depreciation but NO ebitda in income
        income = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingIncome": 5000,
            }
        ]
        cashflow = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "depreciationAndAmortization": 1500,
            }
        ]
        stmts = {"income": income, "balance": [], "cashflow": cashflow}
        records = _extract_fmp_records(stmts, "TEST", "quarterly")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda_recs) == 1
        assert ebitda_recs[0]["field_value"] == 6500.0  # 5000 + abs(1500)

    def test_does_not_overwrite_existing_ebitda(self):
        income = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingIncome": 5000,
                "ebitda": 7777,
            }
        ]
        cashflow = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "depreciationAndAmortization": 1500,
            }
        ]
        stmts = {"income": income, "balance": [], "cashflow": cashflow}
        records = _extract_fmp_records(stmts, "TEST", "quarterly")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda_recs) == 1
        assert ebitda_recs[0]["field_value"] == 7777.0

    def test_computes_fcf_when_missing(self):
        cashflow = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingCashFlow": 8000,
                "capitalExpenditure": -2000,
            }
        ]
        stmts = {"income": [], "balance": [], "cashflow": cashflow}
        records = _extract_fmp_records(stmts, "TEST", "quarterly")
        fcf_recs = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf_recs) == 1
        assert fcf_recs[0]["field_value"] == 6000.0  # 8000 - abs(-2000)

    def test_does_not_overwrite_existing_fcf(self):
        cashflow = [
            {
                "date": "2024-03-31",
                "reportedCurrency": "USD",
                "operatingCashFlow": 8000,
                "capitalExpenditure": -2000,
                "freeCashFlow": 5555,
            }
        ]
        stmts = {"income": [], "balance": [], "cashflow": cashflow}
        records = _extract_fmp_records(stmts, "TEST", "quarterly")
        fcf_recs = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf_recs) == 1
        assert fcf_recs[0]["field_value"] == 5555.0


# ── Field mapping constants ───────────────────────────────────────────


class TestFieldMappings:

    def test_income_field_map_completeness(self):
        expected_fmp = {"revenue", "netIncome", "grossProfit", "operatingIncome",
                        "ebitda", "eps", "epsdiluted"}
        assert set(INCOME_FIELD_MAP.keys()) == expected_fmp

    def test_balance_field_map_completeness(self):
        expected_fmp = {"totalStockholdersEquity", "totalDebt", "totalAssets",
                        "totalLiabilities", "bookValuePerShare"}
        assert set(BALANCE_FIELD_MAP.keys()) == expected_fmp

    def test_cashflow_field_map_completeness(self):
        expected_fmp = {"operatingCashFlow", "capitalExpenditure",
                        "freeCashFlow", "depreciationAndAmortization"}
        assert set(CASHFLOW_FIELD_MAP.keys()) == expected_fmp

    def test_canonical_names_unique(self):
        all_canonical = (
            list(INCOME_FIELD_MAP.values())
            + list(BALANCE_FIELD_MAP.values())
            + list(CASHFLOW_FIELD_MAP.values())
        )
        assert len(all_canonical) == len(set(all_canonical))


# ── FmpFundamentalsDownloader.download() integration ──────────────────


class TestFmpDownloaderDownload:

    def _make_dl(self, api_key="test_key"):
        return FmpFundamentalsDownloader(
            api_key=api_key, api_delay=0, max_retries=2
        )

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_download_full_flow(self, mock_get, mock_sleep):
        """End-to-end: download returns extracted records for a non-Swiss ticker."""
        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "income-statement" in url:
                resp.json.return_value = SAMPLE_INCOME[:1]
            elif "balance-sheet" in url:
                resp.json.return_value = SAMPLE_BALANCE
            elif "cash-flow" in url:
                resp.json.return_value = SAMPLE_CASHFLOW
            else:
                resp.json.return_value = []
            return resp

        mock_get.side_effect = side_effect

        dl = self._make_dl()
        records = dl.download("HSBA.L")

        assert len(records) > 0
        assert dl._success_count == 1
        assert all(r["symbol"] == "HSBA.L" for r in records)

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_download_swiss_tries_variants(self, mock_get, mock_sleep):
        """Swiss tickers cycle through suffix variants until data is found."""
        call_urls = []

        def side_effect(url, **kwargs):
            call_urls.append(url)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            # Return data only for .ZU variant
            if ".ZU" in url and "income-statement" in url:
                resp.json.return_value = SAMPLE_INCOME[:1]
            else:
                resp.json.return_value = []
            return resp

        mock_get.side_effect = side_effect

        dl = self._make_dl()
        records = dl.download("NESN.SW")

        # Should have tried .SW first, then .ZU (where data was found)
        assert any(".ZU" in u for u in call_urls)
        assert len(records) > 0

    def test_download_no_api_key_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            dl = FmpFundamentalsDownloader(api_key="", api_delay=0)
            # Ensure api_key is empty
            dl.api_key = ""
            records = dl.download("AAPL")

        assert records == []
        assert dl._failure_count == 1

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_download_empty_data_counts_as_failure(self, mock_get, mock_sleep):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        mock_get.return_value = mock_resp

        dl = self._make_dl()
        records = dl.download("UNKNOWN")

        assert records == []
        assert dl._failure_count == 1

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch("modules.input.fmp_downloader.requests.Session.get")
    def test_download_circuit_open_skips(self, mock_get, mock_sleep):
        dl = self._make_dl()
        dl.circuit_breaker._state = 1  # OPEN
        dl.circuit_breaker._opened_at = _time.time()  # just opened

        records = dl.download("HSBA.L")

        assert records == []
        assert dl._failure_count == 1
        mock_get.assert_not_called()


# ── _fetch_all_statements tests ───────────────────────────────────────


class TestFetchAllStatements:

    def _make_dl(self):
        return FmpFundamentalsDownloader(api_key="test_key", api_delay=0, max_retries=2)

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch.object(FmpFundamentalsDownloader, "_fetch_statement")
    def test_fetches_all_three_statements(self, mock_fetch, mock_sleep):
        mock_fetch.side_effect = [SAMPLE_INCOME, SAMPLE_BALANCE, SAMPLE_CASHFLOW]

        dl = self._make_dl()
        result = dl._fetch_all_statements("NESN.SW", "quarter")

        assert "income" in result
        assert "balance" in result
        assert "cashflow" in result
        assert result["income"] == SAMPLE_INCOME

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch.object(FmpFundamentalsDownloader, "_fetch_statement")
    def test_none_result_becomes_empty_list(self, mock_fetch, mock_sleep):
        mock_fetch.return_value = None

        dl = self._make_dl()
        result = dl._fetch_all_statements("NESN.SW", "quarter")

        assert result["income"] == []
        assert result["balance"] == []
        assert result["cashflow"] == []

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch.object(FmpFundamentalsDownloader, "_fetch_statement")
    def test_http_403_returns_empty_and_does_not_retry(self, mock_fetch, mock_sleep):
        error_resp = MagicMock(status_code=403)
        mock_fetch.side_effect = requests.exceptions.HTTPError(response=error_resp)

        dl = self._make_dl()
        result = dl._fetch_all_statements("NESN.SW", "quarter")

        assert result["income"] == []
        # 403 should break immediately, not exhaust retries
        # 3 statements x 1 call each (no retries on 403)
        assert mock_fetch.call_count == 3

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch.object(FmpFundamentalsDownloader, "_fetch_statement")
    def test_http_429_retries_with_sleep(self, mock_fetch, mock_sleep):
        """429 rate limit triggers a sleep(5) and retry."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                error_resp = MagicMock(status_code=429)
                raise requests.exceptions.HTTPError(response=error_resp)
            return SAMPLE_INCOME

        mock_fetch.side_effect = side_effect

        dl = self._make_dl()
        result = dl._fetch_all_statements("NESN.SW", "quarter")

        assert result["income"] == SAMPLE_INCOME
        mock_sleep.assert_called_with(5)

    @patch("modules.input.fmp_downloader.time.sleep")
    @patch.object(FmpFundamentalsDownloader, "_fetch_statement")
    def test_generic_exception_retries(self, mock_fetch, mock_sleep):
        mock_fetch.side_effect = ConnectionError("Network unreachable")

        dl = self._make_dl()
        result = dl._fetch_all_statements("NESN.SW", "quarter")

        # With max_retries=2, each statement exhausts retries then defaults to []
        assert result.get("income", []) == []


# ── _execute_download ─────────────────────────────────────────────────


class TestExecuteDownload:

    def test_returns_none(self):
        dl = FmpFundamentalsDownloader(api_key="k")
        assert dl._execute_download() is None
