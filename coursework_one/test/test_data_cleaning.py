"""
Tests for modules.processing.data_cleaner

Covers Spec §7.2 Issue 6: robust parsing, NaN handling,
and EAV pattern for fundamentals.
"""

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# ── Helper function tests ────────────────────────────────────────────────


class TestSafeFloat:

    def test_valid_float(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(42.5) == 42.5

    def test_valid_int_as_float(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(42) == 42.0

    def test_nan_returns_none(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(float("nan")) is None

    def test_inf_returns_none(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(float("inf")) is None

    def test_none_returns_none(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(None) is None

    def test_string_float(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float("123.45") == 123.45

    def test_invalid_string_returns_none(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float("not_a_number") is None

    def test_numpy_nan(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(np.nan) is None

    def test_numpy_float(self):
        from modules.processing.data_cleaner import _safe_float

        assert _safe_float(np.float64(42.5)) == 42.5


class TestSafeInt:

    def test_valid_int(self):
        from modules.processing.data_cleaner import _safe_int

        assert _safe_int(42) == 42

    def test_float_to_int(self):
        from modules.processing.data_cleaner import _safe_int

        assert _safe_int(42.9) == 42

    def test_nan_returns_none(self):
        from modules.processing.data_cleaner import _safe_int

        assert _safe_int(float("nan")) is None

    def test_none_returns_none(self):
        from modules.processing.data_cleaner import _safe_int

        assert _safe_int(None) is None


# ── Price cleaning tests ─────────────────────────────────────────────────


class TestCleanPriceDataframe:

    def test_produces_correct_records(self, sample_price_df):
        from modules.processing.data_cleaner import clean_price_dataframe

        records = clean_price_dataframe(sample_price_df, "AAPL", "USD")
        assert len(records) == 3
        assert records[0]["symbol"] == "AAPL"
        assert records[0]["currency"] == "USD"
        assert records[0]["open_price"] == 150.0
        assert records[0]["volume"] == 1000000

    def test_handles_nan_values(self, sample_price_df_with_nans):
        from modules.processing.data_cleaner import clean_price_dataframe

        records = clean_price_dataframe(sample_price_df_with_nans, "TEST", "GBP")
        assert len(records) == 2
        assert records[1]["open_price"] is None  # was NaN
        assert records[0]["low_price"] is None  # was NaN

    def test_empty_dataframe_returns_empty(self):
        from modules.processing.data_cleaner import clean_price_dataframe

        assert clean_price_dataframe(pd.DataFrame(), "TEST", "USD") == []

    def test_none_returns_empty(self):
        from modules.processing.data_cleaner import clean_price_dataframe

        assert clean_price_dataframe(None, "TEST", "USD") == []

    def test_preserves_date_type(self, sample_price_df):
        from modules.processing.data_cleaner import clean_price_dataframe

        records = clean_price_dataframe(sample_price_df, "AAPL", "USD")
        assert isinstance(records[0]["cob_date"], date)


# ── Fundamentals cleaning tests ──────────────────────────────────────────


class TestCleanFundamentalsData:

    def test_extracts_balance_sheet_fields(self, sample_fund_data):
        from modules.processing.data_cleaner import clean_fundamentals_data

        records = clean_fundamentals_data(sample_fund_data, "AAPL", "USD")
        field_names = {r["field_name"] for r in records}
        assert "stockholders_equity" in field_names
        assert "total_debt" in field_names
        assert "total_assets" in field_names
        # Currency must propagate to every record
        assert all(r["currency"] == "USD" for r in records)

    def test_extracts_income_stmt_fields(self, sample_fund_data):
        from modules.processing.data_cleaner import clean_fundamentals_data

        records = clean_fundamentals_data(sample_fund_data, "AAPL", "USD")
        field_names = {r["field_name"] for r in records}
        assert "net_income" in field_names
        assert "basic_eps" in field_names
        assert "total_revenue" in field_names

    def test_extracts_book_value_per_share(self, sample_fund_data):
        from modules.processing.data_cleaner import clean_fundamentals_data

        records = clean_fundamentals_data(sample_fund_data, "AAPL", "USD")
        bvps_records = [r for r in records if r["field_name"] == "book_value_per_share"]
        assert len(bvps_records) == 1
        assert bvps_records[0]["field_value"] == 25.5

    def test_handles_empty_fund_data(self):
        from modules.processing.data_cleaner import clean_fundamentals_data

        result = clean_fundamentals_data(
            {"balance_sheet": pd.DataFrame(), "income_stmt": pd.DataFrame(), "info": {}}, "AAPL", "USD"
        )
        assert result == []

    def test_handles_none_statements(self):
        from modules.processing.data_cleaner import clean_fundamentals_data

        result = clean_fundamentals_data(
            {"balance_sheet": None, "income_stmt": None, "info": None}, "AAPL", "USD"
        )
        assert result == []

    def test_multiple_quarters(self, sample_fund_data):
        from modules.processing.data_cleaner import clean_fundamentals_data

        records = clean_fundamentals_data(sample_fund_data, "AAPL")
        # Should have records across 4 quarters for multiple fields
        dates = {r["report_date"] for r in records}
        assert len(dates) >= 4  # 4 quarters from BS/IS + today for bvps


# ── FX cleaning tests ───────────────────────────────────────────────────


class TestCleanFxDataframe:

    def test_produces_records(self, sample_fx_df):
        from modules.processing.data_cleaner import clean_fx_dataframe

        records = clean_fx_dataframe(sample_fx_df, "GBPUSD=X")
        assert len(records) == 2
        assert records[0]["currency_pair"] == "GBPUSD=X"
        assert records[0]["close_rate"] == 1.2680

    def test_empty_returns_empty(self):
        from modules.processing.data_cleaner import clean_fx_dataframe

        assert clean_fx_dataframe(pd.DataFrame(), "GBPUSD=X") == []


# ── VIX cleaning tests ──────────────────────────────────────────────────


class TestCleanVixDataframe:

    def test_produces_records(self, sample_vix_df):
        from modules.processing.data_cleaner import clean_vix_dataframe

        records = clean_vix_dataframe(sample_vix_df)
        assert len(records) == 2
        assert records[0]["close_price"] == 13.8
        assert records[0]["adj_close_price"] == 13.8

    def test_empty_returns_empty(self):
        from modules.processing.data_cleaner import clean_vix_dataframe

        assert clean_vix_dataframe(pd.DataFrame()) == []


# ── Pydantic model tests ────────────────────────────────────────────────


class TestDailyPriceModel:

    def test_valid_construction(self):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 2), open_price=150.0, currency="USD")
        assert p.symbol == "AAPL"

    def test_nan_coercion_to_none(self):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(symbol="AAPL", cob_date=date(2024, 1, 2), open_price=float("nan"), currency="USD")
        assert p.open_price is None

    def test_whitespace_stripping(self):
        from modules.data_models.models import DailyPrice

        p = DailyPrice(symbol="AAPL    ", cob_date=date(2024, 1, 2), currency="USD")
        assert p.symbol == "AAPL"


class TestFundamentalRecordModel:

    def test_valid_construction(self):
        from modules.data_models.models import FundamentalRecord

        r = FundamentalRecord(
            symbol="AAPL", report_date=date(2024, 9, 30), field_name="net_income", field_value=5000.0
        )
        assert r.field_name == "net_income"
        assert r.period_type == "quarterly"

    def test_null_value_allowed(self):
        from modules.data_models.models import FundamentalRecord

        r = FundamentalRecord(
            symbol="AAPL", report_date=date(2024, 9, 30), field_name="ebitda", field_value=None
        )
        assert r.field_value is None


# ── Risk-Free Rate Cleaning Tests ────────────────────────────────────


class TestCleanRiskFreeRateDataframe:

    def test_valid_fred_csv(self):
        from modules.processing.data_cleaner import clean_risk_free_rate_dataframe

        df = pd.DataFrame({"DATE": ["2024-01-02", "2024-01-03", "2024-01-04"], "DGS3MO": [5.22, 5.23, 5.20]})
        records = clean_risk_free_rate_dataframe(df)
        assert len(records) == 3
        assert records[0]["cob_date"] == date(2024, 1, 2)
        assert records[0]["rate_pct"] == 5.22
        assert records[0]["series_id"] == "DGS3MO"

    def test_dot_values_become_none(self):
        from modules.processing.data_cleaner import clean_risk_free_rate_dataframe

        df = pd.DataFrame({"DATE": ["2024-01-02", "2024-01-03"], "DGS3MO": [5.22, "."]})
        records = clean_risk_free_rate_dataframe(df)
        assert len(records) == 2
        assert records[0]["rate_pct"] == 5.22
        assert records[1]["rate_pct"] is None

    def test_empty_dataframe(self):
        from modules.processing.data_cleaner import clean_risk_free_rate_dataframe

        records = clean_risk_free_rate_dataframe(pd.DataFrame())
        assert records == []

    def test_none_dataframe(self):
        from modules.processing.data_cleaner import clean_risk_free_rate_dataframe

        records = clean_risk_free_rate_dataframe(None)
        assert records == []

    def test_custom_series_id(self):
        from modules.processing.data_cleaner import clean_risk_free_rate_dataframe

        df = pd.DataFrame({"DATE": ["2024-01-02"], "DGS10": [4.05]})
        records = clean_risk_free_rate_dataframe(df, series_id="DGS10")
        assert len(records) == 1
        assert records[0]["series_id"] == "DGS10"
        assert records[0]["rate_pct"] == 4.05


class TestComputeEbitdaFallback:
    """Tests for EBITDA computation from Operating Income + Depreciation."""

    def test_computes_from_components(self):
        """EBITDA = Operating Income + abs(Depreciation)."""
        from modules.processing.data_cleaner import _compute_ebitda_fallback

        col_date = pd.Timestamp("2024-09-30")
        stmt_df = pd.DataFrame(
            {
                col_date: [6000, -1500],
            },
            index=["OperatingIncome", "ReconciledDepreciation"],
        )
        result = _compute_ebitda_fallback(stmt_df, col_date)
        assert result == 7500  # 6000 + abs(-1500)

    def test_positive_depreciation(self):
        """Handles depreciation reported as a positive value."""
        from modules.processing.data_cleaner import _compute_ebitda_fallback

        col_date = pd.Timestamp("2024-09-30")
        stmt_df = pd.DataFrame(
            {
                col_date: [6000, 1500],
            },
            index=["OperatingIncome", "DepreciationAndAmortization"],
        )
        result = _compute_ebitda_fallback(stmt_df, col_date)
        assert result == 7500

    def test_returns_none_missing_operating_income(self):
        """Returns None when operating income is missing."""
        from modules.processing.data_cleaner import _compute_ebitda_fallback

        col_date = pd.Timestamp("2024-09-30")
        stmt_df = pd.DataFrame(
            {
                col_date: [1500],
            },
            index=["ReconciledDepreciation"],
        )
        result = _compute_ebitda_fallback(stmt_df, col_date)
        assert result is None

    def test_returns_none_missing_depreciation(self):
        """Returns None when depreciation is missing."""
        from modules.processing.data_cleaner import _compute_ebitda_fallback

        col_date = pd.Timestamp("2024-09-30")
        stmt_df = pd.DataFrame(
            {
                col_date: [6000],
            },
            index=["OperatingIncome"],
        )
        result = _compute_ebitda_fallback(stmt_df, col_date)
        assert result is None

    def test_returns_none_empty_dataframe(self):
        """Returns None when statement is empty."""
        from modules.processing.data_cleaner import _compute_ebitda_fallback

        result = _compute_ebitda_fallback(pd.DataFrame(), pd.Timestamp("2024-09-30"))
        assert result is None


class TestEbitdaFallbackInFundamentals:
    """Tests for EBITDA fallback integration in clean_fundamentals_data."""

    def test_computed_ebitda_when_direct_missing(self):
        """When income stmt has no EBITDA row, compute from OperatingIncome + Depreciation."""
        from modules.processing.data_cleaner import clean_fundamentals_data

        dates = pd.to_datetime(["2024-09-30"])
        # Income statement WITHOUT EBITDA but WITH OperatingIncome and depreciation
        income_stmt = pd.DataFrame(
            {
                dates[0]: [5000, 30000, 2.5, 2.4, 6000, 8000, -1200],
            },
            index=[
                "NetIncome",
                "TotalRevenue",
                "BasicEPS",
                "DilutedEPS",
                "OperatingIncome",
                "GrossProfit",
                "ReconciledDepreciation",
            ],
        )
        fund_data = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": income_stmt,
            "annual_cash_flow": pd.DataFrame(),
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {},
        }
        records = clean_fundamentals_data(fund_data, "TEST", "USD")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda_recs) == 1
        # Should be computed: 6000 + abs(-1200) = 7200
        assert ebitda_recs[0]["field_value"] == 7200

    def test_ebitda_from_ticker_info_fallback(self):
        """When income stmt has no EBITDA and no components, use ticker.info."""
        from modules.processing.data_cleaner import clean_fundamentals_data

        dates = pd.to_datetime(["2024-09-30"])
        # Income statement WITHOUT EBITDA and WITHOUT depreciation
        income_stmt = pd.DataFrame(
            {
                dates[0]: [5000, 30000, 2.5, 2.4, 6000, 8000],
            },
            index=[
                "NetIncome",
                "TotalRevenue",
                "BasicEPS",
                "DilutedEPS",
                "OperatingIncome",
                "GrossProfit",
            ],
        )
        fund_data = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": income_stmt,
            "annual_cash_flow": pd.DataFrame(),
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {"bookValue": 25.5, "ebitda": 95000000},
        }
        records = clean_fundamentals_data(fund_data, "TEST", "USD")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda" and r["field_value"] is not None]
        # Should have the ticker.info fallback value
        assert any(r["field_value"] == 95000000 for r in ebitda_recs)

    def test_direct_ebitda_not_overwritten_by_info(self):
        """When income stmt HAS EBITDA, ticker.info doesn't create duplicate."""
        from modules.processing.data_cleaner import clean_fundamentals_data

        dates = pd.to_datetime(["2024-09-30"])
        income_stmt = pd.DataFrame(
            {
                dates[0]: [5000, 30000, 8000, 2.5, 2.4, 6000, 7500],
            },
            index=[
                "NetIncome",
                "TotalRevenue",
                "EBITDA",
                "BasicEPS",
                "DilutedEPS",
                "OperatingIncome",
                "GrossProfit",
            ],
        )
        fund_data = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": income_stmt,
            "annual_cash_flow": pd.DataFrame(),
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {"ebitda": 95000000},
        }
        records = clean_fundamentals_data(fund_data, "TEST", "USD")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda" and r["field_value"] is not None]
        # Direct value (8000) should be present, not overwritten by info
        assert ebitda_recs[0]["field_value"] == 8000


class TestTickerInfoTTMFallback:
    """Tests for comprehensive ticker.info TTM extraction."""

    def test_info_fills_all_fundamental_fields(self):
        """ticker.info should provide TTM values for all fundamental fields."""
        from modules.processing.data_cleaner import clean_fundamentals_data

        fund_data = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": pd.DataFrame(),
            "annual_cash_flow": pd.DataFrame(),
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {
                "bookValue": 25.5,
                "ebitda": 95000000,
                "totalRevenue": 400000000,
                "netIncomeToCommon": 90000000,
                "operatingCashflow": 120000000,
                "freeCashflow": 85000000,
                "totalDebt": 50000000,
                "totalAssets": 350000000,
                "grossProfits": 180000000,
                "trailingEps": 5.2,
                "stockholdersEquity": 200000000,
            },
        }
        records = clean_fundamentals_data(fund_data, "TEST", "USD")
        field_names = {r["field_name"] for r in records if r["field_value"] is not None}
        assert "book_value_per_share" in field_names
        assert "ebitda" in field_names
        assert "total_revenue" in field_names
        assert "net_income" in field_names
        assert "operating_cash_flow" in field_names
        assert "free_cash_flow" in field_names
        assert "total_debt" in field_names
        assert "total_assets" in field_names
        assert "gross_profit" in field_names
        assert "diluted_eps" in field_names
        assert "stockholders_equity" in field_names

    def test_info_does_not_overwrite_statement_data(self):
        """Statement data takes priority over ticker.info."""
        from modules.processing.data_cleaner import clean_fundamentals_data

        dates = pd.to_datetime(["2024-09-30"])
        income_stmt = pd.DataFrame(
            {
                dates[0]: [5000, 30000, 8000, 2.5, 2.4, 6000, 7500],
            },
            index=[
                "NetIncome",
                "TotalRevenue",
                "EBITDA",
                "BasicEPS",
                "DilutedEPS",
                "OperatingIncome",
                "GrossProfit",
            ],
        )
        fund_data = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": income_stmt,
            "annual_cash_flow": pd.DataFrame(),
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {
                "ebitda": 95000000,  # Should NOT overwrite the 8000 from stmt
                "totalRevenue": 999999999,  # Should NOT overwrite 30000
            },
        }
        records = clean_fundamentals_data(fund_data, "TEST", "USD")
        # Statement data from 2024-09-30 should still be 8000
        stmt_ebitda = [
            r for r in records if r["field_name"] == "ebitda" and r["report_date"] == date(2024, 9, 30)
        ]
        assert stmt_ebitda[0]["field_value"] == 8000


class TestComputedFreeCashFlow:
    """Tests for computed free_cash_flow from operating_cash_flow - capex."""

    def test_computed_fcf_from_components(self):
        """free_cash_flow = operating_cash_flow - abs(capital_expenditure)."""
        from modules.processing.data_cleaner import clean_fundamentals_data

        dates = pd.to_datetime(["2024-09-30"])
        cash_flow = pd.DataFrame(
            {
                dates[0]: [120000, -30000],
            },
            index=["OperatingCashFlow", "CapitalExpenditure"],
        )
        fund_data = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": pd.DataFrame(),
            "annual_cash_flow": cash_flow,
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {},
        }
        records = clean_fundamentals_data(fund_data, "TEST", "USD")
        fcf_recs = [
            r for r in records if r["field_name"] == "free_cash_flow" and r["field_value"] is not None
        ]
        assert len(fcf_recs) >= 1
        # 120000 - abs(-30000) = 90000
        assert fcf_recs[0]["field_value"] == 90000


class TestEdgarComputedFields:
    """Tests for EDGAR computed EBITDA and free_cash_flow."""

    def test_edgar_computes_ebitda(self):
        """EDGAR should compute EBITDA from OperatingIncomeLoss + D&A."""
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        company_facts = {
            "facts": {
                "us-gaap": {
                    "OperatingIncomeLoss": {
                        "units": {
                            "USD": [
                                {"form": "10-Q", "end": "2024-09-30", "val": 5000000, "fp": "Q3"},
                            ]
                        }
                    },
                    "DepreciationDepletionAndAmortization": {
                        "units": {
                            "USD": [
                                {"form": "10-Q", "end": "2024-09-30", "val": 1200000, "fp": "Q3"},
                            ]
                        }
                    },
                }
            }
        }
        records = extract_edgar_fundamentals(company_facts, "AAPL")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda_recs) == 1
        # 5000000 + abs(1200000) = 6200000
        assert ebitda_recs[0]["field_value"] == 6200000

    def test_edgar_computes_free_cash_flow(self):
        """EDGAR should compute FCF from OCF - abs(capex)."""
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        company_facts = {
            "facts": {
                "us-gaap": {
                    "NetCashProvidedByUsedInOperatingActivities": {
                        "units": {
                            "USD": [
                                {"form": "10-Q", "end": "2024-06-30", "val": 10000000, "fp": "Q2"},
                            ]
                        }
                    },
                    "PaymentsToAcquirePropertyPlantAndEquipment": {
                        "units": {
                            "USD": [
                                {"form": "10-Q", "end": "2024-06-30", "val": 3000000, "fp": "Q2"},
                            ]
                        }
                    },
                }
            }
        }
        records = extract_edgar_fundamentals(company_facts, "AAPL")
        fcf_recs = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf_recs) == 1
        # 10000000 - abs(3000000) = 7000000
        assert fcf_recs[0]["field_value"] == 7000000


class TestFinnhubComputedFields:
    """Tests for Finnhub computed EBITDA and free_cash_flow."""

    def test_finnhub_computes_ebitda(self):
        """Finnhub should compute EBITDA from operating_income + depreciation."""
        from modules.input.finnhub_downloader import extract_finnhub_fundamentals

        reports = {
            "quarterly": [
                {
                    "endDate": "2024-09-30",
                    "report": {
                        "ic": [
                            {"concept": "operatingIncome", "value": 5000000},
                            {"concept": "netIncome", "value": 3000000},
                        ],
                        "bs": [],
                        "cf": [
                            {"concept": "depreciationAmortization", "value": 1200000},
                        ],
                    },
                }
            ],
            "annual": [],
        }
        records = extract_finnhub_fundamentals(reports, "HSBA.L", currency="GBP")
        ebitda_recs = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda_recs) == 1
        assert ebitda_recs[0]["field_value"] == 6200000

    def test_finnhub_computes_free_cash_flow(self):
        """Finnhub should compute FCF from OCF - abs(capex)."""
        from modules.input.finnhub_downloader import extract_finnhub_fundamentals

        reports = {
            "quarterly": [
                {
                    "endDate": "2024-09-30",
                    "report": {
                        "ic": [],
                        "bs": [],
                        "cf": [
                            {"concept": "operatingCashflow", "value": 10000000},
                            {"concept": "capitalExpenditures", "value": 3000000},
                        ],
                    },
                }
            ],
            "annual": [],
        }
        records = extract_finnhub_fundamentals(reports, "HSBA.L", currency="GBP")
        fcf_recs = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf_recs) == 1
        assert fcf_recs[0]["field_value"] == 7000000


class TestRiskFreeRateRecordModel:

    def test_valid_construction(self):
        from modules.data_models.models import RiskFreeRateRecord

        r = RiskFreeRateRecord(cob_date=date(2024, 1, 2), rate_pct=5.22)
        assert r.rate_pct == 5.22
        assert r.series_id == "DGS3MO"

    def test_dot_coerces_to_none(self):
        from modules.data_models.models import RiskFreeRateRecord

        r = RiskFreeRateRecord(cob_date=date(2024, 1, 2), rate_pct=".")
        assert r.rate_pct is None

    def test_nan_coerces_to_none(self):
        from modules.data_models.models import RiskFreeRateRecord

        r = RiskFreeRateRecord(cob_date=date(2024, 1, 2), rate_pct=float("nan"))
        assert r.rate_pct is None

    def test_none_rate_allowed(self):
        from modules.data_models.models import RiskFreeRateRecord

        r = RiskFreeRateRecord(cob_date=date(2024, 1, 2), rate_pct=None)
        assert r.rate_pct is None


# ── DataQualityChecker extra coverage ─────────────────────────────────


class TestDataQualityFxChecks:

    def test_fx_null_close_detected(self):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("fx")
        records = [
            {"close_rate": None, "currency_pair": "GBPUSD=X"},
            {"close_rate": 1.25, "currency_pair": "GBPUSD=X"},
        ]
        report = dq.check_fx_records(records)
        assert report["null_close"] == 1
        assert len(report["issues"]) == 1

    def test_fx_non_positive_rate_detected(self):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("fx")
        records = [
            {"close_rate": -0.5, "currency_pair": "GBPUSD=X"},
            {"close_rate": 0, "currency_pair": "EURUSD=X"},
        ]
        report = dq.check_fx_records(records)
        assert report["non_positive_rate"] == 2

    def test_fundamentals_empty_returns_zero(self):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("fundamentals")
        report = dq.check_fundamentals_records([])
        assert report["total"] == 0

    def test_log_report_with_issues_logs_warning(self):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("test")
        report = {"total": 5, "issues": ["5 records have NULL close"]}
        dq.log_report(report, "AAPL")  # should not raise

    def test_log_report_no_issues_logs_debug(self):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("test")
        report = {"total": 5, "issues": []}
        dq.log_report(report, "AAPL")  # should not raise

    def test_log_report_no_symbol(self):
        from modules.processing.data_quality import DataQualityChecker

        dq = DataQualityChecker("test")
        report = {"total": 0, "issues": []}
        dq.log_report(report)  # no symbol — should not raise
