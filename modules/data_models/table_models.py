"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : SQLAlchemy ORM table models
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

These models mirror the PostgreSQL tables defined in
static/schema/create_tables.sql (schema: systematic_equity).

"""

from sqlalchemy import Column, Table, types
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class CompanyStatic(Base):
    """Investable universe reference data (678 companies)."""

    __table__ = Table(
        "company_static",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("security", types.Text, nullable=False),
        Column("gics_sector", types.Text),
        Column("gics_industry", types.Text),
        Column("country", types.String(3)),
        Column("region", types.Text),
        schema="systematic_equity",
    )


class DailyPrices(Base):
    """Daily OHLCV + adjusted close prices for all tickers."""

    __table__ = Table(
        "daily_prices",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("cob_date", types.Date, primary_key=True),
        Column("open_price", types.Numeric(18, 6)),
        Column("high_price", types.Numeric(18, 6)),
        Column("low_price", types.Numeric(18, 6)),
        Column("close_price", types.Numeric(18, 6)),
        Column("adj_close_price", types.Numeric(18, 6)),
        Column("volume", types.BigInteger),
        Column("currency", types.String(3), nullable=False),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class Fundamentals(Base):
    """Annual and quarterly financial data in EAV (Entity-Attribute-Value) format."""

    __table__ = Table(
        "fundamentals",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("report_date", types.Date, primary_key=True),
        Column("field_name", types.String(64), primary_key=True),
        Column("field_value", types.Numeric(24, 6)),
        Column("period_type", types.String(10), primary_key=True),
        Column("currency", types.String(3)),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class FxRates(Base):
    """Daily FX rates for multi-currency portfolio conversion."""

    __table__ = Table(
        "fx_rates",
        Base.metadata,
        Column("currency_pair", types.String(12), primary_key=True),
        Column("cob_date", types.Date, primary_key=True),
        Column("open_rate", types.Numeric(18, 8)),
        Column("high_rate", types.Numeric(18, 8)),
        Column("low_rate", types.Numeric(18, 8)),
        Column("close_rate", types.Numeric(18, 8)),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class VixData(Base):
    """Daily CBOE Volatility Index (^VIX) data."""

    __table__ = Table(
        "vix_data",
        Base.metadata,
        Column("cob_date", types.Date, primary_key=True),
        Column("open_price", types.Numeric(12, 4)),
        Column("high_price", types.Numeric(12, 4)),
        Column("low_price", types.Numeric(12, 4)),
        Column("close_price", types.Numeric(12, 4)),
        Column("adj_close_price", types.Numeric(12, 4)),
        Column("volume", types.BigInteger),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class RiskFreeRate(Base):
    """Daily 3-month US Treasury rate from FRED (DGS3MO)."""

    __table__ = Table(
        "risk_free_rate",
        Base.metadata,
        Column("cob_date", types.Date, primary_key=True),
        Column("rate_pct", types.Numeric(8, 4)),
        Column("series_id", types.String(16)),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class BenchmarkIndex(Base):
    """Daily OHLCV for benchmark indices (e.g. S&P 500)."""

    __table__ = Table(
        "benchmark_index",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("cob_date", types.Date, primary_key=True),
        Column("open_price", types.Numeric(18, 4)),
        Column("high_price", types.Numeric(18, 4)),
        Column("low_price", types.Numeric(18, 4)),
        Column("close_price", types.Numeric(18, 4)),
        Column("adj_close_price", types.Numeric(18, 4)),
        Column("volume", types.BigInteger),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class CompanyRatios(Base):
    """Point-in-time financial ratios and market data (EAV pattern)."""

    __table__ = Table(
        "company_ratios",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("snapshot_date", types.Date, primary_key=True),
        Column("field_name", types.String(64), primary_key=True),
        Column("field_value", types.Numeric(24, 6)),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class EsgScores(Base):
    """ESG sustainability scores (Sustainalytics via yfinance)."""

    __table__ = Table(
        "esg_scores",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("cob_date", types.Date, primary_key=True),
        Column("total_esg", types.Numeric(10, 4)),
        Column("environment_score", types.Numeric(10, 4)),
        Column("social_score", types.Numeric(10, 4)),
        Column("governance_score", types.Numeric(10, 4)),
        Column("peer_percentile", types.Numeric(10, 4)),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class NewsSentiment(Base):
    """Aggregated news sentiment scores per ticker per day."""

    __table__ = Table(
        "news_sentiment",
        Base.metadata,
        Column("symbol", types.String(12), primary_key=True),
        Column("cob_date", types.Date, primary_key=True),
        Column("article_count", types.Integer),
        Column("avg_sentiment", types.Numeric(8, 4)),
        Column("positive_count", types.Integer),
        Column("negative_count", types.Integer),
        Column("neutral_count", types.Integer),
        Column("max_sentiment", types.Numeric(8, 4)),
        Column("min_sentiment", types.Numeric(8, 4)),
        Column("positive_ratio", types.Numeric(8, 4)),
        Column("sentiment_score", types.Numeric(8, 4)),
        Column("score_dispersion", types.Numeric(8, 4)),
        Column("ingestion_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )


class IngestionLog(Base):
    """Pipeline run audit trail – records every download attempt."""

    __table__ = Table(
        "ingestion_log",
        Base.metadata,
        Column("log_id", types.Integer, primary_key=True, autoincrement=True),
        Column("run_id", types.String(64)),
        Column("run_timestamp", types.DateTime(timezone=True)),
        Column("data_source", types.String(32), nullable=False),
        Column("symbol", types.String(12)),
        Column("status", types.String(16), nullable=False),
        Column("rows_affected", types.Integer),
        Column("error_message", types.Text),
        Column("run_frequency", types.String(16)),
        Column("date_range_start", types.Date),
        Column("date_range_end", types.Date),
        schema="systematic_equity",
    )


class PipelineMetadata(Base):
    """Tracks last successful run per data source/ticker for incremental loads."""

    __table__ = Table(
        "pipeline_metadata",
        Base.metadata,
        Column("data_source", types.String(32), primary_key=True),
        Column("symbol", types.String(12), primary_key=True),
        Column("last_success_date", types.Date),
        Column("last_run_timestamp", types.DateTime(timezone=True)),
        schema="systematic_equity",
    )
