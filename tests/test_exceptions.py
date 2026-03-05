"""
Tests for modules.utils.exceptions.

Covers the full custom exception hierarchy:
  PipelineError (base)
  +-- DataSourceError
  |   +-- APIConnectionError
  |   +-- APIRateLimitError
  |   +-- DataNotFoundError
  |   +-- CircuitBreakerOpenError
  +-- DataValidationError
  |   +-- SchemaValidationError
  |   +-- CrossFieldValidationError
  +-- StorageError
  |   +-- DatabaseConnectionError
  |   +-- DatabaseWriteError
  |   +-- ObjectStoreError
  +-- ConfigurationError

Test categories:
  1. Inheritance hierarchy (isinstance / issubclass)
  2. String formatting of error messages
  3. Structured details dict population
  4. Catching exceptions by parent class
"""

import pytest

from modules.utils.exceptions import (
    APIConnectionError,
    APIRateLimitError,
    CircuitBreakerOpenError,
    ConfigurationError,
    CrossFieldValidationError,
    DatabaseConnectionError,
    DatabaseWriteError,
    DataNotFoundError,
    DataSourceError,
    DataValidationError,
    ObjectStoreError,
    PipelineError,
    SchemaValidationError,
    StorageError,
)

# ── 1. Inheritance hierarchy ─────────────────────────────────────────


class TestExceptionHierarchy:
    """Verify the inheritance tree matches the specification."""

    def test_pipeline_error_is_exception(self):
        assert issubclass(PipelineError, Exception)

    def test_data_source_error_inherits_pipeline(self):
        assert issubclass(DataSourceError, PipelineError)

    def test_api_connection_error_inherits_data_source(self):
        err = APIConnectionError(source="yahoo_finance", ticker="AAPL", cause="timeout")
        assert isinstance(err, DataSourceError)
        assert isinstance(err, PipelineError)
        assert isinstance(err, Exception)

    def test_api_rate_limit_error_inherits_data_source(self):
        err = APIRateLimitError(source="yahoo_finance", retry_after=30.0)
        assert isinstance(err, DataSourceError)
        assert isinstance(err, PipelineError)

    def test_data_not_found_error_inherits_data_source(self):
        err = DataNotFoundError(source="yahoo_finance", ticker="DEAD")
        assert isinstance(err, DataSourceError)
        assert isinstance(err, PipelineError)

    def test_circuit_breaker_open_error_inherits_data_source(self):
        err = CircuitBreakerOpenError(circuit_name="prices", recovery_timeout=120.0)
        assert isinstance(err, DataSourceError)
        assert isinstance(err, PipelineError)

    def test_data_validation_error_inherits_pipeline(self):
        assert issubclass(DataValidationError, PipelineError)

    def test_schema_validation_error_inherits_data_validation(self):
        err = SchemaValidationError(field="close_price", value=-5.0, reason="must be non-negative")
        assert isinstance(err, DataValidationError)
        assert isinstance(err, PipelineError)

    def test_cross_field_validation_error_inherits_data_validation(self):
        err = CrossFieldValidationError(fields=["high", "low"], reason="high < low")
        assert isinstance(err, DataValidationError)
        assert isinstance(err, PipelineError)

    def test_storage_error_inherits_pipeline(self):
        assert issubclass(StorageError, PipelineError)

    def test_database_connection_error_inherits_storage(self):
        err = DatabaseConnectionError(host="localhost", port="5432", database="fift", cause="refused")
        assert isinstance(err, StorageError)
        assert isinstance(err, PipelineError)

    def test_database_write_error_inherits_storage(self):
        err = DatabaseWriteError(table="daily_prices", operation="upsert", cause="unique constraint")
        assert isinstance(err, StorageError)
        assert isinstance(err, PipelineError)

    def test_object_store_error_inherits_storage(self):
        err = ObjectStoreError(path="raw/prices.parquet", operation="put", cause="bucket not found")
        assert isinstance(err, StorageError)
        assert isinstance(err, PipelineError)

    def test_configuration_error_inherits_pipeline(self):
        err = ConfigurationError(param="api_delay", reason="must be > 0")
        assert isinstance(err, PipelineError)


# ── 2. String formatting ────────────────────────────────────────────


class TestExceptionMessages:
    """Verify human-readable string representations."""

    def test_pipeline_error_str(self):
        err = PipelineError("Something went wrong")
        assert str(err) == "Something went wrong"

    def test_api_connection_error_full_message(self):
        err = APIConnectionError(source="yahoo_finance", ticker="AAPL", cause="connection timed out")
        msg = str(err)
        assert "yahoo_finance" in msg
        assert "AAPL" in msg
        assert "connection timed out" in msg

    def test_api_connection_error_minimal_message(self):
        err = APIConnectionError(source="yahoo_finance")
        msg = str(err)
        assert "yahoo_finance" in msg
        assert "AAPL" not in msg

    def test_api_rate_limit_error_with_retry(self):
        err = APIRateLimitError(source="yahoo_finance", retry_after=30.0)
        msg = str(err)
        assert "Rate limited" in msg
        assert "30.0" in msg

    def test_api_rate_limit_error_without_retry(self):
        err = APIRateLimitError(source="yahoo_finance")
        msg = str(err)
        assert "Rate limited" in msg
        assert "retry after" not in msg

    def test_data_not_found_error_message(self):
        err = DataNotFoundError(source="yahoo_finance", ticker="DEAD")
        msg = str(err)
        assert "DEAD" in msg
        assert "yahoo_finance" in msg

    def test_circuit_breaker_open_error_message(self):
        err = CircuitBreakerOpenError(circuit_name="prices", recovery_timeout=120.0)
        msg = str(err)
        assert "prices" in msg
        assert "OPEN" in msg
        assert "120.0" in msg

    def test_schema_validation_error_message(self):
        err = SchemaValidationError(field="volume", value=-100, reason="must be non-negative")
        msg = str(err)
        assert "volume" in msg
        assert "-100" in msg
        assert "must be non-negative" in msg

    def test_cross_field_validation_error_message(self):
        err = CrossFieldValidationError(fields=["high", "low"], reason="high < low")
        msg = str(err)
        assert "high" in msg
        assert "low" in msg

    def test_database_connection_error_message(self):
        err = DatabaseConnectionError(host="db.host", port="5432", database="fift", cause="refused")
        msg = str(err)
        assert "db.host" in msg
        assert "5432" in msg
        assert "fift" in msg

    def test_database_write_error_message(self):
        err = DatabaseWriteError(table="daily_prices", operation="insert", cause="constraint violation")
        msg = str(err)
        assert "daily_prices" in msg
        assert "insert" in msg

    def test_object_store_error_message(self):
        err = ObjectStoreError(path="raw/data.parquet", operation="get", cause="not found")
        msg = str(err)
        assert "raw/data.parquet" in msg
        assert "get" in msg

    def test_configuration_error_message(self):
        err = ConfigurationError(param="api_delay_seconds", reason="must be positive")
        msg = str(err)
        assert "api_delay_seconds" in msg
        assert "must be positive" in msg


# ── 3. Details dict population ──────────────────────────────────────


class TestExceptionDetails:
    """Verify the structured details dict is populated correctly."""

    def test_pipeline_error_details_default_empty(self):
        err = PipelineError("msg")
        assert err.details == {}

    def test_pipeline_error_details_custom(self):
        err = PipelineError("msg", details={"key": "val"})
        assert err.details == {"key": "val"}

    def test_api_connection_error_details(self):
        err = APIConnectionError(source="yf", ticker="AAPL", cause="timeout")
        assert err.details["source"] == "yf"
        assert err.details["ticker"] == "AAPL"
        assert err.details["cause"] == "timeout"

    def test_api_rate_limit_error_details_with_retry(self):
        err = APIRateLimitError(source="yf", retry_after=60.0)
        assert err.details["source"] == "yf"
        assert err.details["retry_after"] == 60.0

    def test_api_rate_limit_error_details_without_retry(self):
        err = APIRateLimitError(source="yf")
        assert err.details["source"] == "yf"
        assert "retry_after" not in err.details

    def test_data_not_found_error_details(self):
        err = DataNotFoundError(source="yf", ticker="DEAD")
        assert err.details == {"source": "yf", "ticker": "DEAD"}

    def test_circuit_breaker_open_error_details(self):
        err = CircuitBreakerOpenError(circuit_name="fx", recovery_timeout=60.0)
        assert err.details["circuit_name"] == "fx"
        assert err.details["recovery_timeout"] == 60.0

    def test_schema_validation_error_details(self):
        err = SchemaValidationError(field="close", value=None, reason="null")
        assert err.details["field"] == "close"
        assert err.details["value"] == "None"
        assert err.details["reason"] == "null"

    def test_cross_field_validation_error_details(self):
        err = CrossFieldValidationError(fields=["a", "b"], reason="mismatch")
        assert err.details["fields"] == ["a", "b"]
        assert err.details["reason"] == "mismatch"

    def test_database_connection_error_details(self):
        err = DatabaseConnectionError(host="h", port="5432", database="db", cause="refused")
        assert err.details == {"host": "h", "port": "5432", "database": "db", "cause": "refused"}

    def test_database_write_error_details(self):
        err = DatabaseWriteError(table="tbl", operation="upsert", cause="deadlock")
        assert err.details == {"table": "tbl", "operation": "upsert", "cause": "deadlock"}

    def test_object_store_error_details(self):
        err = ObjectStoreError(path="/raw/f", operation="put", cause="denied")
        assert err.details == {"path": "/raw/f", "operation": "put", "cause": "denied"}

    def test_configuration_error_details(self):
        err = ConfigurationError(param="batch_size", reason="too large")
        assert err.details == {"param": "batch_size", "reason": "too large"}


# ── 4. Catching by parent class ─────────────────────────────────────


class TestExceptionCatching:
    """Verify that parent-class except clauses catch child exceptions."""

    def test_catch_api_connection_as_data_source(self):
        with pytest.raises(DataSourceError):
            raise APIConnectionError(source="yf", ticker="X", cause="net")

    def test_catch_api_rate_limit_as_pipeline(self):
        with pytest.raises(PipelineError):
            raise APIRateLimitError(source="yf", retry_after=10)

    def test_catch_data_not_found_as_data_source(self):
        with pytest.raises(DataSourceError):
            raise DataNotFoundError(source="yf", ticker="DEAD")

    def test_catch_circuit_breaker_open_as_pipeline(self):
        with pytest.raises(PipelineError):
            raise CircuitBreakerOpenError(circuit_name="test", recovery_timeout=60)

    def test_catch_schema_validation_as_data_validation(self):
        with pytest.raises(DataValidationError):
            raise SchemaValidationError(field="f", value=0, reason="bad")

    def test_catch_cross_field_validation_as_pipeline(self):
        with pytest.raises(PipelineError):
            raise CrossFieldValidationError(fields=["a"], reason="bad")

    def test_catch_database_connection_as_storage(self):
        with pytest.raises(StorageError):
            raise DatabaseConnectionError(host="h", port="p", database="d")

    def test_catch_database_write_as_storage(self):
        with pytest.raises(StorageError):
            raise DatabaseWriteError(table="t", operation="insert", cause="fail")

    def test_catch_object_store_as_storage(self):
        with pytest.raises(StorageError):
            raise ObjectStoreError(path="p", operation="get", cause="err")

    def test_catch_configuration_as_pipeline(self):
        with pytest.raises(PipelineError):
            raise ConfigurationError(param="x", reason="missing")

    def test_data_source_not_caught_by_storage(self):
        """DataSourceError should NOT be caught by StorageError."""
        with pytest.raises(DataSourceError):
            try:
                raise APIConnectionError(source="yf", ticker="X")
            except StorageError:
                pytest.fail("StorageError should not catch DataSourceError")
