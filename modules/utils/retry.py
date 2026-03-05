"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Retry decorator with configurable backoff strategies
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Implements a reusable ``@retry`` decorator that supports:
  - Exponential backoff (default)
  - Linear backoff
  - Constant delay
  - Configurable jitter to prevent thundering-herd effects
  - Exception whitelisting (only retry on specified exception types)
  - Pre-retry callback for structured logging

This decorator is applied to all Yahoo Finance API calls to handle
transient failures gracefully (Spec §7.2 Issue 5).

References:
  - AWS Architecture Blog: "Exponential Backoff And Jitter" (2015)
  - Nygard, M. (2007). Release It! Pragmatic Bookshelf.

"""

import functools
import random
import time
from typing import Callable, Type

from modules.utils.info_logger import pipeline_logger


def retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    backoff_strategy: str = "exponential",
    max_delay: float = 60.0,
    jitter: bool = True,
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable = None,
):
    """Decorator factory for retrying functions with configurable backoff.

    Supports three backoff strategies:
    - ``exponential``: delay = base^attempt (e.g. 2, 4, 8, 16...)
    - ``linear``: delay = base * attempt (e.g. 2, 4, 6, 8...)
    - ``constant``: delay = base (e.g. 2, 2, 2, 2...)

    When ``jitter=True``, a random factor in [0.5, 1.5] is multiplied
    to the computed delay to decorrelate concurrent callers.

    :param max_attempts: Maximum number of attempts (including first)
    :type max_attempts: int
    :param backoff_base: Base value for backoff calculation
    :type backoff_base: float
    :param backoff_strategy: One of 'exponential', 'linear', 'constant'
    :type backoff_strategy: str
    :param max_delay: Maximum delay cap in seconds
    :type max_delay: float
    :param jitter: Whether to add random jitter to delays
    :type jitter: bool
    :param retryable_exceptions: Tuple of exception types to retry on
    :type retryable_exceptions: tuple
    :param on_retry: Optional callback(attempt, exception, delay) for logging
    :type on_retry: callable or None
    :return: Decorated function with retry logic
    :rtype: callable

    :example:
        >>> @retry(max_attempts=3, backoff_strategy='exponential')
        ... def download_data(ticker):
        ...     return yf.download(ticker)

        >>> @retry(retryable_exceptions=(ConnectionError, TimeoutError))
        ... def fetch_with_timeout(url):
        ...     return requests.get(url, timeout=10)
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        break

                    delay = _compute_delay(attempt, backoff_base, backoff_strategy, max_delay, jitter)

                    if on_retry:
                        on_retry(attempt, e, delay)
                    else:
                        pipeline_logger.warning(
                            f"[retry] {func.__name__} attempt "
                            f"{attempt}/{max_attempts} failed: {e}. "
                            f"Retrying in {delay:.1f}s"
                        )

                    time.sleep(delay)

            pipeline_logger.error(f"[retry] {func.__name__} exhausted all " f"{max_attempts} attempts")
            raise last_exception

        wrapper._max_attempts = max_attempts
        wrapper._backoff_strategy = backoff_strategy
        return wrapper

    return decorator


def _compute_delay(attempt: int, base: float, strategy: str, max_delay: float, jitter: bool) -> float:
    """Calculate the delay before the next retry attempt.

    :param attempt: Current attempt number (1-indexed)
    :type attempt: int
    :param base: Base value for backoff
    :type base: float
    :param strategy: Backoff strategy name
    :type strategy: str
    :param max_delay: Maximum delay cap
    :type max_delay: float
    :param jitter: Whether to apply random jitter
    :type jitter: bool
    :return: Delay in seconds
    :rtype: float
    """
    if strategy == "exponential":
        delay = base**attempt
    elif strategy == "linear":
        delay = base * attempt
    elif strategy == "constant":
        delay = base
    else:
        delay = base**attempt

    delay = min(delay, max_delay)

    if jitter:
        delay *= random.uniform(0.5, 1.5)

    return delay
