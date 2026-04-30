"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Extract data from PostgreSQL queries
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

from sqlalchemy import text

from modules.db_ops.postgres_config import PostgresConfig
from modules.db_ops.sql_conn import DatabaseMethods
from modules.utils.info_logger import pipeline_logger


def get_postgres_data(sql_query: str, **kwargs):
    """Execute a query against PostgreSQL and return results.

    :param sql_query: SQL query string
    :type sql_query: str
    :param kwargs: Connection parameters (username, password, host, port, database)
    :return: List of result rows
    :rtype: list
    """
    pg_config = PostgresConfig(
        username=kwargs.get("username"),
        password=kwargs.get("password"),
        host=kwargs.get("host"),
        port=kwargs.get("port"),
        database=kwargs.get("database"),
    )

    with DatabaseMethods(
        "postgres",
        username=pg_config.username,
        password=pg_config.password,
        host=pg_config.host,
        port=pg_config.port,
        database=pg_config.database,
    ) as db:
        try:
            result = db.session.execute(text(sql_query))
            return result.all()
        except Exception as e:
            pipeline_logger.error(f"An error occurred: {e}")
            raise
