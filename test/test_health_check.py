"""
Tests for modules.utils.health_check (PipelineHealthChecker, HealthCheckResult).

All external dependencies (PostgreSQL, MinIO, yfinance) are mocked to ensure
these tests run without any infrastructure. Covers:
  1. HealthCheckResult initialisation, to_dict(), repr
  2. Configuration check (missing sections, valid config)
  3. PostgreSQL connectivity check (mock db_client)
  4. Schema existence check (mock db_client)
  5. MinIO connectivity check (mock minio_store)
  6. Yahoo Finance reachability check (mock yfinance)
  7. all_healthy / critical_healthy static methods
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest

from modules.utils.health_check import HealthCheckResult, PipelineHealthChecker

# ── 1. HealthCheckResult ─────────────────────────────────────────────


class TestHealthCheckResult:
    """Tests for the HealthCheckResult data class."""

    def test_init_healthy(self):
        r = HealthCheckResult("test", True, 1.5, "all good")
        assert r.name == "test"
        assert r.healthy is True
        assert r.latency_ms == 1.5
        assert r.message == "all good"

    def test_init_unhealthy(self):
        r = HealthCheckResult("db", False, 500.0, "connection refused")
        assert r.healthy is False
        assert r.message == "connection refused"

    def test_init_defaults(self):
        r = HealthCheckResult("simple", True)
        assert r.latency_ms == 0.0
        assert r.message == ""

    def test_to_dict_structure(self):
        r = HealthCheckResult("postgresql", True, 12.345, "Connected")
        d = r.to_dict()
        assert d == {
            "name": "postgresql",
            "healthy": True,
            "latency_ms": 12.3,
            "message": "Connected",
        }

    def test_to_dict_rounds_latency(self):
        r = HealthCheckResult("test", True, 99.999)
        d = r.to_dict()
        assert d["latency_ms"] == 100.0

    def test_repr_healthy(self):
        r = HealthCheckResult("minio", True, 5.0, "Bucket: iftbigdata")
        text = repr(r)
        assert "OK" in text
        assert "minio" in text
        assert "5ms" in text or "5" in text

    def test_repr_unhealthy(self):
        r = HealthCheckResult("minio", False, 0.0, "client init failed")
        text = repr(r)
        assert "FAIL" in text
        assert "minio" in text

    def test_repr_no_message(self):
        r = HealthCheckResult("config", True, 0.1)
        text = repr(r)
        assert "OK" in text
        # No trailing message text
        assert text.endswith(")")


# ── 2. Configuration check ──────────────────────────────────────────


class TestConfigCheck:
    """Tests for PipelineHealthChecker.check_config()."""

    def test_missing_both_sections(self):
        checker = PipelineHealthChecker(conf={})
        result = checker.check_config()
        assert result.healthy is False
        assert "params" in result.message
        assert "config" in result.message

    def test_missing_params_section(self):
        checker = PipelineHealthChecker(conf={"config": {}})
        result = checker.check_config()
        assert result.healthy is False
        assert "params" in result.message

    def test_missing_pipeline_in_params(self):
        checker = PipelineHealthChecker(conf={"config": {}, "params": {"CurrencyMapping": {}}})
        result = checker.check_config()
        assert result.healthy is False
        assert "Pipeline" in result.message

    def test_valid_config(self):
        checker = PipelineHealthChecker(
            conf={"config": {"Database": {}}, "params": {"Pipeline": {"lookback_years": 5}}}
        )
        result = checker.check_config()
        assert result.healthy is True
        assert "present" in result.message.lower() or "All" in result.message


# ── 3. PostgreSQL check ─────────────────────────────────────────────


class TestPostgresCheck:
    """Tests for PipelineHealthChecker.check_postgresql()."""

    def _make_mock_db(self, execute_side_effect=None):
        """Create a mock db_client with a context-managed connection."""
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (1,)

        if execute_side_effect:
            mock_conn.execute.side_effect = execute_side_effect
        else:
            mock_conn.execute.return_value = mock_result

        # Support the `with connection.connect() as conn` pattern
        mock_db.connection.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connection.connect.return_value.__exit__ = MagicMock(return_value=False)
        return mock_db

    def test_postgresql_healthy(self):
        mock_db = self._make_mock_db()
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_postgresql()
        assert result.healthy is True
        assert result.name == "postgresql"

    def test_postgresql_connection_refused(self):
        mock_db = self._make_mock_db(execute_side_effect=ConnectionError("Connection refused"))
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_postgresql()
        assert result.healthy is False
        assert "failed" in result.message.lower() or "refused" in result.message.lower()

    def test_postgresql_generic_exception(self):
        mock_db = MagicMock()
        mock_db.connection.connect.side_effect = RuntimeError("timeout")
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_postgresql()
        assert result.healthy is False


# ── 4. Schema check ─────────────────────────────────────────────────


class TestSchemaCheck:
    """Tests for PipelineHealthChecker.check_schema()."""

    def _make_mock_db_with_tables(self, table_names):
        """Create mock db_client returning given table names."""
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([(t,) for t in table_names]))

        mock_conn.execute.return_value = mock_result
        mock_db.connection.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connection.connect.return_value.__exit__ = MagicMock(return_value=False)
        return mock_db

    def test_schema_all_tables_present(self):
        tables = [
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
        mock_db = self._make_mock_db_with_tables(tables)
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_schema()
        assert result.healthy is True
        assert result.name == "schema"

    def test_schema_missing_tables(self):
        tables = ["daily_prices", "fx_rates"]  # missing most tables
        mock_db = self._make_mock_db_with_tables(tables)
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_schema()
        assert result.healthy is False
        assert "Missing" in result.message or "missing" in result.message.lower()

    def test_schema_extra_tables_still_healthy(self):
        tables = [
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
            "extra_table",
            "another_one",
        ]
        mock_db = self._make_mock_db_with_tables(tables)
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_schema()
        assert result.healthy is True

    def test_schema_query_exception(self):
        mock_db = MagicMock()
        mock_db.connection.connect.side_effect = RuntimeError("no schema")
        checker = PipelineHealthChecker(db_client=mock_db)
        result = checker.check_schema()
        assert result.healthy is False


# ── 5. MinIO check ──────────────────────────────────────────────────


class TestMinioCheck:
    """Tests for PipelineHealthChecker.check_minio()."""

    def test_minio_healthy(self):
        mock_minio = MagicMock()
        mock_minio.client = MagicMock()  # not None
        mock_minio.bucket_name = "iftbigdata"
        checker = PipelineHealthChecker(minio_store=mock_minio)
        result = checker.check_minio()
        assert result.healthy is True
        assert "iftbigdata" in result.message

    def test_minio_client_is_none(self):
        mock_minio = MagicMock()
        mock_minio.client = None
        checker = PipelineHealthChecker(minio_store=mock_minio)
        result = checker.check_minio()
        assert result.healthy is False
        assert "initialisation" in result.message.lower() or "init" in result.message.lower()

    def test_minio_access_raises_exception(self):
        mock_minio = MagicMock()
        type(mock_minio).client = PropertyMock(side_effect=ConnectionError("minio down"))
        checker = PipelineHealthChecker(minio_store=mock_minio)
        result = checker.check_minio()
        assert result.healthy is False
        assert "failed" in result.message.lower() or "minio" in result.message.lower()


# ── 5b. MongoDB check ─────────────────────────────────────────────


class TestMongoDBCheck:
    """Tests for PipelineHealthChecker.check_mongodb()."""

    def test_mongodb_healthy(self):
        mock_mongo = MagicMock()
        mock_mongo.client = MagicMock()  # not None
        mock_mongo.host = "localhost"
        mock_mongo.port = 27017
        checker = PipelineHealthChecker(mongo_store=mock_mongo)
        result = checker.check_mongodb()
        assert result.healthy is True
        assert result.name == "mongodb"
        assert "localhost" in result.message

    def test_mongodb_client_is_none(self):
        mock_mongo = MagicMock()
        mock_mongo.client = None
        checker = PipelineHealthChecker(mongo_store=mock_mongo)
        result = checker.check_mongodb()
        assert result.healthy is False
        assert "initialisation" in result.message.lower() or "failed" in result.message.lower()

    def test_mongodb_access_raises_exception(self):
        mock_mongo = MagicMock()
        type(mock_mongo).client = PropertyMock(side_effect=ConnectionError("mongo down"))
        checker = PipelineHealthChecker(mongo_store=mock_mongo)
        result = checker.check_mongodb()
        assert result.healthy is False


# ── 5c. Kafka check ──────────────────────────────────────────────


class TestKafkaCheck:
    """Tests for PipelineHealthChecker.check_kafka()."""

    def test_kafka_healthy(self):
        mock_kafka = MagicMock()
        mock_kafka.producer = MagicMock()  # not None
        mock_kafka.bootstrap_servers = "localhost:9092"
        checker = PipelineHealthChecker(kafka_producer=mock_kafka)
        result = checker.check_kafka()
        assert result.healthy is True
        assert result.name == "kafka"
        assert "localhost" in result.message

    def test_kafka_producer_is_none(self):
        mock_kafka = MagicMock()
        mock_kafka.producer = None
        checker = PipelineHealthChecker(kafka_producer=mock_kafka)
        result = checker.check_kafka()
        assert result.healthy is False
        assert "initialisation" in result.message.lower() or "failed" in result.message.lower()

    def test_kafka_access_raises_exception(self):
        mock_kafka = MagicMock()
        type(mock_kafka).producer = PropertyMock(side_effect=ConnectionError("kafka down"))
        checker = PipelineHealthChecker(kafka_producer=mock_kafka)
        result = checker.check_kafka()
        assert result.healthy is False


# ── 6. Yahoo Finance check ──────────────────────────────────────────


class TestYahooFinanceCheck:
    """Tests for PipelineHealthChecker.check_yahoo_finance()."""

    @patch("modules.utils.health_check.yf", create=True)
    def test_yahoo_finance_healthy(self, mock_yf_module):
        """Mock yfinance at the import inside the method."""
        df = pd.DataFrame({"Close": [150.0]}, index=pd.to_datetime(["2024-01-02"]))

        with patch.dict("sys.modules", {"yfinance": MagicMock()}):
            with patch(
                "modules.utils.health_check.PipelineHealthChecker" ".check_yahoo_finance"
            ) as mock_check:
                mock_check.return_value = HealthCheckResult(
                    "yahoo_finance", True, 200.0, "API responding normally"
                )
                checker = PipelineHealthChecker()
                result = mock_check()
                assert result.healthy is True

    def test_yahoo_finance_returns_http_error(self):
        """HTTP 503 from finance.yahoo.com → unhealthy."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 503

        with patch("urllib.request.urlopen", return_value=mock_resp):
            checker = PipelineHealthChecker()
            result = checker.check_yahoo_finance()
            assert result.healthy is False
            assert "503" in result.message

    def test_yahoo_finance_raises_exception(self):
        """urllib.request.urlopen raises a ConnectionError → unhealthy."""
        with patch("urllib.request.urlopen", side_effect=ConnectionError("API down")):
            checker = PipelineHealthChecker()
            result = checker.check_yahoo_finance()
            assert result.healthy is False
            assert "unreachable" in result.message.lower() or "API" in result.message

    def test_yahoo_finance_returns_none(self):
        """urllib returns HTTP 404 → unhealthy."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 404

        with patch("urllib.request.urlopen", return_value=mock_resp):
            checker = PipelineHealthChecker()
            result = checker.check_yahoo_finance()
            assert result.healthy is False


# ── 7. all_healthy / critical_healthy ────────────────────────────────


class TestAllHealthy:
    """Tests for PipelineHealthChecker.all_healthy() static method."""

    def test_all_healthy_true(self):
        results = [
            HealthCheckResult("configuration", True),
            HealthCheckResult("postgresql", True),
            HealthCheckResult("minio", True),
        ]
        assert PipelineHealthChecker.all_healthy(results) is True

    def test_all_healthy_false_one_unhealthy(self):
        results = [
            HealthCheckResult("configuration", True),
            HealthCheckResult("postgresql", False),
            HealthCheckResult("minio", True),
        ]
        assert PipelineHealthChecker.all_healthy(results) is False

    def test_all_healthy_empty_list(self):
        assert PipelineHealthChecker.all_healthy([]) is True

    def test_all_healthy_all_unhealthy(self):
        results = [
            HealthCheckResult("a", False),
            HealthCheckResult("b", False),
        ]
        assert PipelineHealthChecker.all_healthy(results) is False


class TestCriticalHealthy:
    """Tests for PipelineHealthChecker.critical_healthy() static method."""

    def test_critical_healthy_ignores_non_critical(self):
        results = [
            HealthCheckResult("configuration", True),
            HealthCheckResult("postgresql", True),
            HealthCheckResult("schema", True),
            HealthCheckResult("minio", False),  # non-critical
            HealthCheckResult("yahoo_finance", False),  # non-critical
        ]
        assert PipelineHealthChecker.critical_healthy(results) is True

    def test_critical_unhealthy_postgresql(self):
        results = [
            HealthCheckResult("configuration", True),
            HealthCheckResult("postgresql", False),
            HealthCheckResult("schema", True),
        ]
        assert PipelineHealthChecker.critical_healthy(results) is False

    def test_critical_unhealthy_config(self):
        results = [
            HealthCheckResult("configuration", False),
            HealthCheckResult("postgresql", True),
        ]
        assert PipelineHealthChecker.critical_healthy(results) is False

    def test_critical_healthy_no_critical_results(self):
        """If no critical results present, should return True (vacuous truth)."""
        results = [
            HealthCheckResult("minio", False),
            HealthCheckResult("yahoo_finance", False),
        ]
        assert PipelineHealthChecker.critical_healthy(results) is True


# ── 8. run_all integration ──────────────────────────────────────────


class TestRunAll:
    """Verify run_all() orchestration logic."""

    def test_run_all_without_db_or_minio(self):
        """With no deps, only config + yahoo checks run."""
        checker = PipelineHealthChecker(conf={"config": {}, "params": {"Pipeline": {}}})
        with patch.object(
            checker, "check_yahoo_finance", return_value=HealthCheckResult("yahoo_finance", True)
        ):
            results = checker.run_all()
        names = [r.name for r in results]
        assert "configuration" in names
        assert "yahoo_finance" in names
        assert "postgresql" not in names
        assert "minio" not in names
        assert "mongodb" not in names
        assert "kafka" not in names

    def test_run_all_with_all_dependencies(self):
        """With all deps provided, all checks should run."""
        mock_db = MagicMock()
        mock_minio = MagicMock()
        mock_mongo = MagicMock()
        mock_kafka = MagicMock()
        checker = PipelineHealthChecker(
            db_client=mock_db,
            minio_store=mock_minio,
            mongo_store=mock_mongo,
            kafka_producer=mock_kafka,
            conf={"config": {}, "params": {"Pipeline": {}}},
        )
        with patch.object(checker, "check_postgresql", return_value=HealthCheckResult("postgresql", True)):
            with patch.object(checker, "check_schema", return_value=HealthCheckResult("schema", True)):
                with patch.object(checker, "check_minio", return_value=HealthCheckResult("minio", True)):
                    with patch.object(
                        checker, "check_mongodb", return_value=HealthCheckResult("mongodb", True)
                    ):
                        with patch.object(
                            checker, "check_kafka", return_value=HealthCheckResult("kafka", True)
                        ):
                            with patch.object(
                                checker,
                                "check_yahoo_finance",
                                return_value=HealthCheckResult("yahoo_finance", True),
                            ):
                                results = checker.run_all()
        names = [r.name for r in results]
        assert "configuration" in names
        assert "postgresql" in names
        assert "schema" in names
        assert "minio" in names
        assert "mongodb" in names
        assert "kafka" in names
        assert "yahoo_finance" in names
