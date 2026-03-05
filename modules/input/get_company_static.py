"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Read and load company static data
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

The investable universe is defined in Spec §7.1: 678 companies
across 8 countries and 11 GICS sectors, stored in the
systematic_equity.company_static table (seeded by Docker postgres_seed).

"""

import csv

from modules.db_ops.extract_from_query import get_postgres_data
from modules.utils.info_logger import pipeline_logger


def get_equity_static(database: str = "fift", **kwargs) -> list[tuple]:
    """Read the full investable universe from systematic_equity.company_static.

    Returns all 678 companies seeded by the Docker postgres_seed container.

    :param database: PostgreSQL database name
    :type database: str
    :return: List of tuples (symbol, security, gics_sector, gics_industry,
             country, region)
    :rtype: list[tuple]
    """
    sql_query = "SELECT * FROM systematic_equity.company_static"
    static_data = get_postgres_data(sql_query=sql_query, database=database, **kwargs)
    pipeline_logger.info(f"Loaded {len(static_data)} companies from equity_static")
    return static_data


def get_ticker_list(database: str = "fift", **kwargs) -> list[str]:
    """Read just the ticker symbols from equity_static.

    :param database: PostgreSQL database name
    :type database: str
    :return: List of raw ticker symbols (may contain trailing whitespace)
    :rtype: list[str]
    """
    sql_query = "SELECT TRIM(symbol) FROM systematic_equity.company_static"
    result = get_postgres_data(sql_query=sql_query, database=database, **kwargs)
    return [row[0].strip() for row in result]


def load_company_static_csv(csv_path: str) -> list[dict]:
    """Parse the ift_coursework CSV into records for database loading.

    The CSV has columns: Symbol, Security, GICS Sector, GICS Industry,
    Country, Region. Symbol values have trailing whitespace (Spec §7.2 Issue 1).

    :param csv_path: Path to the systematic_equity_company_static CSV
    :type csv_path: str
    :return: List of dicts suitable for load_company_static()
    :rtype: list[dict]
    """
    records = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(
                {
                    "symbol": row.get("Symbol", "").strip(),
                    "security": row.get("Security", "").strip(),
                    "gics_sector": row.get("GICS Sector", "").strip(),
                    "gics_industry": row.get("GICS Industry", "").strip(),
                    "country": row.get("Country", "").strip()[:3],
                    "region": row.get("Region", "").strip(),
                }
            )
    pipeline_logger.info(f"Parsed {len(records)} companies from {csv_path}")
    return records
