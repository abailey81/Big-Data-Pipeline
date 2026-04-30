"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Yahoo Finance price downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads daily OHLCV price data using yfinance. Extends
``BaseDownloader`` for shared circuit breaker + rate limiter
infrastructure, and adds batch download support.

"""

import time  # noqa: F401 — needed for test mocking (patch price_downloader.time.sleep)

import pandas as pd
import yfinance as yf

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter


class PriceDownloader(BaseDownloader):
    """Downloads daily OHLCV price data from Yahoo Finance.

    Supports single-ticker and batch downloads with retry logic,
    rate limiting, and circuit breaker protection to handle
    Yahoo Finance API constraints (Spec §7.2 Issue 5).

    Inherits from ``BaseDownloader`` (Template Method pattern).

    :param api_delay: Delay in seconds between API calls
    :type api_delay: float
    :param max_retries: Maximum retry attempts per download
    :type max_retries: int
    :param backoff_base: Exponential backoff base multiplier
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
            source_name="prices",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=10,
            cb_recovery_timeout=120.0,
        )

    def _execute_download(self, yf_ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Execute a single Yahoo Finance price download.

        :param yf_ticker: Yahoo Finance ticker symbol
        :param start_date: Start date (YYYY-MM-DD)
        :param end_date: End date (YYYY-MM-DD)
        :return: Price DataFrame
        :rtype: pd.DataFrame
        """
        return yf.download(
            yf_ticker,
            start=start_date,
            end=end_date,
            progress=False,
            auto_adjust=False,
            timeout=30,
        )

    def download_single(self, yf_ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Download price data for a single ticker with retry logic.

        Checks the circuit breaker before each attempt. If the circuit
        is open, the request is short-circuited to avoid overwhelming
        a degraded API. Uses rate limiter for request throttling.

        :param yf_ticker: Yahoo Finance ticker symbol
        :type yf_ticker: str
        :param start_date: Start date (YYYY-MM-DD)
        :type start_date: str
        :param end_date: End date (YYYY-MM-DD)
        :type end_date: str
        :return: Price DataFrame or empty DataFrame on failure
        :rtype: pd.DataFrame
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning(
                f"Circuit OPEN — skipping {yf_ticker} " f"(state: {self.circuit_breaker.state.value})"
            )
            self._failure_count += 1
            return pd.DataFrame()

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                df = self._execute_download(yf_ticker, start_date, end_date)
                if df is not None and not df.empty:
                    self.circuit_breaker.record_success()
                    self._success_count += 1
                    return df
                pipeline_logger.warning(f"Empty result for {yf_ticker} on attempt {attempt + 1}")
            except Exception as e:
                pipeline_logger.warning(f"Retry {attempt + 1}/{self.max_retries} for {yf_ticker}: {e}")
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    pipeline_logger.warning(f"Circuit opened during {yf_ticker} download — aborting retries")
                    self._failure_count += 1
                    return pd.DataFrame()
                self._jitter_wait(attempt)

        pipeline_logger.error(
            f"Failed to download prices for {yf_ticker} after " f"{self.max_retries} attempts"
        )
        self._failure_count += 1
        return pd.DataFrame()

    def download_batch(self, yf_tickers: list[str], start_date: str, end_date: str) -> dict:
        """Download price data for a batch of tickers.

        Checks the circuit breaker before attempting batch download.
        Uses rate limiter for request throttling.

        :param yf_tickers: List of Yahoo Finance ticker symbols
        :type yf_tickers: list[str]
        :param start_date: Start date (YYYY-MM-DD)
        :type start_date: str
        :param end_date: End date (YYYY-MM-DD)
        :type end_date: str
        :return: Dictionary mapping tickers to their price DataFrames
        :rtype: dict
        """
        results = {}
        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping batch download")
            return results

        tickers_str = " ".join(yf_tickers)
        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                df = yf.download(
                    tickers_str,
                    start=start_date,
                    end=end_date,
                    group_by="ticker",
                    progress=False,
                    auto_adjust=False,
                    timeout=30,
                )
                if df is not None and not df.empty:
                    self.circuit_breaker.record_success()
                    if len(yf_tickers) == 1:
                        results[yf_tickers[0]] = df
                    else:
                        for ticker in yf_tickers:
                            if ticker in df.columns.get_level_values(0):
                                ticker_df = df[ticker].dropna(how="all")
                                if not ticker_df.empty:
                                    results[ticker] = ticker_df
                    return results
            except Exception as e:
                pipeline_logger.warning(f"Batch retry {attempt + 1}/{self.max_retries}: {e}")
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    pipeline_logger.warning("Circuit opened during batch download — aborting retries")
                    return results
                self._jitter_wait(attempt)

        pipeline_logger.error(f"Batch download failed after {self.max_retries} attempts")
        return results
