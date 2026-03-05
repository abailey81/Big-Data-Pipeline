"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Data cleaning, validation, and transformation
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Cleaning pipeline: Yahoo Finance → Pydantic validation → dict for upsert.
Handles all data quality issues from Spec §7.2 Issue 6.

"""

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from modules.data_models.models import DailyPrice, FundamentalRecord, FxRate, RiskFreeRateRecord, VixRecord
from modules.utils.info_logger import pipeline_logger

# ── Coercion Helpers ────────────────────────────────────────────────────


def _safe_float(val) -> Optional[float]:
    """Safely convert a value to float, returning None for NaN/invalid.

    :param val: Input value (any type)
    :return: Float value or None if conversion fails or NaN
    :rtype: Optional[float]
    """
    if val is None:
        return None
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    """Safely convert a value to int, returning None for NaN/invalid.

    :param val: Input value (any type)
    :return: Integer value or None if conversion fails
    :rtype: Optional[int]
    """
    f = _safe_float(val)
    if f is None:
        return None
    return int(f)


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten multi-level columns from yfinance downloads.

    Detects which MultiIndex level contains OHLCV field names
    (Open, High, Low, Close, Volume) and uses that level.
    Handles both single-ticker downloads (level 0 = fields) and
    multi-ticker batch downloads (level -1 = fields).

    :param df: DataFrame potentially with MultiIndex columns
    :type df: pd.DataFrame
    :return: DataFrame with single-level lowercase column names
    :rtype: pd.DataFrame
    """
    if isinstance(df.columns, pd.MultiIndex):
        ohlcv = {"Close", "Open", "High", "Low", "Volume", "Adj Close"}
        picked = -1
        for level_idx in range(df.columns.nlevels):
            level_vals = {str(v) for v in df.columns.get_level_values(level_idx)}
            if level_vals & ohlcv:
                picked = level_idx
                break
        df.columns = df.columns.get_level_values(picked)
    col_map = {col: str(col).lower().replace(" ", "_") for col in df.columns}
    return df.rename(columns=col_map)


# ── Price Data Cleaning ─────────────────────────────────────────────────


def clean_price_dataframe(df: pd.DataFrame, db_symbol: str, currency: str) -> list[dict]:
    """Clean a price DataFrame downloaded from Yahoo Finance.

    Handles both single-ticker and multi-level column formats.
    Validates each row using the DailyPrice Pydantic model.
    Skips invalid rows rather than crashing (Spec §7.2 Issue 4).

    :param df: Raw price DataFrame from yfinance
    :type df: pd.DataFrame
    :param db_symbol: Database symbol for this ticker
    :type db_symbol: str
    :param currency: Inferred 3-letter ISO currency code
    :type currency: str
    :return: List of validated price record dictionaries
    :rtype: list[dict]
    """
    if df is None or df.empty:
        return []

    df = _flatten_columns(df)
    records = []

    for idx, row in df.iterrows():
        try:
            cob = idx.date() if hasattr(idx, "date") else idx
            record = DailyPrice(
                symbol=db_symbol,
                cob_date=cob,
                open_price=_safe_float(row.get("open")),
                high_price=_safe_float(row.get("high")),
                low_price=_safe_float(row.get("low")),
                close_price=_safe_float(row.get("close")),
                adj_close_price=_safe_float(row.get("adj_close", row.get("adj close"))),
                volume=_safe_int(row.get("volume")),
                currency=currency,
            )
            records.append(record.model_dump())
        except Exception as e:
            pipeline_logger.warning(f"Skipping invalid price row for {db_symbol} on {idx}: {e}")
    return records


# ── Fundamentals Data Cleaning ──────────────────────────────────────────

# Map of spec-required fields to their yfinance balance sheet / income names.
# yfinance field naming is inconsistent across versions; we try multiple aliases.
# yfinance 2.x uses CamelCase (e.g. StockholdersEquity);
# older versions used spaces (e.g. Stockholders Equity).
# List CamelCase first so it matches the current yfinance API.
BALANCE_SHEET_FIELDS = {
    "stockholders_equity": [
        "StockholdersEquity",
        "CommonStockEquity",
        "TotalEquityGrossMinorityInterest",
        "Total Stockholders Equity",
        "Stockholders Equity",
        "Common Stock Equity",
        "Total Equity Gross Minority Interest",
    ],
    "total_debt": [
        "TotalDebt",
        "NetDebt",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligation",
        "Total Debt",
        "Net Debt",
        "Long Term Debt",
        "Long Term Debt And Capital Lease Obligation",
    ],
    "total_assets": [
        "TotalAssets",
        "Total Assets",
    ],
    "total_liabilities": [
        "TotalLiabilitiesNetMinorityInterest",
        "TotalNonCurrentLiabilitiesNetMinorityInterest",
        "Total Liabilities Net Minority Interest",
        "Total Non Current Liabilities Net Minority Interest",
    ],
    "book_value": [
        "TangibleBookValue",
        "StockholdersEquity",
        "CommonStockEquity",
        "Total Stockholders Equity",
        "Stockholders Equity",
        "Common Stock Equity",
    ],
}

INCOME_STMT_FIELDS = {
    "net_income": [
        "NetIncome",
        "NetIncomeCommonStockholders",
        "Net Income",
        "Net Income Common Stockholders",
    ],
    "total_revenue": [
        "TotalRevenue",
        "OperatingRevenue",
        "Total Revenue",
        "Operating Revenue",
    ],
    "ebitda": [
        "EBITDA",
        "NormalizedEBITDA",
        "ReconciledEBITDA",
        "Normalized EBITDA",
        "Reconciled EBITDA",
    ],
    "basic_eps": [
        "BasicEPS",
        "Basic EPS",
    ],
    "diluted_eps": [
        "DilutedEPS",
        "Diluted EPS",
    ],
    "operating_income": [
        "OperatingIncome",
        "OperatingExpense",
        "Operating Income",
        "Operating Expense",
    ],
    "gross_profit": [
        "GrossProfit",
        "Gross Profit",
    ],
}

CASH_FLOW_FIELDS = {
    "operating_cash_flow": [
        "OperatingCashFlow",
        "CashFlowFromContinuingOperatingActivities",
        "Operating Cash Flow",
        "Cash Flow From Continuing Operating Activities",
    ],
    "capital_expenditure": [
        "CapitalExpenditure",
        "PurchaseOfPPE",
        "Capital Expenditure",
    ],
    "free_cash_flow": [
        "FreeCashFlow",
        "Free Cash Flow",
    ],
}


def _extract_field_from_statement(
    stmt_df: pd.DataFrame, col_date, field_aliases: list[str]
) -> Optional[float]:
    """Try multiple field name aliases to extract a value from a financial statement.

    :param stmt_df: Financial statement DataFrame (fields as index, dates as columns)
    :param col_date: Column date to extract from
    :param field_aliases: List of field name aliases to try in order
    :return: Extracted float value or None
    :rtype: Optional[float]
    """
    for alias in field_aliases:
        if alias in stmt_df.index:
            return _safe_float(stmt_df.loc[alias, col_date])
    return None


# Depreciation aliases used for EBITDA computation fallback
_DEPRECIATION_ALIASES = [
    "ReconciledDepreciation",
    "DepreciationAndAmortization",
    "DepreciationAmortizationDepletion",
    "Depreciation",
    "Reconciled Depreciation",
    "Depreciation And Amortization",
    "Depreciation Amortization Depletion",
]

_OPERATING_INCOME_ALIASES = [
    "OperatingIncome",
    "Operating Income",
]


def _compute_ebitda_fallback(stmt_df: pd.DataFrame, col_date) -> Optional[float]:
    """Compute EBITDA from Operating Income + Depreciation when direct field is missing.

    EBITDA = Operating Income + Depreciation & Amortisation.
    Depreciation is often reported as a negative value in yfinance,
    so we take the absolute value before adding.

    :param stmt_df: Income statement DataFrame (fields as index, dates as columns)
    :param col_date: Column date to extract from
    :return: Computed EBITDA or None if components are unavailable
    :rtype: Optional[float]
    """
    op_inc = _extract_field_from_statement(stmt_df, col_date, _OPERATING_INCOME_ALIASES)
    depreciation = _extract_field_from_statement(stmt_df, col_date, _DEPRECIATION_ALIASES)
    if op_inc is not None and depreciation is not None:
        return op_inc + abs(depreciation)
    return None


def _process_statement(
    stmt_df: pd.DataFrame, field_map: dict, db_symbol: str, currency: str, period_type: str, stmt_label: str
) -> list[dict]:
    """Extract records from a single financial statement DataFrame.

    :param stmt_df: Financial statement (fields as index, dates as columns)
    :param field_map: Mapping of canonical names to alias lists
    :param db_symbol: Database symbol
    :param currency: Currency code
    :param period_type: 'quarterly' or 'annual'
    :param stmt_label: Label for logging (e.g. 'BS', 'IS', 'CF')
    :return: List of validated record dicts
    """
    records = []
    if stmt_df is None or not isinstance(stmt_df, pd.DataFrame) or stmt_df.empty:
        return records
    for col_date in stmt_df.columns:
        report_date = col_date.date() if hasattr(col_date, "date") else col_date
        for canonical_name, aliases in field_map.items():
            val = _extract_field_from_statement(stmt_df, col_date, aliases)
            # Computed EBITDA fallback: Operating Income + Depreciation
            if canonical_name == "ebitda" and val is None:
                val = _compute_ebitda_fallback(stmt_df, col_date)
            try:
                record = FundamentalRecord(
                    symbol=db_symbol,
                    report_date=report_date,
                    field_name=canonical_name,
                    field_value=val,
                    period_type=period_type,
                    currency=currency,
                )
                records.append(record.model_dump())
            except Exception as e:
                pipeline_logger.debug(f"Skip {stmt_label} {canonical_name} for {db_symbol}: {e}")
    return records


def clean_fundamentals_data(fund_data: dict, db_symbol: str, currency: str = None) -> list[dict]:
    """Extract and clean fundamental data from the downloader result.

    Processes annual (~5 years) and quarterly (~6-7 quarters) balance
    sheets, income statements, and cash flows into normalised EAV
    records. Also extracts book_value_per_share from ticker.info.

    :param fund_data: Dict with annual/quarterly statement DataFrames and info
    :type fund_data: dict
    :param db_symbol: Database symbol for this ticker
    :type db_symbol: str
    :param currency: Optional currency code for this ticker
    :type currency: str or None
    :return: List of validated fundamental record dictionaries
    :rtype: list[dict]
    """
    records = []

    # ── Statement processing config: (data_key, field_map, label) ──
    statement_configs = [
        ("annual_balance_sheet", BALANCE_SHEET_FIELDS, "annual", "BS-A"),
        ("annual_income_stmt", INCOME_STMT_FIELDS, "annual", "IS-A"),
        ("annual_cash_flow", CASH_FLOW_FIELDS, "annual", "CF-A"),
        ("quarterly_balance_sheet", BALANCE_SHEET_FIELDS, "quarterly", "BS-Q"),
        ("quarterly_income_stmt", INCOME_STMT_FIELDS, "quarterly", "IS-Q"),
        ("quarterly_cash_flow", CASH_FLOW_FIELDS, "quarterly", "CF-Q"),
    ]

    for data_key, field_map, period_type, label in statement_configs:
        stmt_df = fund_data.get(data_key)
        records.extend(_process_statement(stmt_df, field_map, db_symbol, currency, period_type, label))

    # ── Backwards compatibility: handle old-format data with ──
    # ── 'balance_sheet' / 'income_stmt' keys (no prefix)     ──
    if "balance_sheet" in fund_data and "annual_balance_sheet" not in fund_data:
        records.extend(
            _process_statement(
                fund_data.get("balance_sheet"), BALANCE_SHEET_FIELDS, db_symbol, currency, "quarterly", "BS"
            )
        )
    if "income_stmt" in fund_data and "annual_income_stmt" not in fund_data:
        records.extend(
            _process_statement(
                fund_data.get("income_stmt"), INCOME_STMT_FIELDS, db_symbol, currency, "quarterly", "IS"
            )
        )

    # ── Comprehensive ticker.info TTM fallback for ALL fundamental fields ──
    # yfinance ticker.info provides trailing-twelve-month (TTM) values for
    # most fundamental fields. These fill current-period gaps where the
    # financial statement DataFrames lack data (common for non-US tickers).
    info = fund_data.get("info", {})
    if info and isinstance(info, dict):
        # Map of ticker.info keys → canonical field names
        info_field_map = {
            "bookValue": "book_value_per_share",
            "ebitda": "ebitda",
            "totalRevenue": "total_revenue",
            "revenue": "total_revenue",
            "netIncomeToCommon": "net_income",
            "operatingCashflow": "operating_cash_flow",
            "freeCashflow": "free_cash_flow",
            "totalDebt": "total_debt",
            "totalAssets": "total_assets",
            "grossProfits": "gross_profit",
            "operatingIncome": "operating_income",
            "trailingEps": "diluted_eps",
            "forwardEps": "basic_eps",
            "stockholdersEquity": "stockholders_equity",
            "totalLiab": "total_liabilities",
        }

        # Build a set of (field_name, report_date) that already have non-NULL values
        today = date.today()
        existing_today = set()
        for r in records:
            if r.get("report_date") == today and r.get("field_value") is not None:
                existing_today.add(r["field_name"])

        for info_key, canonical_name in info_field_map.items():
            if canonical_name in existing_today:
                continue  # Already have a non-NULL value for today
            val = _safe_float(info.get(info_key))
            if val is not None:
                try:
                    record = FundamentalRecord(
                        symbol=db_symbol,
                        report_date=today,
                        field_name=canonical_name,
                        field_value=val,
                        currency=currency,
                    )
                    records.append(record.model_dump())
                    existing_today.add(canonical_name)
                except Exception:
                    pass

    # ── Computed free_cash_flow from operating_cash_flow - capex ──
    # For each (report_date, period_type) where free_cash_flow is NULL but
    # both components exist, compute the derived value.
    record_index = {}
    for r in records:
        k = (r["field_name"], r["report_date"], r.get("period_type", "quarterly"))
        if r.get("field_value") is not None:
            record_index[k] = r["field_value"]

    all_periods = set()
    for r in records:
        all_periods.add((r["report_date"], r.get("period_type", "quarterly")))

    for report_date, period_type in all_periods:
        fcf_key = ("free_cash_flow", report_date, period_type)
        if fcf_key in record_index:
            continue
        ocf = record_index.get(("operating_cash_flow", report_date, period_type))
        capex = record_index.get(("capital_expenditure", report_date, period_type))
        if ocf is not None and capex is not None:
            fcf_val = ocf - abs(capex)
            try:
                record = FundamentalRecord(
                    symbol=db_symbol,
                    report_date=report_date,
                    field_name="free_cash_flow",
                    field_value=fcf_val,
                    period_type=period_type,
                    currency=currency,
                )
                records.append(record.model_dump())
            except Exception:
                pass

    return records


# ── FX Data Cleaning ────────────────────────────────────────────────────


def clean_fx_dataframe(df: pd.DataFrame, currency_pair: str) -> list[dict]:
    """Clean an FX rate DataFrame downloaded from Yahoo Finance.

    :param df: Raw FX rate DataFrame from yfinance
    :type df: pd.DataFrame
    :param currency_pair: Currency pair identifier (e.g. 'GBPUSD=X')
    :type currency_pair: str
    :return: List of validated FX rate record dictionaries
    :rtype: list[dict]
    """
    if df is None or df.empty:
        return []

    df = _flatten_columns(df)
    records = []

    for idx, row in df.iterrows():
        try:
            cob = idx.date() if hasattr(idx, "date") else idx
            close = _safe_float(row.get("close"))
            # Skip rows where close is NULL — this indicates a failed/partial
            # download (e.g. 401 crumb error) rather than a valid trading day.
            if close is None:
                pipeline_logger.debug(f"Skipping FX row for {currency_pair} on {idx}: NULL close")
                continue
            record = FxRate(
                currency_pair=currency_pair,
                cob_date=cob,
                open_rate=_safe_float(row.get("open")),
                high_rate=_safe_float(row.get("high")),
                low_rate=_safe_float(row.get("low")),
                close_rate=close,
            )
            records.append(record.model_dump())
        except Exception as e:
            pipeline_logger.warning(f"Skipping FX row for {currency_pair} on {idx}: {e}")
    return records


# ── VIX Data Cleaning ───────────────────────────────────────────────────


def clean_vix_dataframe(df: pd.DataFrame) -> list[dict]:
    """Clean a VIX DataFrame downloaded from Yahoo Finance.

    :param df: Raw VIX DataFrame from yfinance
    :type df: pd.DataFrame
    :return: List of validated VIX record dictionaries
    :rtype: list[dict]
    """
    if df is None or df.empty:
        return []

    df = _flatten_columns(df)
    records = []

    for idx, row in df.iterrows():
        try:
            cob = idx.date() if hasattr(idx, "date") else idx
            record = VixRecord(
                cob_date=cob,
                open_price=_safe_float(row.get("open")),
                high_price=_safe_float(row.get("high")),
                low_price=_safe_float(row.get("low")),
                close_price=_safe_float(row.get("close")),
                adj_close_price=_safe_float(row.get("adj_close")),
                volume=_safe_int(row.get("volume")),
            )
            records.append(record.model_dump())
        except Exception as e:
            pipeline_logger.warning(f"Skipping VIX row on {idx}: {e}")
    return records


# ── Risk-Free Rate Data Cleaning ─────────────────────────────────────


def clean_risk_free_rate_dataframe(df: pd.DataFrame, series_id: str = "DGS3MO") -> list[dict]:
    """Clean a risk-free rate DataFrame downloaded from FRED CSV endpoint.

    FRED returns columns 'DATE' and the series name (e.g. 'DGS3MO').
    Missing values are represented as '.' which the Pydantic validator
    coerces to None.

    :param df: Raw rate DataFrame from FRED
    :type df: pd.DataFrame
    :param series_id: FRED series identifier
    :type series_id: str
    :return: List of validated risk-free rate record dictionaries
    :rtype: list[dict]
    """
    if df is None or df.empty:
        return []

    records = []
    date_col = "DATE" if "DATE" in df.columns else df.columns[0]
    rate_col = series_id if series_id in df.columns else df.columns[-1]

    for _, row in df.iterrows():
        try:
            cob = pd.to_datetime(row[date_col]).date()
            record = RiskFreeRateRecord(
                cob_date=cob,
                rate_pct=row.get(rate_col),
                series_id=series_id,
            )
            records.append(record.model_dump())
        except Exception as e:
            pipeline_logger.warning(f"Skipping risk-free rate row on {row.get(date_col)}: {e}")
    return records
