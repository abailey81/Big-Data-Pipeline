"""
Tests for GDELT news article downloader.

Covers:
  - modules.input.gdelt_downloader.GdeltDownloader
  - modules.input.gdelt_downloader.parse_gdelt_articles
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from modules.input.gdelt_downloader import GdeltDownloader, parse_gdelt_articles

# ---------------------------------------------------------------------------
# GdeltDownloader init
# ---------------------------------------------------------------------------


class TestGdeltDownloaderInit:

    def test_default_params(self):
        dl = GdeltDownloader()
        assert dl.source_name == "gdelt_news"
        assert dl.max_articles == 15
        assert dl.timeout == 20
        assert dl.max_retries == 3

    def test_custom_params(self):
        dl = GdeltDownloader(max_articles=25, timeout=30, max_retries=5)
        assert dl.max_articles == 25
        assert dl.timeout == 30
        assert dl.max_retries == 5


# ---------------------------------------------------------------------------
# _execute_download
# ---------------------------------------------------------------------------


class TestGdeltExecuteDownload:

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_returns_articles_on_success(self, mock_get):
        articles = [{"title": "AAPL beats estimates", "url": "http://example.com/1"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": articles}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        result = dl._execute_download("AAPL", company_name="Apple Inc")
        assert result == articles

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_returns_none_when_no_articles(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        result = dl._execute_download("AAPL")
        assert result is None

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_company_name_cleanup(self, mock_get):
        """Company name is cleaned: 'Apple Inc,' → 'Apple'."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": [{"title": "test"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        dl._execute_download("AAPL", company_name="Apple Inc, Class A")

        call_args = mock_get.call_args
        query = call_args[1]["params"]["query"] if "params" in call_args[1] else call_args[0][1]["query"]
        assert '"Apple"' in query
        assert "AAPL" in query

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_symbol_without_company_name(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": [{"title": "test"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        dl._execute_download("AAPL")

        call_args = mock_get.call_args
        query = call_args[1]["params"]["query"] if "params" in call_args[1] else call_args[0][1]["query"]
        assert "AAPL" in query

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_exchange_suffix_stripped_from_query(self, mock_get):
        """BP.L → base symbol 'BP' used in query."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": [{"title": "test"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        dl._execute_download("BP.L", company_name="BP plc")

        call_args = mock_get.call_args
        query = call_args[1]["params"]["query"] if "params" in call_args[1] else call_args[0][1]["query"]
        assert "BP" in query

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_http_error_raises(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        with pytest.raises(Exception, match="HTTP 500"):
            dl._execute_download("AAPL")


# ---------------------------------------------------------------------------
# download (with retry + circuit breaker)
# ---------------------------------------------------------------------------


class TestGdeltDownload:

    @patch("modules.input.gdelt_downloader.requests.get")
    def test_successful_download(self, mock_get):
        articles = [{"title": "Test article"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"articles": articles}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        dl = GdeltDownloader()
        result = dl.download("AAPL")
        assert result == articles
        assert dl._success_count == 1

    @patch("modules.input.base_downloader.time.sleep")
    @patch("modules.input.gdelt_downloader.requests.get")
    def test_retries_on_failure(self, mock_get, mock_sleep):
        mock_get.side_effect = Exception("Network error")

        dl = GdeltDownloader(max_retries=2)
        result = dl.download("AAPL")
        assert result is None
        assert dl._failure_count == 1

    def test_circuit_breaker_open_skips(self):
        dl = GdeltDownloader()
        dl.circuit_breaker.allow_request = MagicMock(return_value=False)
        result = dl.download("AAPL")
        assert result is None
        assert dl._failure_count == 1


# ---------------------------------------------------------------------------
# parse_gdelt_articles
# ---------------------------------------------------------------------------


class TestParseGdeltArticles:

    def test_empty_list_returns_empty(self):
        assert parse_gdelt_articles([], "AAPL") == []

    def test_none_input_returns_empty(self):
        assert parse_gdelt_articles(None, "AAPL") == []

    def test_parses_valid_article(self):
        articles = [
            {
                "title": "Apple beats Q4 estimates",
                "url": "http://example.com/article1",
                "seendate": "20240615T143000Z",
                "domain": "reuters.com",
                "sourcecountry": "United States",
                "language": "English",
            }
        ]
        result = parse_gdelt_articles(articles, "AAPL")
        assert len(result) == 1
        r = result[0]
        assert r["symbol"] == "AAPL"
        assert r["title"] == "Apple beats Q4 estimates"
        assert r["publisher"] == "reuters.com"
        assert r["article_type"] == "GDELT"
        assert r["related_tickers"] == ["AAPL"]
        assert r["source_country"] == "United States"
        assert r["published_at"].year == 2024

    def test_skips_non_english_articles(self):
        articles = [
            {"title": "Article en français", "language": "French", "seendate": "20240615T143000Z"},
        ]
        result = parse_gdelt_articles(articles, "AAPL")
        assert len(result) == 0

    def test_skips_empty_title(self):
        articles = [
            {"title": "", "language": "English", "seendate": "20240615T143000Z"},
        ]
        result = parse_gdelt_articles(articles, "AAPL")
        assert len(result) == 0

    def test_handles_missing_seendate(self):
        """Invalid seendate falls back to datetime.now()."""
        articles = [
            {"title": "Test article", "seendate": "invalid-date", "language": "English"},
        ]
        result = parse_gdelt_articles(articles, "AAPL")
        assert len(result) == 1
        assert result[0]["published_at"] is not None

    def test_handles_missing_language(self):
        """Articles with no language field are included (treated as English)."""
        articles = [
            {"title": "Test article", "seendate": "20240615T143000Z"},
        ]
        result = parse_gdelt_articles(articles, "AAPL")
        assert len(result) == 1

    def test_multiple_articles_mixed(self):
        articles = [
            {"title": "Good news", "language": "English", "seendate": "20240615T143000Z"},
            {"title": "Mauvaise nouvelle", "language": "French", "seendate": "20240615T143000Z"},
            {"title": "More good news", "language": "English", "seendate": "20240616T100000Z"},
        ]
        result = parse_gdelt_articles(articles, "MSFT")
        assert len(result) == 2
        assert all(r["symbol"] == "MSFT" for r in result)

    def test_handles_malformed_article_gracefully(self):
        """Unparseable articles are skipped, not raised."""
        articles = [
            None,  # This should be caught by the except clause
            {"title": "Valid article", "language": "English", "seendate": "20240615T143000Z"},
        ]
        result = parse_gdelt_articles(articles, "AAPL")
        # The None article should be skipped; valid one parsed
        assert len(result) >= 1
