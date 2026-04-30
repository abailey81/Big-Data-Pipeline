"""
Tests for Main.py orchestration functions.

Covers:
  - _signal_handler() and _check_shutdown()
  - generate_run_id()
  - _run_health_checks() with mocked checker
  - _detect_inactive_tickers() with mocked DB queries
  - _extract_ratios_from_info() and _compute_derived_ratios()
  - _run_prices() with mocked dependencies
  - _run_fundamentals() with mocked dependencies
  - _run_fx() with mocked dependencies
  - _run_vix() with mocked dependencies
  - _run_risk_free_rate() with mocked dependencies
  - _run_ratios() with mocked dependencies
  - _run_edgar_fundamentals() with mocked dependencies
  - _run_finnhub_fundamentals() with mocked dependencies
  - _run_benchmark() with mocked dependencies
  - _run_esg() with mocked dependencies
  - _run_sentiment() with mocked dependencies
  - main() dry run and scheduled mode
"""

import signal
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Signal handler / shutdown tests ──────────────────────────────────


class TestSignalHandler:
    """Tests for request_shutdown and check_shutdown."""

    def setup_method(self):
        from modules.orchestration import state

        state._shutdown_requested = False

    def teardown_method(self):
        from modules.orchestration import state

        state._shutdown_requested = False

    def test_signal_handler_sets_flag(self):
        from modules.orchestration import state
        from modules.orchestration.state import request_shutdown

        assert state._shutdown_requested is False
        request_shutdown(signal.SIGINT, None)
        assert state._shutdown_requested is True

    def test_signal_handler_sigterm(self):
        from modules.orchestration import state
        from modules.orchestration.state import request_shutdown

        request_shutdown(signal.SIGTERM, None)
        assert state._shutdown_requested is True

    def test_check_shutdown_false_when_not_requested(self):
        from modules.orchestration.state import check_shutdown as _check_shutdown

        assert _check_shutdown("prices") is False

    def test_check_shutdown_true_when_requested(self):
        from modules.orchestration import state
        from modules.orchestration.state import check_shutdown as _check_shutdown

        state._shutdown_requested = True
        assert _check_shutdown("prices") is True

    def test_check_shutdown_empty_stage(self):
        from modules.orchestration import state
        from modules.orchestration.state import check_shutdown as _check_shutdown

        state._shutdown_requested = True
        assert _check_shutdown() is True


# ── generate_run_id tests ────────────────────────────────────────────


class TestGenerateRunId:

    def test_run_id_format(self):
        from modules.utils import generate_run_id

        rid = generate_run_id()
        assert isinstance(rid, str)
        assert len(rid) > 0

    def test_run_ids_are_unique(self):
        from modules.utils import generate_run_id

        ids = {generate_run_id() for _ in range(10)}
        assert len(ids) == 10


# ── _run_health_checks tests ────────────────────────────────────────


class TestRunHealthChecks:

    @patch("modules.orchestration.state.PipelineHealthChecker")
    def test_healthy_returns_true(self, mock_checker_cls):
        from modules.orchestration.state import run_health_checks as _run_health_checks

        mock_checker = MagicMock()
        result = SimpleNamespace(name="PostgreSQL", healthy=True, message="OK")
        mock_checker.run_all.return_value = [result]
        mock_checker.critical_healthy.return_value = True
        mock_checker_cls.return_value = mock_checker

        db = MagicMock()
        minio = MagicMock()
        tracker = MagicMock()
        conf = {"config": {}}

        assert _run_health_checks(db, minio, conf, tracker) is True

    @patch("modules.orchestration.state.PipelineHealthChecker")
    def test_unhealthy_critical_returns_false(self, mock_checker_cls):
        from modules.orchestration.state import run_health_checks as _run_health_checks

        mock_checker = MagicMock()
        result = SimpleNamespace(name="PostgreSQL", healthy=False, message="conn refused")
        mock_checker.run_all.return_value = [result]
        mock_checker.critical_healthy.return_value = False
        mock_checker_cls.return_value = mock_checker

        db = MagicMock()
        minio = MagicMock()
        tracker = MagicMock()
        conf = {"config": {}}

        assert _run_health_checks(db, minio, conf, tracker) is False

    @patch("modules.orchestration.state.PipelineHealthChecker")
    def test_noncritical_failure_still_passes(self, mock_checker_cls):
        from modules.orchestration.state import run_health_checks as _run_health_checks

        mock_checker = MagicMock()
        r1 = SimpleNamespace(name="PostgreSQL", healthy=True, message="OK")
        r2 = SimpleNamespace(name="Kafka", healthy=False, message="not running")
        mock_checker.run_all.return_value = [r1, r2]
        mock_checker.critical_healthy.return_value = True
        mock_checker_cls.return_value = mock_checker

        assert _run_health_checks(MagicMock(), MagicMock(), {"config": {}}, MagicMock()) is True


# ── _detect_inactive_tickers tests ───────────────────────────────────


class TestDetectInactiveTickers:

    def test_no_candidates_returns_empty(self):
        from modules.orchestration.state import detect_inactive_tickers as _detect_inactive_tickers

        db = MagicMock()
        db.read_query.return_value = []
        result = _detect_inactive_tickers(db)
        assert result == set()

    @patch("yfinance.Ticker")
    def test_stale_tickers_checked_live(self, mock_yf_ticker):
        from modules.orchestration.state import detect_inactive_tickers as _detect_inactive_tickers

        db = MagicMock()
        # Signal 1: stale tickers
        db.read_query.side_effect = [
            [("OLDTICK",)],  # stale prices
            [],  # ingestion log
            [],  # ratio gaps
        ]

        # Mock live verification - ticker is confirmed inactive
        mock_ticker_instance = MagicMock()
        mock_ticker_instance.fast_info = {"regularMarketPrice": 0}
        mock_yf_ticker.return_value = mock_ticker_instance

        result = _detect_inactive_tickers(db, [("OLDTICK", "OLDTICK", "USD")])
        assert isinstance(result, set)

    def test_query_failure_handled_gracefully(self):
        from modules.orchestration.state import detect_inactive_tickers as _detect_inactive_tickers

        db = MagicMock()
        db.read_query.side_effect = Exception("connection lost")
        result = _detect_inactive_tickers(db)
        assert result == set()


# ── _extract_ratios_from_info tests ──────────────────────────────────


class TestExtractRatiosFromInfo:

    def test_empty_info_returns_empty(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_info

        assert _extract_ratios_from_info({}, "AAPL") == []
        assert _extract_ratios_from_info(None, "AAPL") == []

    def test_extracts_known_fields(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_info

        info = {
            "marketCap": 3000000000000,
            "trailingPE": 28.5,
            "priceToBook": 45.2,
            "beta": 1.2,
            "returnOnEquity": 0.15,
        }
        records = _extract_ratios_from_info(info, "AAPL")
        assert len(records) >= 5
        field_names = {r["field_name"] for r in records}
        assert "market_cap" in field_names
        assert "pe_ratio_trailing" in field_names
        assert "price_to_book" in field_names
        assert "beta" in field_names
        assert "return_on_equity" in field_names

    def test_skips_nan_and_inf(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_info

        info = {
            "marketCap": float("nan"),
            "trailingPE": float("inf"),
            "beta": 1.1,
        }
        records = _extract_ratios_from_info(info, "TEST")
        field_names = {r["field_name"] for r in records}
        assert "market_cap" not in field_names
        assert "pe_ratio_trailing" not in field_names
        assert "beta" in field_names

    def test_record_structure(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_info

        info = {"beta": 1.05}
        records = _extract_ratios_from_info(info, "MSFT")
        assert len(records) >= 1
        r = [x for x in records if x["field_name"] == "beta"][0]
        assert r["symbol"] == "MSFT"
        assert r["field_value"] == 1.05
        assert isinstance(r["snapshot_date"], date)


class TestComputeDerivedRatios:

    def test_book_to_price_computed(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"bookValue": 25.0, "regularMarketPrice": 100.0}
        records = _compute_derived_ratios(info, "AAPL", date.today())
        field_names = {r["field_name"] for r in records}
        assert "book_to_price" in field_names
        bp = [r for r in records if r["field_name"] == "book_to_price"][0]
        assert abs(bp["field_value"] - 0.25) < 0.001

    def test_earnings_to_price_computed(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"trailingEps": 5.0, "regularMarketPrice": 100.0}
        records = _compute_derived_ratios(info, "AAPL", date.today())
        field_names = {r["field_name"] for r in records}
        assert "earnings_to_price" in field_names

    def test_no_price_no_derived(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"bookValue": 25.0}  # no price
        records = _compute_derived_ratios(info, "AAPL", date.today())
        field_names = {r["field_name"] for r in records}
        assert "book_to_price" not in field_names

    def test_roe_computed_from_equity(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {
            "netIncomeToCommon": 50000,
            "totalStockholderEquity": 200000,
        }
        records = _compute_derived_ratios(info, "MSFT", date.today())
        field_names = {r["field_name"] for r in records}
        assert "roe_computed" in field_names

    def test_cashflow_to_price(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {
            "operatingCashflow": 100000,
            "marketCap": 500000,
            "regularMarketPrice": 50.0,
        }
        records = _compute_derived_ratios(info, "TEST", date.today())
        field_names = {r["field_name"] for r in records}
        assert "cashflow_to_price" in field_names


# ── _run_fx tests ────────────────────────────────────────────────────


class TestRunFx:

    @patch("modules.orchestration.stage_macro.FxDownloader")
    @patch("modules.orchestration.stage_macro.DataQualityChecker")
    @patch("modules.orchestration.stage_macro.clean_fx_dataframe")
    def test_run_fx_success_path(self, mock_clean, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_macro import run_fx as _run_fx

        mock_dl = MagicMock()
        mock_dl.stats = {"total": 4, "success": 4}
        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        fx_df = pd.DataFrame(
            {"Open": [1.27, 1.28], "High": [1.28, 1.29], "Low": [1.26, 1.27], "Close": [1.275, 1.285]},
            index=idx,
        )
        mock_dl.download_all.return_value = {"GBPUSD=X": fx_df}
        mock_dl_cls.return_value = mock_dl

        mock_dq = MagicMock()
        mock_dq_cls.return_value = mock_dq

        mock_clean.return_value = [
            {"currency_pair": "GBPUSD=X", "cob_date": "2024-01-02", "close_rate": 1.275}
        ]

        db = MagicMock()
        db.upsert_fx_rates.return_value = 1
        minio = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}
        metrics = MagicMock()

        result = _run_fx(db, minio, params, "2024-01-01", "2024-01-05", "run-1", "daily", metrics=metrics)
        db.upsert_fx_rates.assert_called_once()
        metrics.record_outcome.assert_called()

    @patch("modules.orchestration.stage_macro.FxDownloader")
    @patch("modules.orchestration.stage_macro.DataQualityChecker")
    @patch("modules.orchestration.stage_macro.clean_fx_dataframe")
    def test_run_fx_empty_data(self, mock_clean, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_macro import run_fx as _run_fx

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download_all.return_value = {}
        mock_dl_cls.return_value = mock_dl
        mock_dq_cls.return_value = MagicMock()

        db = MagicMock()
        minio = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}

        _run_fx(db, minio, params, "2024-01-01", "2024-01-05", "run-1", "daily")
        db.upsert_fx_rates.assert_not_called()


# ── _run_vix tests ───────────────────────────────────────────────────


class TestRunVix:

    @patch("modules.orchestration.stage_macro.VixDownloader")
    @patch("modules.orchestration.stage_macro.DataQualityChecker")
    @patch("modules.orchestration.stage_macro.clean_vix_dataframe")
    def test_run_vix_success(self, mock_clean, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_macro import run_vix as _run_vix

        idx = pd.to_datetime(["2024-01-02"])
        vix_df = pd.DataFrame(
            {
                "Open": [14.0],
                "High": [15.0],
                "Low": [13.5],
                "Close": [14.5],
                "Adj Close": [14.5],
                "Volume": [0],
            },
            index=idx,
        )
        mock_dl = MagicMock()
        mock_dl.stats = {"total": 1, "success": 1}
        mock_dl.download.return_value = vix_df
        mock_dl_cls.return_value = mock_dl
        mock_dq_cls.return_value = MagicMock()
        mock_clean.return_value = [{"cob_date": "2024-01-02", "close_price": 14.5}]

        db = MagicMock()
        db.upsert_vix_data.return_value = 1
        minio = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}
        metrics = MagicMock()

        result = _run_vix(db, minio, params, "2024-01-01", "2024-01-05", "run-1", "daily", metrics=metrics)
        db.upsert_vix_data.assert_called_once()

    @patch("modules.orchestration.stage_macro.VixDownloader")
    @patch("modules.orchestration.stage_macro.DataQualityChecker")
    def test_run_vix_empty_df(self, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_macro import run_vix as _run_vix

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download.return_value = pd.DataFrame()
        mock_dl_cls.return_value = mock_dl
        mock_dq_cls.return_value = MagicMock()

        db = MagicMock()
        metrics = MagicMock()
        _run_vix(
            db,
            MagicMock(),
            {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1},
            "2024-01-01",
            "2024-01-05",
            "run-1",
            "daily",
            metrics=metrics,
        )
        metrics.record_outcome.assert_called_with("vix", "^VIX", "SKIPPED")


# ── _run_risk_free_rate tests ────────────────────────────────────────


class TestRunRiskFreeRate:

    @patch("modules.orchestration.stage_macro.RiskFreeRateDownloader")
    @patch("modules.orchestration.stage_macro.clean_risk_free_rate_dataframe")
    def test_run_rfr_success(self, mock_clean, mock_dl_cls):
        from modules.orchestration.stage_macro import run_risk_free_rate as _run_risk_free_rate

        idx = pd.to_datetime(["2024-01-02"])
        rfr_df = pd.DataFrame({"Close": [5.25]}, index=idx)
        mock_dl = MagicMock()
        mock_dl.stats = {"total": 1}
        mock_dl.download.return_value = rfr_df
        mock_dl_cls.return_value = mock_dl
        mock_clean.return_value = [{"cob_date": "2024-01-02", "rate_value": 5.25}]

        db = MagicMock()
        db.upsert_risk_free_rate.return_value = 1
        minio = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}
        metrics = MagicMock()

        _run_risk_free_rate(db, minio, params, "2024-01-01", "2024-01-05", "run-1", "daily", metrics=metrics)
        db.upsert_risk_free_rate.assert_called_once()

    @patch("modules.orchestration.stage_macro.RiskFreeRateDownloader")
    def test_run_rfr_empty(self, mock_dl_cls):
        from modules.orchestration.stage_macro import run_risk_free_rate as _run_risk_free_rate

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download.return_value = pd.DataFrame()
        mock_dl_cls.return_value = mock_dl

        db = MagicMock()
        metrics = MagicMock()
        _run_risk_free_rate(
            db,
            MagicMock(),
            {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1},
            "2024-01-01",
            "2024-01-05",
            "run-1",
            "daily",
            metrics=metrics,
        )
        metrics.record_outcome.assert_called_with("risk_free_rate", "DGS3MO", "SKIPPED")


# ── _run_benchmark tests ────────────────────────────────────────────


class TestRunBenchmark:

    @patch("modules.orchestration.stage_macro.yf.download")
    @patch("modules.orchestration.stage_macro.clean_price_dataframe")
    def test_run_benchmark_success(self, mock_clean, mock_yf_dl):
        from modules.orchestration.stage_macro import run_benchmark as _run_benchmark

        idx = pd.to_datetime(["2024-01-02"])
        bench_df = pd.DataFrame(
            {
                "Open": [4700],
                "High": [4720],
                "Low": [4690],
                "Close": [4710],
                "Adj Close": [4710],
                "Volume": [3e9],
            },
            index=idx,
        )
        mock_yf_dl.return_value = bench_df
        mock_clean.return_value = [{"symbol": "^GSPC", "cob_date": "2024-01-02", "close_price": 4710}]

        db = MagicMock()
        db.upsert_benchmark_index.return_value = 1
        minio = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}
        metrics = MagicMock()

        _run_benchmark(db, minio, params, "2024-01-01", "2024-01-05", "run-1", "daily", metrics=metrics)
        db.upsert_benchmark_index.assert_called()


# ── _run_prices tests ────────────────────────────────────────────────


class TestRunPrices:

    @patch("modules.orchestration.stage_prices.PriceDownloader")
    @patch("modules.orchestration.stage_prices.DataQualityChecker")
    @patch("modules.orchestration.stage_prices.clean_price_dataframe")
    def test_run_prices_empty_ticker_map(self, mock_clean, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_prices import run_prices as _run_prices

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download_batch.return_value = {}
        mock_dl_cls.return_value = mock_dl
        mock_dq_cls.return_value = MagicMock()

        db = MagicMock()
        minio = MagicMock()
        params = {
            "api_delay_seconds": 0,
            "max_retries": 1,
            "backoff_base": 1.0,
            "batch_size": 50,
            "price_post_workers": 2,
        }

        _run_prices(db, minio, [], params, "2024-01-01", "2024-01-05", "run-1", "daily")
        db.upsert_daily_prices.assert_not_called()


# ── _run_fundamentals tests ─────────────────────────────────────────


class TestRunFundamentals:

    @patch("modules.orchestration.stage_fundamentals.ConcurrentDownloadExecutor")
    @patch("modules.orchestration.stage_fundamentals.DataQualityChecker")
    def test_run_fundamentals_empty_tickers(self, mock_dq_cls, mock_exec_cls):
        from modules.orchestration.stage_fundamentals import run_fundamentals as _run_fundamentals

        mock_dq_cls.return_value = MagicMock()
        mock_exec = MagicMock()
        mock_exec_cls.return_value = mock_exec

        db = MagicMock()
        minio = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0, "fundamentals_workers": 1}

        _run_fundamentals(db, minio, [], params, "2024-01-01", "2024-01-05", "run-1", "daily")
        mock_exec.map_with_progress.assert_called_once()


# ── _run_edgar_fundamentals tests ────────────────────────────────────


class TestRunEdgar:

    @patch("modules.orchestration.stage_fundamentals.futures_wait")
    @patch("modules.orchestration.stage_fundamentals.ThreadPoolExecutor")
    @patch("modules.orchestration.stage_fundamentals.EdgarFundamentalsDownloader")
    def test_run_edgar_us_tickers_only(self, mock_dl_cls, mock_pool_cls, mock_wait):
        from modules.orchestration.stage_fundamentals import run_edgar_fundamentals as _run_edgar_fundamentals

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl_cls.return_value = mock_dl
        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_wait.return_value = (set(), set())

        db = MagicMock()
        minio = MagicMock()
        params = {"max_retries": 1, "backoff_base": 1.0, "edgar_workers": 2}

        # Only non-US tickers → should skip
        result = _run_edgar_fundamentals(
            db, minio, [("VOD.L", "VOD.L", "GBP")], params, "2020-01-01", "run-1", "daily"
        )
        assert result is None

    @patch("modules.orchestration.stage_fundamentals.futures_wait")
    @patch("modules.orchestration.stage_fundamentals.ThreadPoolExecutor")
    @patch("modules.orchestration.stage_fundamentals.EdgarFundamentalsDownloader")
    def test_run_edgar_with_us_tickers(self, mock_dl_cls, mock_pool_cls, mock_wait):
        from modules.orchestration.stage_fundamentals import run_edgar_fundamentals as _run_edgar_fundamentals

        mock_dl = MagicMock()
        mock_dl.stats = {"total": 1}
        mock_dl_cls.return_value = mock_dl
        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_wait.return_value = (set(), set())

        db = MagicMock()
        params = {"max_retries": 1, "backoff_base": 1.0, "edgar_workers": 2}

        result = _run_edgar_fundamentals(
            db, MagicMock(), [("AAPL", "AAPL", "USD")], params, "2020-01-01", "run-1", "daily"
        )
        assert result is not None


# ── _run_finnhub_fundamentals tests ──────────────────────────────────


class TestRunFinnhub:

    @patch.dict("os.environ", {"FINNHUB_API_KEY": ""})
    def test_run_finnhub_no_api_key(self):
        from modules.orchestration.stage_fundamentals import run_finnhub_fundamentals as _run_finnhub_fundamentals

        db = MagicMock()
        params = {"max_retries": 1, "backoff_base": 1.0}
        conf = {}
        result = _run_finnhub_fundamentals(
            db, MagicMock(), [("VOD.L", "VOD.L", "GBP")], params, "2020-01-01", "run-1", "daily", conf
        )
        assert result is None

    @patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
    @patch("modules.orchestration.stage_fundamentals.futures_wait")
    @patch("modules.orchestration.stage_fundamentals.ThreadPoolExecutor")
    @patch("modules.orchestration.stage_fundamentals.FinnhubFundamentalsDownloader")
    def test_run_finnhub_no_nonus_tickers(self, mock_dl_cls, mock_pool_cls, mock_wait):
        from modules.orchestration.stage_fundamentals import run_finnhub_fundamentals as _run_finnhub_fundamentals

        mock_dl_cls.return_value = MagicMock()
        db = MagicMock()
        params = {"max_retries": 1, "backoff_base": 1.0, "finnhub_workers": 2}
        conf = {}
        result = _run_finnhub_fundamentals(
            db, MagicMock(), [("AAPL", "AAPL", "USD")], params, "2020-01-01", "run-1", "daily", conf
        )
        assert result is None


# ── _run_esg tests ───────────────────────────────────────────────────


class TestRunEsg:

    @patch("modules.orchestration.stage_esg.clean_esg_record")
    @patch("modules.orchestration.stage_esg.EsgDownloader")
    def test_run_esg_processes_tickers(self, mock_dl_cls, mock_clean):
        from modules.orchestration.stage_esg import run_esg as _run_esg

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download_batch.return_value = {}
        mock_dl.download.return_value = {"total_esg": 75.0}
        mock_dl._download_count = 0
        mock_dl._success_count = 0
        mock_dl_cls.return_value = mock_dl

        mock_clean.return_value = {"symbol": "AAPL", "total_esg": 75.0}

        db = MagicMock()
        db.upsert_esg_scores.return_value = 1
        mongo = MagicMock()
        kafka = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}
        metrics = MagicMock()

        result = _run_esg(
            db, mongo, kafka, [("AAPL", "AAPL", "USD")], params, "run-1", "daily", metrics=metrics
        )
        db.upsert_esg_scores.assert_called_once()
        metrics.record_outcome.assert_called()

    @patch("modules.orchestration.stage_esg.EsgDownloader")
    def test_run_esg_empty_ticker_list(self, mock_dl_cls):
        from modules.orchestration.stage_esg import run_esg as _run_esg

        mock_dl = MagicMock()
        mock_dl.download_batch.return_value = {}
        mock_dl_cls.return_value = mock_dl

        db = MagicMock()
        _run_esg(
            db,
            MagicMock(),
            MagicMock(),
            [],
            {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0},
            "run-1",
            "daily",
        )
        db.upsert_esg_scores.assert_not_called()


# ── _run_sentiment tests ────────────────────────────────────────────


class TestRunSentiment:

    @patch("modules.orchestration.stage_sentiment.ThreadPoolExecutor")
    @patch("modules.orchestration.stage_sentiment.NewsApiDownloader")
    @patch("modules.orchestration.stage_sentiment.GdeltDownloader")
    @patch("modules.orchestration.stage_sentiment.NewsDownloader")
    def test_run_sentiment_uses_thread_pool(
        self, mock_dl_cls, mock_gdelt_cls, mock_newsapi_cls, mock_pool_cls
    ):
        from modules.orchestration.stage_sentiment import run_news_sentiment as _run_news_sentiment

        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl_cls.return_value = mock_dl
        mock_gdelt_cls.return_value = MagicMock()
        mock_newsapi = MagicMock()
        mock_newsapi.api_key = ""
        mock_newsapi_cls.return_value = mock_newsapi

        mock_pool = MagicMock()
        mock_pool.__enter__ = MagicMock(return_value=mock_pool)
        mock_pool.__exit__ = MagicMock(return_value=False)
        mock_pool.map.return_value = iter([])
        mock_pool_cls.return_value = mock_pool

        db = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0, "sentiment_workers": 2}

        _run_news_sentiment(
            db, MagicMock(), MagicMock(), MagicMock(), [("AAPL", "AAPL", "USD")], params, "run-1", "daily"
        )
        mock_pool_cls.assert_called_once()


# ── RATIO_FIELDS and FINNHUB_METRIC_FIELDS tests ────────────────────


class TestRatioFieldMappings:

    def test_ratio_fields_not_empty(self):
        from modules.orchestration.stage_ratios import RATIO_FIELDS

        assert len(RATIO_FIELDS) > 20

    def test_finnhub_metric_fields_not_empty(self):
        from modules.orchestration.stage_ratios import FINNHUB_METRIC_FIELDS

        assert len(FINNHUB_METRIC_FIELDS) > 15

    def test_no_duplicate_canonical_names(self):
        from modules.orchestration.stage_ratios import RATIO_FIELDS

        values = list(RATIO_FIELDS.values())
        assert len(values) == len(set(values))


# ── main() dry run test ──────────────────────────────────────────────


# ── _compute_earnings_stability tests ────────────────────────────────


class TestComputeEarningsStability:

    def test_returns_empty_when_db_query_fails(self):
        from modules.orchestration.stage_ratios import _compute_earnings_stability

        db = MagicMock()
        db.connection.connect.side_effect = Exception("db error")
        result = _compute_earnings_stability(db, "AAPL", date.today())
        assert result == []

    @patch("sqlalchemy.text")
    def test_returns_empty_when_not_enough_rows(self, mock_text):
        from modules.orchestration.stage_ratios import _compute_earnings_stability

        db = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [(2.5,), (2.3,)]
        db.connection.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = _compute_earnings_stability(db, "AAPL", date.today())
        assert result == []

    @patch("sqlalchemy.text")
    def test_returns_stability_with_enough_data(self, mock_text):
        from modules.orchestration.stage_ratios import _compute_earnings_stability

        db = MagicMock()
        mock_conn = MagicMock()
        # 6 quarters of EPS (descending date order)
        mock_conn.execute.return_value.fetchall.return_value = [
            (3.0,),
            (2.8,),
            (2.5,),
            (2.6,),
            (2.4,),
            (2.2,),
        ]
        db.connection.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = _compute_earnings_stability(db, "AAPL", date.today())
        assert len(result) == 1
        assert result[0]["field_name"] == "earnings_stability"
        assert result[0]["field_value"] > 0


# ── _compute_debt_equity_from_fundamentals tests ─────────────────────


class TestComputeDebtEquityFromFundamentals:

    def test_returns_empty_when_db_query_fails(self):
        from modules.orchestration.stage_ratios import _compute_debt_equity_from_fundamentals

        db = MagicMock()
        db.connection.connect.side_effect = Exception("db error")
        result = _compute_debt_equity_from_fundamentals(db, "AAPL", date.today())
        assert result == []

    @patch("sqlalchemy.text")
    def test_returns_empty_when_no_rows(self, mock_text):
        from modules.orchestration.stage_ratios import _compute_debt_equity_from_fundamentals

        db = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        db.connection.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = _compute_debt_equity_from_fundamentals(db, "AAPL", date.today())
        assert result == []

    @patch("sqlalchemy.text")
    def test_returns_de_ratios(self, mock_text):
        from modules.orchestration.stage_ratios import _compute_debt_equity_from_fundamentals

        db = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [(50000.0, 200000.0)]
        db.connection.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = _compute_debt_equity_from_fundamentals(db, "AAPL", date.today())
        assert len(result) == 2  # debt_to_equity + debt_to_equity_inv
        names = {r["field_name"] for r in result}
        assert "debt_to_equity" in names
        assert "debt_to_equity_inv" in names


# ── _fetch_finnhub_metric_ratios tests ───────────────────────────────


class TestFetchFinnhubMetricRatios:

    def test_returns_empty_no_api_key(self):
        from modules.orchestration.stage_ratios import _fetch_finnhub_metric_ratios

        assert _fetch_finnhub_metric_ratios("AAPL", "", "AAPL") == []

    def test_returns_empty_non_us_ticker(self):
        from modules.orchestration.stage_ratios import _fetch_finnhub_metric_ratios

        assert _fetch_finnhub_metric_ratios("VOD.L", "key123", "VOD.L") == []

    @patch("urllib.request.urlopen")
    def test_returns_records_for_us_ticker(self, mock_urlopen):
        import json

        from modules.orchestration.stage_ratios import _fetch_finnhub_metric_ratios

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "metric": {
                    "beta": 1.2,
                    "marketCapitalization": 3000000,
                    "peNormalizedAnnual": 28.5,
                }
            }
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        records = _fetch_finnhub_metric_ratios("AAPL", "test_key", "AAPL")
        assert len(records) >= 3
        field_names = {r["field_name"] for r in records}
        assert "beta" in field_names
        assert "market_cap" in field_names

    @patch("urllib.request.urlopen")
    def test_handles_http_error(self, mock_urlopen):
        import urllib.error

        from modules.orchestration.stage_ratios import _fetch_finnhub_metric_ratios

        mock_urlopen.side_effect = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        result = _fetch_finnhub_metric_ratios("AAPL", "key", "AAPL")
        assert result == []


# ── _extract_ratios_from_fast_info tests ─────────────────────────────


class TestExtractRatiosFromFastInfo:

    def test_returns_records_from_fast_info(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_fast_info

        mock_ticker = MagicMock()
        fi = MagicMock()
        fi.market_cap = 3000000000000
        fi.shares = 15000000000
        fi.year_high = 200.0
        fi.year_low = 150.0
        mock_ticker.fast_info = fi

        records = _extract_ratios_from_fast_info(mock_ticker, "AAPL")
        assert len(records) == 4
        field_names = {r["field_name"] for r in records}
        assert "market_cap" in field_names
        assert "shares_outstanding" in field_names

    def test_returns_empty_on_exception(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_fast_info

        mock_ticker = MagicMock()
        type(mock_ticker).fast_info = PropertyMock(side_effect=Exception("no data"))

        records = _extract_ratios_from_fast_info(mock_ticker, "DEAD")
        assert records == []

    def test_skips_nan_values(self):
        from modules.orchestration.stage_ratios import _extract_ratios_from_fast_info

        mock_ticker = MagicMock()
        fi = MagicMock()
        fi.market_cap = float("nan")
        fi.shares = 1000
        fi.year_high = float("inf")
        fi.year_low = 0.0  # Zero should also be skipped
        mock_ticker.fast_info = fi

        records = _extract_ratios_from_fast_info(mock_ticker, "TEST")
        field_names = {r["field_name"] for r in records}
        assert "market_cap" not in field_names
        assert "fifty_two_week_high" not in field_names
        assert "fifty_two_week_low" not in field_names


# ── _run_ratios tests ────────────────────────────────────────────────


class TestRunRatios:

    @patch("modules.orchestration.stage_ratios.ThreadPoolExecutor")
    @patch("modules.orchestration.stage_ratios.futures_wait")
    def test_run_ratios_creates_workers(self, mock_wait, mock_pool_cls):
        from modules.orchestration.stage_ratios import run_ratios as _run_ratios

        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_wait.return_value = (set(), set())

        db = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0, "ratios_workers": 2}

        _run_ratios(db, MagicMock(), [("AAPL", "AAPL", "USD")], params, "run-1", "daily")
        mock_pool_cls.assert_called_once()

    @patch("modules.orchestration.stage_ratios.ThreadPoolExecutor")
    @patch("modules.orchestration.stage_ratios.futures_wait")
    def test_run_ratios_empty_tickers(self, mock_wait, mock_pool_cls):
        from modules.orchestration.stage_ratios import run_ratios as _run_ratios

        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_wait.return_value = (set(), set())

        db = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0, "ratios_workers": 2}

        _run_ratios(db, MagicMock(), [], params, "run-1", "daily")


# ── BENCHMARK_SYMBOLS tests ─────────────────────────────────────────


class TestBenchmarkSymbols:

    def test_benchmark_symbols_present(self):
        from modules.orchestration.stage_macro import BENCHMARK_SYMBOLS

        assert len(BENCHMARK_SYMBOLS) == 5
        assert "^GSPC" in BENCHMARK_SYMBOLS
        assert "^FTSE" in BENCHMARK_SYMBOLS


# ── _run_ratios deeper tests ────────────────────────────────────────


class TestRunRatiosDeep:

    @patch("modules.orchestration.stage_ratios.futures_wait")
    @patch("modules.orchestration.stage_ratios.ThreadPoolExecutor")
    def test_run_ratios_with_metrics(self, mock_pool_cls, mock_wait):
        from modules.orchestration.stage_ratios import run_ratios as _run_ratios

        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_wait.return_value = (set(), set())

        db = MagicMock()
        metrics = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0, "ratios_workers": 2}

        _run_ratios(
            db,
            MagicMock(),
            [("AAPL", "AAPL", "USD"), ("MSFT", "MSFT", "USD")],
            params,
            "run-1",
            "daily",
            metrics=metrics,
            kafka_producer=MagicMock(),
            mongo_store=MagicMock(),
        )
        mock_pool.submit.assert_called()


# ── _run_fx deep tests ──────────────────────────────────────────────


class TestRunFxDeep:

    @patch("modules.orchestration.stage_macro.FxDownloader")
    @patch("modules.orchestration.stage_macro.DataQualityChecker")
    @patch("modules.orchestration.stage_macro.clean_fx_dataframe")
    def test_fx_with_kafka_and_mongo(self, mock_clean, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_macro import run_fx as _run_fx

        mock_dl = MagicMock()
        mock_dl.stats = {}
        idx = pd.to_datetime(["2024-01-02"])
        # yfinance returns MultiIndex columns for FX data
        arrays = [["Close", "Open", "High", "Low"], ["GBPUSD=X", "GBPUSD=X", "GBPUSD=X", "GBPUSD=X"]]
        cols = pd.MultiIndex.from_arrays(arrays)
        fx_df = pd.DataFrame([[1.275, 1.27, 1.28, 1.26]], index=idx, columns=cols)
        mock_dl.download_all.return_value = {"GBPUSD=X": fx_df}
        mock_dl_cls.return_value = mock_dl
        mock_dq_cls.return_value = MagicMock()
        mock_clean.return_value = [
            {"currency_pair": "GBPUSD=X", "cob_date": "2024-01-02", "close_rate": 1.275}
        ]

        db = MagicMock()
        db.upsert_fx_rates.return_value = 1
        kafka = MagicMock()
        mongo = MagicMock()
        metrics = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}

        _run_fx(
            db,
            MagicMock(),
            params,
            "2024-01-01",
            "2024-01-05",
            "run-1",
            "daily",
            metrics=metrics,
            kafka_producer=kafka,
            mongo_store=mongo,
            progress_update=MagicMock(),
        )
        mongo.store_document.assert_called_once()


# ── _run_vix deep tests ──────────────────────────────────────────────


class TestRunVixDeep:

    @patch("modules.orchestration.stage_macro.VixDownloader")
    @patch("modules.orchestration.stage_macro.DataQualityChecker")
    @patch("modules.orchestration.stage_macro.clean_vix_dataframe")
    def test_vix_with_kafka_and_mongo(self, mock_clean, mock_dq_cls, mock_dl_cls):
        from modules.orchestration.stage_macro import run_vix as _run_vix

        idx = pd.to_datetime(["2024-01-02"])
        # yfinance returns MultiIndex columns
        arrays = [
            ["Open", "High", "Low", "Close", "Adj Close", "Volume"],
            ["^VIX", "^VIX", "^VIX", "^VIX", "^VIX", "^VIX"],
        ]
        cols = pd.MultiIndex.from_arrays(arrays)
        vix_df = pd.DataFrame([[14.0, 15.0, 13.5, 14.5, 14.5, 0]], index=idx, columns=cols)
        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download.return_value = vix_df
        mock_dl_cls.return_value = mock_dl
        mock_dq_cls.return_value = MagicMock()
        mock_clean.return_value = [{"cob_date": "2024-01-02", "close_price": 14.5}]

        db = MagicMock()
        db.upsert_vix_data.return_value = 1
        kafka = MagicMock()
        mongo = MagicMock()
        metrics = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}

        _run_vix(
            db,
            MagicMock(),
            params,
            "2024-01-01",
            "2024-01-05",
            "run-1",
            "daily",
            metrics=metrics,
            kafka_producer=kafka,
            mongo_store=mongo,
            progress_update=MagicMock(),
        )
        mongo.store_document.assert_called_once()
        db.upsert_vix_data.assert_called_once()


# ── _run_risk_free_rate deep tests ───────────────────────────────────


class TestRunRfrDeep:

    @patch("modules.orchestration.stage_macro.RiskFreeRateDownloader")
    @patch("modules.orchestration.stage_macro.clean_risk_free_rate_dataframe")
    def test_rfr_with_kafka_and_mongo(self, mock_clean, mock_dl_cls):
        from modules.orchestration.stage_macro import run_risk_free_rate as _run_risk_free_rate

        idx = pd.to_datetime(["2024-01-02"])
        rfr_df = pd.DataFrame({"Close": [5.25]}, index=idx)
        mock_dl = MagicMock()
        mock_dl.stats = {}
        mock_dl.download.return_value = rfr_df
        mock_dl_cls.return_value = mock_dl
        mock_clean.return_value = [{"cob_date": "2024-01-02", "rate_value": 5.25}]

        db = MagicMock()
        db.upsert_risk_free_rate.return_value = 1
        kafka = MagicMock()
        mongo = MagicMock()
        metrics = MagicMock()
        params = {"api_delay_seconds": 0, "max_retries": 1, "backoff_base": 1.0}

        _run_risk_free_rate(
            db,
            MagicMock(),
            params,
            "2024-01-01",
            "2024-01-05",
            "run-1",
            "daily",
            metrics=metrics,
            kafka_producer=kafka,
            mongo_store=mongo,
            progress_update=MagicMock(),
        )
        db.upsert_risk_free_rate.assert_called_once()
        mongo.store_document.assert_called_once()


class TestMainDryRun:

    @patch("Main.set_env_variables")
    @patch("Main.ReadConfig")
    @patch("modules.orchestration.state.DatabaseMethods")
    @patch("modules.orchestration.state.PostgresConfig")
    @patch("Main.arg_parse_cmd")
    def test_main_dry_run_exits_cleanly(self, mock_arg_cmd, mock_pg_cls, mock_db_cls, mock_rc, mock_sev):
        from Main import main

        # Setup mock args
        mock_parser = MagicMock()
        mock_args = SimpleNamespace(
            env_type="dev",
            date_run="2024-06-15",
            frequency="daily",
            sources=["prices"],
            start_date=None,
            end_date=None,
            tickers=None,
            init_schema=False,
            dry_run=True,
            schedule=False,
        )
        mock_parser.parse_args.return_value = mock_args
        mock_arg_cmd.return_value = mock_parser

        # Mock config
        mock_rc.return_value = {
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
                    "Minio": {"BucketName": "iftbigdata", "RawDataPath": "raw-data"},
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
                "CurrencyMapping": {".L": "GBP", "default": "USD"},
            },
        }

        mock_pg = MagicMock()
        mock_pg.username = "postgres"
        mock_pg.password = "postgres"
        mock_pg.host = "localhost"
        mock_pg.port = "5438"
        mock_pg.database = "fift"
        mock_pg_cls.return_value = mock_pg

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        # Should exit cleanly in dry_run mode
        main()
        mock_db.close.assert_called_once()
