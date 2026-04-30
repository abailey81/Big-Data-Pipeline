"""
Tests for SimFin fundamentals downloader.

Covers:
  - modules.input.simfin_downloader helper functions
  - modules.input.simfin_downloader.SimFinFundamentalsDownloader
  - modules.input.simfin_downloader compact format unpacking
  - modules.input.simfin_downloader record extraction and field mapping
  - EBITDA / FCF computation from components
  - Total debt aggregation from short-term + long-term columns
  - Error handling for network errors, empty responses, missing data
"""

import time as _time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from modules.input.simfin_downloader import (
    SIMFIN_FIELD_MAP,
    SimFinFundamentalsDownloader,
    _extract_simfin_records,
    _find_column_value,
    _simfin_base_ticker,
    _simfin_ticker,
    _unpack_compact,
)


# ── Helper function tests ────────────────────────────────────────────


class TestSimFinTicker:

    def test_strips_whitespace(self):
        assert _simfin_ticker("HSBA.L   ") == "HSBA.L"

    def test_already_clean(self):
        assert _simfin_ticker("AAPL") == "AAPL"

    def test_empty_string(self):
        assert _simfin_ticker("") == ""

    def test_non_us_ticker_preserved(self):
        assert _simfin_ticker("TTE.PA") == "TTE.PA"


class TestSimFinBaseTicker:

    def test_strips_suffix(self):
        assert _simfin_base_ticker("HSBA.L") == "HSBA"

    def test_no_suffix_unchanged(self):
        assert _simfin_base_ticker("AAPL") == "AAPL"

    def test_double_dot_takes_last(self):
        assert _simfin_base_ticker("BRK.B.US") == "BRK.B"

    def test_strips_whitespace(self):
        assert _simfin_base_ticker("  HSBA.L  ") == "HSBA"

    def test_empty_string(self):
        assert _simfin_base_ticker("") == ""


# ── SimFinFundamentalsDownloader init tests ──────────────────────────


class TestSimFinDownloaderInit:

    def test_init_sets_api_key(self):
        dl = SimFinFundamentalsDownloader(api_key="test_key")
        assert dl.api_key == "test_key"

    def test_init_sets_source_name(self):
        dl = SimFinFundamentalsDownloader(api_key="test_key")
        assert dl.source_name == "simfin_fundamentals"

    def test_init_default_retries(self):
        dl = SimFinFundamentalsDownloader(api_key="k")
        assert dl.max_retries == 3

    def test_init_custom_retries(self):
        dl = SimFinFundamentalsDownloader(api_key="k", max_retries=5)
        assert dl.max_retries == 5

    def test_stats_initial(self):
        dl = SimFinFundamentalsDownloader(api_key="k")
        s = dl.stats
        assert s["downloads"] == 0
        assert s["successes"] == 0
        assert s["failures"] == 0

    @patch.dict("os.environ", {"SIMFIN_API_KEY": "env_key"})
    def test_init_falls_back_to_env_var(self):
        dl = SimFinFundamentalsDownloader()
        assert dl.api_key == "env_key"

    def test_init_session_has_auth_header(self):
        dl = SimFinFundamentalsDownloader(api_key="my_key")
        assert dl._session.headers["Authorization"] == "api-key my_key"


# ── Compact format unpacking tests ──────────────────────────────────


class TestUnpackCompact:

    def test_top_level_columns_data(self):
        raw = {
            "columns": ["Revenue", "Net Income", "Report Date"],
            "data": [
                [5000, 1200, "2024-03-31"],
                [4800, 1100, "2023-12-31"],
            ],
        }
        rows = _unpack_compact(raw)
        assert len(rows) == 2
        assert rows[0]["Revenue"] == 5000
        assert rows[0]["Net Income"] == 1200
        assert rows[1]["Report Date"] == "2023-12-31"

    def test_nested_statements_list(self):
        raw = {
            "statements": [
                {
                    "columns": ["Revenue", "Report Date"],
                    "data": [[5000, "2024-03-31"]],
                },
                {
                    "columns": ["Total Assets", "Report Date"],
                    "data": [[80000, "2024-03-31"]],
                },
            ]
        }
        rows = _unpack_compact(raw)
        assert len(rows) == 2
        assert rows[0]["Revenue"] == 5000
        assert rows[1]["Total Assets"] == 80000

    def test_statement_type_keys(self):
        raw = {
            "pl": {
                "columns": ["Revenue", "Report Date"],
                "data": [[5000, "2024-03-31"]],
            },
            "bs": {
                "columns": ["Total Assets", "Report Date"],
                "data": [[80000, "2024-03-31"]],
            },
            "cf": {
                "columns": ["Net Cash from Operating Activities", "Report Date"],
                "data": [[2000, "2024-03-31"]],
            },
        }
        rows = _unpack_compact(raw)
        assert len(rows) == 3

    def test_mismatched_column_data_length_skipped(self):
        raw = {
            "columns": ["Revenue", "Net Income", "Report Date"],
            "data": [
                [5000, 1200],  # only 2 values, 3 columns
                [4800, 1100, "2023-12-31"],  # correct
            ],
        }
        rows = _unpack_compact(raw)
        assert len(rows) == 1
        assert rows[0]["Revenue"] == 4800

    def test_empty_data_array(self):
        raw = {
            "columns": ["Revenue"],
            "data": [],
        }
        rows = _unpack_compact(raw)
        assert len(rows) == 0

    def test_empty_dict(self):
        rows = _unpack_compact({})
        assert rows == []


# ── _find_column_value tests ─────────────────────────────────────────


class TestFindColumnValue:

    def test_finds_first_matching_column(self):
        row = {"Currency": "GBP", "currency": "EUR"}
        val = _find_column_value(row, {"Currency", "currency"})
        assert val in ("GBP", "EUR")

    def test_returns_none_when_no_match(self):
        row = {"Revenue": 5000}
        assert _find_column_value(row, {"Currency", "currency"}) is None

    def test_skips_empty_string_values(self):
        row = {"Currency": "", "currency": "GBP"}
        val = _find_column_value(row, {"Currency", "currency"})
        assert val == "GBP"

    def test_skips_none_values(self):
        row = {"Currency": None, "currency": "GBP"}
        val = _find_column_value(row, {"Currency", "currency"})
        assert val == "GBP"


# ── Realistic fixture data ──────────────────────────────────────────

# Compact format with columns + data arrays (income + balance + cashflow merged)
SAMPLE_COMPACT_PL = {
    "columns": [
        "Report Date",
        "Currency",
        "Fiscal Period",
        "Revenue",
        "Net Income",
        "Gross Profit",
        "Operating Income (EBIT)",
        "EBITDA",
        "Earnings Per Share, Basic",
        "Earnings Per Share, Diluted",
    ],
    "data": [
        ["2024-03-31", "GBP", "Q1", 15000000, 3500000, 8000000, 5000000, 6200000, 1.75, 1.70],
        ["2023-12-31", "GBP", "Q4", 14500000, 3200000, 7800000, 4800000, 6000000, 1.60, 1.55],
    ],
}

SAMPLE_COMPACT_BS = {
    "columns": [
        "Report Date",
        "Currency",
        "Fiscal Period",
        "Total Equity",
        "Total Assets",
        "Total Liabilities",
        "Book Value per Share",
        "Short Term Debt",
        "Long Term Debt",
    ],
    "data": [
        ["2024-03-31", "GBP", "Q1", 40000000, 80000000, 40000000, 20.0, 5000000, 15000000],
        ["2023-12-31", "GBP", "Q4", 38000000, 78000000, 40000000, 19.0, 4500000, 14000000],
    ],
}

SAMPLE_COMPACT_CF = {
    "columns": [
        "Report Date",
        "Currency",
        "Fiscal Period",
        "Net Cash from Operating Activities",
        "Capital Expenditures",
        "Free Cash Flow",
        "Depreciation & Amortization",
    ],
    "data": [
        ["2024-03-31", "GBP", "Q1", 6000000, -2000000, 4000000, 1200000],
        ["2023-12-31", "GBP", "Q4", 5500000, -1800000, 3700000, 1100000],
    ],
}


def _build_statements_response():
    """Build a realistic SimFin response with nested statements."""
    return {
        "statements": [
            SAMPLE_COMPACT_PL,
            SAMPLE_COMPACT_BS,
            SAMPLE_COMPACT_CF,
        ]
    }


def _build_flat_response():
    """Build a single flat compact response with all fields merged."""
    return {
        "columns": [
            "Report Date",
            "Currency",
            "Fiscal Period",
            "Revenue",
            "Net Income",
            "Gross Profit",
            "Operating Income (EBIT)",
            "Earnings Per Share, Basic",
            "Earnings Per Share, Diluted",
            "Total Equity",
            "Total Assets",
            "Total Liabilities",
            "Book Value per Share",
            "Short Term Debt",
            "Long Term Debt",
            "Net Cash from Operating Activities",
            "Capital Expenditures",
            "Depreciation & Amortization",
        ],
        "data": [
            [
                "2024-03-31", "GBP", "Q1",
                15000000, 3500000, 8000000, 5000000,
                1.75, 1.70,
                40000000, 80000000, 40000000, 20.0,
                5000000, 15000000,
                6000000, -2000000, 1200000,
            ],
        ],
    }


# ── _extract_simfin_records tests ───────────────────────────────────


class TestExtractSimFinRecords:

    def test_extracts_records_from_statements_format(self):
        raw = _build_statements_response()
        records = _extract_simfin_records(raw, "HSBA.L", "quarterly")
        assert len(records) > 0

    def test_symbol_set_correctly(self):
        raw = _build_statements_response()
        records = _extract_simfin_records(raw, "HSBA.L", "quarterly")
        for r in records:
            assert r["symbol"] == "HSBA.L"

    def test_currency_from_data(self):
        raw = _build_statements_response()
        records = _extract_simfin_records(raw, "HSBA.L", "quarterly")
        for r in records:
            assert r["currency"] == "GBP"

    def test_currency_defaults_to_usd_when_missing(self):
        raw = {
            "columns": ["Report Date", "Revenue"],
            "data": [["2024-03-31", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        for r in records:
            assert r["currency"] == "USD"

    def test_maps_revenue_to_total_revenue(self):
        raw = _build_flat_response()
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        revenue = [r for r in records if r["field_name"] == "total_revenue"]
        assert len(revenue) == 1
        assert revenue[0]["field_value"] == 15000000

    def test_maps_net_income(self):
        raw = _build_flat_response()
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        ni = [r for r in records if r["field_name"] == "net_income"]
        assert len(ni) == 1
        assert ni[0]["field_value"] == 3500000

    def test_maps_balance_sheet_fields(self):
        raw = _build_flat_response()
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "stockholders_equity" in field_names
        assert "total_assets" in field_names
        assert "total_liabilities" in field_names
        assert "book_value" in field_names

    def test_maps_cash_flow_fields(self):
        raw = _build_flat_response()
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "operating_cash_flow" in field_names
        assert "capital_expenditure" in field_names

    def test_period_type_detected_from_q_prefix(self):
        raw = {
            "columns": ["Report Date", "Fiscal Period", "Revenue"],
            "data": [["2024-03-31", "Q1", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "annual")
        # Q1 in data should override the passed-in "annual"
        assert records[0]["period_type"] == "quarterly"

    def test_period_type_annual_from_fy(self):
        raw = {
            "columns": ["Report Date", "Fiscal Period", "Revenue"],
            "data": [["2024-03-31", "FY", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert records[0]["period_type"] == "annual"

    def test_period_type_annual_from_full_year(self):
        raw = {
            "columns": ["Report Date", "Fiscal Period", "Revenue"],
            "data": [["2024-03-31", "full-year", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert records[0]["period_type"] == "annual"

    def test_start_date_filter(self):
        raw = _build_statements_response()
        records = _extract_simfin_records(
            raw, "TEST", "quarterly", start_date="2024-01-01"
        )
        for r in records:
            assert r["report_date"] >= date(2024, 1, 1)

    def test_empty_raw_returns_empty(self):
        assert _extract_simfin_records({}, "TEST", "quarterly") == []
        assert _extract_simfin_records(None, "TEST", "quarterly") == []

    def test_deduplicates_by_field_date_period(self):
        raw = {
            "columns": ["Report Date", "Revenue"],
            "data": [
                ["2024-03-31", 5000],
                ["2024-03-31", 6000],  # same date, same field
            ],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        revenue = [r for r in records if r["field_name"] == "total_revenue"]
        assert len(revenue) == 1  # deduped — first value wins

    def test_skips_non_numeric_values(self):
        raw = {
            "columns": ["Report Date", "Revenue"],
            "data": [["2024-03-31", "N/A"]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert len(records) == 0

    def test_skips_rows_without_date(self):
        raw = {
            "columns": ["Revenue"],
            "data": [[5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert len(records) == 0

    def test_skips_bad_date(self):
        raw = {
            "columns": ["Report Date", "Revenue"],
            "data": [["not-a-date", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert len(records) == 0


# ── Total debt computation ──────────────────────────────────────────


class TestTotalDebtComputation:

    def test_total_debt_from_short_and_long(self):
        raw = _build_flat_response()
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        debt = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt) == 1
        assert debt[0]["field_value"] == 20000000  # 5M + 15M

    def test_total_debt_with_only_short_term(self):
        raw = {
            "columns": ["Report Date", "Short Term Debt"],
            "data": [["2024-03-31", 5000000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        debt = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt) == 1
        assert debt[0]["field_value"] == 5000000

    def test_total_debt_with_only_long_term(self):
        raw = {
            "columns": ["Report Date", "Long Term Debt"],
            "data": [["2024-03-31", 15000000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        debt = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt) == 1
        assert debt[0]["field_value"] == 15000000

    def test_total_debt_alternative_column_names(self):
        raw = {
            "columns": ["Report Date", "Short-Term Debt", "Non-Current Debt"],
            "data": [["2024-03-31", 3000000, 12000000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        debt = [r for r in records if r["field_name"] == "total_debt"]
        assert len(debt) == 1
        assert debt[0]["field_value"] == 15000000


# ── EBITDA and FCF computation ──────────────────────────────────────


class TestEBITDAComputation:

    def test_ebitda_computed_when_missing(self):
        raw = {
            "columns": [
                "Report Date",
                "Operating Income (EBIT)",
                "Depreciation & Amortization",
            ],
            "data": [["2024-03-31", 5000000, 1200000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        ebitda = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda) == 1
        # EBITDA = operating_income + abs(depreciation)
        assert ebitda[0]["field_value"] == 6200000

    def test_ebitda_not_overridden_when_present(self):
        raw = {
            "columns": [
                "Report Date",
                "Operating Income (EBIT)",
                "Depreciation & Amortization",
                "EBITDA",
            ],
            "data": [["2024-03-31", 5000000, 1200000, 7000000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        ebitda = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda) == 1
        assert ebitda[0]["field_value"] == 7000000  # original value preserved


class TestFCFComputation:

    def test_fcf_computed_when_missing(self):
        raw = {
            "columns": [
                "Report Date",
                "Net Cash from Operating Activities",
                "Capital Expenditures",
            ],
            "data": [["2024-03-31", 6000000, -2000000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        fcf = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf) == 1
        # FCF = operating_cash_flow - abs(capex)
        assert fcf[0]["field_value"] == 4000000

    def test_fcf_not_overridden_when_present(self):
        raw = {
            "columns": [
                "Report Date",
                "Net Cash from Operating Activities",
                "Capital Expenditures",
                "Free Cash Flow",
            ],
            "data": [["2024-03-31", 6000000, -2000000, 3500000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        fcf = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf) == 1
        assert fcf[0]["field_value"] == 3500000  # original value preserved

    def test_fcf_positive_capex_handled(self):
        """Capital expenditures may be reported as positive; abs() is taken."""
        raw = {
            "columns": [
                "Report Date",
                "Net Cash from Operating Activities",
                "Capital Expenditures",
            ],
            "data": [["2024-03-31", 6000000, 2000000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        fcf = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf) == 1
        assert fcf[0]["field_value"] == 4000000


# ── Depreciation helper field removal ───────────────────────────────


class TestDepreciationFieldRemoval:

    def test_internal_depreciation_field_not_in_output(self):
        raw = {
            "columns": [
                "Report Date",
                "Operating Income (EBIT)",
                "Depreciation & Amortization",
            ],
            "data": [["2024-03-31", 5000000, 1200000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        field_names = {r["field_name"] for r in records}
        assert "_depreciation" not in field_names


# ── Multiple currency / period column variants ──────────────────────


class TestColumnVariants:

    def test_lowercase_currency_column(self):
        raw = {
            "columns": ["Report Date", "currency", "Revenue"],
            "data": [["2024-03-31", "EUR", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert records[0]["currency"] == "EUR"

    def test_uppercase_currency_column(self):
        raw = {
            "columns": ["Report Date", "Currency", "Revenue"],
            "data": [["2024-03-31", "CHF", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert records[0]["currency"] == "CHF"

    def test_lowercase_date_column(self):
        raw = {
            "columns": ["date", "Revenue"],
            "data": [["2024-03-31", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert len(records) == 1
        assert records[0]["report_date"] == date(2024, 3, 31)

    def test_fiscal_period_end_date_column(self):
        raw = {
            "columns": ["Fiscal Period End", "Revenue"],
            "data": [["2024-06-30", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert len(records) == 1
        assert records[0]["report_date"] == date(2024, 6, 30)

    def test_period_column_lowercase(self):
        raw = {
            "columns": ["Report Date", "period", "Revenue"],
            "data": [["2024-03-31", "Q2", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "annual")
        assert records[0]["period_type"] == "quarterly"

    def test_period_column_title_case(self):
        raw = {
            "columns": ["Report Date", "Period", "Revenue"],
            "data": [["2024-03-31", "annual", 5000]],
        }
        records = _extract_simfin_records(raw, "TEST", "quarterly")
        assert records[0]["period_type"] == "annual"


# ── Field mapping completeness ──────────────────────────────────────


class TestFieldMapping:

    def test_field_map_has_income_statement_fields(self):
        assert "Revenue" in SIMFIN_FIELD_MAP
        assert "Net Income" in SIMFIN_FIELD_MAP
        assert "Gross Profit" in SIMFIN_FIELD_MAP
        assert "Operating Income (EBIT)" in SIMFIN_FIELD_MAP

    def test_field_map_has_balance_sheet_fields(self):
        assert "Total Equity" in SIMFIN_FIELD_MAP
        assert "Total Assets" in SIMFIN_FIELD_MAP
        assert "Total Liabilities" in SIMFIN_FIELD_MAP

    def test_field_map_has_cash_flow_fields(self):
        assert "Net Cash from Operating Activities" in SIMFIN_FIELD_MAP
        assert "Capital Expenditures" in SIMFIN_FIELD_MAP
        assert "Free Cash Flow" in SIMFIN_FIELD_MAP

    def test_canonical_names_are_lowercase_snake_case(self):
        for canonical in SIMFIN_FIELD_MAP.values():
            assert canonical == canonical.lower()
            assert " " not in canonical


# ── _fetch_statements tests ─────────────────────────────────────────


class TestFetchStatements:

    @patch("modules.input.simfin_downloader.requests.Session.get")
    def test_fetch_statements_returns_first_element_of_list(self, mock_get):
        dl = SimFinFundamentalsDownloader(api_key="k")
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"columns": ["Revenue"], "data": [[5000]]}]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = dl._fetch_statements("AAPL", "quarterly")
        assert result == {"columns": ["Revenue"], "data": [[5000]]}

    @patch("modules.input.simfin_downloader.requests.Session.get")
    def test_fetch_statements_returns_none_for_empty_list(self, mock_get):
        dl = SimFinFundamentalsDownloader(api_key="k")
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = dl._fetch_statements("AAPL", "quarterly")
        assert result is None

    @patch("modules.input.simfin_downloader.requests.Session.get")
    def test_fetch_statements_returns_none_for_error_dict(self, mock_get):
        dl = SimFinFundamentalsDownloader(api_key="k")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "not found"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = dl._fetch_statements("AAPL", "quarterly")
        assert result is None

    @patch("modules.input.simfin_downloader.requests.Session.get")
    def test_fetch_statements_returns_dict_with_columns(self, mock_get):
        dl = SimFinFundamentalsDownloader(api_key="k")
        raw_dict = {"columns": ["Revenue"], "data": [[5000]]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = raw_dict
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = dl._fetch_statements("AAPL", "quarterly")
        assert result == raw_dict

    @patch("modules.input.simfin_downloader.requests.Session.get")
    def test_fetch_statements_raises_on_http_error(self, mock_get):
        dl = SimFinFundamentalsDownloader(api_key="k")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=500)
        )
        mock_get.return_value = mock_resp

        with pytest.raises(requests.exceptions.HTTPError):
            dl._fetch_statements("AAPL", "quarterly")


# ── _fetch_with_retries tests ───────────────────────────────────────


class TestFetchWithRetries:

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_statements")
    def test_returns_data_on_success(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fetch.return_value = {"columns": ["Revenue"], "data": [[5000]]}

        result = dl._fetch_with_retries("AAPL", "quarterly")
        assert result is not None
        assert "columns" in result

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_statements")
    def test_returns_none_on_401(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        resp_mock = MagicMock()
        resp_mock.status_code = 401
        mock_fetch.side_effect = requests.exceptions.HTTPError(response=resp_mock)

        result = dl._fetch_with_retries("AAPL", "quarterly")
        assert result is None

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_statements")
    def test_returns_none_on_403(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        resp_mock = MagicMock()
        resp_mock.status_code = 403
        mock_fetch.side_effect = requests.exceptions.HTTPError(response=resp_mock)

        result = dl._fetch_with_retries("AAPL", "quarterly")
        assert result is None

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_statements")
    def test_retries_on_429(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0, max_retries=3)
        resp_mock = MagicMock()
        resp_mock.status_code = 429
        call_count = 0

        def side_effect(ticker, period):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise requests.exceptions.HTTPError(response=resp_mock)
            return {"columns": ["Revenue"], "data": [[5000]]}

        mock_fetch.side_effect = side_effect
        result = dl._fetch_with_retries("AAPL", "quarterly")
        assert result is not None
        assert call_count == 3

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_statements")
    def test_returns_none_after_max_retries(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0, max_retries=2)
        mock_fetch.side_effect = ConnectionError("network error")

        result = dl._fetch_with_retries("AAPL", "quarterly")
        assert result is None


# ── download() full flow tests ──────────────────────────────────────


class TestDownloadFullFlow:

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_returns_records_for_us_ticker(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fetch.return_value = _build_flat_response()

        records = dl.download("AAPL")
        assert len(records) > 0
        assert dl._success_count == 1

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_tries_base_ticker_on_non_us(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        # First two calls (HSBA.L quarterly, HSBA.L annual) return None,
        # then HSBA quarterly returns data
        call_log = []

        def side_effect(ticker, period):
            call_log.append((ticker, period))
            if ticker == "HSBA":
                return _build_flat_response()
            return None

        mock_fetch.side_effect = side_effect
        records = dl.download("HSBA.L")
        assert len(records) > 0
        # Should have tried HSBA.L first, then HSBA
        tickers_tried = [t for t, _ in call_log]
        assert "HSBA.L" in tickers_tried
        assert "HSBA" in tickers_tried

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_returns_empty_without_api_key(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="")
        records = dl.download("AAPL")
        assert records == []
        assert dl._failure_count == 1
        mock_fetch.assert_not_called()

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_returns_empty_when_no_data(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fetch.return_value = None

        records = dl.download("AAPL")
        assert records == []
        assert dl._failure_count == 1

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_circuit_open_skips(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        dl.circuit_breaker._state = 1  # OPEN
        dl.circuit_breaker._opened_at = _time.time()  # just opened

        records = dl.download("AAPL")
        assert records == []
        assert dl._failure_count == 1
        mock_fetch.assert_not_called()

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_increments_download_count(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        mock_fetch.return_value = _build_flat_response()

        dl.download("AAPL")
        dl.download("MSFT")
        assert dl._download_count == 2

    @patch("modules.input.simfin_downloader.time.sleep")
    @patch.object(SimFinFundamentalsDownloader, "_fetch_with_retries")
    def test_download_stops_after_first_successful_ticker(self, mock_fetch, mock_sleep):
        dl = SimFinFundamentalsDownloader(api_key="k", api_delay=0)
        call_log = []

        def side_effect(ticker, period):
            call_log.append((ticker, period))
            return _build_flat_response()

        mock_fetch.side_effect = side_effect
        dl.download("HSBA.L")

        # Should stop after HSBA.L succeeds — should NOT try HSBA
        tickers_tried = {t for t, _ in call_log}
        assert "HSBA.L" in tickers_tried
        assert "HSBA" not in tickers_tried
