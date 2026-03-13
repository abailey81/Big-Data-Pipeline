"""
Tests for progress tracker module.

Covers:
  - modules.utils.progress_tracker.PipelineProgressTracker
  - Both Rich-available and plain-text fallback paths
"""

from unittest.mock import MagicMock, patch

import pytest

from modules.utils.progress_tracker import PipelineProgressTracker

# ── Banner tests ──────────────────────────────────────────────────────


class TestPrintBanner:

    def test_banner_with_rich(self):
        tracker = PipelineProgressTracker("run-123", total_tickers=50)
        # Should not raise
        tracker.print_banner()

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_banner_without_rich(self):
        tracker = PipelineProgressTracker("run-abc", total_tickers=10)
        tracker._console = None
        tracker.print_banner()


# ── Source progress tests ─────────────────────────────────────────────


class TestSourceProgress:

    def test_context_manager_yields_callable(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=5)
        with tracker.source_progress("prices", 3) as update:
            assert callable(update)

    def test_update_increments_outcomes(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=5)
        with tracker.source_progress("prices", 3) as update:
            update("AAPL", "SUCCESS")
            update("MSFT", "FAILED")
            update("GOOG", "SKIPPED")
        outcomes = tracker._source_outcomes["prices"]
        assert outcomes["success"] == 1
        assert outcomes["failed"] == 1
        assert outcomes["skipped"] == 1

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_plain_text_fallback(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=2)
        tracker._console = None
        with tracker.source_progress("fx", 2) as update:
            update("GBPUSD=X", "SUCCESS")
            update("EURUSD=X", "SUCCESS")
        assert tracker._source_outcomes["fx"]["success"] == 2

    def test_zero_total_uses_plain_update(self):
        tracker = PipelineProgressTracker("run-1")
        with tracker.source_progress("empty", 0) as update:
            pass  # no items to process

    def test_unknown_status_ignored(self):
        tracker = PipelineProgressTracker("run-1")
        with tracker.source_progress("test", 1) as update:
            update("AAPL", "UNKNOWN_STATUS")
        outcomes = tracker._source_outcomes["test"]
        assert outcomes["success"] == 0
        assert outcomes["failed"] == 0
        assert outcomes["skipped"] == 0


# ── Phase indicators tests ────────────────────────────────────────────


class TestPhaseIndicators:

    def test_phase_start_with_rich(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_phase_start("edgar_fundamentals")

    def test_phase_complete_with_rich(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_phase_complete("prices", 12.5, 50000)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_phase_start_without_rich(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        tracker.print_phase_start("vix")

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_phase_complete_without_rich(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        tracker.print_phase_complete("fx", 3.2, 6244)


# ── Circuit breaker status tests ──────────────────────────────────────


class TestCircuitBreakerStatus:

    def _make_breaker(self, name="prices", state="CLOSED", failures=0, trips=0):
        cb = MagicMock()
        cb.to_dict.return_value = {
            "name": name,
            "state": state,
            "failure_count": failures,
            "total_trips": trips,
            "failure_threshold": 10,
        }
        return cb

    def test_print_circuit_breaker_status_rich(self):
        tracker = PipelineProgressTracker("run-1")
        breakers = [
            self._make_breaker("prices", "CLOSED"),
            self._make_breaker("fx", "OPEN", failures=5, trips=1),
            self._make_breaker("vix", "HALF_OPEN", failures=3),
        ]
        tracker.print_circuit_breaker_status(breakers)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_print_circuit_breaker_status_plain(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        breakers = [self._make_breaker("prices", "CLOSED")]
        tracker.print_circuit_breaker_status(breakers)


# ── Summary table tests ──────────────────────────────────────────────


class TestPrintSummary:

    def _make_metrics(self):
        return {
            "run_id": "run-test",
            "total_elapsed_seconds": 45.3,
            "sources": {
                "prices": {
                    "elapsed_seconds": 30.0,
                    "total_rows": 50000,
                    "success": 600,
                    "failed": 5,
                    "skipped": 0,
                },
                "fx": {
                    "elapsed_seconds": 5.0,
                    "total_rows": 6244,
                    "success": 4,
                    "failed": 0,
                    "skipped": 0,
                },
            },
        }

    def test_print_summary_rich(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_summary(self._make_metrics())

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_print_summary_plain(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        tracker.print_summary(self._make_metrics())

    def test_print_summary_empty_sources(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_summary({"sources": {}, "total_elapsed_seconds": 0})

    def test_print_summary_zero_syms(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_summary(
            {
                "run_id": "x",
                "total_elapsed_seconds": 0,
                "sources": {
                    "test": {
                        "elapsed_seconds": 0,
                        "total_rows": 0,
                        "success": 0,
                        "failed": 0,
                        "skipped": 0,
                    },
                },
            }
        )


# ── Health check display tests ────────────────────────────────────────


class TestPrintHealthChecks:

    def _make_result(self, name="postgresql", healthy=True, latency_ms=5.0, message="OK"):
        r = MagicMock()
        r.name = name
        r.healthy = healthy
        r.latency_ms = latency_ms
        r.message = message
        return r

    def test_print_health_checks_rich(self):
        tracker = PipelineProgressTracker("run-1")
        results = [
            self._make_result("postgresql", True, 5.0, "Connected"),
            self._make_result("minio", False, 0.0, "Connection refused"),
            self._make_result("yahoo_finance", True, 120.0, "Reachable"),
        ]
        tracker.print_health_checks(results)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_print_health_checks_plain(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        results = [self._make_result("postgresql", True, 5.0, "OK")]
        tracker.print_health_checks(results)


# ── Downloader stats display tests ────────────────────────────────────


class TestPrintDownloaderStats:

    def _make_downloader(self, source="prices", downloads=100, successes=95, failures=5, waits=3):
        dl = MagicMock()
        rate = round(successes / downloads * 100, 1) if downloads > 0 else 0
        dl.stats = {
            "source": source,
            "downloads": downloads,
            "successes": successes,
            "failures": failures,
            "success_rate": rate,
            "rate_limiter": {"total_waits": waits},
        }
        return dl

    def test_print_downloader_stats_rich(self):
        tracker = PipelineProgressTracker("run-1")
        downloaders = [
            self._make_downloader("prices", 600, 595, 5, 10),
            self._make_downloader("fx", 4, 4, 0, 0),
        ]
        tracker.print_downloader_stats(downloaders)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_print_downloader_stats_plain(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        downloaders = [self._make_downloader("prices")]
        tracker.print_downloader_stats(downloaders)

    def test_low_success_rate_styling(self):
        tracker = PipelineProgressTracker("run-1")
        downloaders = [self._make_downloader("test", 100, 30, 70, 0)]
        tracker.print_downloader_stats(downloaders)

    def test_medium_success_rate_styling(self):
        tracker = PipelineProgressTracker("run-1")
        downloaders = [self._make_downloader("test", 100, 60, 40, 0)]
        tracker.print_downloader_stats(downloaders)


# ── Close / lifecycle tests ──────────────────────────────────────────


class TestTrackerLifecycle:

    def test_close_without_live(self):
        """close() is safe to call even when Live was never started."""
        tracker = PipelineProgressTracker("run-1")
        tracker.close()  # should not raise

    def test_close_stops_live(self):
        tracker = PipelineProgressTracker("run-1")
        mock_live = MagicMock()
        tracker._live = mock_live
        tracker.close()
        mock_live.stop.assert_called_once()
        assert tracker._live is None

    def test_close_idempotent(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.close()
        tracker.close()  # second call should not raise


# ── _PipelineHeader tests ────────────────────────────────────────────


class TestPipelineHeader:

    def test_header_renders_without_error(self):
        from modules.utils.progress_tracker import _PipelineHeader

        tracker = PipelineProgressTracker("run-test", total_tickers=100)
        tracker._source_outcomes = {
            "prices": {"success": 90, "failed": 5, "skipped": 5},
            "fx": {"success": 4, "failed": 0, "skipped": 0},
        }
        header = _PipelineHeader(tracker)
        console = MagicMock()
        options = MagicMock()
        # __rich_console__ is a generator — consume it
        panels = list(header.__rich_console__(console, options))
        assert len(panels) > 0


# ── Parallel group start tests ───────────────────────────────────────


class TestParallelGroupStart:

    def test_rich_parallel_group(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_parallel_group_start("Group A", ["prices", "fx", "vix"], 3)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_plain_parallel_group(self):
        tracker = PipelineProgressTracker("run-1")
        tracker._console = None
        tracker.print_parallel_group_start("Group B", ["edgar", "finnhub"], 2)

    def test_single_thread_label(self):
        tracker = PipelineProgressTracker("run-1")
        tracker.print_parallel_group_start("Solo", ["prices"], 1)


# ── Data verification tests ─────────────────────────────────────────


class TestDataVerification:

    def _make_mock_db(self):
        """Create a mock db client with query-aware responses."""
        mock_db = MagicMock()

        def _query_router(query):
            if "GROUP BY field_name" in query and "fundamentals" in query:
                return [
                    ("net_income", 500, 480, 96.0, "2023-01-01", "2024-06-15"),
                ]
            if "GROUP BY field_name" in query and "company_ratios" in query:
                return [
                    ("pe_trailing", 300, 250, 83.3, "2024-01-01", "2024-06-15"),
                ]
            return [(100, 5, "2024-01-01", "2024-06-15", 99.5, 99.0, 98.0)]

        mock_db.read_query.side_effect = _query_router
        return mock_db

    def test_verification_runs_with_rich(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=50)
        mock_db = self._make_mock_db()
        tracker.print_data_verification(mock_db)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_verification_runs_plain_text(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=50)
        tracker._console = None
        mock_db = self._make_mock_db()
        tracker.print_data_verification(mock_db)

    def test_verification_handles_query_errors(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=50)
        mock_db = MagicMock()
        mock_db.read_query.side_effect = Exception("Connection lost")
        # Should not raise — errors are caught and logged
        tracker.print_data_verification(mock_db)

    def test_verification_handles_empty_tables(self):
        tracker = PipelineProgressTracker("run-1", total_tickers=50)
        mock_db = MagicMock()

        def _empty_router(query):
            if "GROUP BY field_name" in query:
                return []
            return [(0, 0, None, None, 0, 0, 0)]

        mock_db.read_query.side_effect = _empty_router
        tracker.print_data_verification(mock_db)

    @patch("modules.utils.progress_tracker.RICH_AVAILABLE", False)
    def test_verification_plain_with_fund_and_ratio_fields(self):
        """Tests the fundamentals and ratios breakdown paths in plain-text mode."""
        tracker = PipelineProgressTracker("run-1", total_tickers=50)
        tracker._console = None
        mock_db = MagicMock()

        def side_effect(query):
            if "GROUP BY field_name" in query and "fundamentals" in query:
                return [
                    ("net_income", 500, 480, 96.0, "2023-01-01", "2024-06-15"),
                    ("total_revenue", 500, 500, 100.0, "2023-01-01", "2024-06-15"),
                ]
            if "GROUP BY field_name" in query and "company_ratios" in query:
                return [
                    ("pe_trailing", 300, 250, 83.3, "2024-01-01", "2024-06-15"),
                ]
            return [(100, 5, "2024-01-01", "2024-06-15", 99.5, 99.0, 98.0)]

        mock_db.read_query.side_effect = side_effect
        tracker.print_data_verification(mock_db)
