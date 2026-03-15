"""
Pytest fixtures for Systematic Equity Pipeline tests.
"""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_price_df():
    """Sample Yahoo Finance price DataFrame for testing."""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    return pd.DataFrame(
        {
            "Open": [150.0, 151.5, 149.0],
            "High": [152.0, 153.0, 151.0],
            "Low": [149.0, 150.0, 148.0],
            "Close": [151.0, 152.0, 150.0],
            "Adj Close": [150.5, 151.5, 149.5],
            "Volume": [1000000, 1100000, 950000],
        },
        index=idx,
    )


@pytest.fixture
def sample_price_df_with_nans():
    """Price DataFrame containing NaN values for edge-case testing."""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    return pd.DataFrame(
        {
            "Open": [150.0, np.nan],
            "High": [152.0, 153.0],
            "Low": [np.nan, 150.0],
            "Close": [151.0, 152.0],
            "Adj Close": [150.5, np.nan],
            "Volume": [1000000, np.nan],
        },
        index=idx,
    )


@pytest.fixture
def sample_fx_df():
    """Sample FX rate DataFrame for testing."""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    return pd.DataFrame(
        {
            "Open": [1.2650, 1.2700],
            "High": [1.2700, 1.2750],
            "Low": [1.2600, 1.2650],
            "Close": [1.2680, 1.2720],
        },
        index=idx,
    )


@pytest.fixture
def sample_vix_df():
    """Sample VIX DataFrame for testing."""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    return pd.DataFrame(
        {
            "Open": [13.5, 14.0],
            "High": [14.2, 15.1],
            "Low": [13.0, 13.8],
            "Close": [13.8, 14.5],
            "Adj Close": [13.8, 14.5],
            "Volume": [0, 0],
        },
        index=idx,
    )


@pytest.fixture
def sample_balance_sheet():
    """Sample quarterly balance sheet DataFrame (yfinance 2.x CamelCase)."""
    dates = pd.to_datetime(["2024-09-30", "2024-06-30", "2024-03-31", "2023-12-31"])
    return pd.DataFrame(
        {
            dates[0]: [50000, 20000, 100000, 60000],
            dates[1]: [48000, 18000, 98000, 58000],
            dates[2]: [46000, 16000, 96000, 56000],
            dates[3]: [44000, 14000, 94000, 54000],
        },
        index=["StockholdersEquity", "TotalDebt", "TotalAssets", "TotalLiabilitiesNetMinorityInterest"],
    )


@pytest.fixture
def sample_income_stmt():
    """Sample quarterly income statement DataFrame (yfinance 2.x CamelCase)."""
    dates = pd.to_datetime(["2024-09-30", "2024-06-30", "2024-03-31", "2023-12-31"])
    return pd.DataFrame(
        {
            dates[0]: [5000, 30000, 8000, 2.5, 2.4, 6000],
            dates[1]: [4800, 29000, 7500, 2.4, 2.3, 5800],
            dates[2]: [4600, 28000, 7200, 2.3, 2.2, 5600],
            dates[3]: [4400, 27000, 7000, 2.2, 2.1, 5400],
        },
        index=["NetIncome", "TotalRevenue", "EBITDA", "BasicEPS", "DilutedEPS", "OperatingIncome"],
    )


@pytest.fixture
def sample_fund_data(sample_balance_sheet, sample_income_stmt):
    """Combined fundamentals data dict as returned by FundamentalsDownloader."""
    return {
        "annual_balance_sheet": sample_balance_sheet,
        "annual_income_stmt": sample_income_stmt,
        "annual_cash_flow": pd.DataFrame(),
        "quarterly_balance_sheet": sample_balance_sheet,
        "quarterly_income_stmt": sample_income_stmt,
        "quarterly_cash_flow": pd.DataFrame(),
        "info": {"bookValue": 25.5, "priceToBook": 3.2},
    }


@pytest.fixture
def postgres_config_dict():
    """Sample PostgresConfig constructor args."""
    return {
        "username": "postgres",
        "password": "postgres",
        "host": "localhost",
        "port": "5438",
        "database": "fift",
    }


# ── Fixtures for downloader / minio / main function tests ──────────────


@pytest.fixture
def mock_parsed_args():
    """Factory for creating mock parsed CLI arguments."""

    def _make(**overrides):
        defaults = {
            "env_type": "dev",
            "date_run": "2024-06-15",
            "frequency": "daily",
            "sources": ["prices", "fundamentals", "fx", "vix"],
            "start_date": None,
            "end_date": None,
            "tickers": None,
            "init_schema": False,
            "dry_run": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    return _make


@pytest.fixture
def sample_conf():
    """Minimal configuration dict matching ReadConfig output."""
    return {
        "config": {
            "env_variables": [],
            "Database": {
                "Postgres": {
                    "Host": "localhost",
                    "Database": "fift",
                    "Username": "postgres",
                    "Password": "postgres",
                    "Port": 5438,
                    "Schema": "systematic_equity",
                },
                "Minio": {
                    "BucketName": "iftbigdata",
                    "RawDataPath": "raw-data",
                },
            },
        },
        "params": {
            "Pipeline": {
                "lookback_years": 5,
                "api_delay_seconds": 0.5,
                "max_retries": 3,
                "backoff_base": 2.0,
                "batch_size": 50,
            },
            "CurrencyMapping": {
                ".L": "GBP",
                ".PA": "EUR",
                ".S": "CHF",
                "default": "USD",
            },
        },
    }
