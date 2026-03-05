"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Finnhub fundamentals downloader for non-US tickers
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads standardised quarterly and annual financial statements from
Finnhub's free API (60 requests/min, no daily cap).  Used to
supplement Yahoo Finance for non-US tickers (.L, .PA, .DE, .MI,
.AS, .TO, .SW) which only have ~1.5 years of quarterly data from
yfinance.

Requires a free Finnhub API key (https://finnhub.io/register).

"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Map of exchange suffixes to Finnhub exchange codes
SUFFIX_TO_EXCHANGE = {
    ".L": "L",  # London
    ".PA": "PA",  # Euronext Paris
    ".AS": "AS",  # Euronext Amsterdam
    ".DE": "DE",  # XETRA / Frankfurt
    ".MC": "MC",  # Madrid
    ".MI": "MI",  # Milan / Borsa Italiana
    ".TO": "TO",  # Toronto
    ".SW": "SW",  # SIX Swiss Exchange
    ".S": "SW",  # SIX Swiss (alternate suffix)
}

# Finnhub standardized statement fields → canonical field names
INCOME_FIELD_MAP = {
    "revenue": "total_revenue",
    "totalRevenue": "total_revenue",
    "netIncome": "net_income",
    "grossProfit": "gross_profit",
    "operatingIncome": "operating_income",
    "ebitda": "ebitda",
    "eps": "diluted_eps",
    "epsBasic": "basic_eps",
    "epsDiluted": "diluted_eps",
}

BALANCE_FIELD_MAP = {
    "totalAssets": "total_assets",
    "totalLiabilities": "total_liabilities",
    "totalEquity": "stockholders_equity",
    "totalStockholderEquity": "stockholders_equity",
    "stockholdersEquity": "stockholders_equity",
    "totalDebt": "total_debt",
    "longTermDebt": "total_debt",
    "bookValuePerShare": "book_value",
}

CASHFLOW_FIELD_MAP = {
    "operatingCashflow": "operating_cash_flow",
    "cashFromOperatingActivities": "operating_cash_flow",
    "netCashFromOperatingActivities": "operating_cash_flow",
    "capitalExpenditures": "capital_expenditure",
    "capitalExpenditure": "capital_expenditure",
    "freeCashFlow": "free_cash_flow",
    "depreciationAmortization": "_depreciation",
    "depreciation": "_depreciation",
    "depreciationAndAmortization": "_depreciation",
}

# Merged map of all statement fields for EBITDA/FCF post-processing
_ALL_FIELD_MAPS = {**INCOME_FIELD_MAP, **BALANCE_FIELD_MAP, **CASHFLOW_FIELD_MAP}


def _finnhub_ticker(db_symbol: str) -> str:
    """Convert DB symbol to Finnhub format.

    Finnhub uses the same suffixed format as Yahoo Finance for most
    exchanges, but some need adjustment.

    :param db_symbol: Database ticker (e.g. 'HSBA.L', 'TTE.PA')
    :return: Finnhub-compatible ticker
    """
    return db_symbol.strip()


def is_non_us_ticker(symbol: str) -> bool:
    """Check if a ticker is non-US (has exchange suffix).

    :param symbol: Ticker symbol
    :return: True if non-US
    """
    return "." in symbol.strip()


class FinnhubFundamentalsDownloader(BaseDownloader):
    """Downloads standardised financials from Finnhub for non-US tickers.

    Uses Finnhub's ``/stock/financials-reported`` endpoint for
    quarterly and annual statements.  Protected by a circuit breaker
    and token-bucket rate limiter (Finnhub allows 60 req/min on free).

    :param api_key: Finnhub API key
    :param api_delay: Delay between API calls
    :param max_retries: Maximum retry attempts
    :param backoff_base: Exponential backoff multiplier
    """

    def __init__(
        self,
        api_key: str,
        api_delay: float = 1.1,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="finnhub_fundamentals",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=10,
            cb_recovery_timeout=30.0,
        )
        self.api_key = api_key

    def _fetch_json(self, url: str) -> Optional[dict]:
        """Fetch JSON from Finnhub with authentication.

        :param url: Full API URL with query params
        :return: Parsed JSON dict or None on failure
        """
        separator = "&" if "?" in url else "?"
        full_url = f"{url}{separator}token={self.api_key}"
        req = urllib.request.Request(full_url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def _download_financials(self, symbol: str, freq: str = "quarterly") -> Optional[list]:
        """Download financial statements for a single ticker.

        :param symbol: Finnhub ticker symbol
        :param freq: 'quarterly' or 'annual'
        :return: List of financial reports or None
        """
        url = f"{FINNHUB_BASE}/stock/financials-reported" f"?symbol={symbol}&freq={freq}"
        data = self._fetch_json(url)
        if data and "data" in data:
            return data["data"]
        return None

    def _execute_download(self, symbol: str, **kwargs) -> Optional[list]:
        """Execute a single Finnhub financial statement download.

        Required by BaseDownloader ABC. Delegates to _download_financials.

        :param symbol: Finnhub ticker symbol
        :return: List of financial reports or None
        """
        return self._download_financials(symbol, kwargs.get("freq", "quarterly"))

    def download(self, db_symbol: str) -> Optional[dict]:
        """Download quarterly + annual financials for one non-US ticker.

        :param db_symbol: Database symbol (e.g. 'HSBA.L')
        :return: Dict with 'quarterly' and 'annual' report lists, or None
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping Finnhub download")
            self._failure_count += 1
            return None

        fh_ticker = _finnhub_ticker(db_symbol)
        result = {}

        for freq in ("quarterly", "annual"):
            for attempt in range(self.max_retries):
                try:
                    self.rate_limiter.acquire()
                    reports = self._download_financials(fh_ticker, freq)
                    self.circuit_breaker.record_success()
                    result[freq] = reports or []
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 403:
                        pipeline_logger.debug(f"Finnhub 403 for {fh_ticker} ({freq})")
                        result[freq] = []
                        break
                    if e.code == 429:
                        time.sleep(5)  # Fixed wait for server-enforced rate limit
                        continue
                    pipeline_logger.warning(
                        f"Finnhub retry {attempt + 1}/{self.max_retries} for "
                        f"{fh_ticker} ({freq}): HTTP {e.code}"
                    )
                    self.circuit_breaker.record_failure()
                    if not self._check_circuit():
                        self._failure_count += 1
                        return None
                    self._jitter_wait(attempt)
                except Exception as e:
                    pipeline_logger.warning(
                        f"Finnhub retry {attempt + 1}/{self.max_retries} for " f"{fh_ticker} ({freq}): {e}"
                    )
                    self.circuit_breaker.record_failure()
                    if not self._check_circuit():
                        self._failure_count += 1
                        return None
                    self._jitter_wait(attempt)
            else:
                result[freq] = []

        if result.get("quarterly") or result.get("annual"):
            self._success_count += 1
        else:
            self._failure_count += 1
        return result


def extract_finnhub_fundamentals(
    reports: dict, db_symbol: str, start_date: str = None, currency: str = None
) -> list[dict]:
    """Extract fundamental records from Finnhub financials-reported data.

    Processes both quarterly and annual reports, mapping Finnhub's
    field names to canonical names used in the fundamentals table.

    :param reports: Dict with 'quarterly' and 'annual' keys
    :param db_symbol: Database symbol
    :param start_date: Only include records on or after this date
    :param currency: Currency code override
    :return: List of fundamental record dicts
    """
    if not reports:
        return []

    cutoff = None
    if start_date:
        cutoff = datetime.strptime(start_date, "%Y-%m-%d").date()

    seen = set()
    records = []

    all_field_maps = [
        ("ic", INCOME_FIELD_MAP),  # income statement
        ("bs", BALANCE_FIELD_MAP),  # balance sheet
        ("cf", CASHFLOW_FIELD_MAP),  # cash flow
    ]

    for period_type in ("quarterly", "annual"):
        report_list = reports.get(period_type, [])
        if not report_list:
            continue

        for report in report_list:
            end_date_str = report.get("endDate") or report.get("period")
            if not end_date_str:
                continue

            try:
                report_date = datetime.strptime(end_date_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            if cutoff and report_date < cutoff:
                continue

            # Determine currency from report or use suffix-based default
            report_currency = currency
            if not report_currency:
                report_obj = report.get("report", {})
                report_currency = report_obj.get("currency", "USD")

            # Extract from report.report (standardized financials)
            report_data = report.get("report", {})
            if not report_data:
                continue

            for stmt_key, field_map in all_field_maps:
                stmt = report_data.get(stmt_key, [])
                if not isinstance(stmt, list):
                    continue

                for item in stmt:
                    concept = item.get("concept", "")
                    val = item.get("value")

                    canonical = field_map.get(concept)
                    if canonical is None:
                        continue

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
                            "currency": report_currency,
                        }
                    )

    # ── Post-processing: computed EBITDA and free_cash_flow ──
    record_lookup = {}
    for r in records:
        k = (r["field_name"], r["report_date"], r["period_type"])
        record_lookup[k] = r["field_value"]

    all_periods = set()
    for r in records:
        all_periods.add((r["report_date"], r["period_type"], r.get("currency", "USD")))

    for report_date, period_type, cur in all_periods:
        # Compute EBITDA: operating_income + abs(depreciation)
        ebitda_key = ("ebitda", report_date, period_type)
        if ebitda_key not in record_lookup:
            op_inc = record_lookup.get(("operating_income", report_date, period_type))
            dep = record_lookup.get(("_depreciation", report_date, period_type))
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
                            "period_type": period_type,
                            "currency": cur,
                        }
                    )
                    record_lookup[ebitda_key] = ebitda_val

        # Compute free_cash_flow: operating_cash_flow - abs(capex)
        fcf_key = ("free_cash_flow", report_date, period_type)
        if fcf_key not in record_lookup:
            ocf = record_lookup.get(("operating_cash_flow", report_date, period_type))
            capex = record_lookup.get(("capital_expenditure", report_date, period_type))
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
                            "period_type": period_type,
                            "currency": cur,
                        }
                    )

    # Remove internal helper fields (_depreciation) before returning
    records = [r for r in records if not r["field_name"].startswith("_")]

    return records
