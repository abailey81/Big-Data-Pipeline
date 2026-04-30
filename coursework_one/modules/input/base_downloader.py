"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Abstract base downloader (Template Method pattern)
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Implements the Template Method design pattern (GoF, 1994) for Yahoo
Finance downloaders. This provides a uniform interface and shared
resilience infrastructure across all data source types:

  - Price downloader (daily OHLCV)
  - Fundamentals downloader (quarterly balance sheet + income statement)
  - FX rate downloader (currency pairs)
  - VIX index downloader

Template Method pattern::

    BaseDownloader (abstract)
    ├── _validate_params()    — hook: parameter validation
    ├── _pre_download()       — hook: pre-download setup
    ├── _execute_download()   — abstract: actual API call
    ├── _post_download()      — hook: post-download processing
    └── download()            — template: orchestrates the workflow

Each concrete downloader only overrides the methods it needs,
while inheriting circuit breaker integration, rate limiting,
and structured logging from the base class.

References:
  - Gamma, E. et al. (1994). Design Patterns. Addison-Wesley.

"""

import random
import time
from abc import ABC, abstractmethod

from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.rate_limiter import TokenBucketRateLimiter


class BaseDownloader(ABC):
    """Abstract base class for all Yahoo Finance data downloaders.

    Provides shared infrastructure for circuit breaker protection,
    rate limiting, retry logic, and structured logging. Concrete
    subclasses implement ``_execute_download()`` for their specific
    data type.

    :param source_name: Identifier for this data source (prices, fx, etc.)
    :type source_name: str
    :param api_delay: Delay between API calls in seconds
    :type api_delay: float
    :param max_retries: Maximum retry attempts
    :type max_retries: int
    :param backoff_base: Exponential backoff base multiplier
    :type backoff_base: float
    :param circuit_breaker: Optional pre-configured circuit breaker
    :type circuit_breaker: CircuitBreaker or None
    :param rate_limiter: Optional pre-configured rate limiter
    :type rate_limiter: TokenBucketRateLimiter or None
    :param cb_failure_threshold: Circuit breaker failure threshold
    :type cb_failure_threshold: int
    :param cb_recovery_timeout: Circuit breaker recovery timeout (seconds)
    :type cb_recovery_timeout: float
    """

    def __init__(
        self,
        source_name: str,
        api_delay: float = 0.5,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        circuit_breaker: CircuitBreaker = None,
        rate_limiter: TokenBucketRateLimiter = None,
        cb_failure_threshold: int = 10,
        cb_recovery_timeout: float = 120.0,
    ):
        self.source_name = source_name
        self.api_delay = api_delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            source_name,
            failure_threshold=cb_failure_threshold,
            recovery_timeout=cb_recovery_timeout,
        )

        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            rate=1.0 / max(api_delay, 0.1),
            capacity=10,
            name=source_name,
        )

        self._download_count = 0
        self._success_count = 0
        self._failure_count = 0

    def _check_circuit(self) -> bool:
        """Check if the circuit breaker allows a request.

        :return: True if requests are allowed
        :rtype: bool
        :raises CircuitBreakerOpenError: if circuit is OPEN (optional)
        """
        return self.circuit_breaker.allow_request()

    def _jitter_wait(self, attempt: int) -> float:
        """Exponential backoff with full jitter for retry storms.

        Uses the AWS-recommended "full jitter" strategy: sleep for a
        random duration in ``[0, backoff_base ** attempt]`` seconds.
        This spreads concurrent retries across time, avoiding the
        thundering herd problem when many parallel workers fail together.

        Reference: Exponential Backoff And Jitter (AWS Architecture Blog,
        Marc Brooker, 2015).

        :param attempt: Current retry attempt (0-indexed)
        :type attempt: int
        :return: Actual time waited in seconds
        :rtype: float
        """
        cap = self.backoff_base**attempt
        wait = random.uniform(0, cap)
        if wait > 0:
            time.sleep(wait)
        return wait

    def _validate_params(self, **kwargs) -> bool:
        """Hook: validate download parameters before execution.

        Override in subclasses for source-specific validation.

        :return: True if parameters are valid
        :rtype: bool
        """
        return True

    def _pre_download(self, **kwargs):
        """Hook: pre-download setup (e.g. parameter transformation).

        Override in subclasses for source-specific preparation.
        """
        pass

    @abstractmethod
    def _execute_download(self, **kwargs):
        """Abstract: execute the actual Yahoo Finance API call.

        Must be implemented by each concrete downloader subclass.

        :return: Downloaded data (DataFrame or dict)
        """
        raise NotImplementedError

    def _post_download(self, result, **kwargs):
        """Hook: post-download processing (e.g. column renaming).

        Override in subclasses for source-specific post-processing.

        :param result: Raw download result
        :return: Processed result
        """
        return result

    @property
    def stats(self) -> dict:
        """Download statistics for this data source.

        :return: Dictionary with download, success, failure counts
        :rtype: dict
        """
        return {
            "source": self.source_name,
            "downloads": self._download_count,
            "successes": self._success_count,
            "failures": self._failure_count,
            "success_rate": (
                round(self._success_count / self._download_count * 100, 1)
                if self._download_count > 0
                else 0.0
            ),
            "circuit_breaker": self.circuit_breaker.to_dict(),
            "rate_limiter": self.rate_limiter.to_dict(),
        }
