"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : SimFin fundamentals downloader for non-US tickers
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads quarterly and annual financial statements from SimFin's free
API (v3).  Used to supplement Yahoo Finance for non-US tickers (.L, .PA,
.DE, .MI, .AS, .TO, .SW) which often have limited quarterly data from
yfinance.

SimFin returns data in a compact format: a ``columns`` array of field
names plus a ``data`` array of value rows.  This module unpacks that
format into canonical EAV records.

Rate limits (free tier): 2000 requests/day, generous per-second allowance.

Requires a free SimFin API key (https://simfin.com/).

"""

import os
import time
from datetime import datetime
from typing import Optional

import requests

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

SIMFIN_BASE = "https://backend.simfin.com/api/v3"

# Fiscal years to request (covers the full pipeline backfill window)
_FYEARS = "2018,2019,2020,2021,2022,2023,2024,2025,2026"

# ── SimFin field name → canonical field name mappings ───────────────

# SimFin uses human-readable column headers in compact format
SIMFIN_FIELD_MAP = {
    # Income statement (pl)
    "Revenue": "total_revenue",
    "Net Income": "net_income",
    "Gross Profit": "gross_profit",
    "Operating Income (EBIT)": "operating_income",
    "EBITDA": "ebitda",
    "Earnings Per Share, Basic": "basic_eps",
    "Earnings Per Share, Diluted": "diluted_eps",

    # Balance sheet (bs)
    "Total Equity": "stockholders_equity",
    "Total Assets": "total_assets",
    "Total Liabilities": "total_liabilities",
    "Book Value per Share": "book_value",

    # Cash flow (cf)
    "Net Cash from Operating Activities": "operating_cash_flow",
    "Capital Expenditures": "capital_expenditure",
    "Free Cash Flow": "free_cash_flow",
    "Depreciation & Amortization": "_depreciation",
}

# SimFin does not have a single "Total Debt" column — we sum
# short-term + long-term debt columns when available.
_DEBT_SHORT_COLS = {
    "Short Term Debt",
    "Short-Term Debt",
    "Current Debt",
}
_DEBT_LONG_COLS = {
    "Long Term Debt",
    "Long-Term Debt",
    "Non-Current Debt",
}

# Date columns SimFin may use for the report period end date
_DATE_COLUMNS = {"Report Date", "Fiscal Period End", "Period End Date", "date"}

# Currency column
_CURRENCY_COLUMNS = {"Currency", "currency"}

# Period type column
_PERIOD_COLUMNS = {"Fiscal Period", "Period", "period"}


def _simfin_ticker(db_symbol: str) -> str:
    """Convert DB symbol to SimFin ticker format.

    SimFin uses plain symbols for US tickers (AAPL) and may or may
    not support suffixed non-US tickers.  For non-US tickers we try
    the full suffixed symbol first; callers may retry with the base
    symbol if no data is returned.

    :param db_symbol: Database ticker (e.g. 'HSBA.L', 'AAPL')
    :return: SimFin ticker
    """
    return db_symbol.strip()


def _simfin_base_ticker(db_symbol: str) -> str:
    """Return the base symbol without exchange suffix.

    :param db_symbol: Database ticker (e.g. 'HSBA.L')
    :return: Base symbol (e.g. 'HSBA')
    """
    symbol = db_symbol.strip()
    if "." in symbol:
        return symbol.rsplit(".", 1)[0]
    return symbol


class SimFinFundamentalsDownloader(BaseDownloader):
    """Downloads financial statements from SimFin API (v3).

    Fetches profit & loss, balance sheet, and cash flow statements in
    compact format for both quarterly and annual periods.  Protected
    by a circuit breaker and token-bucket rate limiter.

    :param api_key: SimFin API key (defaults to SIMFIN_API_KEY env var)
    :type api_key: str
    :param api_delay: Delay between API calls in seconds
    :type api_delay: float
    :param max_retries: Maximum retry attempts per request
    :type max_retries: int
    :param backoff_base: Exponential backoff base multiplier
    :type backoff_base: float
    :param circuit_breaker: Optional pre-configured circuit breaker
    :type circuit_breaker: CircuitBreaker or None
    :param rate_limiter: Optional pre-configured rate limiter
    :type rate_limiter: TokenBucketRateLimiter or None
    """

    def __init__(
        self,
        api_key: str = None,
        api_delay: float = 0.5,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="simfin_fundamentals",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=15,
            cb_recovery_timeout=60.0,
        )
        self.api_key = api_key or os.environ.get("SIMFIN_API_KEY", "")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"api-key {self.api_key}",
                "Accept": "application/json",
            }
        )

    # ── Internal helpers ────────────────────────────────────────────

    def _fetch_statements(
        self, ticker: str, period: str
    ) -> Optional[dict]:
        """Fetch combined statements from SimFin compact endpoint.

        :param ticker: SimFin ticker symbol
        :param period: 'quarterly' or 'annual'
        :return: Raw JSON response dict or None on failure
        """
        url = f"{SIMFIN_BASE}/companies/statements/compact"
        params = {
            "ticker": ticker,
            "statements": "pl,bs,cf",
            "period": period,
            "fyear": _FYEARS,
        }

        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()

        data = resp.json()

        # SimFin v3 returns a list with one element per company,
        # or an error dict / empty list on no data
        if isinstance(data, list) and len(data) > 0:
            return data[0] if isinstance(data[0], dict) else None
        if isinstance(data, dict):
            # Single company response
            if "columns" in data or "statements" in data:
                return data
            if "error" in data:
                pipeline_logger.debug(
                    f"SimFin error for {ticker}/{period}: {data.get('error')}"
                )
                return None
        return None

    def _execute_download(self, **kwargs):
        """Execute download (required by BaseDownloader ABC).

        Not used directly — the ``download()`` method orchestrates
        the full workflow with retry logic.
        """
        return None

    def _fetch_with_retries(
        self, ticker: str, period: str
    ) -> Optional[dict]:
        """Fetch statements with retry and circuit breaker logic.

        :param ticker: SimFin ticker
        :param period: 'quarterly' or 'annual'
        :return: Raw JSON response or None
        """
        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                data = self._fetch_statements(ticker, period)
                self.circuit_breaker.record_success()
                return data
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (401, 403):
                    pipeline_logger.debug(
                        f"SimFin {status} for {ticker} — "
                        f"invalid key or no access"
                    )
                    return None
                if status == 429:
                    pipeline_logger.debug(
                        f"SimFin 429 rate limit for {ticker}, waiting"
                    )
                    time.sleep(5)
                    continue
                pipeline_logger.warning(
                    f"SimFin retry {attempt + 1}/{self.max_retries} for "
                    f"{ticker} ({period}): HTTP {status}"
                )
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    return None
                self._jitter_wait(attempt)
            except Exception as e:
                pipeline_logger.warning(
                    f"SimFin retry {attempt + 1}/{self.max_retries} for "
                    f"{ticker} ({period}): {e}"
                )
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    return None
                self._jitter_wait(attempt)

        return None

    # ── Public API ──────────────────────────────────────────────────

    def download(
        self, db_symbol: str, yf_ticker: str = None
    ) -> list[dict]:
        """Download quarterly + annual fundamentals for one ticker.

        Tries the full suffixed ticker first; if no data is returned,
        falls back to the base symbol (without exchange suffix).
        Returns a flat list of EAV fundamental records ready for
        ``db_client.upsert_fundamentals()``.

        :param db_symbol: Database symbol (e.g. 'HSBA.L')
        :param yf_ticker: yfinance ticker (unused, for interface compat)
        :return: List of fundamental record dicts
        """
        self._download_count += 1

        if not self.api_key:
            pipeline_logger.debug("SimFin API key not set — skipping")
            self._failure_count += 1
            return []

        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping SimFin download")
            self._failure_count += 1
            return []

        # Try full ticker first, then base symbol for non-US
        ticker_candidates = [_simfin_ticker(db_symbol)]
        base = _simfin_base_ticker(db_symbol)
        if base != ticker_candidates[0]:
            ticker_candidates.append(base)

        all_records = []

        for sf_ticker in ticker_candidates:
            found_data = False

            for period, period_type in [
                ("quarterly", "quarterly"),
                ("annual", "annual"),
            ]:
                raw = self._fetch_with_retries(sf_ticker, period)
                if not raw:
                    continue

                records = _extract_simfin_records(raw, db_symbol, period_type)
                if records:
                    found_data = True
                    all_records.extend(records)

            if found_data:
                break  # Found data with this ticker variant

        if all_records:
            self._success_count += 1
            pipeline_logger.debug(
                f"SimFin extracted {len(all_records)} records for {db_symbol}"
            )
        else:
            self._failure_count += 1

        return all_records


# ── Record extraction (module-level for testability) ────────────────


def _unpack_compact(raw: dict) -> list[dict]:
    """Unpack SimFin compact format into a list of row dicts.

    SimFin compact format has ``columns`` (list of field names) and
    ``data`` (list of value lists).  Some responses nest statements
    under a ``statements`` key.

    :param raw: Raw SimFin API response
    :return: List of dicts, one per row
    """
    rows = []

    # Case 1: top-level columns + data
    if "columns" in raw and "data" in raw:
        columns = raw["columns"]
        for row_vals in raw["data"]:
            if len(row_vals) == len(columns):
                rows.append(dict(zip(columns, row_vals)))
        return rows

    # Case 2: nested under 'statements' (list of statement objects)
    statements = raw.get("statements", [])
    if isinstance(statements, list):
        for stmt in statements:
            if isinstance(stmt, dict) and "columns" in stmt and "data" in stmt:
                columns = stmt["columns"]
                for row_vals in stmt["data"]:
                    if len(row_vals) == len(columns):
                        rows.append(dict(zip(columns, row_vals)))

    # Case 3: single statement dict at top level with statement-type keys
    if not rows:
        for key in ("pl", "bs", "cf"):
            stmt = raw.get(key)
            if isinstance(stmt, dict) and "columns" in stmt and "data" in stmt:
                columns = stmt["columns"]
                for row_vals in stmt["data"]:
                    if len(row_vals) == len(columns):
                        rows.append(dict(zip(columns, row_vals)))

    return rows


def _find_column_value(row: dict, candidates: set) -> Optional[str]:
    """Find the first matching column value from a set of candidates.

    :param row: Row dict from unpacked compact data
    :param candidates: Set of candidate column names
    :return: Value string or None
    """
    for col in candidates:
        val = row.get(col)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _extract_simfin_records(
    raw: dict,
    db_symbol: str,
    period_type: str,
    start_date: str = None,
) -> list[dict]:
    """Extract EAV fundamental records from SimFin compact data.

    :param raw: Raw SimFin API response dict
    :param db_symbol: Database symbol for output records
    :param period_type: 'quarterly' or 'annual'
    :param start_date: Optional cutoff date (YYYY-MM-DD)
    :return: List of fundamental record dicts
    """
    if not raw:
        return []

    rows = _unpack_compact(raw)
    if not rows:
        return []

    cutoff = None
    if start_date:
        cutoff = datetime.strptime(start_date, "%Y-%m-%d").date()

    seen = set()
    records = []

    for row in rows:
        # Determine report date
        date_str = _find_column_value(row, _DATE_COLUMNS)
        if not date_str:
            continue

        try:
            report_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        if cutoff and report_date < cutoff:
            continue

        # Determine currency
        currency = _find_column_value(row, _CURRENCY_COLUMNS) or "USD"

        # Detect period type from row if available (override default)
        row_period = _find_column_value(row, _PERIOD_COLUMNS)
        effective_period = period_type
        if row_period:
            rp_lower = row_period.lower()
            if rp_lower.startswith("q") or "quarter" in rp_lower:
                effective_period = "quarterly"
            elif "annual" in rp_lower or rp_lower in ("fy", "full-year", "full year"):
                effective_period = "annual"

        # Extract mapped fields
        for simfin_field, canonical in SIMFIN_FIELD_MAP.items():
            val = row.get(simfin_field)
            if val is None:
                continue

            key = (canonical, report_date, effective_period)
            if key in seen:
                continue
            seen.add(key)

            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue

            records.append(
                {
                    "symbol": db_symbol,
                    "report_date": report_date,
                    "field_name": canonical,
                    "field_value": fval,
                    "period_type": effective_period,
                    "currency": currency,
                }
            )

        # Handle total_debt = short-term debt + long-term debt
        debt_key = ("total_debt", report_date, effective_period)
        if debt_key not in seen:
            short_debt = None
            long_debt = None
            for col in _DEBT_SHORT_COLS:
                v = row.get(col)
                if v is not None:
                    try:
                        short_debt = float(v)
                    except (ValueError, TypeError):
                        pass
                    break
            for col in _DEBT_LONG_COLS:
                v = row.get(col)
                if v is not None:
                    try:
                        long_debt = float(v)
                    except (ValueError, TypeError):
                        pass
                    break

            if short_debt is not None or long_debt is not None:
                total_debt = (short_debt or 0.0) + (long_debt or 0.0)
                seen.add(debt_key)
                records.append(
                    {
                        "symbol": db_symbol,
                        "report_date": report_date,
                        "field_name": "total_debt",
                        "field_value": total_debt,
                        "period_type": effective_period,
                        "currency": currency,
                    }
                )

    # ── Post-processing: compute EBITDA and FCF if missing ──────

    record_lookup = {}
    for r in records:
        k = (r["field_name"], r["report_date"], r["period_type"])
        record_lookup[k] = r["field_value"]

    all_periods = set()
    for r in records:
        all_periods.add(
            (r["report_date"], r["period_type"], r.get("currency", "USD"))
        )

    for report_date, pt, cur in all_periods:
        # Compute EBITDA: operating_income + abs(depreciation)
        ebitda_key = ("ebitda", report_date, pt)
        if ebitda_key not in record_lookup:
            op_inc = record_lookup.get(("operating_income", report_date, pt))
            dep = record_lookup.get(("_depreciation", report_date, pt))
            if op_inc is not None and dep is not None:
                ebitda_val = float(op_inc) + abs(float(dep))
                if ebitda_key not in seen:
                    seen.add(ebitda_key)
                    records.append(
                        {
                            "symbol": db_symbol,
                            "report_date": report_date,
                            "field_name": "ebitda",
                            "field_value": ebitda_val,
                            "period_type": pt,
                            "currency": cur,
                        }
                    )
                    record_lookup[ebitda_key] = ebitda_val

        # Compute free_cash_flow: operating_cash_flow - abs(capex)
        fcf_key = ("free_cash_flow", report_date, pt)
        if fcf_key not in record_lookup:
            ocf = record_lookup.get(("operating_cash_flow", report_date, pt))
            capex = record_lookup.get(("capital_expenditure", report_date, pt))
            if ocf is not None and capex is not None:
                fcf_val = float(ocf) - abs(float(capex))
                if fcf_key not in seen:
                    seen.add(fcf_key)
                    records.append(
                        {
                            "symbol": db_symbol,
                            "report_date": report_date,
                            "field_name": "free_cash_flow",
                            "field_value": fcf_val,
                            "period_type": pt,
                            "currency": cur,
                        }
                    )

    # Remove internal helper fields (_depreciation) before returning
    records = [r for r in records if not r["field_name"].startswith("_")]

    return records
