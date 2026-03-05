"""
Tests for modules.utils.retry (retry decorator and _compute_delay).

Covers:
  1. Success on first try (no retries needed)
  2. Eventual success after transient failures
  3. Exhaustion of all attempts
  4. Backoff strategies: exponential, linear, constant
  5. Selective exception retrying
  6. on_retry callback invocation
  7. functools.wraps preservation on decorated functions

time.sleep is mocked in most tests to avoid actual delays.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from modules.utils.retry import _compute_delay, retry

# ── 1. Success on first try ──────────────────────────────────────────


class TestRetrySuccess:
    """Function succeeds immediately -- no retries should occur."""

    @patch("modules.utils.retry.time.sleep")
    def test_no_retry_on_immediate_success(self, mock_sleep):
        @retry(max_attempts=3)
        def always_ok():
            return "ok"

        result = always_ok()
        assert result == "ok"
        mock_sleep.assert_not_called()

    @patch("modules.utils.retry.time.sleep")
    def test_return_value_passed_through(self, mock_sleep):
        @retry(max_attempts=3)
        def returns_dict():
            return {"key": 42}

        assert returns_dict() == {"key": 42}

    @patch("modules.utils.retry.time.sleep")
    def test_arguments_forwarded(self, mock_sleep):
        @retry(max_attempts=3)
        def add(a, b):
            return a + b

        assert add(3, 7) == 10


# ── 2. Eventual success ─────────────────────────────────────────────


class TestRetryEventualSuccess:
    """Function fails on first attempts, then succeeds."""

    @patch("modules.utils.retry.time.sleep")
    def test_succeeds_on_second_attempt(self, mock_sleep):
        call_count = {"n": 0}

        @retry(max_attempts=3, jitter=False)
        def fail_once():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise ConnectionError("transient")
            return "recovered"

        result = fail_once()
        assert result == "recovered"
        assert call_count["n"] == 2
        assert mock_sleep.call_count == 1

    @patch("modules.utils.retry.time.sleep")
    def test_succeeds_on_last_attempt(self, mock_sleep):
        call_count = {"n": 0}

        @retry(max_attempts=4, jitter=False)
        def fail_three_times():
            call_count["n"] += 1
            if call_count["n"] < 4:
                raise ValueError("still failing")
            return "finally"

        result = fail_three_times()
        assert result == "finally"
        assert call_count["n"] == 4
        # Should have slept 3 times (between attempts 1-2, 2-3, 3-4)
        assert mock_sleep.call_count == 3


# ── 3. Exhaustion of all attempts ───────────────────────────────────


class TestRetryExhausted:
    """Function always fails -- should raise after max_attempts."""

    @patch("modules.utils.retry.time.sleep")
    def test_raises_last_exception_after_exhaustion(self, mock_sleep):
        @retry(max_attempts=3, jitter=False)
        def always_fails():
            raise RuntimeError("permanent failure")

        with pytest.raises(RuntimeError, match="permanent failure"):
            always_fails()

    @patch("modules.utils.retry.time.sleep")
    def test_sleeps_between_attempts(self, mock_sleep):
        @retry(max_attempts=3, jitter=False)
        def always_fails():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            always_fails()

        # 3 attempts -> 2 sleeps (between 1-2 and 2-3; no sleep after last)
        assert mock_sleep.call_count == 2

    @patch("modules.utils.retry.time.sleep")
    def test_single_attempt_no_retry(self, mock_sleep):
        @retry(max_attempts=1)
        def fails():
            raise ValueError("one shot")

        with pytest.raises(ValueError, match="one shot"):
            fails()

        mock_sleep.assert_not_called()


# ── 4. Backoff strategies ───────────────────────────────────────────


class TestRetryStrategies:
    """Verify _compute_delay for each backoff strategy."""

    def test_exponential_strategy(self):
        # delay = base^attempt => 2^1=2, 2^2=4, 2^3=8
        delay = _compute_delay(attempt=1, base=2.0, strategy="exponential", max_delay=60.0, jitter=False)
        assert delay == pytest.approx(2.0)

        delay = _compute_delay(attempt=3, base=2.0, strategy="exponential", max_delay=60.0, jitter=False)
        assert delay == pytest.approx(8.0)

    def test_linear_strategy(self):
        # delay = base * attempt => 2*1=2, 2*2=4, 2*3=6
        delay = _compute_delay(attempt=1, base=2.0, strategy="linear", max_delay=60.0, jitter=False)
        assert delay == pytest.approx(2.0)

        delay = _compute_delay(attempt=3, base=2.0, strategy="linear", max_delay=60.0, jitter=False)
        assert delay == pytest.approx(6.0)

    def test_constant_strategy(self):
        # delay = base always
        for attempt in [1, 2, 5, 10]:
            delay = _compute_delay(
                attempt=attempt, base=3.0, strategy="constant", max_delay=60.0, jitter=False
            )
            assert delay == pytest.approx(3.0)

    def test_unknown_strategy_defaults_to_exponential(self):
        delay = _compute_delay(attempt=2, base=2.0, strategy="unknown_strategy", max_delay=60.0, jitter=False)
        # Falls back to exponential: 2^2 = 4
        assert delay == pytest.approx(4.0)

    def test_max_delay_cap(self):
        # Exponential: 2^10 = 1024, but capped at 60
        delay = _compute_delay(attempt=10, base=2.0, strategy="exponential", max_delay=60.0, jitter=False)
        assert delay == pytest.approx(60.0)

    def test_jitter_applies_random_factor(self):
        """With jitter, delay should be multiplied by uniform(0.5, 1.5)."""
        with patch("modules.utils.retry.random.uniform", return_value=1.2):
            delay = _compute_delay(attempt=1, base=2.0, strategy="exponential", max_delay=60.0, jitter=True)
            assert delay == pytest.approx(2.0 * 1.2)

    def test_jitter_range(self):
        """Multiple samples should stay in [0.5*base, 1.5*base] for attempt=1."""
        delays = set()
        for _ in range(50):
            d = _compute_delay(attempt=1, base=10.0, strategy="constant", max_delay=60.0, jitter=True)
            delays.add(d)
            assert 5.0 <= d <= 15.0


# ── 5. Selective exceptions ─────────────────────────────────────────


class TestRetrySelectiveExceptions:
    """Only retry on specified exception types."""

    @patch("modules.utils.retry.time.sleep")
    def test_retries_only_specified_exceptions(self, mock_sleep):
        call_count = {"n": 0}

        @retry(max_attempts=3, retryable_exceptions=(ConnectionError,), jitter=False)
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("net fail")
            return "ok"

        assert flaky() == "ok"
        assert call_count["n"] == 3

    @patch("modules.utils.retry.time.sleep")
    def test_does_not_retry_non_matching_exception(self, mock_sleep):
        @retry(max_attempts=3, retryable_exceptions=(ConnectionError,))
        def wrong_error():
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            wrong_error()

        mock_sleep.assert_not_called()

    @patch("modules.utils.retry.time.sleep")
    def test_multiple_retryable_exception_types(self, mock_sleep):
        call_count = {"n": 0}

        @retry(max_attempts=5, retryable_exceptions=(ConnectionError, TimeoutError), jitter=False)
        def alternating_errors():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("first")
            if call_count["n"] == 2:
                raise TimeoutError("second")
            return "done"

        assert alternating_errors() == "done"
        assert call_count["n"] == 3


# ── 6. on_retry callback ────────────────────────────────────────────


class TestRetryCallback:
    """Verify that on_retry is invoked with correct arguments."""

    @patch("modules.utils.retry.time.sleep")
    def test_on_retry_called_with_correct_args(self, mock_sleep):
        callback = MagicMock()

        @retry(max_attempts=3, jitter=False, backoff_base=2.0, backoff_strategy="constant", on_retry=callback)
        def fail_twice():
            fail_twice._count = getattr(fail_twice, "_count", 0) + 1
            if fail_twice._count < 3:
                raise RuntimeError(f"fail #{fail_twice._count}")
            return "ok"

        fail_twice()
        assert callback.call_count == 2

        # First callback: attempt=1, exception, delay
        args1 = callback.call_args_list[0]
        assert args1[0][0] == 1  # attempt number
        assert isinstance(args1[0][1], RuntimeError)
        assert isinstance(args1[0][2], float)  # delay

    @patch("modules.utils.retry.time.sleep")
    def test_on_retry_not_called_on_success(self, mock_sleep):
        callback = MagicMock()

        @retry(max_attempts=3, on_retry=callback)
        def succeeds():
            return "ok"

        succeeds()
        callback.assert_not_called()


# ── 7. Decorated function preservation ──────────────────────────────


class TestRetryDecorated:
    """Verify functools.wraps preserves function metadata."""

    def test_preserves_function_name(self):
        @retry(max_attempts=2)
        def my_special_function():
            pass

        assert my_special_function.__name__ == "my_special_function"

    def test_preserves_docstring(self):
        @retry(max_attempts=2)
        def documented_fn():
            """This is the docstring."""
            pass

        assert documented_fn.__doc__ == "This is the docstring."

    def test_wrapper_has_max_attempts_attribute(self):
        @retry(max_attempts=5, backoff_strategy="linear")
        def fn():
            pass

        assert fn._max_attempts == 5
        assert fn._backoff_strategy == "linear"
