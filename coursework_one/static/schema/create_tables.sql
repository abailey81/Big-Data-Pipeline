-- =============================================================================
-- Kolmogorov's team
-- Systematic Equity Pipeline – PostgreSQL Schema for Flow-Based Multi-Factor Strategy
-- =============================================================================
-- Database: fift
-- Schema:   systematic_equity
--
-- Design Principles (from Spec §8.1):
--   1. All tables indexed by (symbol, date) for retrieval by company and year.
--   2. Upsert-safe via PRIMARY KEY (INSERT ... ON CONFLICT DO UPDATE).
--   3. Historical data preserved – never overwrite via append-only pattern.
--   4. ingestion_timestamp on every table for full audit traceability.
--   5. Flexible to company additions/removals without schema changes.
--
-- Tables (12): company_static, daily_prices, fundamentals, fx_rates,
--              vix_data, risk_free_rate, benchmark_index, company_ratios,
--              esg_scores, news_sentiment, ingestion_log, pipeline_metadata
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS systematic_equity AUTHORIZATION postgres;

-- -------------------------------------------------------------------------
-- 1. company_static – Investable Universe (678 companies)
--    Loaded from the provided ift_coursework CSV.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.company_static (
    "symbol"          VARCHAR(12)  PRIMARY KEY,
    "security"        TEXT         NOT NULL,
    "gics_sector"     TEXT,
    "gics_industry"   TEXT,
    "country"         CHAR(3),
    "region"          TEXT
);

-- -------------------------------------------------------------------------
-- 2. daily_prices – OHLCV + Adjusted Close for all 678 tickers
--    PK: (symbol, cob_date) ensures one row per ticker per day.
--    Stores raw local-currency prices (no USD conversion in CW1).
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.daily_prices (
    "symbol"              VARCHAR(12)   NOT NULL,
    "cob_date"            DATE          NOT NULL,
    "open_price"          NUMERIC(18,6),
    "high_price"          NUMERIC(18,6),
    "low_price"           NUMERIC(18,6),
    "close_price"         NUMERIC(18,6),
    "adj_close_price"     NUMERIC(18,6),
    "volume"              BIGINT,
    "currency"            CHAR(3)       NOT NULL,
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("symbol", "cob_date")
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_cob
    ON fift.systematic_equity.daily_prices ("cob_date");
CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol
    ON fift.systematic_equity.daily_prices ("symbol");

-- -------------------------------------------------------------------------
-- 3. fundamentals – Annual & quarterly balance sheet, income statement,
--    and cash flow items (EAV pattern for flexible field storage).
--    Fields: book_value_per_share, net_income, shareholders_equity,
--            total_debt, eps, total_revenue, operating_income, ebitda,
--            operating_cash_flow, capital_expenditure, free_cash_flow
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.fundamentals (
    "symbol"              VARCHAR(12)   NOT NULL,
    "report_date"         DATE          NOT NULL,
    "field_name"          VARCHAR(64)   NOT NULL,
    "field_value"         NUMERIC(24,6),
    "period_type"         VARCHAR(10)   NOT NULL DEFAULT 'quarterly',
    "currency"            CHAR(3),
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("symbol", "report_date", "field_name", "period_type")
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol
    ON fift.systematic_equity.fundamentals ("symbol");
CREATE INDEX IF NOT EXISTS idx_fundamentals_report_date
    ON fift.systematic_equity.fundamentals ("report_date");

-- -------------------------------------------------------------------------
-- 4. fx_rates – Daily FX rates (GBPUSD=X, EURUSD=X, CADUSD=X, CHFUSD=X)
--    PK: (currency_pair, cob_date) – one rate per pair per day.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.fx_rates (
    "currency_pair"       VARCHAR(12)   NOT NULL,
    "cob_date"            DATE          NOT NULL,
    "open_rate"           NUMERIC(18,8),
    "high_rate"           NUMERIC(18,8),
    "low_rate"            NUMERIC(18,8),
    "close_rate"          NUMERIC(18,8),
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("currency_pair", "cob_date")
);

CREATE INDEX IF NOT EXISTS idx_fx_rates_cob
    ON fift.systematic_equity.fx_rates ("cob_date");
CREATE INDEX IF NOT EXISTS idx_fx_rates_pair
    ON fift.systematic_equity.fx_rates ("currency_pair");

-- -------------------------------------------------------------------------
-- 5. vix_data – Daily CBOE Volatility Index (^VIX)
--    Required for volatility regime classification in CW2 (§4.4).
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.vix_data (
    "cob_date"            DATE          PRIMARY KEY,
    "open_price"          NUMERIC(12,4),
    "high_price"          NUMERIC(12,4),
    "low_price"           NUMERIC(12,4),
    "close_price"         NUMERIC(12,4),
    "adj_close_price"     NUMERIC(12,4),
    "volume"              BIGINT,
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- -------------------------------------------------------------------------
-- 6. risk_free_rate – Daily 3-month US Treasury rate from FRED (DGS3MO)
--    Used for Sharpe ratio calculation in CW2 (Spec §7.3, Priority P2).
--    PK: (cob_date) – one rate per trading day.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.risk_free_rate (
    "cob_date"            DATE          PRIMARY KEY,
    "rate_pct"            NUMERIC(8,4),
    "series_id"           VARCHAR(16)   NOT NULL DEFAULT 'DGS3MO',
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- -------------------------------------------------------------------------
-- 6b. benchmark_index – Daily OHLCV for benchmark indices (e.g. ^GSPC)
--     Used for relative performance and beta calculation in CW2.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.benchmark_index (
    "symbol"              VARCHAR(12)   NOT NULL,
    "cob_date"            DATE          NOT NULL,
    "open_price"          NUMERIC(18,4),
    "high_price"          NUMERIC(18,4),
    "low_price"           NUMERIC(18,4),
    "close_price"         NUMERIC(18,4),
    "adj_close_price"     NUMERIC(18,4),
    "volume"              BIGINT,
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("symbol", "cob_date")
);

CREATE INDEX IF NOT EXISTS idx_benchmark_cob
    ON fift.systematic_equity.benchmark_index ("cob_date");

-- -------------------------------------------------------------------------
-- 6c. company_ratios – Point-in-time financial ratios and market data
--     Stores market cap, P/E, P/B, EV/EBITDA, dividend yield etc.
--     from Yahoo Finance ticker.info (EAV pattern).
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.company_ratios (
    "symbol"              VARCHAR(12)   NOT NULL,
    "snapshot_date"       DATE          NOT NULL,
    "field_name"          VARCHAR(64)   NOT NULL,
    "field_value"         NUMERIC(24,6),
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("symbol", "snapshot_date", "field_name")
);

CREATE INDEX IF NOT EXISTS idx_company_ratios_symbol
    ON fift.systematic_equity.company_ratios ("symbol");

-- -------------------------------------------------------------------------
-- 7. esg_scores – ESG sustainability scores (Sustainalytics via yfinance)
--    Enhances the quality factor with non-financial quality signals
--    (Appendix 1: "valuation factors, momentum effects, ESG signals").
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.esg_scores (
    "symbol"              VARCHAR(12)   NOT NULL,
    "cob_date"            DATE          NOT NULL,
    "total_esg"           NUMERIC(10,4),
    "environment_score"   NUMERIC(10,4),
    "social_score"        NUMERIC(10,4),
    "governance_score"    NUMERIC(10,4),
    "peer_percentile"     NUMERIC(10,4),
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("symbol", "cob_date")
);

CREATE INDEX IF NOT EXISTS idx_esg_scores_symbol
    ON fift.systematic_equity.esg_scores ("symbol");

-- -------------------------------------------------------------------------
-- 7b. news_sentiment – Aggregated news sentiment scores per ticker
--     Derived from yfinance Ticker.news headlines via keyword scoring.
--     Raw articles stored in MongoDB (news_sentiment collection);
--     this table holds the per-day aggregation for factor construction.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.news_sentiment (
    "symbol"              VARCHAR(12)   NOT NULL,
    "cob_date"            DATE          NOT NULL,
    "article_count"       INTEGER,
    "avg_sentiment"       NUMERIC(8,4),
    "positive_count"      INTEGER,
    "negative_count"      INTEGER,
    "neutral_count"       INTEGER,
    "max_sentiment"       NUMERIC(8,4),
    "min_sentiment"       NUMERIC(8,4),
    "positive_ratio"      NUMERIC(8,4),
    "sentiment_score"     NUMERIC(8,4),
    "score_dispersion"    NUMERIC(8,4),
    "ingestion_timestamp" TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("symbol", "cob_date")
);

CREATE INDEX IF NOT EXISTS idx_news_sentiment_symbol
    ON fift.systematic_equity.news_sentiment ("symbol");
CREATE INDEX IF NOT EXISTS idx_news_sentiment_cob
    ON fift.systematic_equity.news_sentiment ("cob_date");

-- -------------------------------------------------------------------------
-- 8. ingestion_log – Pipeline run audit trail
--    Records every download attempt per ticker per run (Spec §8.3).
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.ingestion_log (
    "log_id"              SERIAL        PRIMARY KEY,
    "run_id"              VARCHAR(64)   NOT NULL,
    "run_timestamp"       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    "data_source"         VARCHAR(32)   NOT NULL,
    "symbol"              VARCHAR(12),
    "status"              VARCHAR(16)   NOT NULL,
    "rows_affected"       INTEGER       DEFAULT 0,
    "error_message"       TEXT,
    "run_frequency"       VARCHAR(16),
    "date_range_start"    DATE,
    "date_range_end"      DATE
);

CREATE INDEX IF NOT EXISTS idx_ingestion_log_run
    ON fift.systematic_equity.ingestion_log ("run_id");
CREATE INDEX IF NOT EXISTS idx_ingestion_log_symbol
    ON fift.systematic_equity.ingestion_log ("symbol");
CREATE INDEX IF NOT EXISTS idx_ingestion_log_status
    ON fift.systematic_equity.ingestion_log ("status");

-- -------------------------------------------------------------------------
-- 7. pipeline_metadata – Tracks last successful run per source/ticker
--    Supports incremental loading for efficiency.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fift.systematic_equity.pipeline_metadata (
    "data_source"         VARCHAR(32)   NOT NULL,
    "symbol"              VARCHAR(12)   NOT NULL DEFAULT '__ALL__',
    "last_success_date"   DATE,
    "last_run_timestamp"  TIMESTAMPTZ,
    "ingestion_timestamp" TIMESTAMPTZ   DEFAULT NOW(),
    PRIMARY KEY ("data_source", "symbol")
);
