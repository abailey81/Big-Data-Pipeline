"""
Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Data export utilities
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Provides structured data export from PostgreSQL to CSV and JSON formats
for downstream consumption by Coursework Two portfolio construction.

Supports:
  - Per-company data retrieval by symbol
  - Per-year data retrieval by date range
  - Bulk export of entire tables
  - Summary statistics generation

"""

import csv
import io
import json
from datetime import date, datetime
from typing import Optional

from modules.utils.info_logger import pipeline_logger


class DataExporter:
    """Export processed data from PostgreSQL to structured formats.

    Facilitates easy retrieval of investment indicators by company
    or by year, as required by the coursework specification.

    :param db_client: DatabaseMethods instance for PostgreSQL queries
    :type db_client: modules.db_ops.sql_conn.DatabaseMethods

    :example:
        >>> exporter = DataExporter(db_client)
        >>> csv_data = exporter.prices_to_csv("AAPL", "2020-01-01", "2024-12-31")
        >>> summary = exporter.get_company_summary("AAPL")
    """

    # Tables available for export with their key columns
    EXPORTABLE_TABLES = {
        "daily_prices": {"key": "symbol", "date_col": "cob_date"},
        "fundamentals": {"key": "symbol", "date_col": "report_date"},
        "company_ratios": {"key": "symbol", "date_col": "snapshot_date"},
        "fx_rates": {"key": "currency_pair", "date_col": "cob_date"},
        "vix_data": {"key": None, "date_col": "cob_date"},
        "risk_free_rate": {"key": None, "date_col": "cob_date"},
        "benchmark_index": {"key": "symbol", "date_col": "cob_date"},
        "esg_scores": {"key": "symbol", "date_col": "cob_date"},
        "news_sentiment": {"key": "symbol", "date_col": "cob_date"},
    }

    # Whitelist of valid table and column names to prevent SQL injection
    # via table/column identifiers (which cannot be parameterised).
    _VALID_IDENTIFIERS = {
        "tables": set(),  # populated from EXPORTABLE_TABLES keys
        "columns": {
            "symbol",
            "currency_pair",
            "cob_date",
            "report_date",
            "snapshot_date",
        },
    }

    def __init__(self, db_client):
        self.db_client = db_client
        self._VALID_IDENTIFIERS["tables"] = set(self.EXPORTABLE_TABLES.keys())

    def _validate_identifier(self, name: str, kind: str = "tables") -> str:
        """Validate that a SQL identifier is in the whitelist.

        :param name: Identifier to validate
        :type name: str
        :param kind: 'tables' or 'columns'
        :type kind: str
        :return: The validated identifier
        :rtype: str
        :raises ValueError: If identifier is not whitelisted
        """
        if name not in self._VALID_IDENTIFIERS[kind]:
            raise ValueError(f"Invalid {kind} identifier: '{name}'")
        return name

    def query_by_symbol(
        self,
        table: str,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Retrieve records for a specific company/symbol from a table.

        Uses parameterised queries to prevent SQL injection.

        :param table: Table name (e.g. 'daily_prices', 'fundamentals')
        :type table: str
        :param symbol: Ticker symbol to filter by
        :type symbol: str
        :param start_date: Optional start date filter (YYYY-MM-DD)
        :type start_date: str, optional
        :param end_date: Optional end date filter (YYYY-MM-DD)
        :type end_date: str, optional
        :return: List of record dictionaries
        :rtype: list[dict]
        :raises ValueError: If table name is not in EXPORTABLE_TABLES
        """
        if table not in self.EXPORTABLE_TABLES:
            raise ValueError(f"Unknown table '{table}'. " f"Available: {list(self.EXPORTABLE_TABLES.keys())}")

        meta = self.EXPORTABLE_TABLES[table]
        key_col = meta["key"]
        date_col = meta["date_col"]

        if key_col is None:
            raise ValueError(f"Table '{table}' does not support symbol filtering")

        # Validate identifiers against whitelist (not user input)
        safe_table = self._validate_identifier(table, "tables")
        safe_key = self._validate_identifier(key_col, "columns")
        safe_date = self._validate_identifier(date_col, "columns")

        # Build parameterised query — symbol/dates are bound, never interpolated
        params = {"sym": symbol}
        sql = f"SELECT * FROM systematic_equity.{safe_table} WHERE TRIM({safe_key}) = :sym"
        if start_date:
            sql += f" AND {safe_date} >= :start_dt"
            params["start_dt"] = start_date
        if end_date:
            sql += f" AND {safe_date} <= :end_dt"
            params["end_dt"] = end_date
        sql += f" ORDER BY {safe_date}"

        rows = self.db_client.read_query(sql, params)
        return rows if rows else []

    def query_by_year(self, table: str, year: int) -> list[dict]:
        """Retrieve all records from a table for a given year.

        :param table: Table name
        :type table: str
        :param year: Calendar year to filter
        :type year: int
        :return: List of record rows
        :rtype: list[dict]
        :raises ValueError: If table name is not in EXPORTABLE_TABLES
        """
        if table not in self.EXPORTABLE_TABLES:
            raise ValueError(f"Unknown table '{table}'")

        date_col = self.EXPORTABLE_TABLES[table]["date_col"]
        safe_table = self._validate_identifier(table, "tables")
        safe_date = self._validate_identifier(date_col, "columns")

        sql = (
            f"SELECT * FROM systematic_equity.{safe_table} "
            f"WHERE EXTRACT(YEAR FROM {safe_date}) = :yr "
            f"ORDER BY {safe_date}"
        )

        rows = self.db_client.read_query(sql, {"yr": int(year)})
        return rows if rows else []

    def to_csv_string(self, rows: list, headers: Optional[list] = None) -> str:
        """Convert query results to a CSV-formatted string.

        :param rows: List of row tuples from database query
        :type rows: list
        :param headers: Optional column headers
        :type headers: list, optional
        :return: CSV string
        :rtype: str
        """
        output = io.StringIO()
        writer = csv.writer(output)
        if headers:
            writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        return output.getvalue()

    def to_json_string(self, rows: list, headers: list) -> str:
        """Convert query results to a JSON-formatted string.

        :param rows: List of row tuples from database query
        :type rows: list
        :param headers: Column headers for key names
        :type headers: list
        :return: JSON string
        :rtype: str
        """
        records = []
        for row in rows:
            record = {}
            for i, val in enumerate(row):
                key = headers[i] if i < len(headers) else f"col_{i}"
                if isinstance(val, (date, datetime)):
                    val = val.isoformat()
                record[key] = val
            records.append(record)
        return json.dumps(records, indent=2, default=str)

    def get_table_summary(self, table: str) -> dict:
        """Get summary statistics for a table.

        :param table: Table name
        :type table: str
        :return: Summary dict with row_count, date_range, symbol_count
        :rtype: dict
        """
        if table not in self.EXPORTABLE_TABLES:
            raise ValueError(f"Unknown table '{table}'")

        meta = self.EXPORTABLE_TABLES[table]
        date_col = meta["date_col"]
        key_col = meta["key"]
        safe_table = self._validate_identifier(table, "tables")
        safe_date = self._validate_identifier(date_col, "columns")

        summary = {"table": table}

        # Row count
        count_rows = self.db_client.read_query(f"SELECT COUNT(*) FROM systematic_equity.{safe_table}")
        summary["row_count"] = count_rows[0][0] if count_rows else 0

        # Date range
        date_rows = self.db_client.read_query(
            f"SELECT MIN({safe_date}), MAX({safe_date}) FROM systematic_equity.{safe_table}"
        )
        if date_rows and date_rows[0][0]:
            summary["date_range"] = {
                "start": str(date_rows[0][0]),
                "end": str(date_rows[0][1]),
            }

        # Symbol count (if applicable)
        if key_col:
            safe_key = self._validate_identifier(key_col, "columns")
            sym_rows = self.db_client.read_query(
                f"SELECT COUNT(DISTINCT TRIM({safe_key})) FROM systematic_equity.{safe_table}"
            )
            summary["symbol_count"] = sym_rows[0][0] if sym_rows else 0

        pipeline_logger.info(f"Table summary for {table}: {summary}")
        return summary

    def get_company_summary(self, symbol: str) -> dict:
        """Get a complete data availability summary for a single company.

        :param symbol: Ticker symbol
        :type symbol: str
        :return: Dictionary with per-table record counts and date ranges
        :rtype: dict
        """
        summary = {"symbol": symbol, "tables": {}}

        for table, meta in self.EXPORTABLE_TABLES.items():
            key_col = meta["key"]
            date_col = meta["date_col"]

            if key_col is None:
                continue

            safe_table = self._validate_identifier(table, "tables")
            safe_key = self._validate_identifier(key_col, "columns")
            safe_date = self._validate_identifier(date_col, "columns")

            rows = self.db_client.read_query(
                f"SELECT COUNT(*), MIN({safe_date}), MAX({safe_date}) "
                f"FROM systematic_equity.{safe_table} "
                f"WHERE TRIM({safe_key}) = :sym",
                {"sym": symbol},
            )

            if rows and rows[0][0] > 0:
                summary["tables"][table] = {
                    "record_count": rows[0][0],
                    "date_range": {
                        "start": str(rows[0][1]),
                        "end": str(rows[0][2]),
                    },
                }

        return summary
