"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Alpha Vantage fundamentals downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads quarterly and annual income statement, balance sheet, and cash
flow data from the Alpha Vantage API.  Used as a supplementary source to
Yahoo Finance and Finnhub for fundamental data coverage.

The free tier allows 25 requests/day per key and 5 requests/minute.
The pipeline supports any number of keys (ALPHA_VANTAGE_KEY_1,
ALPHA_VANTAGE_KEY_2, ...) in fallback order.  Only KEY_1 is required;
additional keys are optional and activate automatically when prior
keys hit their daily rate limit.  More keys = more daily quota.

Output records use the EAV (Entity-Attribute-Value) format expected by
the fundamentals table::

    {"symbol": str, "report_date": date, "field_name": str,
     "field_value": float, "period_type": "quarterly"|"annual",
     "currency": str}

Extends ``BaseDownloader`` for shared circuit breaker + rate limiter
infrastructure.

"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

AV_BASE_URL = "https://www.alphavantage.co/query"

# ── Yahoo Finance suffix → Alpha Vantage exchange suffix ──
YF_TO_AV_SUFFIX = {
    ".L": ".LON",
    ".PA": ".PAR",
    ".DE": ".DEU",
    ".AS": ".AMS",
    ".TO": ".TRT",
    ".SW": ".SWX",
    ".MI": ".MIL",
    ".MC": ".MCE",
}

# ── Alpha Vantage field → canonical field name (by statement) ──
INCOME_FIELD_MAP = {
    "totalRevenue": "total_revenue",
    "netIncome": "net_income",
    "grossProfit": "gross_profit",
    "operatingIncome": "operating_income",
    "ebitda": "ebitda",
    "eps": "basic_eps",
}

BALANCE_FIELD_MAP = {
    "totalShareholderEquity": "stockholders_equity",
    "totalAssets": "total_assets",
    "totalLiabilities": "total_liabilities",
    "shortLongTermDebtTotal": "total_debt",
}

CASHFLOW_FIELD_MAP = {
    "operatingCashflow": "operating_cash_flow",
    "capitalExpenditures": "capital_expenditure",
}

# Fields that require special computation (not direct mappings)
_DEBT_FALLBACK_FIELDS = ("shortTermDebt", "longTermDebt")


class AlphaVantageFundamentalsDownloader(BaseDownloader):
    """Downloads fundamental data from the Alpha Vantage REST API.

    Uses any number of API keys (``ALPHA_VANTAGE_KEY_1``, ``_2``, ...)
    in fallback order — each key is used until rate-limited, then
    the next key takes over automatically.  Only one key is required;
    additional keys extend the daily quota (25 requests/day each).

    :param api_delay: Delay between API calls in seconds
    :type api_delay: float
    :param max_retries: Maximum retry attempts per download
    :type max_retries: int
    :param backoff_base: Exponential backoff multiplier
    :type backoff_base: float
    :param circuit_breaker: Optional pre-configured CircuitBreaker
    :type circuit_breaker: CircuitBreaker or None
    :param rate_limiter: Optional pre-configured rate limiter
    :type rate_limiter: TokenBucketRateLimiter or None
    """

    # Class-level fallback state (shared across all instances)
    _current_key_idx = 0
    _exhausted_keys: set = set()
    _key_lock = threading.Lock()

    def __init__(
        self,
        api_delay: float = 3.1,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="alphavantage_fundamentals",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=10,
            cb_recovery_timeout=120.0,
        )

        # Load all available API keys, skipping empty / missing ones.
        # Works with 1 key or many — additional keys are fallbacks
        # that activate only when prior keys are rate-limited.
        self._api_keys: list[str] = []
        for i in range(1, 20):
            key = os.environ.get(f"ALPHA_VANTAGE_KEY_{i}", "").strip()
            if key:
                self._api_keys.append(key)

        if not self._api_keys:
            pipeline_logger.warning(
                "No ALPHA_VANTAGE_KEY_* environment variables set — "
                "AlphaVantageFundamentalsDownloader will not be able to "
                "make API calls"
            )

        pipeline_logger.info(
            f"AlphaVantage downloader initialised with "
            f"{len(self._api_keys)} API key(s) (fallback order), "
            f"api_delay={api_delay}s"
        )

    # ────────────────────────────────────────────────────────────
    # Key rotation
    # ────────────────────────────────────────────────────────────

    def _get_current_key(self) -> tuple[Optional[str], int]:
        """Return the current active API key and its index (fallback order).

        Uses each key until it's exhausted, then moves to the next.

        :return: Tuple of (API key string or None, key index)
        :rtype: tuple[str | None, int]
        """
        if not self._api_keys:
            return None, -1

        with self._key_lock:
            idx = AlphaVantageFundamentalsDownloader._current_key_idx
            if idx >= len(self._api_keys):
                return None, -1  # All keys exhausted
            return self._api_keys[idx], idx

    def _mark_key_exhausted(self, caller_idx: int):
        """Mark a specific key as rate-limited and advance to the next.

        Thread-safe: only advances if the caller's key is still the
        current active key, preventing multi-threaded key skipping.

        :param caller_idx: The key index the caller was using
        :type caller_idx: int
        """
        with self._key_lock:
            # Only advance if this caller's key is still the active one
            if AlphaVantageFundamentalsDownloader._current_key_idx != caller_idx:
                return  # Another thread already advanced past this key
            if caller_idx < len(self._api_keys):
                key_num = caller_idx + 1
                AlphaVantageFundamentalsDownloader._exhausted_keys.add(caller_idx)
                AlphaVantageFundamentalsDownloader._current_key_idx = caller_idx + 1
                remaining = len(self._api_keys) - caller_idx - 1
                pipeline_logger.info(
                    f"AlphaVantage key #{key_num} exhausted — "
                    f"falling back to next key ({remaining} remaining)"
                )

    # ────────────────────────────────────────────────────────────
    # Symbol conversion
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _convert_to_av_symbol(yf_ticker: str) -> str:
        """Convert a Yahoo Finance ticker to Alpha Vantage format.

        US tickers pass through unchanged.  Non-US tickers have their
        exchange suffix mapped (e.g. ``.L`` -> ``.LON``).

        :param yf_ticker: Yahoo Finance ticker (e.g. ``VOD.L``, ``AAPL``)
        :type yf_ticker: str
        :return: Alpha Vantage-compatible ticker
        :rtype: str
        """
        yf_ticker = yf_ticker.strip()

        for yf_suffix, av_suffix in YF_TO_AV_SUFFIX.items():
            if yf_ticker.endswith(yf_suffix):
                base = yf_ticker[: -len(yf_suffix)]
                return f"{base}{av_suffix}"

        # No recognised suffix — assume US ticker, pass through
        return yf_ticker

    # ────────────────────────────────────────────────────────────
    # HTTP fetch
    # ────────────────────────────────────────────────────────────

    def _fetch_json(self, function: str, symbol: str) -> Optional[dict]:
        """Fetch a single Alpha Vantage endpoint and return parsed JSON.

        Checks for the ``"Note"`` key (rate-limit exhaustion) and
        ``"Error Message"`` key (invalid request) in the response.

        :param function: AV function name (INCOME_STATEMENT, etc.)
        :type function: str
        :param symbol: AV-formatted ticker symbol
        :type symbol: str
        :return: Parsed JSON dict, or None on rate-limit / error
        :rtype: dict or None
        """
        api_key, key_idx = self._get_current_key()
        if api_key is None:
            pipeline_logger.warning("All Alpha Vantage keys exhausted for today")
            return None

        url = f"{AV_BASE_URL}?function={function}&symbol={symbol}&apikey={api_key}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SystematicEquityPipeline/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        # ── Rate-limit exhaustion → fall back to next key ──
        if "Note" in data:
            pipeline_logger.warning(
                f"AlphaVantage rate limit hit for {symbol} "
                f"({function}): {data['Note']}"
            )
            self._mark_key_exhausted(key_idx)
            # Retry with the next key immediately (with rate limiter)
            next_key, next_idx = self._get_current_key()
            if next_key is not None:
                self.rate_limiter.acquire()
                retry_url = f"{AV_BASE_URL}?function={function}&symbol={symbol}&apikey={next_key}"
                retry_req = urllib.request.Request(
                    retry_url,
                    headers={"User-Agent": "SystematicEquityPipeline/1.0", "Accept": "application/json"},
                )
                try:
                    with urllib.request.urlopen(retry_req, timeout=30) as retry_resp:
                        retry_data = json.loads(retry_resp.read().decode())
                    if "Note" not in retry_data and "Error Message" not in retry_data:
                        return retry_data
                    if "Note" in retry_data:
                        self._mark_key_exhausted(next_idx)
                except Exception:
                    pass
            return None

        # ── Invalid request / bad symbol ──
        if "Error Message" in data:
            pipeline_logger.warning(
                f"AlphaVantage error for {symbol} "
                f"({function}): {data['Error Message']}"
            )
            return None

        return data

    # ────────────────────────────────────────────────────────────
    # Statement extraction helpers
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_records_from_reports(
        reports: list[dict],
        field_map: dict[str, str],
        db_symbol: str,
        period_type: str,
    ) -> list[dict]:
        """Extract EAV records from a list of AV report dicts.

        :param reports: List of report dicts from AV API
        :param field_map: Mapping of AV field names to canonical names
        :param db_symbol: Database symbol for the ``symbol`` column
        :param period_type: ``"quarterly"`` or ``"annual"``
        :return: List of EAV record dicts
        :rtype: list[dict]
        """
        records = []

        for report in reports:
            fiscal_date_str = report.get("fiscalDateEnding")
            if not fiscal_date_str:
                continue

            try:
                report_date = datetime.strptime(fiscal_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            currency = report.get("reportedCurrency", "USD")

            # ── Direct field mappings ──
            for av_field, canonical_field in field_map.items():
                raw_val = report.get(av_field)
                if raw_val is None or raw_val == "None":
                    continue
                try:
                    fval = float(raw_val)
                except (ValueError, TypeError):
                    continue

                records.append(
                    {
                        "symbol": db_symbol,
                        "report_date": report_date,
                        "field_name": canonical_field,
                        "field_value": fval,
                        "period_type": period_type,
                        "currency": currency,
                    }
                )

        return records

    @staticmethod
    def _extract_balance_sheet_extras(
        reports: list[dict],
        db_symbol: str,
        period_type: str,
    ) -> list[dict]:
        """Extract total_debt (fallback) and book_value from balance sheet.

        If ``shortLongTermDebtTotal`` is missing, computes total_debt
        as ``shortTermDebt + longTermDebt``.  Computes book_value as
        ``totalShareholderEquity / commonStockSharesOutstanding`` when
        available.

        :param reports: List of balance-sheet report dicts
        :param db_symbol: Database symbol
        :param period_type: ``"quarterly"`` or ``"annual"``
        :return: Additional EAV record dicts
        :rtype: list[dict]
        """
        records = []

        for report in reports:
            fiscal_date_str = report.get("fiscalDateEnding")
            if not fiscal_date_str:
                continue

            try:
                report_date = datetime.strptime(fiscal_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            currency = report.get("reportedCurrency", "USD")

            # ── total_debt fallback: shortTermDebt + longTermDebt ──
            direct_debt = report.get("shortLongTermDebtTotal")
            if direct_debt is None or direct_debt == "None":
                short_raw = report.get("shortTermDebt")
                long_raw = report.get("longTermDebt")

                short_val = 0.0
                long_val = 0.0
                has_component = False

                if short_raw is not None and short_raw != "None":
                    try:
                        short_val = float(short_raw)
                        has_component = True
                    except (ValueError, TypeError):
                        pass

                if long_raw is not None and long_raw != "None":
                    try:
                        long_val = float(long_raw)
                        has_component = True
                    except (ValueError, TypeError):
                        pass

                if has_component:
                    records.append(
                        {
                            "symbol": db_symbol,
                            "report_date": report_date,
                            "field_name": "total_debt",
                            "field_value": short_val + long_val,
                            "period_type": period_type,
                            "currency": currency,
                        }
                    )

            # ── book_value: equity / shares outstanding ──
            equity_raw = report.get("totalShareholderEquity")
            shares_raw = report.get("commonStockSharesOutstanding")

            if (
                equity_raw is not None
                and equity_raw != "None"
                and shares_raw is not None
                and shares_raw != "None"
            ):
                try:
                    equity_val = float(equity_raw)
                    shares_val = float(shares_raw)
                    if shares_val > 0:
                        records.append(
                            {
                                "symbol": db_symbol,
                                "report_date": report_date,
                                "field_name": "book_value",
                                "field_value": equity_val / shares_val,
                                "period_type": period_type,
                                "currency": currency,
                            }
                        )
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        return records

    @staticmethod
    def _extract_diluted_eps(
        reports: list[dict],
        db_symbol: str,
        period_type: str,
    ) -> list[dict]:
        """Extract diluted_eps from income statement reports.

        Alpha Vantage provides a single ``eps`` field in its income
        statement response.  We map it to both ``basic_eps`` (via the
        standard field map) and ``diluted_eps`` (via this helper).

        :param reports: List of income-statement report dicts
        :param db_symbol: Database symbol
        :param period_type: ``"quarterly"`` or ``"annual"``
        :return: EAV records for diluted_eps
        :rtype: list[dict]
        """
        records = []

        for report in reports:
            fiscal_date_str = report.get("fiscalDateEnding")
            if not fiscal_date_str:
                continue

            try:
                report_date = datetime.strptime(fiscal_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            currency = report.get("reportedCurrency", "USD")

            eps_raw = report.get("eps")
            if eps_raw is not None and eps_raw != "None":
                try:
                    records.append(
                        {
                            "symbol": db_symbol,
                            "report_date": report_date,
                            "field_name": "diluted_eps",
                            "field_value": float(eps_raw),
                            "period_type": period_type,
                            "currency": currency,
                        }
                    )
                except (ValueError, TypeError):
                    pass

        return records

    @staticmethod
    def _compute_free_cash_flow(
        reports: list[dict],
        db_symbol: str,
        period_type: str,
    ) -> list[dict]:
        """Compute free_cash_flow = operatingCashflow - abs(capitalExpenditures).

        :param reports: List of cash-flow report dicts
        :param db_symbol: Database symbol
        :param period_type: ``"quarterly"`` or ``"annual"``
        :return: EAV records for free_cash_flow
        :rtype: list[dict]
        """
        records = []

        for report in reports:
            fiscal_date_str = report.get("fiscalDateEnding")
            if not fiscal_date_str:
                continue

            try:
                report_date = datetime.strptime(fiscal_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            currency = report.get("reportedCurrency", "USD")

            ocf_raw = report.get("operatingCashflow")
            capex_raw = report.get("capitalExpenditures")

            if (
                ocf_raw is not None
                and ocf_raw != "None"
                and capex_raw is not None
                and capex_raw != "None"
            ):
                try:
                    ocf = float(ocf_raw)
                    capex = float(capex_raw)
                    records.append(
                        {
                            "symbol": db_symbol,
                            "report_date": report_date,
                            "field_name": "free_cash_flow",
                            "field_value": ocf - abs(capex),
                            "period_type": period_type,
                            "currency": currency,
                        }
                    )
                except (ValueError, TypeError):
                    pass

        return records

    # ────────────────────────────────────────────────────────────
    # BaseDownloader ABC implementation
    # ────────────────────────────────────────────────────────────

    def _execute_download(self, **kwargs):
        """Execute a single Alpha Vantage API call.

        Required by ``BaseDownloader`` ABC.  Not used directly —
        ``download()`` orchestrates all three statement endpoints.

        :return: None (not used)
        """
        raise NotImplementedError(
            "Use download() directly — _execute_download is not used "
            "for AlphaVantage multi-endpoint orchestration"
        )

    # ────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────

    def download(
        self,
        db_symbol: str,
        yf_ticker: str,
    ) -> Optional[list[dict]]:
        """Download fundamentals for a single ticker from Alpha Vantage.

        Makes three API calls (income statement, balance sheet, cash
        flow), extracts all relevant fields, and returns a list of EAV
        records ready for PostgreSQL upsert.

        :param db_symbol: Database symbol (used in output records)
        :type db_symbol: str
        :param yf_ticker: Yahoo Finance ticker (converted to AV format)
        :type yf_ticker: str
        :return: List of EAV fundamental record dicts, or None on failure
        :rtype: list[dict] or None
        """
        self._download_count += 1

        if not self._api_keys:
            pipeline_logger.error(
                f"No API keys — cannot download AlphaVantage "
                f"fundamentals for {db_symbol}"
            )
            self._failure_count += 1
            return []

        if not self._check_circuit():
            pipeline_logger.warning(
                f"Circuit OPEN — skipping AlphaVantage for {db_symbol}"
            )
            self._failure_count += 1
            return []

        av_symbol = self._convert_to_av_symbol(yf_ticker)
        all_records: list[dict] = []

        # ── Endpoints to fetch ──
        endpoints = [
            ("INCOME_STATEMENT", "quarterlyReports", "annualReports"),
            ("BALANCE_SHEET", "quarterlyReports", "annualReports"),
            ("CASH_FLOW", "quarterlyReports", "annualReports"),
        ]

        raw_data: dict[str, Optional[dict]] = {}

        for function, _, _ in endpoints:
            for attempt in range(self.max_retries):
                try:
                    self.rate_limiter.acquire()
                    time.sleep(self.api_delay)

                    data = self._fetch_json(function, av_symbol)

                    if data is None:
                        # Rate limit hit or error — do not retry
                        # (key may be exhausted for the day)
                        pipeline_logger.warning(
                            f"AlphaVantage {function} returned None "
                            f"for {av_symbol} — skipping"
                        )
                        raw_data[function] = None
                        break

                    self.circuit_breaker.record_success()
                    raw_data[function] = data
                    break

                except urllib.error.HTTPError as e:
                    pipeline_logger.warning(
                        f"AlphaVantage HTTP {e.code} for {av_symbol} "
                        f"({function}), attempt {attempt + 1}/"
                        f"{self.max_retries}"
                    )
                    self.circuit_breaker.record_failure()
                    if not self._check_circuit():
                        self._failure_count += 1
                        return []
                    self._jitter_wait(attempt)

                except Exception as e:
                    pipeline_logger.warning(
                        f"AlphaVantage error for {av_symbol} "
                        f"({function}), attempt {attempt + 1}/"
                        f"{self.max_retries}: {e}"
                    )
                    self.circuit_breaker.record_failure()
                    if not self._check_circuit():
                        self._failure_count += 1
                        return []
                    self._jitter_wait(attempt)
            else:
                # Exhausted all retries for this endpoint
                pipeline_logger.error(
                    f"Failed to download AlphaVantage {function} "
                    f"for {av_symbol} after {self.max_retries} attempts"
                )
                raw_data[function] = None

        # ── Extract records from each statement ──

        for period_type, report_key in [
            ("quarterly", "quarterlyReports"),
            ("annual", "annualReports"),
        ]:
            # Income statement
            income_data = raw_data.get("INCOME_STATEMENT")
            if income_data and report_key in income_data:
                reports = income_data[report_key]
                all_records.extend(
                    self._extract_records_from_reports(
                        reports, INCOME_FIELD_MAP, db_symbol, period_type
                    )
                )
                all_records.extend(
                    self._extract_diluted_eps(
                        reports, db_symbol, period_type
                    )
                )

            # Balance sheet
            balance_data = raw_data.get("BALANCE_SHEET")
            if balance_data and report_key in balance_data:
                reports = balance_data[report_key]
                all_records.extend(
                    self._extract_records_from_reports(
                        reports, BALANCE_FIELD_MAP, db_symbol, period_type
                    )
                )
                all_records.extend(
                    self._extract_balance_sheet_extras(
                        reports, db_symbol, period_type
                    )
                )

            # Cash flow
            cashflow_data = raw_data.get("CASH_FLOW")
            if cashflow_data and report_key in cashflow_data:
                reports = cashflow_data[report_key]
                all_records.extend(
                    self._extract_records_from_reports(
                        reports, CASHFLOW_FIELD_MAP, db_symbol, period_type
                    )
                )
                all_records.extend(
                    self._compute_free_cash_flow(
                        reports, db_symbol, period_type
                    )
                )

        # ── Deduplicate (same field+date+period keeps first) ──
        seen: set[tuple] = set()
        deduped: list[dict] = []
        for rec in all_records:
            key = (rec["field_name"], rec["report_date"], rec["period_type"])
            if key not in seen:
                seen.add(key)
                deduped.append(rec)

        if deduped:
            self._success_count += 1
            pipeline_logger.info(
                f"AlphaVantage: {len(deduped)} fundamental records "
                f"for {db_symbol} ({av_symbol})"
            )
        else:
            self._failure_count += 1
            pipeline_logger.warning(
                f"AlphaVantage: 0 records extracted for {db_symbol} "
                f"({av_symbol})"
            )

        return deduped or []
