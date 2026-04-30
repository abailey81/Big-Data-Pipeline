"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : FRED risk-free rate downloader
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads the 3-month US Treasury rate (DGS3MO) from FRED's public
CSV endpoint. No API key or additional packages required.

Used for Sharpe ratio calculation in Phase 2 (Spec §7.3, Priority P2).

"""

import time  # noqa: F401 — needed for test mocking (patch risk_free_rate_downloader.time.sleep)

import pandas as pd

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.info_logger import pipeline_logger
from modules.utils.rate_limiter import TokenBucketRateLimiter

FRED_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv" "?id={series_id}&cosd={start_date}&coed={end_date}"
)
DEFAULT_SERIES = "DGS3MO"


class RiskFreeRateDownloader(BaseDownloader):
    """Downloads daily risk-free rate from FRED public CSV endpoint.

    Uses the 3-month US Treasury rate (DGS3MO) as the risk-free proxy.
    Protected by a circuit breaker and rate limiter.

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
            source_name="risk_free_rate",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
            cb_failure_threshold=5,
            cb_recovery_timeout=60.0,
        )

    def _execute_download(
        self, start_date: str, end_date: str, series_id: str = DEFAULT_SERIES
    ) -> pd.DataFrame:
        """Download rate data from FRED CSV endpoint.

        :param start_date: Start date (YYYY-MM-DD)
        :param end_date: End date (YYYY-MM-DD)
        :param series_id: FRED series identifier
        :return: DataFrame with observation_date and rate columns
        :rtype: pd.DataFrame
        """
        url = FRED_CSV_URL.format(
            series_id=series_id,
            start_date=start_date,
            end_date=end_date,
        )
        return pd.read_csv(url)

    def download(self, start_date: str, end_date: str, series_id: str = DEFAULT_SERIES) -> pd.DataFrame:
        """Download risk-free rate data with retry logic.

        :param start_date: Start date (YYYY-MM-DD)
        :type start_date: str
        :param end_date: End date (YYYY-MM-DD)
        :type end_date: str
        :param series_id: FRED series identifier
        :type series_id: str
        :return: Rate DataFrame or empty DataFrame on failure
        :rtype: pd.DataFrame
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.warning("Circuit OPEN — skipping risk-free rate download")
            self._failure_count += 1
            return pd.DataFrame()

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                df = self._execute_download(start_date, end_date, series_id)
                if df is not None and not df.empty:
                    self.circuit_breaker.record_success()
                    self._success_count += 1
                    return df
            except Exception as e:
                pipeline_logger.warning(f"Retry {attempt + 1}/{self.max_retries} for " f"risk-free rate: {e}")
                self.circuit_breaker.record_failure()
                if not self._check_circuit():
                    pipeline_logger.warning(
                        "Circuit opened during risk-free rate download " "— aborting retries"
                    )
                    self._failure_count += 1
                    return pd.DataFrame()
                self._jitter_wait(attempt)

        pipeline_logger.error(f"Failed to download risk-free rate after " f"{self.max_retries} attempts")
        self._failure_count += 1
        return pd.DataFrame()
