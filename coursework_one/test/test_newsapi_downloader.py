"""
Tests for NewsAPI downloader module.

Covers:
  - modules.input.newsapi_downloader.NewsApiDownloader
  - modules.input.newsapi_downloader.parse_newsapi_articles
  - API key handling (env var, no hardcoding)
  - Rate limiting and error handling
  - Article parsing and normalisation
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# ── NewsApiDownloader tests ───────────────────────────────────────────


class TestNewsApiDownloader:

    def _make_downloader(self, api_key="test_key_123"):
        from modules.input.newsapi_downloader import NewsApiDownloader

        return NewsApiDownloader(
            api_key=api_key,
            api_delay=0,
            max_retries=2,
            backoff_base=1.0,
            max_articles=10,
            timeout=5,
        )

    def test_init_with_explicit_key(self):
        dl = self._make_downloader("my_key")
        assert dl.api_key == "my_key"
        assert dl.max_articles == 10
        assert dl.timeout == 5

    @patch.dict("os.environ", {"NEWSAPI_KEY": "env_key_456"})
    def test_init_falls_back_to_env(self):
        from modules.input.newsapi_downloader import NewsApiDownloader

        dl = NewsApiDownloader(api_delay=0, max_retries=1)
        assert dl.api_key == "env_key_456"

    def test_init_no_key_returns_empty_string(self):
        from modules.input.newsapi_downloader import NewsApiDownloader

        with patch.dict("os.environ", {}, clear=True):
            dl = NewsApiDownloader(api_key="", api_delay=0, max_retries=1)
            assert dl.api_key == ""

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_execute_download_success(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "totalResults": 2,
            "articles": [
                {
                    "title": "AAPL beats estimates",
                    "url": "https://example.com/1",
                    "publishedAt": "2024-01-15T10:00:00Z",
                    "source": {"name": "Reuters"},
                },
                {
                    "title": "Apple revenue rises",
                    "url": "https://example.com/2",
                    "publishedAt": "2024-01-15T11:00:00Z",
                    "source": {"name": "Bloomberg"},
                },
            ],
        }
        mock_get.return_value = mock_resp
        result = dl._execute_download("AAPL")
        assert result is not None
        assert len(result) == 2
        assert result[0]["title"] == "AAPL beats estimates"

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_execute_download_rate_limit(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp
        result = dl._execute_download("AAPL")
        assert result is None

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_execute_download_server_error(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        result = dl._execute_download("AAPL")
        assert result is None

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_execute_download_empty_articles(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "totalResults": 0, "articles": []}
        mock_get.return_value = mock_resp
        result = dl._execute_download("AAPL")
        assert result is None

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_execute_download_network_error(self, mock_get):
        import requests

        dl = self._make_downloader()
        mock_get.side_effect = requests.RequestException("Connection refused")
        result = dl._execute_download("AAPL")
        assert result is None

    def test_execute_download_no_api_key(self):
        dl = self._make_downloader(api_key="")
        result = dl._execute_download("AAPL")
        assert result is None

    def test_download_no_api_key_returns_empty(self):
        dl = self._make_downloader(api_key="")
        result = dl.download("AAPL")
        assert result == []

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_download_success_returns_articles(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "articles": [
                {
                    "title": "Test article",
                    "url": "https://ex.com",
                    "publishedAt": "2024-01-15T10:00:00Z",
                    "source": {"name": "Test"},
                }
            ],
        }
        mock_get.return_value = mock_resp
        result = dl.download("AAPL")
        assert len(result) == 1

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_download_tracks_success_count(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "articles": [
                {
                    "title": "A",
                    "url": "https://ex.com",
                    "publishedAt": "2024-01-15T10:00:00Z",
                    "source": {"name": "Test"},
                }
            ],
        }
        mock_get.return_value = mock_resp
        dl.download("AAPL")
        assert dl._success_count == 1
        assert dl._download_count == 1

    @patch("modules.input.newsapi_downloader.requests.get")
    def test_execute_download_sends_correct_params(self, mock_get):
        dl = self._make_downloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "articles": []}
        mock_get.return_value = mock_resp
        dl._execute_download("MSFT")
        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["q"] == "MSFT"
        assert params["language"] == "en"
        assert params["sortBy"] == "publishedAt"
        assert params["pageSize"] == 10


# ── parse_newsapi_articles tests ──────────────────────────────────────


class TestParseNewsapiArticles:

    def test_parse_valid_articles(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        articles = [
            {
                "title": "Apple reports record Q4",
                "publishedAt": "2024-01-15T14:30:00Z",
                "url": "https://reuters.com/apple-q4",
                "source": {"name": "Reuters"},
            },
            {
                "title": "Tech stocks rally",
                "publishedAt": "2024-01-15T16:00:00Z",
                "url": "https://bloomberg.com/tech-rally",
                "source": {"name": "Bloomberg"},
            },
        ]
        parsed = parse_newsapi_articles(articles, "AAPL")
        assert len(parsed) == 2
        assert parsed[0]["title"] == "Apple reports record Q4"
        assert parsed[0]["publisher"] == "Reuters"
        assert parsed[0]["symbol"] == "AAPL"
        assert parsed[0]["source_api"] == "newsapi"
        assert parsed[0]["url"] == "https://reuters.com/apple-q4"

    def test_parse_empty_list(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        assert parse_newsapi_articles([], "AAPL") == []

    def test_parse_none_input(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        assert parse_newsapi_articles(None, "AAPL") == []

    def test_parse_skips_removed_articles(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        articles = [
            {"title": "[Removed]", "publishedAt": "2024-01-15T10:00:00Z", "url": "", "source": {"name": "X"}},
            {
                "title": "Valid article",
                "publishedAt": "2024-01-15T11:00:00Z",
                "url": "https://ex.com",
                "source": {"name": "Y"},
            },
        ]
        parsed = parse_newsapi_articles(articles, "AAPL")
        assert len(parsed) == 1
        assert parsed[0]["title"] == "Valid article"

    def test_parse_skips_empty_title(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        articles = [
            {"title": "", "publishedAt": "2024-01-15T10:00:00Z", "url": "", "source": {"name": "X"}},
            {"title": None, "publishedAt": "2024-01-15T10:00:00Z", "url": "", "source": {"name": "X"}},
        ]
        parsed = parse_newsapi_articles(articles, "TEST")
        assert len(parsed) == 0

    def test_parse_date_formatting(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        articles = [
            {
                "title": "Test",
                "publishedAt": "2024-03-15T09:45:00Z",
                "url": "https://ex.com",
                "source": {"name": "Test"},
            },
        ]
        parsed = parse_newsapi_articles(articles, "TEST")
        assert parsed[0]["published_at"] == "2024-03-15 09:45:00"

    def test_parse_invalid_date_keeps_raw(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        articles = [
            {
                "title": "Test",
                "publishedAt": "not-a-date",
                "url": "https://ex.com",
                "source": {"name": "Test"},
            },
        ]
        parsed = parse_newsapi_articles(articles, "TEST")
        assert parsed[0]["published_at"] == "not-a-date"

    def test_parse_missing_source_defaults(self):
        from modules.input.newsapi_downloader import parse_newsapi_articles

        articles = [
            {"title": "Test", "publishedAt": "2024-01-15T10:00:00Z", "url": "https://ex.com"},
        ]
        parsed = parse_newsapi_articles(articles, "TEST")
        assert parsed[0]["publisher"] == "NewsAPI"


class TestNewsApiDownloaderDownloadMethod:

    def test_circuit_breaker_open_returns_empty(self):
        """download() returns [] when circuit breaker is OPEN."""
        from modules.input.newsapi_downloader import NewsApiDownloader

        dl = NewsApiDownloader(api_delay=0, max_retries=1)
        dl.api_key = "test-key"
        # Trip the circuit breaker by recording enough failures
        for _ in range(dl.circuit_breaker.failure_threshold + 5):
            dl.circuit_breaker.record_failure()
        result = dl.download("AAPL")
        assert result == []
        assert dl._failure_count == 1

    def test_no_api_key_returns_empty(self):
        """download() returns [] when no API key is set."""
        from modules.input.newsapi_downloader import NewsApiDownloader

        dl = NewsApiDownloader(api_delay=0, max_retries=1)
        dl.api_key = ""
        result = dl.download("AAPL")
        assert result == []

    def test_download_with_invalid_key_handles_gracefully(self):
        """download() with invalid key returns [] after retries."""
        from modules.input.newsapi_downloader import NewsApiDownloader

        dl = NewsApiDownloader(api_delay=0, max_retries=1, backoff_base=0.01)
        dl.api_key = "invalid-key-that-will-fail"
        # This will hit the real NewsAPI and get a 401 — exercises the retry path
        result = dl.download("AAPL")
        assert isinstance(result, list)


# ── Computed ratios tests ─────────────────────────────────────────────


class TestComputedRatios:

    def test_book_to_price(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"regularMarketPrice": 150.0, "bookValue": 30.0}
        records = _compute_derived_ratios(info, "AAPL", "2024-01-15")
        b2p = [r for r in records if r["field_name"] == "book_to_price"]
        assert len(b2p) == 1
        assert abs(b2p[0]["field_value"] - 0.2) < 1e-6

    def test_earnings_to_price(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"regularMarketPrice": 200.0, "trailingEps": 10.0}
        records = _compute_derived_ratios(info, "MSFT", "2024-01-15")
        e2p = [r for r in records if r["field_name"] == "earnings_to_price"]
        assert len(e2p) == 1
        assert abs(e2p[0]["field_value"] - 0.05) < 1e-6

    def test_cashflow_to_price(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {
            "regularMarketPrice": 100.0,
            "operatingCashflow": 50_000_000,
            "marketCap": 500_000_000,
        }
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        cf2p = [r for r in records if r["field_name"] == "cashflow_to_price"]
        assert len(cf2p) == 1
        assert abs(cf2p[0]["field_value"] - 0.1) < 1e-6

    def test_roe_computed(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {
            "netIncomeToCommon": 1_000_000,
            "totalStockholderEquity": 5_000_000,
        }
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        roe = [r for r in records if r["field_name"] == "roe_computed"]
        assert len(roe) == 1
        assert abs(roe[0]["field_value"] - 0.2) < 1e-6

    def test_debt_to_equity_inv(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"debtToEquity": 50.0}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        de = [r for r in records if r["field_name"] == "debt_to_equity_inv"]
        assert len(de) == 1
        assert abs(de[0]["field_value"] - 0.02) < 1e-6

    def test_no_price_skips_price_ratios(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"bookValue": 30.0, "trailingEps": 5.0}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        price_fields = {"book_to_price", "earnings_to_price", "cashflow_to_price"}
        for r in records:
            assert r["field_name"] not in price_fields

    def test_zero_price_skips_price_ratios(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"regularMarketPrice": 0, "bookValue": 30.0}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        b2p = [r for r in records if r["field_name"] == "book_to_price"]
        assert len(b2p) == 0

    def test_zero_equity_skips_roe(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"netIncomeToCommon": 1_000_000, "totalStockholderEquity": 0}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        roe = [r for r in records if r["field_name"] == "roe_computed"]
        assert len(roe) == 0

    def test_zero_debt_equity_skips_inv(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"debtToEquity": 0}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        de = [r for r in records if r["field_name"] == "debt_to_equity_inv"]
        assert len(de) == 0

    def test_empty_info_returns_empty(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        records = _compute_derived_ratios({}, "TEST", "2024-01-15")
        assert records == []

    def test_all_ratios_computed(self):
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {
            "regularMarketPrice": 100.0,
            "bookValue": 25.0,
            "trailingEps": 5.0,
            "operatingCashflow": 10_000_000,
            "marketCap": 100_000_000,
            "netIncomeToCommon": 2_000_000,
            "totalStockholderEquity": 10_000_000,
            "debtToEquity": 40.0,
        }
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        names = {r["field_name"] for r in records}
        assert names == {
            "book_to_price",
            "earnings_to_price",
            "cashflow_to_price",
            "roe_computed",
            "debt_to_equity_inv",
        }


# ── Dynamic delisted detection tests ─────────────────────────────────


class TestDelistedDetection:

    @patch("yfinance.Ticker")
    def test_detect_inactive_no_candidates(self, mock_ticker_cls):
        """When DB returns no stale/failed tickers, result is empty."""
        from modules.orchestration.state import detect_inactive_tickers as _detect_inactive_tickers

        mock_db = MagicMock()
        # Signal 1: no stale tickers
        # Signal 2: no failed tickers
        mock_db.read_query.return_value = []
        result = _detect_inactive_tickers(mock_db)
        assert result == set()

    def test_detect_inactive_finds_zero_price_tickers(self):
        """Tickers with zero price rows are marked inactive."""
        from modules.orchestration.state import detect_inactive_tickers as _detect_inactive_tickers

        mock_db = MagicMock()
        mock_db.read_query.return_value = [("DEAD_TICKER",)]
        result = _detect_inactive_tickers(mock_db)
        assert "DEAD_TICKER" in result

    def test_detect_inactive_empty_means_all_active(self):
        """When all tickers have prices, none are flagged."""
        from modules.orchestration.state import detect_inactive_tickers as _detect_inactive_tickers

        mock_db = MagicMock()
        mock_db.read_query.return_value = []
        result = _detect_inactive_tickers(mock_db)
        assert result == set()

    def test_inactive_tickers_set_initially_empty(self):
        """Module-level _inactive_tickers starts as empty set."""
        from modules.orchestration.state import _inactive_tickers

        assert isinstance(_inactive_tickers, set)


# ── ROE computed fallback tests ──────────────────────────────────────


class TestRoeComputedFallback:

    def test_roe_via_total_stockholder_equity(self):
        """ROE computed using primary totalStockholderEquity field."""
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"netIncomeToCommon": 1_000_000, "totalStockholderEquity": 5_000_000}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        roe = [r for r in records if r["field_name"] == "roe_computed"]
        assert len(roe) == 1
        assert abs(roe[0]["field_value"] - 0.2) < 1e-6

    def test_roe_via_book_value_shares_fallback(self):
        """ROE computed using bookValue * sharesOutstanding when equity is None."""
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {
            "netIncomeToCommon": 2_000_000,
            "totalStockholderEquity": None,
            "bookValue": 20.0,
            "sharesOutstanding": 500_000,
        }
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        roe = [r for r in records if r["field_name"] == "roe_computed"]
        assert len(roe) == 1
        # equity = 20.0 * 500_000 = 10_000_000; ROE = 2M / 10M = 0.2
        assert abs(roe[0]["field_value"] - 0.2) < 1e-6

    def test_roe_skipped_when_no_equity_available(self):
        """ROE skipped when neither totalStockholderEquity nor bookValue*shares."""
        from modules.orchestration.stage_ratios import _compute_derived_ratios

        info = {"netIncomeToCommon": 1_000_000}
        records = _compute_derived_ratios(info, "TEST", "2024-01-15")
        roe = [r for r in records if r["field_name"] == "roe_computed"]
        assert len(roe) == 0
