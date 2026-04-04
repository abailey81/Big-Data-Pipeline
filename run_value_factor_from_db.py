"""Run value factor construction from PostgreSQL tables.

This script:
1. Connects to PostgreSQL
2. Reads fundamentals, prices, and sector data
3. Builds the value factor for one rebalance date
4. Writes the result to a CSV file

Usage example
-------------
poetry run python run_value_factor_from_db.py --rebalance_date 2025-03-31
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from modules.processing.value_factor import (
    FUNDAMENTALS_SQL,
    PRICES_SQL,
    SECTORS_SQL,
    build_value_factor,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build value factor from PostgreSQL data.")
    parser.add_argument(
        "--rebalance_date",
        required=True,
        help="Rebalance date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--db_host",
        default="localhost",
        help="PostgreSQL host.",
    )
    parser.add_argument(
        "--db_port",
        default="5438",
        help="PostgreSQL port.",
    )
    parser.add_argument(
        "--db_name",
        default="fift",
        help="PostgreSQL database name.",
    )
    parser.add_argument(
        "--db_user",
        default="postgres",
        help="PostgreSQL username.",
    )
    parser.add_argument(
        "--db_password",
        default="postgres",
        help="PostgreSQL password.",
    )
    parser.add_argument(
        "--output_dir",
        default=".",
        help="Directory where the CSV output will be written.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the full extraction and value factor build."""
    args = parse_args()

    connection_string = (
        f"postgresql+psycopg2://{args.db_user}:{args.db_password}"
        f"@{args.db_host}:{args.db_port}/{args.db_name}"
    )
    engine = create_engine(connection_string)

    with engine.connect() as conn:
        fundamentals_df = pd.read_sql(
            text(FUNDAMENTALS_SQL),
            conn,
            params={"rebalance_date": args.rebalance_date},
        )
        prices_df = pd.read_sql(
            text(PRICES_SQL),
            conn,
            params={"rebalance_date": args.rebalance_date},
        )
        sectors_df = pd.read_sql(
            text(SECTORS_SQL),
            conn,
        )

    result = build_value_factor(
        fundamentals_df=fundamentals_df,
        prices_df=prices_df,
        sectors_df=sectors_df,
        rebalance_date=args.rebalance_date,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"value_factor_{args.rebalance_date}.csv"
    result.to_csv(output_path, index=False)

    print(f"Value factor file written to: {output_path}")
    print(f"Row count: {len(result)}")


if __name__ == "__main__":
    main()