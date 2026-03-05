"""
Tests for modules.input.base_downloader.BaseDownloader.

Uses a ConcreteTestDownloader subclass to test the abstract base class,
since BaseDownloader cannot be instantiated directly. Covers:
  1. Initialisation with default and custom parameters
  2. Circuit breaker delegation via _check_circuit()
  3. Stats property structure and success rate calculation
  4. Rate limiter creation with correct params
  5. ABC enforcement (cannot instantiate BaseDownloader directly)
"""

from abc import ABC
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from modules.input.base_downloader import BaseDownloader
from modules.utils.circuit_breaker import CircuitBreaker
from modules.utils.rate_limiter import TokenBucketRateLimiter

# ── Concrete test subclass ──────────────────────────────────────────


class ConcreteTestDownloader(BaseDownloader):
    """Minimal concrete implementation for testing the ABC."""

    def _execute_download(self, **kwargs):
        """Return a simple DataFrame to satisfy the abstract contract."""
        return pd.DataFrame({"col": [1, 2, 3]})


# ── 1. Initialisation ───────────────────────────────────────────────


class TestBaseDownloaderInit:
    """Verify __init__ sets correct defaults and custom values."""

    def test_default_parameters(self):
        dl = ConcreteTestDownloader(source_name="test_source")
        assert dl.source_name == "test_source"
        assert dl.api_delay == 0.5
        assert dl.max_retries == 3
        assert dl.backoff_base == 2.0
        assert dl._download_count == 0
        assert dl._success_count == 0
        assert dl._failure_count == 0

    def test_custom_parameters(self):
        dl = ConcreteTestDownloader(
            source_name="custom",
            api_delay=1.0,
            max_retries=5,
            backoff_base=3.0,
            cb_failure_threshold=20,
            cb_recovery_timeout=300.0,
        )
        assert dl.api_delay == 1.0
        assert dl.max_retries == 5
        assert dl.backoff_base == 3.0

    def test_auto_creates_circuit_breaker(self):
        dl = ConcreteTestDownloader(
            source_name="prices",
            cb_failure_threshold=15,
            cb_recovery_timeout=90.0,
        )
        assert isinstance(dl.circuit_breaker, CircuitBreaker)
        assert dl.circuit_breaker.name == "prices"
        assert dl.circuit_breaker.failure_threshold == 15
        assert dl.circuit_breaker.recovery_timeout == 90.0

    def test_auto_creates_rate_limiter(self):
        dl = ConcreteTestDownloader(
            source_name="fx",
            api_delay=0.5,
        )
        assert isinstance(dl.rate_limiter, TokenBucketRateLimiter)
        assert dl.rate_limiter.name == "fx"
        # rate = 1.0 / max(api_delay, 0.1) = 1.0 / 0.5 = 2.0
        assert dl.rate_limiter.rate == pytest.approx(2.0)

    def test_injected_circuit_breaker(self):
        custom_cb = CircuitBreaker("injected", failure_threshold=99)
        dl = ConcreteTestDownloader(
            source_name="test",
            circuit_breaker=custom_cb,
        )
        assert dl.circuit_breaker is custom_cb
        assert dl.circuit_breaker.failure_threshold == 99

    def test_injected_rate_limiter(self):
        custom_rl = TokenBucketRateLimiter(rate=10.0, capacity=20, name="injected")
        dl = ConcreteTestDownloader(
            source_name="test",
            rate_limiter=custom_rl,
        )
        assert dl.rate_limiter is custom_rl
        assert dl.rate_limiter.rate == 10.0


# ── 2. Circuit check ────────────────────────────────────────────────


class TestCircuitCheck:
    """Verify _check_circuit() delegates to circuit_breaker.allow_request()."""

    def test_circuit_allows_when_closed(self):
        dl = ConcreteTestDownloader(source_name="test")
        assert dl._check_circuit() is True

    def test_circuit_blocks_when_open(self):
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False
        dl = ConcreteTestDownloader(source_name="test", circuit_breaker=mock_cb)
        assert dl._check_circuit() is False
        mock_cb.allow_request.assert_called_once()


# ── 3. Stats property ───────────────────────────────────────────────


class TestStatsProperty:
    """Verify stats dict structure and success rate calculation."""

    def test_stats_structure(self):
        dl = ConcreteTestDownloader(source_name="prices")
        s = dl.stats
        expected_keys = {
            "source",
            "downloads",
            "successes",
            "failures",
            "success_rate",
            "circuit_breaker",
            "rate_limiter",
        }
        assert set(s.keys()) == expected_keys

    def test_stats_initial_values(self):
        dl = ConcreteTestDownloader(source_name="fx")
        s = dl.stats
        assert s["source"] == "fx"
        assert s["downloads"] == 0
        assert s["successes"] == 0
        assert s["failures"] == 0
        assert s["success_rate"] == 0.0

    def test_success_rate_calculation(self):
        dl = ConcreteTestDownloader(source_name="test")
        dl._download_count = 10
        dl._success_count = 8
        dl._failure_count = 2
        s = dl.stats
        assert s["success_rate"] == pytest.approx(80.0)

    def test_success_rate_zero_downloads(self):
        dl = ConcreteTestDownloader(source_name="test")
        s = dl.stats
        assert s["success_rate"] == 0.0

    def test_stats_includes_circuit_breaker_dict(self):
        dl = ConcreteTestDownloader(source_name="test")
        s = dl.stats
        assert isinstance(s["circuit_breaker"], dict)
        assert "state" in s["circuit_breaker"]

    def test_stats_includes_rate_limiter_dict(self):
        dl = ConcreteTestDownloader(source_name="test")
        s = dl.stats
        assert isinstance(s["rate_limiter"], dict)
        assert "rate" in s["rate_limiter"]


# ── 4. Rate limiter integration ─────────────────────────────────────


class TestRateLimiterIntegration:
    """Verify rate limiter is created with parameters derived from api_delay."""

    def test_rate_derived_from_api_delay(self):
        dl = ConcreteTestDownloader(source_name="test", api_delay=0.25)
        # rate = 1.0 / max(0.25, 0.1) = 4.0
        assert dl.rate_limiter.rate == pytest.approx(4.0)

    def test_rate_with_very_small_delay(self):
        dl = ConcreteTestDownloader(source_name="test", api_delay=0.01)
        # rate = 1.0 / max(0.01, 0.1) = 1.0 / 0.1 = 10.0
        assert dl.rate_limiter.rate == pytest.approx(10.0)

    def test_capacity_default(self):
        dl = ConcreteTestDownloader(source_name="test", api_delay=1.0)
        assert dl.rate_limiter.capacity == 10


# ── 5. ABC enforcement ──────────────────────────────────────────────


class TestAbstractMethod:
    """Verify BaseDownloader cannot be instantiated directly."""

    def test_cannot_instantiate_base_downloader(self):
        with pytest.raises(TypeError, match="abstract method"):
            BaseDownloader(source_name="test")

    def test_incomplete_subclass_raises(self):
        """A subclass without _execute_download cannot be instantiated."""

        class IncompleteDownloader(BaseDownloader):
            pass

        with pytest.raises(TypeError, match="abstract method"):
            IncompleteDownloader(source_name="test")


# ── 6. Hook methods ─────────────────────────────────────────────────


class TestHookMethods:
    """Verify default hook method behaviour."""

    def test_validate_params_default_returns_true(self):
        dl = ConcreteTestDownloader(source_name="test")
        assert dl._validate_params() is True

    def test_pre_download_default_does_nothing(self):
        dl = ConcreteTestDownloader(source_name="test")
        result = dl._pre_download()
        assert result is None

    def test_post_download_default_returns_input(self):
        dl = ConcreteTestDownloader(source_name="test")
        data = {"key": "value"}
        assert dl._post_download(data) is data

    def test_execute_download_returns_dataframe(self):
        dl = ConcreteTestDownloader(source_name="test")
        result = dl._execute_download()
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 3
