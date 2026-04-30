"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Yahoo Finance FX rate downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads daily FX rate data for currency pairs required by the
investable universe. Extends ``BaseDownloader`` for shared
circuit breaker + rate limiter infrastructure.

Supports parallel pair downloads via ``ConcurrentDownloadExecutor``
for I/O-bound concurrency across independent currency pairs.

"""

import time

import pandas as pd
import yfinance as yf

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

# FX pairs needed based on investable universe currencies
FX_PAIRS = ["GBPUSD=X", "EURUSD=X", "CADUSD=X", "CHFUSD=X"]


class FxDownloader(BaseDownloader):
    """Downloads daily FX rate data from Yahoo Finance.

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
        max_retries: int = 5,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
    ):
        super().__init__(
            source_name="fx",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=5,
            cb_recovery_timeout=60.0,
        )

    def _execute_download(self, pair: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Execute a single Yahoo Finance FX download.

        :param pair: Yahoo Finance FX pair symbol
        :param start_date: Start date (YYYY-MM-DD)
        :param end_date: End date (YYYY-MM-DD)
        :return: FX rate DataFrame
        :rtype: pd.DataFrame
        """
        return yf.download(pair, start=start_date, end=end_date, progress=False, auto_adjust=True)

    @staticmethod
    def _has_valid_close(df: pd.DataFrame) -> bool:
        """Return True if df has at least one non-NaN close value.

        Handles both single-level and MultiIndex yfinance columns.
        """
        try:
            if isinstance(df.columns, pd.MultiIndex):
                # Find the level that contains 'Close'
                for level_idx in range(df.columns.nlevels):
                    vals = [str(v).lower() for v in df.columns.get_level_values(level_idx)]
                    if "close" in vals:
                        idx = vals.index("close")
                        col = df.columns[idx]
                        return df[col].notna().any()
                return False
            else:
                close_cols = [c for c in df.columns if str(c).lower() == "close"]
                if not close_cols:
                    return False
                return df[close_cols[0]].notna().any()
        except Exception:
            return False

    def download(self, pair: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Download FX rate data for a single currency pair.

        :param pair: Yahoo Finance FX pair symbol (e.g. 'GBPUSD=X')
        :type pair: str
        :param start_date: Start date (YYYY-MM-DD)
        :type start_date: str
        :param end_date: End date (YYYY-MM-DD)
        :type end_date: str
        :return: FX rate DataFrame or empty DataFrame on failure
        :rtype: pd.DataFrame
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning(f"Circuit OPEN — skipping FX {pair}")
            self._failure_count += 1
            return pd.DataFrame()

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                df = self._execute_download(pair, start_date, end_date)
                if df is not None and not df.empty and self._has_valid_close(df):
                    self.circuit_breaker.record_success()
                    self._success_count += 1
                    return df

                # Empty or all-NaN close — likely a crumb-poisoning issue
                # (yfinance returns empty/NaN rows on 401 Invalid Crumb errors
                # rather than raising an exception). Back off to let the
                # concurrent prices thread finish refreshing the crumb.
                pipeline_logger.warning(f"Empty/NaN FX data for {pair} on attempt {attempt + 1}")
                if attempt < self.max_retries - 1:
                    wait = self.backoff_base ** (attempt + 1)
                    pipeline_logger.debug(f"FX {pair}: backing off {wait:.1f}s before retry")
                    time.sleep(wait)

            except Exception as e:
                pipeline_logger.warning(f"Retry {attempt + 1}/{self.max_retries} for FX {pair}: {e}")
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    pipeline_logger.warning(f"Circuit opened during FX {pair} — aborting retries")
                    self._failure_count += 1
                    return pd.DataFrame()
                self._jitter_wait(attempt)

        pipeline_logger.error(f"Failed to download FX data for {pair} " f"after {self.max_retries} attempts")
        self._failure_count += 1
        return pd.DataFrame()

    def download_all(self, start_date: str, end_date: str, pairs: list[str] = None) -> dict:
        """Download FX data for all required currency pairs.

        Downloads pairs sequentially to avoid yfinance thread-safety
        issues with concurrent single-ticker ``yf.download()`` calls.

        :param start_date: Start date (YYYY-MM-DD)
        :type start_date: str
        :param end_date: End date (YYYY-MM-DD)
        :type end_date: str
        :param pairs: Optional list of FX pairs to download
        :type pairs: list[str] or None
        :return: Dictionary mapping pairs to their DataFrames
        :rtype: dict
        """
        pairs = pairs or FX_PAIRS
        results = {}
        # Brief startup delay so the concurrent prices thread can finish
        # processing its first batch (which often includes delisted tickers
        # that trigger 401 errors and poison the shared yfinance crumb).
        # Without this, FX downloads #3 and #4 tend to get empty DataFrames.
        time.sleep(8)
        for pair in pairs:
            df = self.download(pair, start_date, end_date)
            if not df.empty:
                results[pair] = df
        return results
