"""
Tests for modules.utils.circuit_breaker.

Covers the full circuit breaker state machine:
  CLOSED → OPEN → HALF_OPEN → CLOSED (recovery)
  HALF_OPEN → OPEN (probe failure)
  Manual reset, metrics export, edge cases.
"""

import time
from unittest.mock import patch

import pytest

from modules.utils.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerStates:
    """Verify all circuit breaker state transitions."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_allows_requests_when_closed(self):
        cb = CircuitBreaker("test")
        assert cb.allow_request() is True

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb._state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_blocks_requests_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_transitions_to_half_open_after_recovery(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN
        time.sleep(0.06)
        # Accessing .state triggers the OPEN → HALF_OPEN transition
        assert cb.state == CircuitState.HALF_OPEN

    def test_allows_probe_in_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        assert cb.allow_request() is True  # HALF_OPEN allows probe

    def test_closes_after_success_threshold_in_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01, success_threshold=2)
        cb.record_failure()
        time.sleep(0.02)
        # Now in HALF_OPEN — need 2 successes
        _ = cb.state  # trigger transition
        cb.record_success()
        assert cb._state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb._state == CircuitState.CLOSED

    def test_reopens_on_probe_failure(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        _ = cb.state  # HALF_OPEN
        cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_success_resets_failure_count_when_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        # Now need 3 more failures to trip
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerMetrics:
    """Test metrics export and trip counting."""

    def test_total_trips_increments_on_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb._total_trips == 1
        # Recover and trip again
        time.sleep(0.02)
        _ = cb.state  # HALF_OPEN
        cb.record_failure()  # re-opens
        assert cb._total_trips == 2

    def test_to_dict_structure(self):
        cb = CircuitBreaker("yahoo_api", failure_threshold=5, recovery_timeout=60.0)
        d = cb.to_dict()
        assert d["name"] == "yahoo_api"
        assert d["state"] == "CLOSED"
        assert d["failure_count"] == 0
        assert d["total_trips"] == 0
        assert d["failure_threshold"] == 5
        assert d["recovery_timeout"] == 60.0

    def test_to_dict_reflects_open_state(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        d = cb.to_dict()
        assert d["state"] == "OPEN"
        assert d["failure_count"] == 1
        assert d["total_trips"] == 1


class TestCircuitBreakerReset:
    """Test manual reset functionality."""

    def test_manual_reset_closes_circuit(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN
        cb.reset()
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._success_count == 0

    def test_reset_allows_requests(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.allow_request() is False
        cb.reset()
        assert cb.allow_request() is True


class TestCircuitBreakerEdgeCases:
    """Boundary conditions and edge cases."""

    def test_threshold_of_one(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_high_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=100)
        for _ in range(99):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_success_threshold_of_one(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01, success_threshold=1)
        cb.record_failure()
        time.sleep(0.02)
        _ = cb.state
        cb.record_success()
        assert cb._state == CircuitState.CLOSED

    def test_rapid_success_failure_alternation(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_success()  # resets counter
        cb.record_failure()
        cb.record_success()  # resets counter
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0
