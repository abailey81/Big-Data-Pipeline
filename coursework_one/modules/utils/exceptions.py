"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Custom exception hierarchy for pipeline error taxonomy
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Implements a structured exception hierarchy for precise error handling
across the pipeline. Each layer of the architecture has its own
exception subtree, enabling:
  - Targeted except clauses at each pipeline stage
  - Structured error reporting in ingestion logs
  - Clean separation between transient (retryable) and permanent errors

Exception tree::

    PipelineError (base)
    ├── DataSourceError (API / download failures)
    │   ├── APIConnectionError (network / timeout)
    │   ├── APIRateLimitError (HTTP 429 / throttling)
    │   ├── DataNotFoundError (ticker delisted / no data)
    │   └── CircuitBreakerOpenError (circuit is open)
    ├── DataValidationError (cleaning / Pydantic)
    │   ├── SchemaValidationError (field-level)
    │   └── CrossFieldValidationError (multi-field)
    ├── StorageError (database / object store)
    │   ├── DatabaseConnectionError (PostgreSQL)
    │   ├── DatabaseWriteError (upsert failures)
    │   └── ObjectStoreError (MinIO)
    └── ConfigurationError (config / env)

"""


class PipelineError(Exception):
    """Base exception for all pipeline errors.

    All custom exceptions inherit from this class, allowing a single
    ``except PipelineError`` to catch any pipeline-specific failure.

    :param message: Human-readable error description
    :type message: str
    :param details: Optional structured error metadata
    :type details: dict or None
    """

    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


# ── Data source errors (transient — generally retryable) ────────────


class DataSourceError(PipelineError):
    """Error originating from an external data source."""

    pass


class APIConnectionError(DataSourceError):
    """Network or connection failure when calling Yahoo Finance."""

    def __init__(self, source: str, ticker: str = "", cause: str = ""):
        details = {"source": source, "ticker": ticker, "cause": cause}
        super().__init__(
            f"Connection failed for {source}"
            + (f" [{ticker}]" if ticker else "")
            + (f": {cause}" if cause else ""),
            details=details,
        )


class APIRateLimitError(DataSourceError):
    """Yahoo Finance rate limiting (HTTP 429 or throttling detected).

    This is a transient error. The pipeline should back off
    and retry after a delay.
    """

    def __init__(self, source: str, retry_after: float = None):
        details = {"source": source}
        msg = f"Rate limited by {source}"
        if retry_after is not None:
            details["retry_after"] = retry_after
            msg += f" (retry after {retry_after}s)"
        super().__init__(msg, details=details)


class DataNotFoundError(DataSourceError):
    """Ticker is delisted, acquired, or returns no data (Spec §7.2 Issue 4).

    This is a permanent error for the current ticker — no retries.
    """

    def __init__(self, source: str, ticker: str):
        super().__init__(
            f"No data found for {ticker} from {source}", details={"source": source, "ticker": ticker}
        )


class CircuitBreakerOpenError(DataSourceError):
    """Request blocked because the circuit breaker is in OPEN state.

    The downstream API has experienced too many consecutive failures.
    Requests are short-circuited until the recovery timeout expires.
    """

    def __init__(self, circuit_name: str, recovery_timeout: float):
        super().__init__(
            f"Circuit breaker '{circuit_name}' is OPEN — "
            f"requests blocked for {recovery_timeout}s recovery period",
            details={"circuit_name": circuit_name, "recovery_timeout": recovery_timeout},
        )


# ── Data validation errors ──────────────────────────────────────────


class DataValidationError(PipelineError):
    """Error during data cleaning or Pydantic validation."""

    pass


class SchemaValidationError(DataValidationError):
    """A single field failed Pydantic validation.

    :param field: Field name that failed validation
    :type field: str
    :param value: The invalid value
    :param reason: Why the value was rejected
    :type reason: str
    """

    def __init__(self, field: str, value=None, reason: str = ""):
        super().__init__(
            f"Validation failed for field '{field}'"
            + (f" (value={value!r})" if value is not None else "")
            + (f": {reason}" if reason else ""),
            details={"field": field, "value": str(value), "reason": reason},
        )


class CrossFieldValidationError(DataValidationError):
    """Multiple fields are mutually inconsistent.

    Example: high_price < low_price after Pydantic coercion.
    """

    def __init__(self, fields: list[str], reason: str = ""):
        super().__init__(
            f"Cross-field validation failed for {fields}: {reason}",
            details={"fields": fields, "reason": reason},
        )


# ── Storage errors ──────────────────────────────────────────────────


class StorageError(PipelineError):
    """Error writing to a data store (PostgreSQL or MinIO)."""

    pass


class DatabaseConnectionError(StorageError):
    """Cannot connect to PostgreSQL."""

    def __init__(self, host: str, port: str, database: str, cause: str = ""):
        super().__init__(
            f"Cannot connect to PostgreSQL at {host}:{port}/{database}" + (f": {cause}" if cause else ""),
            details={"host": host, "port": port, "database": database, "cause": cause},
        )


class DatabaseWriteError(StorageError):
    """Upsert or insert operation failed."""

    def __init__(self, table: str, operation: str = "upsert", cause: str = ""):
        super().__init__(
            f"Failed to {operation} into {table}" + (f": {cause}" if cause else ""),
            details={"table": table, "operation": operation, "cause": cause},
        )


class ObjectStoreError(StorageError):
    """MinIO object storage operation failed."""

    def __init__(self, path: str, operation: str = "put", cause: str = ""):
        super().__init__(
            f"MinIO {operation} failed for {path}" + (f": {cause}" if cause else ""),
            details={"path": path, "operation": operation, "cause": cause},
        )


# ── Configuration errors ───────────────────────────────────────────


class ConfigurationError(PipelineError):
    """Invalid or missing pipeline configuration."""

    def __init__(self, param: str, reason: str = ""):
        super().__init__(
            f"Configuration error for '{param}'" + (f": {reason}" if reason else ""),
            details={"param": param, "reason": reason},
        )
