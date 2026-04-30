"""
Tests for database connection and operations module.

Covers:
  - modules.db_ops.sql_conn.DatabaseMethods
  - All upsert methods, insert_log, init_schema, etc.
  - Mocked SQLAlchemy engine/session (no real DB required)
"""

from datetime import date, datetime
from unittest.mock import MagicMock, mock_open, patch

import pytest


# We need to mock the engine creation before importing DatabaseMethods
@pytest.fixture
def mock_engine():
    """Mock SQLAlchemy engine."""
    engine = MagicMock()
    engine.dispose = MagicMock()
    return engine


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy session."""
    session = MagicMock()
    session.execute = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    return session


@pytest.fixture
def db_methods(mock_engine, mock_session):
    """Create DatabaseMethods with mocked engine and session."""
    with patch("modules.db_ops.sql_conn.create_engine", return_value=mock_engine):
        with patch("modules.db_ops.sql_conn.sessionmaker") as mock_sf:
            mock_sf.return_value = MagicMock()
            from modules.db_ops.sql_conn import DatabaseMethods

            db = DatabaseMethods(
                "postgres", username="test", password="test", host="localhost", port="5432", database="testdb"
            )
            # Replace session factory to return our mock
            db._session_factory = MagicMock(return_value=mock_session)
            # Patch the session property
            type(db).session = property(lambda self: mock_session)
            return db


# ── Init and connection tests ─────────────────────────────────────────


class TestDatabaseMethodsInit:

    @patch("modules.db_ops.sql_conn.create_engine")
    @patch("modules.db_ops.sql_conn.sessionmaker")
    def test_init_stores_connection_params(self, mock_sf, mock_ce):
        from modules.db_ops.sql_conn import DatabaseMethods

        db = DatabaseMethods("postgres", username="u", password="p", host="h", port="5432", database="d")
        assert db.username == "u"
        assert db.password == "p"
        assert db.host == "h"
        assert db.database == "d"
        assert db.port == "5432"

    @patch("modules.db_ops.sql_conn.create_engine")
    @patch("modules.db_ops.sql_conn.sessionmaker")
    def test_init_creates_engine(self, mock_sf, mock_ce):
        from modules.db_ops.sql_conn import DatabaseMethods

        DatabaseMethods("postgres", username="u", password="p", host="h", port="5432", database="d")
        mock_ce.assert_called_once()

    @patch("modules.db_ops.sql_conn.create_engine")
    @patch("modules.db_ops.sql_conn.sessionmaker")
    def test_invalid_db_type_raises(self, mock_sf, mock_ce):
        from modules.db_ops.sql_conn import DatabaseMethods

        with pytest.raises(Exception):
            DatabaseMethods("mysql", username="u", password="p", host="h", port="5432", database="d")

    @patch("modules.db_ops.sql_conn.create_engine")
    @patch("modules.db_ops.sql_conn.sessionmaker")
    def test_context_manager(self, mock_sf, mock_ce):
        from modules.db_ops.sql_conn import DatabaseMethods

        with DatabaseMethods(
            "postgres", username="u", password="p", host="h", port="5432", database="d"
        ) as db:
            assert db is not None
        mock_ce.return_value.dispose.assert_called_once()

    @patch("modules.db_ops.sql_conn.create_engine")
    @patch("modules.db_ops.sql_conn.sessionmaker")
    def test_connection_property(self, mock_sf, mock_ce):
        from modules.db_ops.sql_conn import DatabaseMethods

        db = DatabaseMethods("postgres", username="u", password="p", host="h", port="5432", database="d")
        assert db.connection == mock_ce.return_value

    @patch("modules.db_ops.sql_conn.create_engine")
    @patch("modules.db_ops.sql_conn.sessionmaker")
    def test_close_disposes_engine(self, mock_sf, mock_ce):
        from modules.db_ops.sql_conn import DatabaseMethods

        db = DatabaseMethods("postgres", username="u", password="p", host="h", port="5432", database="d")
        db.close()
        mock_ce.return_value.dispose.assert_called_once()


# ── Schema init tests ─────────────────────────────────────────────────


class TestInitSchema:

    def test_init_schema_reads_file_and_executes(self, db_methods, mock_engine):
        sql_content = "CREATE TABLE t1 (id INT); CREATE TABLE t2 (id INT)"
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        with patch("builtins.open", mock_open(read_data=sql_content)):
            db_methods.init_schema("schema.sql")

        # Should execute two statements
        assert mock_conn.execute.call_count == 2
        mock_conn.commit.assert_called_once()


# ── Read query tests ──────────────────────────────────────────────────


class TestReadQuery:

    def test_read_query_returns_results(self, db_methods, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = [("row1",), ("row2",)]
        mock_session.execute.return_value = mock_result

        result = db_methods.read_query("SELECT * FROM test")
        assert len(result) == 2
        mock_session.close.assert_called_once()


# ── Upsert tests ─────────────────────────────────────────────────────


class TestUpsertDailyPrices:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_daily_prices([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {"symbol": "AAPL", "cob_date": date(2024, 1, 1), "open_price": 150.0, "close_price": 151.0},
            {"symbol": "AAPL", "cob_date": date(2024, 1, 2), "open_price": 151.0, "close_price": 152.0},
        ]
        result = db_methods.upsert_daily_prices(records)
        assert result == 2
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_adds_ingestion_timestamp(self, db_methods, mock_session):
        records = [{"symbol": "AAPL", "cob_date": date(2024, 1, 1)}]
        db_methods.upsert_daily_prices(records)
        assert "ingestion_timestamp" in records[0]

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        records = [{"symbol": "AAPL", "cob_date": date(2024, 1, 1)}]
        with pytest.raises(Exception, match="DB error"):
            db_methods.upsert_daily_prices(records)
        mock_session.rollback.assert_called_once()


class TestUpsertFundamentals:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_fundamentals([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {
                "symbol": "AAPL",
                "report_date": date(2024, 3, 31),
                "field_name": "total_revenue",
                "field_value": 90000,
                "period_type": "quarterly",
                "currency": "USD",
            },
        ]
        result = db_methods.upsert_fundamentals(records)
        assert result == 1

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception):
            db_methods.upsert_fundamentals([{"symbol": "X"}])
        mock_session.rollback.assert_called_once()


class TestUpsertFxRates:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_fx_rates([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {"currency_pair": "GBPUSD=X", "cob_date": date(2024, 1, 1), "close_rate": 1.27},
        ]
        result = db_methods.upsert_fx_rates(records)
        assert result == 1


class TestUpsertVixData:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_vix_data([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {"cob_date": date(2024, 1, 1), "close_price": 13.8},
        ]
        result = db_methods.upsert_vix_data(records)
        assert result == 1


class TestUpsertRiskFreeRate:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_risk_free_rate([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {"cob_date": date(2024, 1, 1), "rate_pct": 4.25, "series_id": "DGS3MO"},
        ]
        result = db_methods.upsert_risk_free_rate(records)
        assert result == 1

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception):
            db_methods.upsert_risk_free_rate([{"cob_date": date(2024, 1, 1)}])
        mock_session.rollback.assert_called_once()


class TestUpsertBenchmarkIndex:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_benchmark_index([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {"symbol": "^GSPC", "cob_date": date(2024, 1, 1), "close_price": 4700.0},
        ]
        result = db_methods.upsert_benchmark_index(records)
        assert result == 1


class TestUpsertCompanyRatios:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_company_ratios([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {
                "symbol": "AAPL",
                "snapshot_date": date(2024, 1, 1),
                "field_name": "pe_trailing",
                "field_value": 25.3,
            },
        ]
        result = db_methods.upsert_company_ratios(records)
        assert result == 1

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception):
            db_methods.upsert_company_ratios([{"symbol": "X"}])
        mock_session.rollback.assert_called_once()


# ── Insert log tests ──────────────────────────────────────────────────


class TestInsertLog:

    def test_insert_log_adds_timestamp(self, db_methods, mock_session):
        entry = {
            "run_id": "run-1",
            "data_source": "prices",
            "symbol": "AAPL",
            "status": "SUCCESS",
            "rows_affected": 100,
        }
        db_methods.insert_log(entry)
        assert "run_timestamp" in entry
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_insert_log_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception):
            db_methods.insert_log({"run_id": "x", "data_source": "y"})
        mock_session.rollback.assert_called_once()


# ── Pipeline metadata tests ───────────────────────────────────────────


class TestUpdatePipelineMetadata:

    def test_update_metadata_executes(self, db_methods, mock_session):
        db_methods.update_pipeline_metadata(data_source="prices", symbol="AAPL", last_date=date(2024, 1, 1))
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_update_metadata_default_symbol(self, db_methods, mock_session):
        db_methods.update_pipeline_metadata(data_source="fx")
        mock_session.execute.assert_called_once()

    def test_update_metadata_exception_handled(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        # Should not raise — just logs the error
        db_methods.update_pipeline_metadata(data_source="vix")
        mock_session.rollback.assert_called_once()


# ── Company static load tests ─────────────────────────────────────────


class TestLoadCompanyStatic:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.load_company_static([]) == 0

    def test_load_returns_count(self, db_methods, mock_session):
        records = [
            {
                "symbol": "AAPL",
                "security": "Apple Inc",
                "gics_sector": "Technology",
                "gics_industry": "Hardware",
                "country": "US",
                "region": "North America",
            },
        ]
        result = db_methods.load_company_static(records)
        assert result == 1

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception):
            db_methods.load_company_static([{"symbol": "X"}])
        mock_session.rollback.assert_called_once()


# ── ESG scores upsert tests ──────────────────────────────────────────


class TestUpsertEsgScores:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_esg_scores([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {
                "symbol": "AAPL",
                "cob_date": date(2024, 6, 15),
                "total_esg": 22.5,
                "environment_score": 15.0,
                "social_score": 25.0,
                "governance_score": 27.0,
                "peer_percentile": 45.0,
            },
        ]
        result = db_methods.upsert_esg_scores(records)
        assert result == 1
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_adds_ingestion_timestamp(self, db_methods, mock_session):
        records = [{"symbol": "MSFT", "cob_date": date(2024, 6, 15)}]
        db_methods.upsert_esg_scores(records)
        assert "ingestion_timestamp" in records[0]

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception, match="DB error"):
            db_methods.upsert_esg_scores([{"symbol": "X", "cob_date": date(2024, 1, 1)}])
        mock_session.rollback.assert_called_once()

    def test_multiple_records(self, db_methods, mock_session):
        records = [
            {"symbol": "AAPL", "cob_date": date(2024, 6, 15), "total_esg": 22.5},
            {"symbol": "MSFT", "cob_date": date(2024, 6, 15), "total_esg": 30.1},
            {"symbol": "GOOG", "cob_date": date(2024, 6, 15), "total_esg": 18.9},
        ]
        result = db_methods.upsert_esg_scores(records)
        assert result == 3


# ── News sentiment upsert tests ──────────────────────────────────────


class TestUpsertNewsSentiment:

    def test_empty_list_returns_zero(self, db_methods):
        assert db_methods.upsert_news_sentiment([]) == 0

    def test_upsert_returns_count(self, db_methods, mock_session):
        records = [
            {
                "symbol": "AAPL",
                "cob_date": date(2024, 6, 15),
                "article_count": 12,
                "avg_sentiment": 0.35,
                "positive_count": 8,
                "negative_count": 2,
                "neutral_count": 2,
                "max_sentiment": 0.85,
                "min_sentiment": -0.20,
                "positive_ratio": 0.6667,
                "sentiment_score": 72.5,
                "score_dispersion": 0.15,
            },
        ]
        result = db_methods.upsert_news_sentiment(records)
        assert result == 1
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_adds_ingestion_timestamp(self, db_methods, mock_session):
        records = [{"symbol": "TSLA", "cob_date": date(2024, 6, 15), "article_count": 5}]
        db_methods.upsert_news_sentiment(records)
        assert "ingestion_timestamp" in records[0]

    def test_exception_rolls_back(self, db_methods, mock_session):
        mock_session.execute.side_effect = Exception("DB error")
        with pytest.raises(Exception, match="DB error"):
            db_methods.upsert_news_sentiment([{"symbol": "X", "cob_date": date(2024, 1, 1)}])
        mock_session.rollback.assert_called_once()

    def test_multiple_records(self, db_methods, mock_session):
        records = [
            {"symbol": "AAPL", "cob_date": date(2024, 6, 15), "article_count": 10},
            {"symbol": "MSFT", "cob_date": date(2024, 6, 15), "article_count": 8},
        ]
        result = db_methods.upsert_news_sentiment(records)
        assert result == 2
