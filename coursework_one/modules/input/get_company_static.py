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
            # Support both capitalised headers (spec) and lowercase (actual CSV)
            records.append(
                {
                    "symbol": (row.get("Symbol") or row.get("symbol", "")).strip(),
                    "security": (row.get("Security") or row.get("security", "")).strip(),
                    "gics_sector": (row.get("GICS Sector") or row.get("gics_sector", "")).strip(),
                    "gics_industry": (row.get("GICS Industry") or row.get("gics_industry", "")).strip(),
                    "country": (row.get("Country") or row.get("country", "")).strip()[:3],
                    "region": (row.get("Region") or row.get("region", "")).strip(),
                }
            )
    pipeline_logger.info(f"Parsed {len(records)} companies from {csv_path}")
    return records
