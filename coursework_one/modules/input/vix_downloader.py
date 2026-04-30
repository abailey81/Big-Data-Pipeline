"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Yahoo Finance VIX index downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads daily CBOE VIX index data. Extends ``BaseDownloader``
for shared circuit breaker + rate limiter infrastructure.

"""

import time  # noqa: F401 — needed for test mocking (patch vix_downloader.time.sleep)

import pandas as pd
import yfinance as yf

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

VIX_SYMBOL = "^VIX"


class VixDownloader(BaseDownloader):
    """Downloads daily CBOE VIX index data from Yahoo Finance.

    Protected by a circuit breaker and rate limiter.
    Inherits from ``BaseDownloader`` (Template Method pattern).

    :param api_delay: Delay in seconds between API calls
    :type api_delay: float
    :param max_retries: Maximum retry attempts
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
            source_name="vix",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=5,
            cb_recovery_timeout=60.0,
        )

    def _execute_download(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Execute the actual Yahoo Finance VIX download.

        :param start_date: Start date (YYYY-MM-DD)
        :param end_date: End date (YYYY-MM-DD)
        :return: VIX DataFrame
        :rtype: pd.DataFrame
        """
        return yf.download(VIX_SYMBOL, start=start_date, end=end_date, progress=False, auto_adjust=False)

    def download(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Download VIX index data.

        :param start_date: Start date (YYYY-MM-DD)
        :type start_date: str
        :param end_date: End date (YYYY-MM-DD)
        :type end_date: str
        :return: VIX DataFrame or empty DataFrame on failure
        :rtype: pd.DataFrame
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping VIX download")
            self._failure_count += 1
            return pd.DataFrame()

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                df = self._execute_download(start_date, end_date)
                if df is not None and not df.empty:
                    self.circuit_breaker.record_success()
                    self._success_count += 1
                    return df
            except Exception as e:
                pipeline_logger.warning(f"Retry {attempt + 1}/{self.max_retries} for VIX: {e}")
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    pipeline_logger.warning("Circuit opened during VIX download — aborting retries")
                    self._failure_count += 1
                    return pd.DataFrame()
                self._jitter_wait(attempt)

        pipeline_logger.error(f"Failed to download VIX data after {self.max_retries} attempts")
        self._failure_count += 1
        return pd.DataFrame()
