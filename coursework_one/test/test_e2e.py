"""
End-to-end pipeline tests.

These tests exercise the full data flow from raw DataFrames through
cleaning, Pydantic validation, and output format verification.
External APIs (yfinance) are mocked, but real cleaning and validation
logic is exercised -- ensuring the entire pipeline chain is correct.

Run with: poetry run pytest -m e2e
"""

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


@pytest.mark.e2e
class TestEndToEndPricePipeline:
    """Full price ingestion flow: download → clean → validate → output."""

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_full_price_ingestion_flow(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader
        from modules.processing.data_cleaner import clean_price_dataframe
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        # 1. Prepare ticker (handles whitespace, currency, Swiss remap)
        db_symbol, yf_ticker, currency = prepare_yfinance_ticker("AAPL   ")
        assert db_symbol == "AAPL"
        assert yf_ticker == "AAPL"
        assert currency == "USD"

        # 2. Download prices (mocked)
        mock_dl.return_value = sample_price_df
        downloader = PriceDownloader(api_delay=0)
        df = downloader.download_single(yf_ticker, "2024-01-01", "2024-01-05")
        assert not df.empty

        # 3. Clean and validate through Pydantic
        records = clean_price_dataframe(df, db_symbol, currency)
        assert len(records) == 3

        # 4. Verify output format matches database schema
        for record in records:
            assert record["symbol"] == "AAPL"
            assert record["currency"] == "USD"
            assert isinstance(record["cob_date"], date)
            assert isinstance(record["open_price"], float)
            assert isinstance(record["volume"], int)
            assert "ingestion_timestamp" not in record  # Added by upsert

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_swiss_ticker_e2e(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader
        from modules.processing.data_cleaner import clean_price_dataframe
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        # Swiss ticker: stored as .S, downloaded as .SW
        db_symbol, yf_ticker, currency = prepare_yfinance_ticker("NOVN.S  ")
        assert db_symbol == "NOVN.S"
        assert yf_ticker == "NOVN.SW"
        assert currency == "CHF"

        mock_dl.return_value = sample_price_df
        downloader = PriceDownloader(api_delay=0)
        df = downloader.download_single(yf_ticker, "2024-01-01", "2024-01-05")
        records = clean_price_dataframe(df, db_symbol, currency)
        assert all(r["symbol"] == "NOVN.S" for r in records)
        assert all(r["currency"] == "CHF" for r in records)


@pytest.mark.e2e
class TestEndToEndFxPipeline:
    """Full FX ingestion flow: download → clean → validate → output."""

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_full_fx_ingestion_flow(self, mock_dl, mock_sleep, sample_fx_df):
        from modules.input.fx_downloader import FxDownloader
        from modules.processing.data_cleaner import clean_fx_dataframe

        mock_dl.return_value = sample_fx_df
        downloader = FxDownloader(api_delay=0)
        fx_data = downloader.download_all("2024-01-01", "2024-01-05")

        for pair, df in fx_data.items():
            records = clean_fx_dataframe(df, pair)
            assert len(records) > 0
            for record in records:
                assert record["currency_pair"] == pair
                assert isinstance(record["cob_date"], date)
                assert isinstance(record["close_rate"], float)


@pytest.mark.e2e
class TestEndToEndFundamentalsPipeline:
    """Full fundamentals flow: download → clean → validate → output."""

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_full_fundamentals_ingestion_flow(
        self, mock_ticker_cls, mock_sleep, sample_balance_sheet, sample_income_stmt
    ):
        from modules.input.fundamentals_downloader import FundamentalsDownloader
        from modules.processing.data_cleaner import clean_fundamentals_data
        from modules.processing.ticker_utils import prepare_yfinance_ticker

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = sample_balance_sheet
        mock_obj.get_income_stmt.return_value = sample_income_stmt
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {"bookValue": 25.5}
        mock_ticker_cls.return_value = mock_obj

        db_symbol, yf_ticker, currency = prepare_yfinance_ticker("AAPL")
        downloader = FundamentalsDownloader(api_delay=0)
        fund_data = downloader.download(yf_ticker)

        assert fund_data is not None
        records = clean_fundamentals_data(fund_data, db_symbol, currency)
        assert len(records) > 0

        # Verify EAV structure
        field_names = {r["field_name"] for r in records}
        assert "stockholders_equity" in field_names
        assert "net_income" in field_names
        assert "book_value_per_share" in field_names

        for record in records:
            assert record["symbol"] == "AAPL"
            assert isinstance(record["report_date"], date)
            assert record["period_type"] in ("quarterly", "annual")
            assert record["currency"] == "USD"

        # Verify both period types are present
        period_types = {r["period_type"] for r in records}
        assert "annual" in period_types
        assert "quarterly" in period_types


@pytest.mark.e2e
class TestEndToEndVixPipeline:
    """Full VIX flow: download → clean → validate → output."""

    @patch("modules.input.vix_downloader.time.sleep")
    @patch("modules.input.vix_downloader.yf.download")
    def test_full_vix_ingestion_flow(self, mock_dl, mock_sleep, sample_vix_df):
        from modules.input.vix_downloader import VixDownloader
        from modules.processing.data_cleaner import clean_vix_dataframe

        mock_dl.return_value = sample_vix_df
        downloader = VixDownloader(api_delay=0)
        df = downloader.download("2024-01-01", "2024-01-05")

        records = clean_vix_dataframe(df)
        assert len(records) == 2
        for record in records:
            assert isinstance(record["cob_date"], date)
            assert isinstance(record["close_price"], float)
