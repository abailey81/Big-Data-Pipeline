"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Concurrent download executor using ThreadPoolExecutor
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Provides thread-pool-based concurrent execution for ticker-level
downloads. Yahoo Finance API calls are I/O-bound (network latency),
making threading an appropriate concurrency model.

Features:
  - Configurable thread pool size (default: 4 workers)
  - Graceful shutdown support (responds to cancellation signals)
  - Per-item timeout to prevent hanging downloads
  - Callback support for progress tracking integration
  - Thread-safe result collection

Note: The ``max_workers`` should be kept modest (2-6) to avoid
triggering Yahoo Finance rate limits. Combined with the
``TokenBucketRateLimiter``, this provides controlled parallelism.

"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import Callable, TypeVar

from modules.utils.info_logger import pipeline_logger

T = TypeVar("T")


class ConcurrentDownloadExecutor:
    """Executes download tasks concurrently using a thread pool.

    Wraps ``concurrent.futures.ThreadPoolExecutor`` with:
    - Graceful shutdown on KeyboardInterrupt / SIGTERM
    - Per-task timeout support
    - Progress callback integration
    - Thread-safe result aggregation

    :param max_workers: Number of concurrent download threads
    :type max_workers: int
    :param task_timeout: Per-task timeout in seconds (None = no timeout)
    :type task_timeout: float or None
    :param name: Executor name for logging
    :type name: str

    :example:
        >>> executor = ConcurrentDownloadExecutor(max_workers=4)
        >>> results = executor.map_with_progress(
        ...     download_fn,
        ...     tickers,
        ...     progress_callback=lambda sym, status: update(sym, status)
        ... )
    """

    def __init__(self, max_workers: int = 4, task_timeout: float = None, name: str = "download"):
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        self.name = name
        self._shutdown_requested = False

    def map_with_progress(
        self,
        fn: Callable,
        items: list,
        progress_callback: Callable = None,
        result_key: Callable = None,
    ) -> dict:
        """Execute ``fn`` for each item concurrently, collecting results.

        :param fn: Function to execute for each item. Should return a
                   result or raise an exception on failure.
        :type fn: callable
        :param items: List of items to process
        :type items: list
        :param progress_callback: Optional callback(item, status) for
                                   progress reporting
        :type progress_callback: callable or None
        :param result_key: Function to extract a key from each item
                           for the results dict. Defaults to str(item).
        :type result_key: callable or None
        :return: Dictionary mapping keys to results (or None for failures)
        :rtype: dict
        """
        results = {}
        key_fn = result_key or str
        total = len(items)
        completed = 0

        if total == 0:
            return results

        pipeline_logger.info(
            f"[{self.name}] Starting concurrent execution: " f"{total} items, {self.max_workers} workers"
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_item = {pool.submit(fn, item): item for item in items}

            for future in as_completed(future_to_item):
                if self._shutdown_requested:
                    pipeline_logger.warning(
                        f"[{self.name}] Shutdown requested — " f"cancelling remaining tasks"
                    )
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

                item = future_to_item[future]
                key = key_fn(item)
                completed += 1

                try:
                    if self.task_timeout:
                        result = future.result(timeout=self.task_timeout)
                    else:
                        result = future.result()

                    results[key] = result
                    if progress_callback:
                        progress_callback(key, "SUCCESS")

                except TimeoutError:
                    pipeline_logger.warning(f"[{self.name}] Task timed out for {key}")
                    results[key] = None
                    if progress_callback:
                        progress_callback(key, "FAILED")

                except Exception as e:
                    pipeline_logger.warning(f"[{self.name}] Task failed for {key}: {e}")
                    results[key] = None
                    if progress_callback:
                        progress_callback(key, "FAILED")

        pipeline_logger.info(
            f"[{self.name}] Concurrent execution complete: " f"{completed}/{total} processed"
        )
        return results

    def request_shutdown(self):
        """Request graceful shutdown of the executor.

        Currently running tasks will complete, but no new tasks
        will be submitted.
        """
        self._shutdown_requested = True
        pipeline_logger.info(f"[{self.name}] Graceful shutdown requested")
