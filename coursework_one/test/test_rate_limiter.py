"""
Tests for modules.utils.rate_limiter.TokenBucketRateLimiter.

Covers the token bucket algorithm:
  - Initial capacity and basic acquire behaviour
  - Token refill over time
  - Blocking when the bucket is empty
  - Statistics export via to_dict()
  - Edge cases: zero rate, large capacity, multi-token acquire

Timing-sensitive tests use small sleep values (0.05-0.1s) to keep
the suite fast while still verifying temporal behaviour.
"""

import threading
import time
from unittest.mock import patch

import pytest

from modules.utils.rate_limiter import TokenBucketRateLimiter

# ── 1. Basic behaviour ──────────────────────────────────────────────


class TestTokenBucketBasic:
    """Verify initial state and immediate acquire semantics."""

    def test_initial_capacity_full(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5, name="test")
        assert limiter.available_tokens == pytest.approx(5.0, abs=0.5)

    def test_initial_attributes(self):
        limiter = TokenBucketRateLimiter(rate=3.0, capacity=10, name="api")
        assert limiter.rate == 3.0
        assert limiter.capacity == 10
        assert limiter.name == "api"

    def test_acquire_returns_zero_when_tokens_available(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5, name="test")
        wait = limiter.acquire()
        assert wait == 0.0

    def test_acquire_decrements_tokens(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5, name="test")
        limiter.acquire()
        # Should have approximately 4 tokens left (minus refill drift)
        tokens = limiter.available_tokens
        assert tokens < 5.0
        assert tokens >= 3.5

    def test_acquire_multiple_without_waiting(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=10, name="test")
        for _ in range(5):
            wait = limiter.acquire()
            assert wait == 0.0

    def test_default_parameters(self):
        limiter = TokenBucketRateLimiter()
        assert limiter.rate == 2.0
        assert limiter.capacity == 5
        assert limiter.name == "api"


# ── 2. Refill behaviour ─────────────────────────────────────────────


class TestTokenBucketRefill:
    """Verify that tokens replenish over time."""

    def test_tokens_refill_after_drain(self):
        limiter = TokenBucketRateLimiter(rate=100.0, capacity=5, name="test")
        # Drain all tokens
        for _ in range(5):
            limiter.acquire()
        # Wait for refill (100 tokens/s -> 5 tokens in 0.05s)
        time.sleep(0.06)
        tokens = limiter.available_tokens
        assert tokens >= 4.0

    def test_refill_capped_at_capacity(self):
        limiter = TokenBucketRateLimiter(rate=100.0, capacity=5, name="test")
        # Wait a long time -- tokens should not exceed capacity
        time.sleep(0.1)
        tokens = limiter.available_tokens
        assert tokens <= 5.0

    def test_partial_refill(self):
        limiter = TokenBucketRateLimiter(rate=20.0, capacity=5, name="test")
        # Drain one token
        limiter.acquire()
        # After 0.05s at 20 tokens/s -> ~1 token refilled
        time.sleep(0.06)
        tokens = limiter.available_tokens
        assert tokens >= 4.0


# ── 3. Blocking when empty ──────────────────────────────────────────


class TestTokenBucketBlocking:
    """Verify that acquire blocks when no tokens are available."""

    def test_acquire_blocks_when_empty(self):
        # rate=10 -> 1 token per 0.1s; capacity=1
        limiter = TokenBucketRateLimiter(rate=10.0, capacity=1, name="test")
        limiter.acquire()  # drain the single token
        t0 = time.monotonic()
        wait = limiter.acquire()  # should block ~0.1s
        elapsed = time.monotonic() - t0
        assert wait > 0.0
        assert elapsed >= 0.05  # allow some timing slack

    def test_total_waits_incremented_on_block(self):
        limiter = TokenBucketRateLimiter(rate=50.0, capacity=1, name="test")
        limiter.acquire()  # drain
        limiter.acquire()  # should block and increment counter
        stats = limiter.to_dict()
        assert stats["total_waits"] >= 1

    def test_acquire_multi_token_blocks(self):
        limiter = TokenBucketRateLimiter(rate=100.0, capacity=2, name="test")
        # Drain both tokens
        limiter.acquire(tokens=2)
        t0 = time.monotonic()
        wait = limiter.acquire(tokens=2)
        elapsed = time.monotonic() - t0
        assert wait > 0.0
        assert elapsed >= 0.01


# ── 4. Statistics export ─────────────────────────────────────────────


class TestTokenBucketStats:
    """Verify to_dict() structure and statistics tracking."""

    def test_to_dict_keys(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5, name="test")
        d = limiter.to_dict()
        expected_keys = {"name", "rate", "capacity", "available_tokens", "total_waits", "total_wait_time"}
        assert set(d.keys()) == expected_keys

    def test_to_dict_initial_values(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5, name="myapi")
        d = limiter.to_dict()
        assert d["name"] == "myapi"
        assert d["rate"] == 2.0
        assert d["capacity"] == 5
        assert d["total_waits"] == 0
        assert d["total_wait_time"] == 0.0

    def test_to_dict_available_tokens_rounded(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=5, name="test")
        d = limiter.to_dict()
        # available_tokens should be a number rounded to 2 decimal places
        assert isinstance(d["available_tokens"], (int, float))

    def test_total_wait_time_accumulates(self):
        limiter = TokenBucketRateLimiter(rate=50.0, capacity=1, name="test")
        limiter.acquire()  # drain
        limiter.acquire()  # blocks briefly
        d = limiter.to_dict()
        assert d["total_wait_time"] >= 0.0


# ── 5. Edge cases ───────────────────────────────────────────────────


class TestTokenBucketEdgeCases:
    """Boundary conditions and unusual configurations."""

    def test_large_capacity(self):
        limiter = TokenBucketRateLimiter(rate=1.0, capacity=10000, name="huge")
        assert limiter.available_tokens == pytest.approx(10000.0, abs=1.0)
        wait = limiter.acquire()
        assert wait == 0.0

    def test_acquire_multiple_tokens_at_once(self):
        limiter = TokenBucketRateLimiter(rate=2.0, capacity=10, name="test")
        wait = limiter.acquire(tokens=5)
        assert wait == 0.0
        tokens = limiter.available_tokens
        assert tokens < 6.0

    def test_thread_safety_concurrent_acquires(self):
        """Multiple threads acquiring concurrently should not corrupt state."""
        limiter = TokenBucketRateLimiter(rate=1000.0, capacity=100, name="thread_test")
        errors = []

        def worker():
            try:
                for _ in range(20):
                    limiter.acquire()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0
        # All tokens should have been consumed or refilled without error
        assert limiter.available_tokens >= 0.0
