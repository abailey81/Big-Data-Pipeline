"""
Tests for modules.utils.concurrent_executor.ConcurrentDownloadExecutor.

Covers:
  1. Basic execution with map_with_progress
  2. Progress callback invocation
  3. Error handling (function raises, executor stays alive)
  4. Empty item list
  5. Graceful shutdown
  6. Custom result_key function

All tests use simple in-memory functions to avoid I/O.
"""

import time
from unittest.mock import MagicMock

import pytest

from modules.utils.concurrent_executor import ConcurrentDownloadExecutor

# ── 1. Basic execution ──────────────────────────────────────────────


class TestBasicExecution:
    """Verify map_with_progress processes items and returns results."""

    def test_simple_function(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)
        results = executor.map_with_progress(lambda x: x * 2, [1, 2, 3, 4])
        assert results == {"1": 2, "2": 4, "3": 6, "4": 8}

    def test_string_items(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)
        results = executor.map_with_progress(lambda t: f"downloaded_{t}", ["AAPL", "MSFT", "GOOG"])
        assert results["AAPL"] == "downloaded_AAPL"
        assert results["MSFT"] == "downloaded_MSFT"
        assert results["GOOG"] == "downloaded_GOOG"

    def test_single_item(self):
        executor = ConcurrentDownloadExecutor(max_workers=1)
        results = executor.map_with_progress(lambda x: x**2, [7])
        assert results == {"7": 49}


# ── 2. Progress callback ────────────────────────────────────────────


class TestProgressCallback:
    """Verify the progress_callback is invoked correctly."""

    def test_callback_called_for_each_item(self):
        callback = MagicMock()
        executor = ConcurrentDownloadExecutor(max_workers=2)
        executor.map_with_progress(lambda x: x, ["A", "B", "C"], progress_callback=callback)
        assert callback.call_count == 3
        # All calls should have status='SUCCESS'
        for c in callback.call_args_list:
            assert c[0][1] == "SUCCESS"

    def test_callback_reports_failure_on_exception(self):
        callback = MagicMock()
        executor = ConcurrentDownloadExecutor(max_workers=1)

        def fail_always(x):
            raise ValueError("boom")

        executor.map_with_progress(fail_always, ["X"], progress_callback=callback)
        callback.assert_called_once_with("X", "FAILED")


# ── 3. Error handling ────────────────────────────────────────────────


class TestErrorHandling:
    """Verify executor resilience to task failures."""

    def test_failed_task_returns_none(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)

        def maybe_fail(x):
            if x == "BAD":
                raise RuntimeError("failure")
            return f"ok_{x}"

        results = executor.map_with_progress(maybe_fail, ["GOOD", "BAD", "ALSO_GOOD"])
        assert results["GOOD"] == "ok_GOOD"
        assert results["BAD"] is None
        assert results["ALSO_GOOD"] == "ok_ALSO_GOOD"

    def test_all_tasks_fail(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)
        results = executor.map_with_progress(
            lambda x: (_ for _ in ()).throw(RuntimeError("fail")), ["A", "B"]
        )
        assert results["A"] is None
        assert results["B"] is None


# ── 4. Empty items ──────────────────────────────────────────────────


class TestEmptyItems:
    """Verify behaviour with an empty item list."""

    def test_empty_list_returns_empty_dict(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)
        results = executor.map_with_progress(lambda x: x, [])
        assert results == {}

    def test_empty_list_does_not_call_callback(self):
        callback = MagicMock()
        executor = ConcurrentDownloadExecutor(max_workers=2)
        executor.map_with_progress(lambda x: x, [], progress_callback=callback)
        callback.assert_not_called()


# ── 5. Shutdown ─────────────────────────────────────────────────────


class TestShutdown:
    """Verify graceful shutdown mechanism."""

    def test_request_shutdown_sets_flag(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)
        assert executor._shutdown_requested is False
        executor.request_shutdown()
        assert executor._shutdown_requested is True

    def test_shutdown_stops_processing(self):
        """After shutdown is requested, remaining tasks should be skipped."""
        executor = ConcurrentDownloadExecutor(max_workers=1)
        processed = []

        def slow_fn(x):
            processed.append(x)
            if len(processed) >= 2:
                executor.request_shutdown()
            time.sleep(0.01)
            return x

        items = list(range(20))
        results = executor.map_with_progress(slow_fn, items)
        # Should have processed fewer items than the full list
        # (exact count depends on timing, but definitely < 20)
        assert len(results) < 20


# ── 6. Result key function ──────────────────────────────────────────


class TestResultKeyFunction:
    """Verify custom result_key function changes dict keys."""

    def test_custom_result_key(self):
        executor = ConcurrentDownloadExecutor(max_workers=2)
        items = [{"ticker": "AAPL", "id": 1}, {"ticker": "MSFT", "id": 2}]
        results = executor.map_with_progress(lambda x: x["id"] * 10, items, result_key=lambda x: x["ticker"])
        assert results["AAPL"] == 10
        assert results["MSFT"] == 20
