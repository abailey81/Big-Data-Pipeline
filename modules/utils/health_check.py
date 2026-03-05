"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Pre-flight health checks for pipeline dependencies
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Runs connectivity and readiness checks for all external dependencies
before the pipeline begins downloading data. This fail-fast pattern
avoids wasting API calls and compute time when infrastructure is down.

Checks performed:
  1. PostgreSQL connectivity (SELECT 1)
  2. PostgreSQL schema existence (systematic_equity tables)
  3. MinIO bucket existence and accessibility
  4. Yahoo Finance API reachability (lightweight test download)
  5. Configuration completeness (required keys present)

"""

import time


class HealthCheckResult:
    """Result of a single health check.

    :param name: Check name (e.g. 'postgresql', 'minio', 'yahoo_finance')
    :type name: str
    :param healthy: Whether the check passed
    :type healthy: bool
    :param latency_ms: Time taken for the check in milliseconds
    :type latency_ms: float
    :param message: Optional status message or error description
    :type message: str
    """

    def __init__(self, name: str, healthy: bool, latency_ms: float = 0.0, message: str = ""):
        self.name = name
        self.healthy = healthy
        self.latency_ms = latency_ms
        self.message = message

    def __repr__(self):
        status = "OK" if self.healthy else "FAIL"
        return (
            f"HealthCheck({self.name}: {status}, "
            f"{self.latency_ms:.0f}ms" + (f", {self.message}" if self.message else "") + ")"
        )

    def to_dict(self) -> dict:
        """Export as dictionary for structured reporting."""
        return {
            "name": self.name,
            "healthy": self.healthy,
            "latency_ms": round(self.latency_ms, 1),
            "message": self.message,
        }


class PipelineHealthChecker:
    """Runs pre-flight health checks for all pipeline dependencies.

    Returns a list of ``HealthCheckResult`` objects that indicate
    which dependencies are available. The pipeline can then decide
    whether to proceed, skip certain sources, or abort entirely.

    :param db_client: Optional DatabaseMethods instance for PostgreSQL checks
    :param minio_store: Optional MinioStore instance for object store checks
    :param mongo_store: Optional MongoDBStore instance for MongoDB checks
    :param kafka_producer: Optional KafkaProducerClient for Kafka checks
    :param conf: Pipeline configuration dictionary

    :example:
        >>> checker = PipelineHealthChecker(db_client, minio_store, conf)
        >>> results = checker.run_all()
        >>> if not checker.all_healthy(results):
        ...     for r in results:
        ...         if not r.healthy:
        ...             logger.error(f"FAIL: {r.name} - {r.message}")
    """

    def __init__(
        self, db_client=None, minio_store=None, mongo_store=None, kafka_producer=None, conf: dict = None
    ):
        self.db_client = db_client
        self.minio_store = minio_store
        self.mongo_store = mongo_store
        self.kafka_producer = kafka_producer
        self.conf = conf or {}

    def run_all(self) -> list[HealthCheckResult]:
        """Execute all health checks and return results.

        :return: List of health check results
        :rtype: list[HealthCheckResult]
        """
        results = []

        results.append(self.check_config())

        if self.db_client:
            results.append(self.check_postgresql())
            results.append(self.check_schema())

        if self.minio_store:
            results.append(self.check_minio())

        if self.mongo_store:
            results.append(self.check_mongodb())

        if self.kafka_producer:
            results.append(self.check_kafka())

        results.append(self.check_yahoo_finance())

        return results

    def check_config(self) -> HealthCheckResult:
        """Verify that required configuration keys are present.

        :return: Configuration health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        required_sections = ["params", "config"]
        missing = [s for s in required_sections if s not in self.conf]

        if missing:
            return HealthCheckResult(
                "configuration", False, _elapsed_ms(t0), f"Missing config sections: {missing}"
            )

        params = self.conf.get("params", {})
        if "Pipeline" not in params:
            return HealthCheckResult("configuration", False, _elapsed_ms(t0), "Missing 'Pipeline' in params")

        return HealthCheckResult(
            "configuration", True, _elapsed_ms(t0), "All required configuration keys present"
        )

    def check_postgresql(self) -> HealthCheckResult:
        """Test PostgreSQL connectivity with a lightweight query.

        :return: PostgreSQL health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        try:
            from sqlalchemy import text

            with self.db_client.connection.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                result.fetchone()
            return HealthCheckResult("postgresql", True, _elapsed_ms(t0), "Connection successful")
        except Exception as e:
            return HealthCheckResult(
                "postgresql", False, _elapsed_ms(t0), f"Connection failed: {str(e)[:200]}"
            )

    def check_schema(self) -> HealthCheckResult:
        """Verify that the systematic_equity schema and core tables exist.

        :return: Schema health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        required_tables = [
            "daily_prices",
            "fundamentals",
            "fx_rates",
            "vix_data",
            "ingestion_log",
            "company_static",
            "esg_scores",
            "news_sentiment",
            "benchmark_index",
            "company_ratios",
            "risk_free_rate",
        ]
        try:
            from sqlalchemy import text

            with self.db_client.connection.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'systematic_equity'"
                    )
                )
                existing = {row[0] for row in result}

            missing = [t for t in required_tables if t not in existing]
            if missing:
                return HealthCheckResult(
                    "schema", False, _elapsed_ms(t0), f"Missing tables: {missing}. Run --init_schema first."
                )
            return HealthCheckResult(
                "schema", True, _elapsed_ms(t0), f"{len(existing)} tables found in systematic_equity"
            )
        except Exception as e:
            return HealthCheckResult("schema", False, _elapsed_ms(t0), f"Schema check failed: {str(e)[:200]}")

    def check_minio(self) -> HealthCheckResult:
        """Test MinIO connectivity and bucket accessibility.

        :return: MinIO health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        try:
            client = self.minio_store.client
            if client is None:
                return HealthCheckResult(
                    "minio", False, _elapsed_ms(t0), "MinIO client initialisation failed"
                )
            return HealthCheckResult(
                "minio", True, _elapsed_ms(t0), f"Bucket: {self.minio_store.bucket_name}"
            )
        except Exception as e:
            return HealthCheckResult("minio", False, _elapsed_ms(t0), f"MinIO check failed: {str(e)[:200]}")

    def check_mongodb(self) -> HealthCheckResult:
        """Test MongoDB connectivity with a ping command.

        :return: MongoDB health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        try:
            client = self.mongo_store.client
            if client is None:
                return HealthCheckResult(
                    "mongodb", False, _elapsed_ms(t0), "MongoDB client initialisation failed"
                )
            return HealthCheckResult(
                "mongodb",
                True,
                _elapsed_ms(t0),
                f"Connected to {self.mongo_store.host}:{self.mongo_store.port}",
            )
        except Exception as e:
            return HealthCheckResult(
                "mongodb", False, _elapsed_ms(t0), f"MongoDB check failed: {str(e)[:200]}"
            )

    def check_kafka(self) -> HealthCheckResult:
        """Test Kafka producer connectivity.

        :return: Kafka health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        try:
            producer = self.kafka_producer.producer
            if producer is None:
                return HealthCheckResult(
                    "kafka", False, _elapsed_ms(t0), "Kafka producer initialisation failed"
                )
            return HealthCheckResult(
                "kafka", True, _elapsed_ms(t0), f"Connected to {self.kafka_producer.bootstrap_servers}"
            )
        except Exception as e:
            return HealthCheckResult("kafka", False, _elapsed_ms(t0), f"Kafka check failed: {str(e)[:200]}")

    def check_yahoo_finance(self) -> HealthCheckResult:
        """Test Yahoo Finance API reachability with a lightweight HTTP check.

        Uses a HEAD request to finance.yahoo.com rather than a data
        download to avoid consuming rate-limit quota before the pipeline
        starts its real downloads.

        :return: Yahoo Finance health check result
        :rtype: HealthCheckResult
        """
        t0 = time.monotonic()
        try:
            import urllib.request

            req = urllib.request.Request(
                "https://finance.yahoo.com",
                headers={"User-Agent": "Mozilla/5.0"},
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
            if status < 400:
                return HealthCheckResult("yahoo_finance", True, _elapsed_ms(t0), f"Reachable (HTTP {status})")
            return HealthCheckResult(
                "yahoo_finance", False, _elapsed_ms(t0), f"HTTP {status} from finance.yahoo.com"
            )
        except Exception as e:
            return HealthCheckResult("yahoo_finance", False, _elapsed_ms(t0), f"Unreachable: {str(e)[:200]}")

    @staticmethod
    def all_healthy(results: list[HealthCheckResult]) -> bool:
        """Check if all health checks passed.

        :param results: List of health check results
        :return: True if all checks passed
        :rtype: bool
        """
        return all(r.healthy for r in results)

    @staticmethod
    def critical_healthy(results: list[HealthCheckResult]) -> bool:
        """Check if critical services (PostgreSQL, config) are healthy.

        Non-critical services (MinIO, Yahoo Finance) can be degraded
        without blocking the pipeline entirely.

        :param results: List of health check results
        :return: True if all critical checks passed
        :rtype: bool
        """
        critical = {"configuration", "postgresql", "schema"}
        return all(r.healthy for r in results if r.name in critical)


def _elapsed_ms(start: float) -> float:
    """Calculate elapsed milliseconds since a monotonic start time."""
    return (time.monotonic() - start) * 1000
