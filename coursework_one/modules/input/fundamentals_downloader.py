"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Yahoo Finance fundamentals downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads both annual and quarterly balance sheet, income statement,
and cash flow data as specified in Spec §2.1 and §7.2 Issue 6:
  - book value per share (from ticker.info or balance sheet)
  - net income (from income statement)
  - shareholders' equity (from balance sheet)
  - total debt (from balance sheet)
  - EPS (from income statement)

Annual statements provide ~5 years of history; quarterly statements
provide ~6-7 quarters of granular data. Both are downloaded to ensure
full 5-year coverage.

Extends ``BaseDownloader`` for shared circuit breaker + rate limiter
infrastructure.

"""

import time

import pandas as pd
import yfinance as yf

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter


class FundamentalsDownloader(BaseDownloader):
    """Downloads annual and quarterly fundamental data from Yahoo Finance.

    Retrieves balance sheet, income statement, and cash flow for both
    annual (~5 years) and quarterly (~6-7 quarters) frequencies, plus
    key statistics from the Ticker.info property.
    Protected by a circuit breaker and rate limiter.

    Inherits from ``BaseDownloader`` (Template Method pattern).

    :param api_delay: Delay in seconds between API calls
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

    def __init__(
        self,
        api_delay: float = 0.5,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="fundamentals",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=10,
            cb_recovery_timeout=120.0,
        )

    def _execute_download(self, yf_ticker: str) -> dict | None:
        """Execute the actual fundamentals download from Yahoo Finance.

        Downloads both annual (~5 years) and quarterly (~6-7 quarters)
        statements to ensure full 5-year historical coverage.

        :param yf_ticker: Yahoo Finance ticker symbol
        :return: Dict with annual/quarterly balance_sheet, income_stmt,
                 cash_flow, and info
        :rtype: dict or None
        """
        ticker_obj = yf.Ticker(yf_ticker)

        result = {
            "annual_balance_sheet": pd.DataFrame(),
            "annual_income_stmt": pd.DataFrame(),
            "annual_cash_flow": pd.DataFrame(),
            "quarterly_balance_sheet": pd.DataFrame(),
            "quarterly_income_stmt": pd.DataFrame(),
            "quarterly_cash_flow": pd.DataFrame(),
            "info": {},
        }

        # ── Annual statements (~5 years of history) ──
        try:
            bs = ticker_obj.get_balance_sheet(freq="yearly")
            if bs is not None and not bs.empty:
                result["annual_balance_sheet"] = bs
        except Exception as e:
            pipeline_logger.debug(f"No annual balance_sheet for {yf_ticker}: {e}")

        try:
            inc = ticker_obj.get_income_stmt(freq="yearly")
            if inc is not None and not inc.empty:
                result["annual_income_stmt"] = inc
        except Exception as e:
            pipeline_logger.debug(f"No annual income_stmt for {yf_ticker}: {e}")

        try:
            cf = ticker_obj.get_cash_flow(freq="yearly")
            if cf is not None and not cf.empty:
                result["annual_cash_flow"] = cf
        except Exception as e:
            pipeline_logger.debug(f"No annual cash_flow for {yf_ticker}: {e}")

        # ── Quarterly statements (~6-7 quarters) ──
        try:
            bs = ticker_obj.get_balance_sheet(freq="quarterly")
            if bs is not None and not bs.empty:
                result["quarterly_balance_sheet"] = bs
        except Exception as e:
            pipeline_logger.debug(f"No quarterly balance_sheet for {yf_ticker}: {e}")

        try:
            inc = ticker_obj.get_income_stmt(freq="quarterly")
            if inc is not None and not inc.empty:
                result["quarterly_income_stmt"] = inc
        except Exception as e:
            pipeline_logger.debug(f"No quarterly income_stmt for {yf_ticker}: {e}")

        try:
            cf = ticker_obj.get_cash_flow(freq="quarterly")
            if cf is not None and not cf.empty:
                result["quarterly_cash_flow"] = cf
        except Exception as e:
            pipeline_logger.debug(f"No quarterly cash_flow for {yf_ticker}: {e}")

        # Key statistics from .info (Spec: book value per share)
        try:
            info = ticker_obj.info
            if info:
                result["info"] = info
        except Exception as e:
            pipeline_logger.debug(f"No ticker.info for {yf_ticker}: {e}")

        # Only return if we got at least some data
        has_data = (
            any(not result[k].empty for k in result if isinstance(result[k], pd.DataFrame)) or result["info"]
        )
        return result if has_data else None

    def download(self, yf_ticker: str) -> dict | None:
        """Download fundamental data for a single ticker.

        Returns a dict with annual and quarterly balance sheets, income
        statements, cash flows, and ticker info.

        :param yf_ticker: Yahoo Finance ticker symbol
        :type yf_ticker: str
        :return: Dict with DataFrames for each statement type, or None
        :rtype: dict or None
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning(f"Circuit OPEN — skipping fundamentals for {yf_ticker}")
            self._failure_count += 1
            return None

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                result = self._execute_download(yf_ticker)

                if result is not None:
                    self.circuit_breaker.record_success()
                    self._success_count += 1
                    return result

                pipeline_logger.warning(f"Empty fundamentals for {yf_ticker} on attempt {attempt + 1}")
                # Back off even on empty results — yfinance may be rate-limited
                # and returning empty DataFrames silently rather than raising.
                if attempt < self.max_retries - 1:
                    wait = self.backoff_base**attempt
                    time.sleep(wait)

            except Exception as e:
                pipeline_logger.warning(
                    f"Retry {attempt + 1}/{self.max_retries} for " f"fundamentals {yf_ticker}: {e}"
                )
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    pipeline_logger.warning(
                        f"Circuit opened during {yf_ticker} fundamentals — " "aborting retries"
                    )
                    self._failure_count += 1
                    return None
                self._jitter_wait(attempt)

        pipeline_logger.error(
            f"Failed to download fundamentals for {yf_ticker} " f"after {self.max_retries} attempts"
        )
        self._failure_count += 1
        return None
