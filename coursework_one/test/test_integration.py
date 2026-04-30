"""
Integration tests for the Systematic Equity Pipeline.

These tests require a running PostgreSQL and MinIO instance.
Run with: poetry run pytest -m integration

When PostgreSQL is not available (e.g. CI without Docker), tests
are automatically skipped to avoid false failures.
"""

import socket

import pytest


def _postgres_is_reachable(host: str = "localhost", port: int = 5438, timeout: float = 1.0) -> bool:
    """Check if PostgreSQL is accepting TCP connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, socket.timeout):
        return False


_SKIP_REASON = "PostgreSQL not available on localhost:5438 (requires Docker)"
_pg_available = _postgres_is_reachable()


@pytest.mark.integration
@pytest.mark.skipif(not _pg_available, reason=_SKIP_REASON)
class TestDatabaseUpsertIdempotency:
    """Tests that upsert logic produces identical results on re-run (Spec §8.1)."""

    def test_price_upsert_idempotent(self, postgres_config_dict):
        """Running upsert twice for the same data should not create duplicates."""
        from datetime import date

        from modules.db_ops.sql_conn import DatabaseMethods

        data = [
            {
                "symbol": "TEST_IDEM",
                "cob_date": date(2024, 1, 2),
                "open_price": 100.0,
                "high_price": 105.0,
                "low_price": 99.0,
                "close_price": 103.0,
                "adj_close_price": 103.0,
                "volume": 500000,
                "currency": "USD",
            }
        ]

        with DatabaseMethods("postgres", **postgres_config_dict) as db:
            n1 = db.upsert_daily_prices(data)
            n2 = db.upsert_daily_prices(data)
            assert n1 == 1
            assert n2 == 1

            result = db.read_query(
                "SELECT COUNT(*) FROM systematic_equity.daily_prices " "WHERE symbol = 'TEST_IDEM'"
            )
            assert result[0][0] == 1  # Still one row, not two

    def test_fundamental_upsert_idempotent(self, postgres_config_dict):
        """Fundamentals upsert should be idempotent."""
        from datetime import date

        from modules.db_ops.sql_conn import DatabaseMethods

        data = [
            {
                "symbol": "TEST_FUND",
                "report_date": date(2024, 9, 30),
                "field_name": "net_income",
                "field_value": 5000.0,
                "period_type": "quarterly",
            }
        ]

        with DatabaseMethods("postgres", **postgres_config_dict) as db:
            db.upsert_fundamentals(data)
            db.upsert_fundamentals(data)
            result = db.read_query(
                "SELECT COUNT(*) FROM systematic_equity.fundamentals "
                "WHERE symbol = 'TEST_FUND' AND field_name = 'net_income'"
            )
            assert result[0][0] == 1


@pytest.mark.integration
@pytest.mark.skipif(not _pg_available, reason=_SKIP_REASON)
class TestEquityStaticRead:
    """Tests that we can read from the seeded equity_static table."""

    def test_read_ticker_list(self, postgres_config_dict):
        """Should read 678 tickers from cash_equity.equity_static."""
        from modules.input.get_company_static import get_ticker_list

        tickers = get_ticker_list(**postgres_config_dict)
        assert len(tickers) == 678
        # All should be strings
        assert all(isinstance(t, str) for t in tickers)

    def test_read_equity_static(self, postgres_config_dict):
        """Should read full company data."""
        from modules.input.get_company_static import get_equity_static

        data = get_equity_static(**postgres_config_dict)
        assert len(data) == 678


@pytest.mark.integration
@pytest.mark.skipif(not _pg_available, reason=_SKIP_REASON)
class TestSchemaInit:
    """Tests schema initialisation."""

    def test_init_schema_runs(self, postgres_config_dict):
        from modules.db_ops.sql_conn import DatabaseMethods

        with DatabaseMethods("postgres", **postgres_config_dict) as db:
            db.init_schema("./static/schema/create_tables.sql")
            result = db.read_query(
                "SELECT table_name FROM information_schema.tables " "WHERE table_schema = 'systematic_equity'"
            )
            table_names = {r[0] for r in result}
            assert "daily_prices" in table_names
            assert "fundamentals" in table_names
            assert "fx_rates" in table_names
            assert "vix_data" in table_names
            assert "ingestion_log" in table_names
