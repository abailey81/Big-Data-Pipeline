"""
Tests for all downloader modules.

Covers:
  - modules.input.price_downloader.PriceDownloader
  - modules.input.fundamentals_downloader.FundamentalsDownloader
  - modules.input.fx_downloader.FxDownloader
  - modules.input.vix_downloader.VixDownloader
  - modules.input.risk_free_rate_downloader.RiskFreeRateDownloader
  - modules.input.edgar_downloader.EdgarFundamentalsDownloader
  - Circuit breaker integration with downloaders

All tests mock external APIs and time.sleep to avoid network calls and delays.
"""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from modules.utils.circuit_breaker import CircuitBreaker, CircuitState

# ── PriceDownloader tests ──────────────────────────────────────────────


class TestPriceDownloader:

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_single_success(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.return_value = sample_price_df
        dl = PriceDownloader(api_delay=0, max_retries=3)
        result = dl.download_single("AAPL", "2024-01-01", "2024-01-05")
        assert not result.empty
        assert len(result) == 3
        mock_dl.assert_called_once()

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_single_retry_then_success(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.side_effect = [Exception("rate limit"), sample_price_df]
        dl = PriceDownloader(api_delay=0, max_retries=3, backoff_base=1.0)
        result = dl.download_single("AAPL", "2024-01-01", "2024-01-05")
        assert not result.empty
        assert mock_dl.call_count == 2

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_single_all_retries_exhausted(self, mock_dl, mock_sleep):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.side_effect = Exception("persistent failure")
        dl = PriceDownloader(api_delay=0, max_retries=2, backoff_base=1.0)
        result = dl.download_single("FAIL", "2024-01-01", "2024-01-05")
        assert result.empty
        assert mock_dl.call_count == 2

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_single_empty_retries(self, mock_dl, mock_sleep):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.return_value = pd.DataFrame()
        dl = PriceDownloader(api_delay=0, max_retries=2)
        result = dl.download_single("EMPTY", "2024-01-01", "2024-01-05")
        assert result.empty

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_single_auto_adjust_false(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.return_value = sample_price_df
        dl = PriceDownloader(api_delay=0)
        dl.download_single("AAPL", "2024-01-01", "2024-01-05")
        call_kwargs = mock_dl.call_args
        assert call_kwargs[1]["auto_adjust"] is False

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_batch_multiple_tickers(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        # Simulate multi-level columns for batch download
        arrays = [["AAPL", "AAPL", "MSFT", "MSFT"], ["Open", "Close", "Open", "Close"]]
        tuples = list(zip(*arrays))
        cols = pd.MultiIndex.from_tuples(tuples)
        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        batch_df = pd.DataFrame(
            [[150.0, 151.0, 300.0, 302.0], [151.5, 152.0, 301.0, 303.0]], index=idx, columns=cols
        )
        mock_dl.return_value = batch_df
        dl = PriceDownloader(api_delay=0)
        results = dl.download_batch(["AAPL", "MSFT"], "2024-01-01", "2024-01-05")
        assert "AAPL" in results
        assert "MSFT" in results

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_batch_single_ticker(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.return_value = sample_price_df
        dl = PriceDownloader(api_delay=0)
        results = dl.download_batch(["AAPL"], "2024-01-01", "2024-01-05")
        assert "AAPL" in results
        assert len(results["AAPL"]) == 3

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_download_batch_retry_on_failure(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        mock_dl.side_effect = [Exception("timeout"), sample_price_df]
        dl = PriceDownloader(api_delay=0, max_retries=3, backoff_base=1.0)
        results = dl.download_batch(["AAPL"], "2024-01-01", "2024-01-05")
        assert "AAPL" in results


# ── FundamentalsDownloader tests ───────────────────────────────────────


class TestFundamentalsDownloader:

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_download_all_data(self, mock_ticker_cls, mock_sleep, sample_balance_sheet, sample_income_stmt):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = sample_balance_sheet
        mock_obj.get_income_stmt.return_value = sample_income_stmt
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {"bookValue": 25.5, "priceToBook": 3.2}
        mock_ticker_cls.return_value = mock_obj
        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None
        assert not result["quarterly_balance_sheet"].empty
        assert not result["quarterly_income_stmt"].empty
        assert not result["annual_balance_sheet"].empty
        assert not result["annual_income_stmt"].empty
        assert result["info"]["bookValue"] == 25.5

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_download_partial_data_only_balance_sheet(
        self, mock_ticker_cls, mock_sleep, sample_balance_sheet
    ):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = sample_balance_sheet
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {}
        mock_ticker_cls.return_value = mock_obj
        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None
        assert not result["quarterly_balance_sheet"].empty
        assert not result["annual_balance_sheet"].empty

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_download_returns_none_after_retries(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_ticker_cls.side_effect = Exception("API error")
        dl = FundamentalsDownloader(api_delay=0, max_retries=2, backoff_base=1.0)
        result = dl.download("FAIL")
        assert result is None

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_download_extracts_info_dict(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = pd.DataFrame()
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {"bookValue": 42.0, "marketCap": 1e12}
        mock_ticker_cls.return_value = mock_obj
        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("TSLA")
        assert result is not None
        assert result["info"]["bookValue"] == 42.0

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_download_annual_gives_5_years(self, mock_ticker_cls, mock_sleep):
        """Verify annual statements provide 5-year coverage."""
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        annual_dates = pd.to_datetime(["2025-09-30", "2024-09-30", "2023-09-30", "2022-09-30", "2021-09-30"])
        quarterly_dates = pd.to_datetime(
            ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"]
        )
        annual_bs = pd.DataFrame([[100000] * 5], index=["Total Assets"], columns=annual_dates)
        quarterly_bs = pd.DataFrame([[100000] * 6], index=["Total Assets"], columns=quarterly_dates)
        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.side_effect = lambda freq="yearly": (
            annual_bs if freq == "yearly" else quarterly_bs
        )
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {}
        mock_ticker_cls.return_value = mock_obj
        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None
        assert len(result["annual_balance_sheet"].columns) == 5
        assert len(result["quarterly_balance_sheet"].columns) == 6


# ── FxDownloader tests ────────────────────────────────────────────────


class TestFxDownloader:

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_download_single_pair(self, mock_dl, mock_sleep, sample_fx_df):
        from modules.input.fx_downloader import FxDownloader

        mock_dl.return_value = sample_fx_df
        dl = FxDownloader(api_delay=0)
        result = dl.download("GBPUSD=X", "2024-01-01", "2024-01-05")
        assert not result.empty
        assert len(result) == 2

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_download_all_default_pairs(self, mock_dl, mock_sleep, sample_fx_df):
        from modules.input.fx_downloader import FX_PAIRS, FxDownloader

        mock_dl.return_value = sample_fx_df
        dl = FxDownloader(api_delay=0)
        results = dl.download_all("2024-01-01", "2024-01-05")
        assert len(results) == len(FX_PAIRS)
        for pair in FX_PAIRS:
            assert pair in results

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_download_all_custom_pairs(self, mock_dl, mock_sleep, sample_fx_df):
        from modules.input.fx_downloader import FxDownloader

        mock_dl.return_value = sample_fx_df
        dl = FxDownloader(api_delay=0)
        custom = ["JPYUSD=X", "AUDUSD=X"]
        results = dl.download_all("2024-01-01", "2024-01-05", pairs=custom)
        assert len(results) == 2

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_download_empty_result(self, mock_dl, mock_sleep):
        from modules.input.fx_downloader import FxDownloader

        mock_dl.return_value = pd.DataFrame()
        dl = FxDownloader(api_delay=0, max_retries=1)
        result = dl.download("GBPUSD=X", "2024-01-01", "2024-01-05")
        assert result.empty

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_download_retry_on_exception(self, mock_dl, mock_sleep, sample_fx_df):
        from modules.input.fx_downloader import FxDownloader

        mock_dl.side_effect = [Exception("network"), sample_fx_df]
        dl = FxDownloader(api_delay=0, max_retries=3, backoff_base=1.0)
        result = dl.download("EURUSD=X", "2024-01-01", "2024-01-05")
        assert not result.empty
        assert mock_dl.call_count == 2


# ── VixDownloader tests ──────────────────────────────────────────────


class TestVixDownloader:

    @patch("modules.input.vix_downloader.time.sleep")
    @patch("modules.input.vix_downloader.yf.download")
    def test_download_success(self, mock_dl, mock_sleep, sample_vix_df):
        from modules.input.vix_downloader import VixDownloader

        mock_dl.return_value = sample_vix_df
        dl = VixDownloader(api_delay=0)
        result = dl.download("2024-01-01", "2024-01-05")
        assert not result.empty
        assert len(result) == 2

    @patch("modules.input.vix_downloader.time.sleep")
    @patch("modules.input.vix_downloader.yf.download")
    def test_download_retry_then_success(self, mock_dl, mock_sleep, sample_vix_df):
        from modules.input.vix_downloader import VixDownloader

        mock_dl.side_effect = [Exception("timeout"), sample_vix_df]
        dl = VixDownloader(api_delay=0, max_retries=3, backoff_base=1.0)
        result = dl.download("2024-01-01", "2024-01-05")
        assert not result.empty

    @patch("modules.input.vix_downloader.time.sleep")
    @patch("modules.input.vix_downloader.yf.download")
    def test_download_all_fail_returns_empty(self, mock_dl, mock_sleep):
        from modules.input.vix_downloader import VixDownloader

        mock_dl.side_effect = Exception("persistent")
        dl = VixDownloader(api_delay=0, max_retries=2, backoff_base=1.0)
        result = dl.download("2024-01-01", "2024-01-05")
        assert result.empty

    @patch("modules.input.vix_downloader.time.sleep")
    @patch("modules.input.vix_downloader.yf.download")
    def test_download_uses_vix_symbol(self, mock_dl, mock_sleep, sample_vix_df):
        from modules.input.vix_downloader import VIX_SYMBOL, VixDownloader

        mock_dl.return_value = sample_vix_df
        dl = VixDownloader(api_delay=0)
        dl.download("2024-01-01", "2024-01-05")
        call_args = mock_dl.call_args
        assert call_args[0][0] == VIX_SYMBOL or call_args[1].get("tickers") == VIX_SYMBOL


# ── Circuit breaker integration with downloaders ──────────────────────


class TestPriceDownloaderCircuitBreaker:

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_skips_download_when_circuit_open(self, mock_dl, mock_sleep):
        from modules.input.price_downloader import PriceDownloader

        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()  # Opens circuit
        dl = PriceDownloader(api_delay=0, circuit_breaker=cb)
        result = dl.download_single("AAPL", "2024-01-01", "2024-01-05")
        assert result.empty
        mock_dl.assert_not_called()

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_records_success_on_circuit_breaker(self, mock_dl, mock_sleep, sample_price_df):
        from modules.input.price_downloader import PriceDownloader

        cb = CircuitBreaker("test", failure_threshold=5)
        mock_dl.return_value = sample_price_df
        dl = PriceDownloader(api_delay=0, circuit_breaker=cb)
        dl.download_single("AAPL", "2024-01-01", "2024-01-05")
        assert cb._failure_count == 0  # Reset by success

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_batch_skips_when_circuit_open(self, mock_dl, mock_sleep):
        from modules.input.price_downloader import PriceDownloader

        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        dl = PriceDownloader(api_delay=0, circuit_breaker=cb)
        results = dl.download_batch(["AAPL", "MSFT"], "2024-01-01", "2024-01-05")
        assert results == {}
        mock_dl.assert_not_called()

    @patch("modules.input.price_downloader.time.sleep")
    @patch("modules.input.price_downloader.yf.download")
    def test_circuit_opens_after_consecutive_failures(self, mock_dl, mock_sleep):
        from modules.input.price_downloader import PriceDownloader

        cb = CircuitBreaker("test", failure_threshold=2)
        mock_dl.side_effect = Exception("API down")
        dl = PriceDownloader(api_delay=0, max_retries=1, backoff_base=1.0, circuit_breaker=cb)
        # First call: 1 failure
        dl.download_single("AAPL", "2024-01-01", "2024-01-05")
        assert cb.state == CircuitState.CLOSED
        # Second call: 2nd failure trips the circuit
        dl.download_single("MSFT", "2024-01-01", "2024-01-05")
        assert cb._state == CircuitState.OPEN


class TestFxDownloaderCircuitBreaker:

    @patch("modules.input.fx_downloader.time.sleep")
    @patch("modules.input.fx_downloader.yf.download")
    def test_skips_when_circuit_open(self, mock_dl, mock_sleep):
        from modules.input.fx_downloader import FxDownloader

        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        dl = FxDownloader(api_delay=0, circuit_breaker=cb)
        result = dl.download("GBPUSD=X", "2024-01-01", "2024-01-05")
        assert result.empty
        mock_dl.assert_not_called()


class TestVixDownloaderCircuitBreaker:

    @patch("modules.input.vix_downloader.time.sleep")
    @patch("modules.input.vix_downloader.yf.download")
    def test_skips_when_circuit_open(self, mock_dl, mock_sleep):
        from modules.input.vix_downloader import VixDownloader

        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        dl = VixDownloader(api_delay=0, circuit_breaker=cb)
        result = dl.download("2024-01-01", "2024-01-05")
        assert result.empty
        mock_dl.assert_not_called()


# ── RiskFreeRateDownloader tests ─────────────────────────────────────


class TestRiskFreeRateDownloader:

    @patch("modules.input.risk_free_rate_downloader.time.sleep")
    @patch("modules.input.risk_free_rate_downloader.pd.read_csv")
    def test_download_success(self, mock_csv, mock_sleep):
        from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader

        mock_csv.return_value = pd.DataFrame(
            {"DATE": ["2024-01-02", "2024-01-03", "2024-01-04"], "DGS3MO": [5.22, 5.23, "."]}
        )
        dl = RiskFreeRateDownloader(api_delay=0)
        result = dl.download("2024-01-01", "2024-01-05")
        assert not result.empty
        assert len(result) == 3

    @patch("modules.input.risk_free_rate_downloader.time.sleep")
    @patch("modules.input.risk_free_rate_downloader.pd.read_csv")
    def test_download_retry_then_success(self, mock_csv, mock_sleep):
        from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader

        mock_csv.side_effect = [
            Exception("network error"),
            pd.DataFrame({"DATE": ["2024-01-02"], "DGS3MO": [5.22]}),
        ]
        dl = RiskFreeRateDownloader(api_delay=0, max_retries=3, backoff_base=1.0)
        result = dl.download("2024-01-01", "2024-01-05")
        assert not result.empty
        assert mock_csv.call_count == 2

    @patch("modules.input.risk_free_rate_downloader.time.sleep")
    @patch("modules.input.risk_free_rate_downloader.pd.read_csv")
    def test_download_all_fail_returns_empty(self, mock_csv, mock_sleep):
        from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader

        mock_csv.side_effect = Exception("persistent failure")
        dl = RiskFreeRateDownloader(api_delay=0, max_retries=2, backoff_base=1.0)
        result = dl.download("2024-01-01", "2024-01-05")
        assert result.empty

    @patch("modules.input.risk_free_rate_downloader.time.sleep")
    @patch("modules.input.risk_free_rate_downloader.pd.read_csv")
    def test_skips_when_circuit_open(self, mock_csv, mock_sleep):
        from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader

        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        dl = RiskFreeRateDownloader(api_delay=0, circuit_breaker=cb)
        result = dl.download("2024-01-01", "2024-01-05")
        assert result.empty
        mock_csv.assert_not_called()

    @patch("modules.input.risk_free_rate_downloader.time.sleep")
    @patch("modules.input.risk_free_rate_downloader.pd.read_csv")
    def test_download_custom_series(self, mock_csv, mock_sleep):
        from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader

        mock_csv.return_value = pd.DataFrame({"DATE": ["2024-01-02"], "DGS10": [4.05]})
        dl = RiskFreeRateDownloader(api_delay=0)
        result = dl.download("2024-01-01", "2024-01-05", series_id="DGS10")
        assert not result.empty


# ── EdgarFundamentalsDownloader tests ────────────────────────────────

# Sample EDGAR company facts structure for testing
SAMPLE_COMPANY_FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc",
    "facts": {
        "us-gaap": {
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {"end": "2021-09-25", "val": 351002000000, "form": "10-K", "fp": "FY", "fy": 2021},
                        {"end": "2022-01-01", "val": 381191000000, "form": "10-Q", "fp": "Q1", "fy": 2022},
                        {"end": "2022-04-02", "val": 350662000000, "form": "10-Q", "fp": "Q2", "fy": 2022},
                        {"end": "2022-07-02", "val": 336309000000, "form": "10-Q", "fp": "Q3", "fy": 2022},
                        {"end": "2023-01-01", "val": 346747000000, "form": "10-Q", "fp": "Q1", "fy": 2023},
                    ]
                },
            },
            "NetIncomeLoss": {
                "label": "Net Income",
                "units": {
                    "USD": [
                        {"end": "2022-01-01", "val": 34630000000, "form": "10-Q", "fp": "Q1", "fy": 2022},
                        {"end": "2022-04-02", "val": 25010000000, "form": "10-Q", "fp": "Q2", "fy": 2022},
                    ]
                },
            },
            "EarningsPerShareDiluted": {
                "label": "Diluted EPS",
                "units": {
                    "USD/shares": [
                        {"end": "2022-01-01", "val": 2.10, "form": "10-Q", "fp": "Q1", "fy": 2022},
                    ]
                },
            },
        }
    },
}


class TestEdgarFundamentalsDownloader:

    @patch("modules.input.edgar_downloader.time.sleep")
    @patch("modules.input.edgar_downloader.urllib.request.urlopen")
    def test_download_success(self, mock_urlopen, mock_sleep):
        from modules.input.edgar_downloader import EdgarFundamentalsDownloader

        # Mock ticker map response
        ticker_map_resp = MagicMock()
        ticker_map_resp.read.return_value = json.dumps({"0": {"ticker": "AAPL", "cik_str": 320193}}).encode()
        ticker_map_resp.__enter__ = lambda s: s
        ticker_map_resp.__exit__ = MagicMock(return_value=False)

        # Mock company facts response
        facts_resp = MagicMock()
        facts_resp.read.return_value = json.dumps(SAMPLE_COMPANY_FACTS).encode()
        facts_resp.__enter__ = lambda s: s
        facts_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [ticker_map_resp, facts_resp]

        dl = EdgarFundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None
        assert result["entityName"] == "Apple Inc"

    @patch("modules.input.edgar_downloader.time.sleep")
    @patch("modules.input.edgar_downloader.urllib.request.urlopen")
    def test_skips_when_circuit_open(self, mock_urlopen, mock_sleep):
        from modules.input.edgar_downloader import EdgarFundamentalsDownloader

        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        dl = EdgarFundamentalsDownloader(api_delay=0, circuit_breaker=cb)
        result = dl.download("AAPL")
        assert result is None
        mock_urlopen.assert_not_called()


class TestExtractEdgarFundamentals:

    def test_extracts_quarterly_and_annual_records(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals(SAMPLE_COMPANY_FACTS, "AAPL")
        assert len(records) > 0
        period_types = {r["period_type"] for r in records}
        assert "quarterly" in period_types
        assert "annual" in period_types  # 10-K entry in sample data
        for r in records:
            assert r["symbol"] == "AAPL"
            assert r["currency"] == "USD"

    def test_extracts_quarterly_only(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals(SAMPLE_COMPANY_FACTS, "AAPL", period_types=("quarterly",))
        for r in records:
            assert r["period_type"] == "quarterly"

    def test_extracts_total_assets(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals(SAMPLE_COMPANY_FACTS, "AAPL")
        asset_records = [r for r in records if r["field_name"] == "total_assets"]
        assert len(asset_records) == 5  # 4 quarterly + 1 annual (10-K)
        dates = sorted([str(r["report_date"]) for r in asset_records])
        assert "2021-09-25" in dates  # annual 10-K
        assert "2022-01-01" in dates
        assert "2022-04-02" in dates

    def test_extracts_eps_from_usd_shares(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals(SAMPLE_COMPANY_FACTS, "AAPL")
        eps_records = [r for r in records if r["field_name"] == "diluted_eps"]
        assert len(eps_records) == 1
        assert eps_records[0]["field_value"] == 2.10

    def test_start_date_filter(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals(SAMPLE_COMPANY_FACTS, "AAPL", start_date="2022-03-01")
        for r in records:
            assert str(r["report_date"]) >= "2022-03-01"

    def test_empty_facts_returns_empty(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals(None, "AAPL")
        assert records == []

    def test_no_us_gaap_returns_empty(self):
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        records = extract_edgar_fundamentals({"facts": {}}, "AAPL")
        assert records == []


class TestIsUsTicker:

    def test_us_ticker(self):
        from modules.input.edgar_downloader import is_us_ticker

        assert is_us_ticker("AAPL") is True
        assert is_us_ticker("MSFT") is True

    def test_london_ticker(self):
        from modules.input.edgar_downloader import is_us_ticker

        assert is_us_ticker("AAL.L") is False

    def test_paris_ticker(self):
        from modules.input.edgar_downloader import is_us_ticker

        assert is_us_ticker("ACA.PA") is False

    def test_swiss_ticker(self):
        from modules.input.edgar_downloader import is_us_ticker

        assert is_us_ticker("NESN.SW") is False


# ── FundamentalsDownloader — exception paths ──────────────────────────


class TestFundamentalsDownloaderExceptions:
    """Test individual statement download exception handling paths."""

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_balance_sheet_exception_continues(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.side_effect = Exception("API error")
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {"bookValue": 10.0}
        mock_ticker_cls.return_value = mock_obj

        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None  # info still populated
        assert result["annual_balance_sheet"].empty

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_income_stmt_exception_continues(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = pd.DataFrame()
        mock_obj.get_income_stmt.side_effect = Exception("API error")
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {"bookValue": 10.0}
        mock_ticker_cls.return_value = mock_obj

        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None
        assert result["annual_income_stmt"].empty

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_cash_flow_exception_continues(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = pd.DataFrame()
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.side_effect = Exception("API error")
        mock_obj.info = {"bookValue": 10.0}
        mock_ticker_cls.return_value = mock_obj

        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_info_exception_continues(self, mock_ticker_cls, mock_sleep, sample_balance_sheet):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = sample_balance_sheet
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        type(mock_obj).info = property(lambda self: (_ for _ in ()).throw(Exception("info error")))
        mock_ticker_cls.return_value = mock_obj

        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is not None  # balance sheet still valid

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_all_empty_returns_none(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = pd.DataFrame()
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {}
        mock_ticker_cls.return_value = mock_obj

        dl = FundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is None

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_empty_data_retries_with_backoff(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_obj = MagicMock()
        mock_obj.get_balance_sheet.return_value = pd.DataFrame()
        mock_obj.get_income_stmt.return_value = pd.DataFrame()
        mock_obj.get_cash_flow.return_value = pd.DataFrame()
        mock_obj.info = {}
        mock_ticker_cls.return_value = mock_obj

        dl = FundamentalsDownloader(api_delay=0, max_retries=2, backoff_base=1.0)
        result = dl.download("EMPTY")
        assert result is None
        assert dl._failure_count == 1

    @patch("modules.input.fundamentals_downloader.time.sleep")
    @patch("modules.input.fundamentals_downloader.yf.Ticker")
    def test_circuit_opens_during_retries(self, mock_ticker_cls, mock_sleep):
        from modules.input.fundamentals_downloader import FundamentalsDownloader

        mock_ticker_cls.side_effect = Exception("persistent failure")

        cb = CircuitBreaker("test", failure_threshold=1)
        dl = FundamentalsDownloader(api_delay=0, max_retries=3, backoff_base=1.0, circuit_breaker=cb)
        result = dl.download("FAIL")
        assert result is None
        assert dl._failure_count == 1


# ── EdgarFundamentalsDownloader — retry / error paths ────────────────


class TestEdgarDownloaderRetry:

    @patch("modules.input.edgar_downloader.time.sleep")
    @patch("modules.input.edgar_downloader.urllib.request.urlopen")
    def test_http_404_returns_none(self, mock_urlopen, mock_sleep):
        import urllib.error

        from modules.input.edgar_downloader import EdgarFundamentalsDownloader

        # Mock ticker map (success)
        ticker_resp = MagicMock()
        ticker_resp.read.return_value = json.dumps({"0": {"ticker": "AAPL", "cik_str": 320193}}).encode()
        ticker_resp.__enter__ = lambda s: s
        ticker_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            ticker_resp,
            urllib.error.HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None),
        ]

        dl = EdgarFundamentalsDownloader(api_delay=0)
        result = dl.download("AAPL")
        assert result is None
        assert dl._success_count == 1  # 404 counted as success

    @patch("modules.input.edgar_downloader.time.sleep")
    @patch("modules.input.edgar_downloader.urllib.request.urlopen")
    def test_http_500_retries(self, mock_urlopen, mock_sleep):
        import urllib.error

        from modules.input.edgar_downloader import EdgarFundamentalsDownloader

        ticker_resp = MagicMock()
        ticker_resp.read.return_value = json.dumps({"0": {"ticker": "TEST", "cik_str": 1}}).encode()
        ticker_resp.__enter__ = lambda s: s
        ticker_resp.__exit__ = MagicMock(return_value=False)

        facts_resp = MagicMock()
        facts_resp.read.return_value = json.dumps({"facts": {}}).encode()
        facts_resp.__enter__ = lambda s: s
        facts_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            ticker_resp,
            urllib.error.HTTPError(url="", code=500, msg="Server Error", hdrs=None, fp=None),
            facts_resp,
        ]

        dl = EdgarFundamentalsDownloader(api_delay=0, max_retries=3, backoff_base=1.0)
        result = dl.download("TEST")
        assert result is not None

    @patch("modules.input.edgar_downloader.time.sleep")
    @patch("modules.input.edgar_downloader.urllib.request.urlopen")
    def test_generic_exception_retries(self, mock_urlopen, mock_sleep):
        from modules.input.edgar_downloader import EdgarFundamentalsDownloader

        ticker_resp = MagicMock()
        ticker_resp.read.return_value = json.dumps({"0": {"ticker": "TEST", "cik_str": 1}}).encode()
        ticker_resp.__enter__ = lambda s: s
        ticker_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [ticker_resp] + [Exception("Network timeout")] * 3

        dl = EdgarFundamentalsDownloader(api_delay=0, max_retries=2, backoff_base=1.0)
        result = dl.download("TEST")
        assert result is None
        assert dl._failure_count == 1

    def test_ticker_not_in_sec_returns_none(self):
        from modules.input.edgar_downloader import EdgarFundamentalsDownloader

        dl = EdgarFundamentalsDownloader(api_delay=0)
        dl._ticker_to_cik = {"AAPL": 320193}
        result = dl._execute_download("UNKNOWN_TICKER")
        assert result is None


# ── EDGAR extract_edgar_fundamentals — computed fields ────────────────


class TestEdgarComputedFields:

    def test_computed_ebitda(self):
        """When EBITDA is missing but operating_income + depreciation exist, compute it."""
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        facts_with_depreciation = {
            "facts": {
                "us-gaap": {
                    "OperatingIncomeLoss": {
                        "units": {
                            "USD": [
                                {"end": "2022-01-01", "val": 100000, "form": "10-Q", "fp": "Q1"},
                            ]
                        }
                    },
                    "DepreciationDepletionAndAmortization": {
                        "units": {
                            "USD": [
                                {"end": "2022-01-01", "val": 20000, "form": "10-Q", "fp": "Q1"},
                            ]
                        }
                    },
                }
            }
        }
        records = extract_edgar_fundamentals(facts_with_depreciation, "TEST")
        ebitda = [r for r in records if r["field_name"] == "ebitda"]
        assert len(ebitda) == 1
        assert ebitda[0]["field_value"] == 120000  # 100000 + abs(20000)

    def test_computed_free_cash_flow(self):
        """When FCF missing but operating_cash_flow + capex exist, compute it."""
        from modules.input.edgar_downloader import extract_edgar_fundamentals

        facts_with_capex = {
            "facts": {
                "us-gaap": {
                    "NetCashProvidedByUsedInOperatingActivities": {
                        "units": {
                            "USD": [
                                {"end": "2022-01-01", "val": 150000, "form": "10-Q", "fp": "Q1"},
                            ]
                        }
                    },
                    "PaymentsToAcquirePropertyPlantAndEquipment": {
                        "units": {
                            "USD": [
                                {"end": "2022-01-01", "val": -30000, "form": "10-Q", "fp": "Q1"},
                            ]
                        }
                    },
                }
            }
        }
        records = extract_edgar_fundamentals(facts_with_capex, "TEST")
        fcf = [r for r in records if r["field_name"] == "free_cash_flow"]
        assert len(fcf) == 1
        assert fcf[0]["field_value"] == 120000  # 150000 - abs(-30000)


# ── FxDownloader._has_valid_close coverage ────────────────────────────


class TestFxHasValidClose:

    def test_multiindex_with_valid_close(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader()
        idx = pd.to_datetime(["2024-01-02"])
        arrays = [["Close"], ["GBPUSD=X"]]
        mi = pd.MultiIndex.from_arrays(arrays)
        df = pd.DataFrame([[1.25]], index=idx, columns=mi)
        assert dl._has_valid_close(df) == True

    def test_multiindex_all_nan_close(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader()
        idx = pd.to_datetime(["2024-01-02"])
        arrays = [["Close"], ["GBPUSD=X"]]
        mi = pd.MultiIndex.from_arrays(arrays)
        df = pd.DataFrame([[float("nan")]], index=idx, columns=mi)
        assert dl._has_valid_close(df) == False

    def test_multiindex_no_close_column(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader()
        idx = pd.to_datetime(["2024-01-02"])
        arrays = [["Open"], ["GBPUSD=X"]]
        mi = pd.MultiIndex.from_arrays(arrays)
        df = pd.DataFrame([[1.25]], index=idx, columns=mi)
        assert dl._has_valid_close(df) == False

    def test_single_level_with_close(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader()
        idx = pd.to_datetime(["2024-01-02"])
        df = pd.DataFrame({"Close": [1.25]}, index=idx)
        assert dl._has_valid_close(df) == True

    def test_single_level_no_close(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader()
        idx = pd.to_datetime(["2024-01-02"])
        df = pd.DataFrame({"Open": [1.25]}, index=idx)
        assert dl._has_valid_close(df) == False

    def test_fx_download_circuit_open(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader()
        for _ in range(20):
            dl.circuit_breaker.record_failure()
        result = dl.download("GBPUSD=X", "2024-01-01", "2024-12-31")
        assert result.empty

    def test_fx_download_empty_nan_retries(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader(max_retries=2, backoff_base=0.01)
        idx = pd.to_datetime(["2024-01-02"])
        nan_df = pd.DataFrame({"Close": [float("nan")]}, index=idx)

        with patch.object(dl, "_execute_download", return_value=nan_df), \
             patch("time.sleep"):
            result = dl.download("GBPUSD=X", "2024-01-01", "2024-12-31")
        assert result.empty

    def test_fx_download_exception_retries(self):
        from modules.input.fx_downloader import FxDownloader

        dl = FxDownloader(max_retries=2, backoff_base=0.01)

        with patch.object(dl, "_execute_download", side_effect=Exception("timeout")), \
             patch("time.sleep"):
            result = dl.download("GBPUSD=X", "2024-01-01", "2024-12-31")
        assert result.empty
