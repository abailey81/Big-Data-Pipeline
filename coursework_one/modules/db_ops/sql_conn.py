"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Database connection and operations
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Follows the DatabaseMethods pattern from the base repository
(Scripts/Python/3_ETL_Duckdb_Postgres/modules/db_ops/sql_conn.py).

"""

from datetime import datetime

from sqlalchemy import create_engine, engine, exc, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine.base import Engine
from sqlalchemy.orm import scoped_session, sessionmaker

from modules.data_models.table_models import (
    BenchmarkIndex,
    CompanyRatios,
    CompanyStatic,
    DailyPrices,
    EsgScores,
    Fundamentals,
    FxRates,
    IngestionLog,
    NewsSentiment,
    PipelineMetadata,
    RiskFreeRate,
    VixData,
)
from modules.utils.info_logger import pipeline_logger


class DatabaseMethods:
    """Database Methods
    ==================

    Notes
    ------------------
    Setup database client for PostgreSQL. Provides upsert methods
    for all pipeline data tables using INSERT ... ON CONFLICT DO UPDATE.

    Methods
    ------------------
    session, init_schema, read_query, upsert_daily_prices,
    upsert_fundamentals, upsert_fx_rates, upsert_vix_data,
    insert_log, load_company_static, close

    :param db_type: Database type ('postgres')
    :type db_type: str
    :param kwargs: Connection parameters
    """

    def __init__(self, db_type: str, **kwargs):
        self.db_type = db_type.lower()
        self.username = kwargs.get("username")
        self.password = kwargs.get("password")
        self.host = kwargs.get("host")
        self.database = kwargs.get("database")
        self.port = kwargs.get("port")
        self._engine = self._open_client_connection(self.db_type)
        self._session_factory = sessionmaker(bind=self._engine, autocommit=False, autoflush=False)

    def __enter__(self) -> "DatabaseMethods":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def connection(self) -> Engine:
        """Returns the SQLAlchemy engine."""
        return self._engine

    @property
    def session(self):
        """Returns a new scoped database session."""
        return scoped_session(self._session_factory)()

    def close(self, commit: bool = True) -> None:
        """Disposes the database engine and connection pool."""
        self._engine.dispose()

    def init_schema(self, sql_file_path: str):
        """Initialise database schema from SQL DDL file.

        :param sql_file_path: Path to the SQL file
        :type sql_file_path: str
        """
        with open(sql_file_path, "r") as f:
            sql_content = f.read()
        with self._engine.connect() as conn:
            for statement in sql_content.split(";"):
                stmt = statement.strip()
                if not stmt:
                    continue
                # Skip comment-only blocks (no executable SQL)
                lines = [ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")]
                if not lines:
                    continue
                conn.execute(text(stmt))
            conn.commit()
        pipeline_logger.info("Database schema initialised successfully")

    def read_query(self, sql_query: str, params: dict = None) -> list:
        """Execute a read query and return results.

        Supports parameterised queries via SQLAlchemy bound parameters
        to prevent SQL injection when accepting user-supplied values.

        :param sql_query: SQL SELECT statement (may contain ``:param`` placeholders)
        :type sql_query: str
        :param params: Optional dict of bound parameters for the query
        :type params: dict, optional
        :return: List of result rows
        :rtype: list
        """
        s = self.session
        try:
            stmt = text(sql_query)
            result = s.execute(stmt, params or {})
            return result.all()
        finally:
            s.close()

    def load_company_static(self, records: list[dict]) -> int:
        """Load company static data (investable universe) into PostgreSQL.

        Uses INSERT ... ON CONFLICT to handle re-runs safely.

        :param records: List of company record dicts
        :type records: list[dict]
        :return: Number of records loaded
        :rtype: int
        """
        if not records:
            return 0
        s = self.session
        try:
            stmt = insert(CompanyStatic).values(records)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_=dict(
                    security=stmt.excluded.security,
                    gics_sector=stmt.excluded.gics_sector,
                    gics_industry=stmt.excluded.gics_industry,
                    country=stmt.excluded.country,
                    region=stmt.excluded.region,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(records)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error loading company_static: {e}")
            raise
        finally:
            s.close()

    def purge_orphan_prices(self) -> int:
        """Remove daily_prices rows whose symbol is not in company_static.

        Prevents stale tickers from inflating the entity count when the
        investable universe changes between runs.

        :return: Number of orphan rows deleted
        :rtype: int
        """
        s = self.session
        try:
            result = s.execute(
                text(
                    "DELETE FROM systematic_equity.daily_prices "
                    "WHERE symbol NOT IN ("
                    "  SELECT TRIM(symbol) FROM systematic_equity.company_static"
                    ")"
                )
            )
            deleted = result.rowcount
            s.commit()
            if deleted:
                pipeline_logger.info(
                    f"Purged {deleted} orphan rows from daily_prices "
                    f"(symbols not in company_static)"
                )
            return deleted
        except Exception as e:
            s.rollback()
            pipeline_logger.warning(f"Orphan price purge failed: {e}")
            return 0
        finally:
            s.close()

    def upsert_daily_prices(self, data_load: list[dict]) -> int:
        """Upsert daily price records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of price record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(DailyPrices).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "cob_date"],
                set_=dict(
                    open_price=stmt.excluded.open_price,
                    high_price=stmt.excluded.high_price,
                    low_price=stmt.excluded.low_price,
                    close_price=stmt.excluded.close_price,
                    adj_close_price=stmt.excluded.adj_close_price,
                    volume=stmt.excluded.volume,
                    currency=stmt.excluded.currency,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting daily prices: {e}")
            raise
        finally:
            s.close()

    def upsert_fundamentals(self, data_load: list[dict]) -> int:
        """Upsert fundamental records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of fundamental record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(Fundamentals).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "report_date", "field_name", "period_type"],
                set_=dict(
                    field_value=stmt.excluded.field_value,
                    currency=stmt.excluded.currency,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting fundamentals: {e}")
            raise
        finally:
            s.close()

    def upsert_fx_rates(self, data_load: list[dict]) -> int:
        """Upsert FX rate records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of FX rate record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(FxRates).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["currency_pair", "cob_date"],
                set_=dict(
                    open_rate=stmt.excluded.open_rate,
                    high_rate=stmt.excluded.high_rate,
                    low_rate=stmt.excluded.low_rate,
                    close_rate=stmt.excluded.close_rate,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting FX rates: {e}")
            raise
        finally:
            s.close()

    def upsert_vix_data(self, data_load: list[dict]) -> int:
        """Upsert VIX data records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of VIX record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(VixData).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["cob_date"],
                set_=dict(
                    open_price=stmt.excluded.open_price,
                    high_price=stmt.excluded.high_price,
                    low_price=stmt.excluded.low_price,
                    close_price=stmt.excluded.close_price,
                    adj_close_price=stmt.excluded.adj_close_price,
                    volume=stmt.excluded.volume,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting VIX data: {e}")
            raise
        finally:
            s.close()

    def upsert_risk_free_rate(self, data_load: list[dict]) -> int:
        """Upsert risk-free rate records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of risk-free rate record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(RiskFreeRate).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["cob_date"],
                set_=dict(
                    rate_pct=stmt.excluded.rate_pct,
                    series_id=stmt.excluded.series_id,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting risk-free rate: {e}")
            raise
        finally:
            s.close()

    def upsert_benchmark_index(self, data_load: list[dict]) -> int:
        """Upsert benchmark index records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of benchmark index record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(BenchmarkIndex).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "cob_date"],
                set_=dict(
                    open_price=stmt.excluded.open_price,
                    high_price=stmt.excluded.high_price,
                    low_price=stmt.excluded.low_price,
                    close_price=stmt.excluded.close_price,
                    adj_close_price=stmt.excluded.adj_close_price,
                    volume=stmt.excluded.volume,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting benchmark index: {e}")
            raise
        finally:
            s.close()

    def upsert_company_ratios(self, data_load: list[dict]) -> int:
        """Upsert company ratio records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of ratio record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(CompanyRatios).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "snapshot_date", "field_name"],
                set_=dict(
                    field_value=stmt.excluded.field_value,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting company ratios: {e}")
            raise
        finally:
            s.close()

    def upsert_esg_scores(self, data_load: list[dict]) -> int:
        """Upsert ESG score records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of ESG score record dictionaries
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(EsgScores).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "cob_date"],
                set_=dict(
                    total_esg=stmt.excluded.total_esg,
                    environment_score=stmt.excluded.environment_score,
                    social_score=stmt.excluded.social_score,
                    governance_score=stmt.excluded.governance_score,
                    peer_percentile=stmt.excluded.peer_percentile,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting ESG scores: {e}")
            raise
        finally:
            s.close()

    def upsert_news_sentiment(self, data_load: list[dict]) -> int:
        """Upsert news sentiment records (INSERT ... ON CONFLICT DO UPDATE).

        :param data_load: List of aggregated sentiment record dicts
        :type data_load: list[dict]
        :return: Number of records processed
        :rtype: int
        """
        if not data_load:
            return 0
        s = self.session
        try:
            for record in data_load:
                record["ingestion_timestamp"] = datetime.now()
            stmt = insert(NewsSentiment).values(data_load)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "cob_date"],
                set_=dict(
                    article_count=stmt.excluded.article_count,
                    avg_sentiment=stmt.excluded.avg_sentiment,
                    positive_count=stmt.excluded.positive_count,
                    negative_count=stmt.excluded.negative_count,
                    neutral_count=stmt.excluded.neutral_count,
                    max_sentiment=stmt.excluded.max_sentiment,
                    min_sentiment=stmt.excluded.min_sentiment,
                    positive_ratio=stmt.excluded.positive_ratio,
                    sentiment_score=stmt.excluded.sentiment_score,
                    score_dispersion=stmt.excluded.score_dispersion,
                    ingestion_timestamp=stmt.excluded.ingestion_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
            return len(data_load)
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error upserting news sentiment: {e}")
            raise
        finally:
            s.close()

    def insert_log(self, log_entry: dict) -> None:
        """Insert an ingestion log entry for audit trail.

        :param log_entry: Log entry dictionary with run_id, data_source,
                          symbol, status, rows_affected, error_message
        :type log_entry: dict
        """
        s = self.session
        try:
            log_entry["run_timestamp"] = datetime.now()
            stmt = insert(IngestionLog).values([log_entry])
            s.execute(stmt)
            s.commit()
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error inserting log entry: {e}")
            raise
        finally:
            s.close()

    def update_pipeline_metadata(self, data_source: str, symbol: str = "__ALL__", last_date=None) -> None:
        """Update pipeline metadata for incremental loading.

        :param data_source: Data source identifier
        :type data_source: str
        :param symbol: Ticker symbol or '__ALL__'
        :type symbol: str
        :param last_date: Last successful date
        """
        s = self.session
        try:
            stmt = insert(PipelineMetadata).values(
                [
                    {
                        "data_source": data_source,
                        "symbol": symbol,
                        "last_success_date": last_date,
                        "last_run_timestamp": datetime.now(),
                    }
                ]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["data_source", "symbol"],
                set_=dict(
                    last_success_date=stmt.excluded.last_success_date,
                    last_run_timestamp=stmt.excluded.last_run_timestamp,
                ),
            )
            s.execute(stmt)
            s.commit()
        except Exception as e:
            s.rollback()
            pipeline_logger.error(f"Error updating pipeline metadata: {e}")
        finally:
            s.close()

    def _conn_postgres(self) -> Engine:
        """Creates and returns a PostgreSQL database engine.

        :return: SQLAlchemy engine for PostgreSQL
        :rtype: Engine
        """
        url_object = engine.URL.create(
            drivername="postgresql",
            username=self.username,
            password=self.password,
            host=self.host,
            database=self.database,
            port=self.port,
        )
        try:
            return create_engine(url_object, pool_size=20, max_overflow=0)
        except Exception as e:
            raise Exception("Error occurred while attempting to create PostgreSQL engine") from e

    def _open_client_connection(self, db_type: str) -> Engine:
        """Opens a database connection based on the specified type.

        :param db_type: Database type ('postgres')
        :type db_type: str
        :return: SQLAlchemy engine
        :rtype: Engine
        :raises exc.ArgumentError: If invalid db_type
        """
        if db_type == "postgres":
            return self._conn_postgres()
        else:
            raise exc.ArgumentError("Only postgres is supported for db_type in this pipeline")
