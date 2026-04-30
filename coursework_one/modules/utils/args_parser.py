"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : args_parser utils
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

import argparse
from datetime import datetime


def arg_parse_cmd():
    """Creates the argument parser for the Systematic Equity pipeline.

    :return: argparse.ArgumentParser instance
    :rtype: argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        description="Systematic Equity Pipeline: Extract and load equity data for multi-factor strategy"
    )
    parser.add_argument(
        "--env_type",
        required=True,
        choices=["dev", "docker"],
        type=str,
        help="Provide environment type: dev or docker where dev is your local machine.",
    )
    parser.add_argument(
        "--date_run",
        required=False,
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Provide date to run in format YYYY-MM-DD",
    )
    parser.add_argument(
        "--frequency",
        required=False,
        choices=["daily", "weekly", "monthly", "quarterly"],
        default=None,
        help=(
            "Pipeline run frequency. Controls the lookback window: "
            "daily=5d, weekly=14d, monthly=35d, quarterly=95d. "
            "Omit for full 6-year backfill (default)."
        ),
    )
    parser.add_argument(
        "--sources",
        required=False,
        nargs="+",
        choices=[
            "prices",
            "fundamentals",
            "fx",
            "vix",
            "risk_free_rate",
            "benchmark",
            "ratios",
            "esg",
            "sentiment",
        ],
        default=[
            "prices",
            "fundamentals",
            "fx",
            "vix",
            "risk_free_rate",
            "benchmark",
            "ratios",
            "esg",
            "sentiment",
        ],
        help="Data sources to download",
    )
    parser.add_argument(
        "--start_date", required=False, help="Override start date for data download (YYYY-MM-DD)"
    )
    parser.add_argument("--end_date", required=False, help="Override end date for data download (YYYY-MM-DD)")
    parser.add_argument(
        "--tickers", required=False, nargs="+", help="Specific tickers to download (overrides full universe)"
    )
    parser.add_argument(
        "--init_schema", action="store_true", help="Initialise the database schema before running"
    )
    parser.add_argument(
        "--dry_run", action="store_true", help="Validate configuration without downloading data"
    )
    parser.add_argument(
        "--schedule", action="store_true", help="Run the pipeline on a recurring schedule using APScheduler"
    )
    return parser
