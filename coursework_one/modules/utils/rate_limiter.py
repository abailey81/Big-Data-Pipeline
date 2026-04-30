"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Token bucket rate limiter for API call throttling
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Implements the Token Bucket algorithm for rate limiting API calls
to Yahoo Finance. This is the industry-standard approach used by
cloud providers (AWS, GCP) and API gateways (Kong, Envoy).

Algorithm:
  - A bucket holds up to ``capacity`` tokens.
  - Tokens are added at a fixed ``refill_rate`` per second.
  - Each API call consumes one token.
  - If no tokens are available, the caller blocks until
    sufficient tokens have been replenished.

This prevents burst traffic from overwhelming the Yahoo Finance
API and triggering rate limits (Spec §7.2 Issue 5).

References:
  - Turner, J. (1986). "New Directions in Communications."
    IEEE Communications Magazine, 24(10), 8-15.
  - Tanenbaum, A.S. (2011). Computer Networks. 5th ed. Pearson.

"""

import threading
import time

from modules.utils.info_logger import pipeline_logger


class TokenBucketRateLimiter:
    """Token bucket rate limiter for controlling API request frequency.

    Thread-safe implementation using a lock to support concurrent
    downloads via ``ThreadPoolExecutor``.

    :param rate: Maximum requests per second (tokens added per second)
    :type rate: float
    :param capacity: Maximum burst size (bucket capacity)
    :type capacity: int
    :param name: Identifier for this limiter (for logging)
    :type name: str

    :example:
        >>> limiter = TokenBucketRateLimiter(rate=2.0, capacity=5)
        >>> for ticker in tickers:
        ...     limiter.acquire()  # blocks if bucket is empty
        ...     download(ticker)
    """

    def __init__(self, rate: float = 2.0, capacity: int = 5, name: str = "api"):
        self.rate = rate
        self.capacity = capacity
        self.name = name
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._total_waits = 0
        self._total_wait_time = 0.0

    def acquire(self, tokens: int = 1) -> float:
        """Acquire tokens, blocking if the bucket is empty.

        Refills the bucket based on elapsed time, then either
        consumes a token immediately or sleeps until one is available.

        :param tokens: Number of tokens to consume (default 1)
        :type tokens: int
        :return: Time waited in seconds (0 if token was available)
        :rtype: float
        """
        with self._lock:
            self._refill()

            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0

            # Calculate wait time for token replenishment
            deficit = tokens - self._tokens
            wait_time = deficit / self.rate
            self._total_waits += 1
            self._total_wait_time += wait_time

        # Sleep outside the lock to avoid blocking other threads
        if wait_time > 0:
            pipeline_logger.debug(
                f"[RateLimiter:{self.name}] Throttling for " f"{wait_time:.2f}s (bucket empty)"
            )
            time.sleep(wait_time)

        with self._lock:
            self._refill()
            self._tokens = max(0, self._tokens - tokens)

        return wait_time

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill.

        Called internally while holding the lock.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self.rate
        self._tokens = min(self.capacity, self._tokens + new_tokens)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens (approximate).

        :return: Number of tokens currently in the bucket
        :rtype: float
        """
        with self._lock:
            self._refill()
            return self._tokens

    def to_dict(self) -> dict:
        """Export rate limiter state for metrics reporting.

        :return: Dictionary with current state and statistics
        :rtype: dict
        """
        with self._lock:
            self._refill()
            return {
                "name": self.name,
                "rate": self.rate,
                "capacity": self.capacity,
                "available_tokens": round(self._tokens, 2),
                "total_waits": self._total_waits,
                "total_wait_time": round(self._total_wait_time, 2),
            }
