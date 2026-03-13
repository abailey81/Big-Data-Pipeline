"""
Tests for extract_from_query module.

Covers:
  - modules.db_ops.extract_from_query.get_postgres_data
"""

from unittest.mock import MagicMock, patch

import pytest

from modules.db_ops.extract_from_query import get_postgres_data


class TestGetPostgresData:

    @patch("modules.db_ops.extract_from_query.DatabaseMethods")
    @patch("modules.db_ops.extract_from_query.PostgresConfig")
    def test_returns_query_results(self, mock_pg_config, mock_db_cls):
        mock_config = MagicMock()
        mock_config.username = "u"
        mock_config.password = "p"
        mock_config.host = "h"
        mock_config.port = "5432"
        mock_config.database = "testdb"
        mock_pg_config.return_value = mock_config

        mock_result = MagicMock()
        mock_result.all.return_value = [("AAPL",), ("MSFT",)]

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.session.execute.return_value = mock_result
        mock_db_cls.return_value = mock_db

        result = get_postgres_data(
            sql_query="SELECT symbol FROM test",
            username="u",
            password="p",
            host="h",
            port="5432",
            database="testdb",
        )
        assert result == [("AAPL",), ("MSFT",)]

    @patch("modules.db_ops.extract_from_query.DatabaseMethods")
    @patch("modules.db_ops.extract_from_query.PostgresConfig")
    def test_raises_on_query_error(self, mock_pg_config, mock_db_cls):
        mock_config = MagicMock()
        mock_config.username = "u"
        mock_config.password = "p"
        mock_config.host = "h"
        mock_config.port = "5432"
        mock_config.database = "testdb"
        mock_pg_config.return_value = mock_config

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.session.execute.side_effect = Exception("Query failed")
        mock_db_cls.return_value = mock_db

        with pytest.raises(Exception, match="Query failed"):
            get_postgres_data(
                sql_query="SELECT broken",
                username="u",
                password="p",
                host="h",
                port="5432",
                database="testdb",
            )

    @patch("modules.db_ops.extract_from_query.DatabaseMethods")
    @patch("modules.db_ops.extract_from_query.PostgresConfig")
    def test_passes_config_to_database_methods(self, mock_pg_config, mock_db_cls):
        mock_config = MagicMock()
        mock_config.username = "testuser"
        mock_config.password = "testpass"
        mock_config.host = "testhost"
        mock_config.port = "1234"
        mock_config.database = "testdb"
        mock_pg_config.return_value = mock_config

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.session.execute.return_value = mock_result
        mock_db_cls.return_value = mock_db

        get_postgres_data(
            sql_query="SELECT 1",
            username="testuser",
            password="testpass",
            host="testhost",
            port="1234",
            database="testdb",
        )

        mock_db_cls.assert_called_once_with(
            "postgres",
            username="testuser",
            password="testpass",
            host="testhost",
            port="1234",
            database="testdb",
        )
