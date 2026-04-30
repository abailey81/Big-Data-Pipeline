"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : SEC EDGAR XBRL fundamentals downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads standardised XBRL financial data from SEC EDGAR's company
facts API. No API key required — only a User-Agent header with
contact information (SEC fair access policy).

Used to supplement Yahoo Finance quarterly fundamentals which only
cover ~6-8 quarters (~1.7 years). EDGAR provides 5+ years of 10-Q
quarterly filings for all US-listed public companies.

Only applicable to US tickers (no .L, .PA, .DE, etc.).

SEC EDGAR API reference:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces

"""

import json
import time  # noqa: F401 — needed for test mocking (patch edgar_downloader.time.sleep)
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_USER_AGENT = "KolmogorovTeam research@kolmogorov.dev"

# XBRL US-GAAP concept → canonical field name
# When multiple concepts map to the same field, the first match wins.
XBRL_FIELD_MAP = {
    # Balance sheet
    "Assets": "total_assets",
    "Liabilities": "total_liabilities",
    "LiabilitiesCurrent": "total_liabilities",
    "StockholdersEquity": "stockholders_equity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "stockholders_equity",
    "LongTermDebt": "total_debt",
    "LongTermDebtNoncurrent": "total_debt",
    "DebtCurrent": "total_debt",
    "ShortTermBorrowings": "total_debt",
    "LongTermDebtAndCapitalLeaseObligations": "total_debt",
    "TangibleAssetValue": "book_value",
    # Income statement
    "NetIncomeLoss": "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "net_income",
    "Revenues": "total_revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "total_revenue",
    "SalesRevenueNet": "total_revenue",
    "EarningsPerShareBasic": "basic_eps",
    "EarningsPerShareDiluted": "diluted_eps",
    "OperatingIncomeLoss": "operating_income",
    "GrossProfit": "gross_profit",
    # Cash flow
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations": "operating_cash_flow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    "CapitalExpenditureDiscontinuedOperations": "capital_expenditure",
}

# XBRL concepts used for EBITDA computation (operating_income + D&A)
XBRL_DEPRECIATION_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "Depreciation",
    "DepreciationAmortizationAndAccretionNet",
]


def is_us_ticker(symbol: str) -> bool:
    """Check if a ticker is US-listed (no exchange suffix).

    :param symbol: Ticker symbol
    :return: True if US-listed
    """
    return "." not in symbol.strip()


class EdgarFundamentalsDownloader(BaseDownloader):
    """Downloads quarterly fundamentals from SEC EDGAR XBRL API.

    Supplements Yahoo Finance which only returns ~6-8 quarters.
    EDGAR provides 5+ years of 10-Q quarterly filing data.
    Only works for US-listed companies.

    :param api_delay: Delay between API calls (SEC allows 10 req/sec)
    :type api_delay: float
    :param max_retries: Maximum retry attempts
    :type max_retries: int
    :param backoff_base: Exponential backoff multiplier
    :type backoff_base: float
    """

    def __init__(
        self,
        api_delay: float = 0.12,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="edgar_fundamentals",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=10,
            cb_recovery_timeout=30.0,
        )
        self._ticker_to_cik: Optional[dict] = None

    def _load_ticker_map(self) -> dict:
        """Load SEC ticker → CIK mapping (cached after first call).

        :return: Dict mapping uppercase ticker to CIK integer
        :rtype: dict
        """
        if self._ticker_to_cik is not None:
            return self._ticker_to_cik

        req = urllib.request.Request(SEC_COMPANY_TICKERS_URL, headers={"User-Agent": SEC_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        self._ticker_to_cik = {}
        for entry in data.values():
            ticker = entry.get("ticker", "").upper()
            cik = entry.get("cik_str")
            if ticker and cik:
                self._ticker_to_cik[ticker] = int(cik)

        pipeline_logger.info(f"Loaded {len(self._ticker_to_cik)} SEC ticker→CIK mappings")
        return self._ticker_to_cik

    def _execute_download(self, ticker: str) -> Optional[dict]:
        """Download company facts JSON from EDGAR for a single ticker.

        :param ticker: US ticker symbol (e.g. 'AAPL')
        :return: Company facts JSON or None if ticker not found
        """
        ticker_map = self._load_ticker_map()
        cik = ticker_map.get(ticker.upper().strip())
        if cik is None:
            return None

        url = SEC_COMPANY_FACTS_URL.format(cik=str(cik).zfill(10))
        req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def download(self, ticker: str) -> Optional[dict]:
        """Download company facts with retry logic.

        :param ticker: US ticker symbol
        :return: Company facts dict or None on failure
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping EDGAR download")
            self._failure_count += 1
            return None

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                data = self._execute_download(ticker)
                self.circuit_breaker.record_success()
                self._success_count += 1
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Ticker not in EDGAR — not an error
                    self._success_count += 1
                    return None
                pipeline_logger.warning(
                    f"Retry {attempt + 1}/{self.max_retries} for " f"EDGAR {ticker}: HTTP {e.code}"
                )
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    self._failure_count += 1
                    return None
                self._jitter_wait(attempt)
            except Exception as e:
                pipeline_logger.warning(f"Retry {attempt + 1}/{self.max_retries} for " f"EDGAR {ticker}: {e}")
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    self._failure_count += 1
                    return None
                self._jitter_wait(attempt)

        pipeline_logger.error(
            f"Failed to download EDGAR data for {ticker} " f"after {self.max_retries} attempts"
        )
        self._failure_count += 1
        return None


def extract_edgar_fundamentals(
    company_facts: dict,
    db_symbol: str,
    start_date: str = None,
    currency: str = "USD",
    period_types: tuple = ("quarterly", "annual"),
) -> list[dict]:
    """Extract fundamental records from EDGAR company facts JSON.

    Supports both 10-Q (quarterly) and 10-K (annual) filings.
    Maps XBRL US-GAAP concepts to the project's canonical field names.

    :param company_facts: Raw company facts JSON from EDGAR
    :param db_symbol: Database symbol for this ticker
    :param start_date: Only include records on or after this date (YYYY-MM-DD)
    :param currency: Currency code (always USD for SEC filings)
    :param period_types: Tuple of period types to extract ('quarterly', 'annual')
    :return: List of record dicts matching FundamentalRecord schema
    """
    if not company_facts:
        return []

    facts = company_facts.get("facts", {})
    us_gaap = facts.get("us-gaap", {})
    if not us_gaap:
        return []

    # Track which (field_name, report_date, period_type) we've already seen
    # to avoid duplicates when multiple XBRL concepts map to the same field
    seen = set()
    records = []

    cutoff = None
    if start_date:
        cutoff = datetime.strptime(start_date, "%Y-%m-%d").date()

    # Filing form → period_type mapping
    form_map = {}
    if "quarterly" in period_types:
        form_map["10-Q"] = "quarterly"
    if "annual" in period_types:
        form_map["10-K"] = "annual"

    for xbrl_concept, canonical_name in XBRL_FIELD_MAP.items():
        concept_data = us_gaap.get(xbrl_concept)
        if not concept_data:
            continue

        units = concept_data.get("units", {})
        # EPS fields use 'USD/shares', others use 'USD'
        unit_key = None
        for k in ["USD", "USD/shares"]:
            if k in units:
                unit_key = k
                break
        if unit_key is None:
            continue

        for entry in units[unit_key]:
            form = entry.get("form", "")
            end_date = entry.get("end")

            period_type = form_map.get(form)
            if period_type is None:
                continue

            # For 10-Q, require fiscal period label
            if form == "10-Q":
                fp = entry.get("fp", "")
                if fp not in ("Q1", "Q2", "Q3", "Q4"):
                    continue
            # For 10-K, accept FY fiscal period
            elif form == "10-K":
                fp = entry.get("fp", "")
                if fp not in ("FY",):
                    continue

            if not end_date:
                continue

            report_date = datetime.strptime(end_date, "%Y-%m-%d").date()

            if cutoff and report_date < cutoff:
                continue

            key = (canonical_name, report_date, period_type)
            if key in seen:
                continue
            seen.add(key)

            records.append(
                {
                    "symbol": db_symbol,
                    "report_date": report_date,
                    "field_name": canonical_name,
                    "field_value": entry.get("val"),
                    "period_type": period_type,
                    "currency": currency,
                }
            )

    # ── Computed fields: EBITDA and free_cash_flow ──
    # Build lookup of existing records by (field_name, report_date, period_type)
    record_lookup = {}
    for r in records:
        k = (r["field_name"], r["report_date"], r["period_type"])
        record_lookup[k] = r["field_value"]

    # Extract depreciation values from XBRL for EBITDA computation
    depreciation_vals = {}  # (report_date, period_type) → value
    for dep_concept in XBRL_DEPRECIATION_CONCEPTS:
        dep_data = us_gaap.get(dep_concept)
        if not dep_data:
            continue
        for unit_key in ["USD"]:
            for entry in dep_data.get("units", {}).get(unit_key, []):
                form = entry.get("form", "")
                period_type = form_map.get(form)
                if period_type is None:
                    continue
                end_date = entry.get("end")
                if not end_date:
                    continue
                report_date = datetime.strptime(end_date, "%Y-%m-%d").date()
                if cutoff and report_date < cutoff:
                    continue
                dep_key = (report_date, period_type)
                if dep_key not in depreciation_vals:
                    val = entry.get("val")
                    if val is not None:
                        depreciation_vals[dep_key] = float(val)

    # Compute EBITDA where missing: operating_income + abs(depreciation)
    for (report_date, period_type), dep_val in depreciation_vals.items():
        ebitda_key = ("ebitda", report_date, period_type)
        if ebitda_key in record_lookup:
            continue  # Already have EBITDA
        op_inc_key = ("operating_income", report_date, period_type)
        op_inc = record_lookup.get(op_inc_key)
        if op_inc is not None:
            ebitda_val = float(op_inc) + abs(dep_val)
            key = ("ebitda", report_date, period_type)
            if key not in seen:
                seen.add(key)
                records.append(
                    {
                        "symbol": db_symbol,
                        "report_date": report_date,
                        "field_name": "ebitda",
                        "field_value": ebitda_val,
                        "period_type": period_type,
                        "currency": currency,
                    }
                )
                record_lookup[ebitda_key] = ebitda_val

    # Compute free_cash_flow where missing: operating_cash_flow - abs(capex)
    all_periods = set()
    for r in records:
        all_periods.add((r["report_date"], r["period_type"]))

    for report_date, period_type in all_periods:
        fcf_key = ("free_cash_flow", report_date, period_type)
        if fcf_key in record_lookup:
            continue
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
                        "currency": currency,
                    }
                )

    return records
