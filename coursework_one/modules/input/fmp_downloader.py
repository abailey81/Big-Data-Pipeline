"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Financial Modeling Prep fundamentals downloader for non-US tickers
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads quarterly and annual financial statements from Financial Modeling
Prep (FMP) free API.  Used to supplement Yahoo Finance for non-US tickers
(.L, .PA, .DE, .MI, .AS, .TO, .SW) which often have limited quarterly
data from yfinance.

FMP provides three statement endpoints (income, balance sheet, cash flow)
and returns JSON arrays of objects with camelCase field names.

Rate limits (free tier): 250 requests/day, 5 requests/second.

Requires a free FMP API key (https://financialmodelingprep.com/).

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

FMP_BASE = "https://financialmodelingprep.com/api/v3"

# ── FMP field name → canonical field name mappings ──────────────────

INCOME_FIELD_MAP = {
    "revenue": "total_revenue",
    "netIncome": "net_income",
    "grossProfit": "gross_profit",
    "operatingIncome": "operating_income",
    "ebitda": "ebitda",
    "eps": "basic_eps",
    "epsdiluted": "diluted_eps",
}

BALANCE_FIELD_MAP = {
    "totalStockholdersEquity": "stockholders_equity",
    "totalDebt": "total_debt",
    "totalAssets": "total_assets",
    "totalLiabilities": "total_liabilities",
    "bookValuePerShare": "book_value",
}

CASHFLOW_FIELD_MAP = {
    "operatingCashFlow": "operating_cash_flow",
    "capitalExpenditure": "capital_expenditure",
    "freeCashFlow": "free_cash_flow",
    "depreciationAndAmortization": "_depreciation",
}

# Suffix mappings for Swiss tickers: yfinance .SW/.S may need
# different FMP suffixes.  FMP typically uses .SW for SIX.
_SWISS_SUFFIX_VARIANTS = [".SW", ".ZU", ".S"]


def _fmp_ticker(db_symbol: str) -> list[str]:
    """Convert DB symbol to candidate FMP ticker(s).

    Most exchanges use the same suffix as yfinance (e.g. HSBA.L).
    Swiss tickers (.SW or .S) may require alternate suffixes on FMP,
    so we return multiple candidates to try in order.

    :param db_symbol: Database ticker (e.g. 'NESN.SW', 'AAPL')
    :return: List of FMP ticker candidates to try
    """
    symbol = db_symbol.strip()

    # Swiss tickers: try several suffix variants
    for sfx in (".SW", ".S"):
        if symbol.upper().endswith(sfx.upper()):
            base = symbol[: -len(sfx)]
            candidates = [base + v for v in _SWISS_SUFFIX_VARIANTS]
            # Also try the original
            if symbol not in candidates:
                candidates.insert(0, symbol)
            return candidates

    # All other tickers: FMP uses the same format as yfinance
    return [symbol]


class FmpFundamentalsDownloader(BaseDownloader):
    """Downloads financial statements from Financial Modeling Prep API.

    Fetches income statement, balance sheet, and cash flow statement
    for both quarterly and annual periods.  Protected by a circuit
    breaker and token-bucket rate limiter.

    :param api_key: FMP API key (defaults to FMP_API_KEY env var)
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
        api_delay: float = 0.2,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="fmp_fundamentals",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=15,
            cb_recovery_timeout=60.0,
        )
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ── Internal helpers ────────────────────────────────────────────

    def _fetch_statement(
        self, endpoint: str, ticker: str, period: str, limit: int = 40
    ) -> Optional[list]:
        """Fetch a single financial statement from FMP.

        :param endpoint: Statement type (income-statement, balance-sheet-statement,
                         cash-flow-statement)
        :param ticker: FMP ticker symbol
        :param period: 'quarter' or 'annual'
        :param limit: Max number of records to return
        :return: List of statement dicts or None on failure
        """
        url = f"{FMP_BASE}/{endpoint}/{ticker}"
        params = {
            "period": period,
            "limit": limit,
            "apikey": self.api_key,
        }

        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()

        data = resp.json()

        # FMP returns an empty list or an error dict on no data
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "Error Message" in data:
            pipeline_logger.debug(
                f"FMP no data for {ticker}/{endpoint}/{period}: "
                f"{data.get('Error Message', '')}"
            )
            return None
        return None

    def _fetch_all_statements(
        self, ticker: str, period: str
    ) -> dict[str, list]:
        """Fetch income, balance sheet, and cash flow for one period type.

        :param ticker: FMP ticker symbol
        :param period: 'quarter' or 'annual'
        :return: Dict mapping statement type to list of records
        """
        statements = {}
        endpoints = [
            ("income", "income-statement"),
            ("balance", "balance-sheet-statement"),
            ("cashflow", "cash-flow-statement"),
        ]

        for stmt_key, endpoint in endpoints:
            for attempt in range(self.max_retries):
                try:
                    self.rate_limiter.acquire()
                    data = self._fetch_statement(endpoint, ticker, period)
                    self.circuit_breaker.record_success()
                    statements[stmt_key] = data or []
                    break
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else 0
                    if status == 403 or status == 401:
                        pipeline_logger.debug(
                            f"FMP {status} for {ticker}/{endpoint} — "
                            f"invalid key or no access"
                        )
                        statements[stmt_key] = []
                        break
                    if status == 429:
                        pipeline_logger.debug(
                            f"FMP 429 rate limit for {ticker}/{endpoint}, waiting"
                        )
                        time.sleep(5)
                        continue
                    pipeline_logger.warning(
                        f"FMP retry {attempt + 1}/{self.max_retries} for "
                        f"{ticker}/{endpoint}: HTTP {status}"
                    )
                    self.circuit_breaker.record_failure()
                    if not self._check_circuit():
                        return statements
                    self._jitter_wait(attempt)
                except Exception as e:
                    pipeline_logger.warning(
                        f"FMP retry {attempt + 1}/{self.max_retries} for "
                        f"{ticker}/{endpoint}: {e}"
                    )
                    self.circuit_breaker.record_failure()
                    if not self._check_circuit():
                        return statements
                    self._jitter_wait(attempt)
            else:
                statements[stmt_key] = []

        return statements

    def _execute_download(self, **kwargs):
        """Execute download (required by BaseDownloader ABC).

        Not used directly — the ``download()`` method orchestrates
        the full multi-statement workflow.
        """
        return None

    # ── Public API ──────────────────────────────────────────────────

    def download(
        self, db_symbol: str, yf_ticker: str = None
    ) -> list[dict]:
        """Download quarterly + annual fundamentals for one ticker.

        Tries multiple FMP ticker variants for Swiss tickers.
        Returns a flat list of EAV fundamental records ready for
        ``db_client.upsert_fundamentals()``.

        :param db_symbol: Database symbol (e.g. 'HSBA.L')
        :param yf_ticker: yfinance ticker (unused, for interface compat)
        :return: List of fundamental record dicts
        """
        self._download_count += 1

        if not self.api_key:
            pipeline_logger.debug("FMP API key not set — skipping")
            self._failure_count += 1
            return []

        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping FMP download")
            self._failure_count += 1
            return []

        ticker_candidates = _fmp_ticker(db_symbol)
        all_records = []

        for fmp_ticker in ticker_candidates:
            found_data = False

            for period, period_type in [("quarter", "quarterly"), ("annual", "annual")]:
                stmts = self._fetch_all_statements(fmp_ticker, period)

                records = _extract_fmp_records(stmts, db_symbol, period_type)
                if records:
                    found_data = True
                    all_records.extend(records)

            if found_data:
                break  # Found data with this ticker variant, stop trying others

        if all_records:
            self._success_count += 1
            pipeline_logger.debug(
                f"FMP extracted {len(all_records)} records for {db_symbol}"
            )
        else:
            self._failure_count += 1

        return all_records


# ── Record extraction (module-level for testability) ────────────────


def _extract_fmp_records(
    statements: dict[str, list],
    db_symbol: str,
    period_type: str,
    start_date: str = None,
) -> list[dict]:
    """Extract EAV fundamental records from FMP statement data.

    :param statements: Dict with 'income', 'balance', 'cashflow' lists
    :param db_symbol: Database symbol for the output records
    :param period_type: 'quarterly' or 'annual'
    :param start_date: Optional cutoff date (YYYY-MM-DD)
    :return: List of fundamental record dicts
    """
    if not statements:
        return []

    cutoff = None
    if start_date:
        cutoff = datetime.strptime(start_date, "%Y-%m-%d").date()

    seen = set()
    records = []

    stmt_field_maps = [
        ("income", INCOME_FIELD_MAP),
        ("balance", BALANCE_FIELD_MAP),
        ("cashflow", CASHFLOW_FIELD_MAP),
    ]

    for stmt_key, field_map in stmt_field_maps:
        stmt_list = statements.get(stmt_key, [])
        if not stmt_list:
            continue

        for item in stmt_list:
            # FMP uses 'date' for the report/filing date
            date_str = item.get("date") or item.get("filingDate")
            if not date_str:
                continue

            try:
                report_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            if cutoff and report_date < cutoff:
                continue

            # Currency from the statement (FMP includes reportedCurrency)
            currency = item.get("reportedCurrency") or item.get("currency") or "USD"

            for fmp_field, canonical in field_map.items():
                val = item.get(fmp_field)
                if val is None:
                    continue

                key = (canonical, report_date, period_type)
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
                        "period_type": period_type,
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
