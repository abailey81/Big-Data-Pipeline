"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Pipeline observability and performance metrics
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Provides lightweight instrumentation for tracking pipeline execution:
  - Per-source timing and record counts
  - Data quality summary (completeness, error rates)
  - Final run report logged at pipeline completion

Thread-safe: all mutable state is protected by ``threading.Lock``
to support concurrent source-level and ticker-level parallelism.

"""

import threading
import time
from collections import defaultdict
from contextlib import contextmanager

from modules.utils.info_logger import pipeline_logger


class PipelineMetrics:
    """Collects and reports pipeline execution metrics.

    Tracks per-source timing, record counts, success/failure rates,
    and data quality indicators across a single pipeline run.

    Thread-safe: protected by ``threading.Lock`` for concurrent
    access from multiple source-processing threads.

    :param run_id: Unique identifier for this pipeline run
    :type run_id: str

    :example:
        >>> metrics = PipelineMetrics('run-abc')
        >>> with metrics.track('prices'):
        ...     load_prices()
        >>> metrics.record_outcome('prices', 'AAPL', 'SUCCESS', rows=252)
        >>> metrics.log_summary()
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.start_time = time.monotonic()
        self._timings = {}
        self._counts = defaultdict(lambda: {"success": 0, "failed": 0, "skipped": 0, "total_rows": 0})
        self._lock = threading.Lock()

    @contextmanager
    def track(self, source: str):
        """Context manager to time a data source processing phase.

        Thread-safe: timing writes are protected by lock.

        :param source: Data source name (prices, fundamentals, fx, vix)
        :type source: str

        :example:
            >>> with metrics.track('prices'):
            ...     download_and_load_prices()
        """
        t0 = time.monotonic()
        pipeline_logger.info(f"[{source.upper()}] Phase started")
        try:
            yield
        finally:
            elapsed = time.monotonic() - t0
            with self._lock:
                self._timings[source] = elapsed
            pipeline_logger.info(f"[{source.upper()}] Phase completed in {elapsed:.1f}s")

    def record_outcome(self, source: str, symbol: str, status: str, rows: int = 0):
        """Record the outcome of processing a single ticker/pair.

        Thread-safe: count updates are protected by lock.

        :param source: Data source name
        :type source: str
        :param symbol: Ticker or pair identifier
        :type symbol: str
        :param status: Outcome status (SUCCESS, FAILED, SKIPPED)
        :type status: str
        :param rows: Number of rows loaded
        :type rows: int
        """
        key = status.lower()
        with self._lock:
            if key in self._counts[source]:
                self._counts[source][key] += 1
            self._counts[source]["total_rows"] += rows

    def log_summary(self):
        """Log a comprehensive pipeline execution summary.

        Outputs timing breakdown, success/failure rates, and total
        record counts for each data source.
        """
        total_elapsed = time.monotonic() - self.start_time
        pipeline_logger.info("=" * 60)
        pipeline_logger.info("PIPELINE RUN SUMMARY")
        pipeline_logger.info(f"  Run ID:       {self.run_id}")
        pipeline_logger.info(f"  Total time:   {total_elapsed:.1f}s")
        pipeline_logger.info("-" * 60)

        for source, timing in self._timings.items():
            c = self._counts[source]
            total_symbols = c["success"] + c["failed"] + c["skipped"]
            success_rate = (c["success"] / total_symbols * 100) if total_symbols > 0 else 0.0
            pipeline_logger.info(
                f"  {source:15s}  "
                f"time={timing:6.1f}s  "
                f"rows={c['total_rows']:>8,}  "
                f"ok={c['success']}  "
                f"fail={c['failed']}  "
                f"skip={c['skipped']}  "
                f"rate={success_rate:.0f}%"
            )

        pipeline_logger.info("=" * 60)

    def to_dict(self) -> dict:
        """Export metrics as a dictionary for structured logging or storage.

        :return: Metrics dictionary with timings and counts per source
        :rtype: dict
        """
        return {
            "run_id": self.run_id,
            "total_elapsed_seconds": time.monotonic() - self.start_time,
            "sources": {
                source: {
                    "elapsed_seconds": self._timings.get(source, 0),
                    **self._counts[source],
                }
                for source in set(list(self._timings) + list(self._counts))
            },
        }
