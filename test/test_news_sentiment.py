"""
Tests for news sentiment pipeline: downloader, scorer, and aggregation.

Covers:
  - NewsDownloader (mock yfinance)
  - parse_news_articles (parsing and edge cases)
  - VADER + financial boost scoring (_score_article_text via score_articles)
  - score_articles (batch scoring)
  - aggregate_sentiment (per-ticker aggregation with composite 0-100 score)
  - deduplicate_articles
  - _run_news_sentiment integration (mock all externals)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from modules.input.news_downloader import NewsDownloader, parse_news_articles
from modules.processing.sentiment_scorer import (
    FINANCIAL_BOOST_LEXICON,
    FINANCIAL_BOOST_PHRASES,
    VADER_AVAILABLE,
    aggregate_sentiment,
    deduplicate_articles,
    score_articles,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def sample_raw_news():
    """Sample raw news articles as returned by yfinance Ticker.news."""
    return [
        {
            "title": "Apple beats earnings expectations with record revenue",
            "publisher": "Reuters",
            "link": "https://example.com/1",
            "providerPublishTime": 1709136000,  # 2024-02-28 16:00 UTC
            "type": "STORY",
            "relatedTickers": ["AAPL"],
        },
        {
            "title": "Tech stocks decline amid recession fears",
            "publisher": "Bloomberg",
            "link": "https://example.com/2",
            "providerPublishTime": 1709049600,  # 2024-02-27 16:00 UTC
            "type": "STORY",
            "relatedTickers": ["AAPL", "MSFT"],
        },
        {
            "title": "Apple announces new product line for 2024",
            "publisher": "CNBC",
            "link": "https://example.com/3",
            "providerPublishTime": 1708963200,
            "type": "STORY",
            "relatedTickers": ["AAPL"],
        },
    ]


@pytest.fixture
def sample_parsed_articles():
    """Sample parsed news articles (output of parse_news_articles)."""
    return [
        {
            "symbol": "AAPL",
            "title": "Apple beats earnings expectations with record revenue",
            "publisher": "Reuters",
            "published_at": datetime(2024, 2, 28, 16, 0, tzinfo=timezone.utc),
            "link": "https://example.com/1",
            "article_type": "STORY",
            "related_tickers": ["AAPL"],
        },
        {
            "symbol": "AAPL",
            "title": "Tech stocks decline amid recession fears",
            "publisher": "Bloomberg",
            "published_at": datetime(2024, 2, 27, 16, 0, tzinfo=timezone.utc),
            "link": "https://example.com/2",
            "article_type": "STORY",
            "related_tickers": ["AAPL", "MSFT"],
        },
    ]


# ── Tests: NewsDownloader ───────────────────────────────────────────────


class TestNewsDownloader:
    """Tests for the NewsDownloader class."""

    def test_init_defaults(self):
        dl = NewsDownloader()
        assert dl.source_name == "news_sentiment"
        assert dl.max_articles == 20
        assert dl.max_retries == 3

    def test_init_custom_params(self):
        dl = NewsDownloader(api_delay=1.0, max_retries=5, max_articles=10)
        assert dl.api_delay == 1.0
        assert dl.max_retries == 5
        assert dl.max_articles == 10

    @patch("modules.input.news_downloader.yf")
    def test_download_success(self, mock_yf, sample_raw_news):
        mock_ticker = MagicMock()
        mock_ticker.news = sample_raw_news
        mock_yf.Ticker.return_value = mock_ticker

        dl = NewsDownloader(api_delay=0.0, max_retries=1)
        result = dl.download("AAPL")

        assert result is not None
        assert len(result) == 3
        assert dl._success_count == 1

    @patch("modules.input.news_downloader.yf")
    def test_download_empty_news(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.news = []
        mock_yf.Ticker.return_value = mock_ticker

        dl = NewsDownloader(api_delay=0.0, max_retries=1)
        result = dl.download("AAPL")

        assert result is None
        assert dl._success_count == 1

    @patch("modules.input.news_downloader.yf")
    def test_download_none_news(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.news = None
        mock_yf.Ticker.return_value = mock_ticker

        dl = NewsDownloader(api_delay=0.0, max_retries=1)
        result = dl.download("AAPL")

        assert result is None

    @patch("modules.input.news_downloader.yf")
    def test_download_respects_max_articles(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.news = [{"title": f"Article {i}"} for i in range(50)]
        mock_yf.Ticker.return_value = mock_ticker

        dl = NewsDownloader(api_delay=0.0, max_articles=5, max_retries=1)
        result = dl.download("AAPL")

        assert result is not None
        assert len(result) == 5

    @patch("modules.input.news_downloader.yf")
    @patch("modules.input.news_downloader.time")
    def test_download_retries_on_exception(self, mock_time, mock_yf):
        mock_ticker = MagicMock()
        type(mock_ticker).news = PropertyMock(side_effect=[Exception("API error"), [{"title": "ok"}]])
        mock_yf.Ticker.return_value = mock_ticker

        dl = NewsDownloader(api_delay=0.0, max_retries=2)
        result = dl.download("AAPL")

        # Should succeed on second attempt
        assert result is not None

    @patch("modules.input.news_downloader.yf")
    @patch("modules.input.news_downloader.time")
    def test_download_all_retries_exhausted(self, mock_time, mock_yf):
        mock_ticker = MagicMock()
        type(mock_ticker).news = PropertyMock(side_effect=Exception("persistent error"))
        mock_yf.Ticker.return_value = mock_ticker

        dl = NewsDownloader(api_delay=0.0, max_retries=2)
        result = dl.download("AAPL")

        assert result is None
        assert dl._failure_count == 1

    def test_stats_property(self):
        dl = NewsDownloader()
        stats = dl.stats
        assert stats["source"] == "news_sentiment"
        assert stats["downloads"] == 0
        assert "circuit_breaker" in stats


# ── Tests: parse_news_articles ──────────────────────────────────────────


class TestParseNewsArticles:
    """Tests for the parse_news_articles helper function."""

    def test_parse_valid_articles(self, sample_raw_news):
        result = parse_news_articles(sample_raw_news, "AAPL")
        assert len(result) == 3
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["publisher"] == "Reuters"
        assert result[0]["article_type"] == "STORY"
        assert isinstance(result[0]["published_at"], datetime)

    def test_parse_empty_list(self):
        result = parse_news_articles([], "AAPL")
        assert result == []

    def test_parse_none(self):
        result = parse_news_articles(None, "AAPL")
        assert result == []

    def test_parse_missing_publish_time(self):
        articles = [{"title": "Test", "publisher": "Test"}]
        result = parse_news_articles(articles, "AAPL")
        assert len(result) == 1
        assert result[0]["published_at"] is not None

    def test_parse_preserves_related_tickers(self, sample_raw_news):
        result = parse_news_articles(sample_raw_news, "AAPL")
        assert result[1]["related_tickers"] == ["AAPL", "MSFT"]

    def test_parse_handles_malformed_article(self):
        articles = [
            {"title": "Good article", "providerPublishTime": 1709136000},
            None,  # This will raise TypeError and be skipped
            {"title": "Another good article"},
        ]
        result = parse_news_articles(articles, "AAPL")
        # The None should be skipped gracefully
        assert len(result) >= 2


# ── Tests: Financial boost lexicon ─────────────────────────────────────


class TestFinancialBoostLexicon:
    """Tests for the VADER + financial domain boost lexicon."""

    def test_positive_lexicon_entries_exist(self):
        # Beat, upgrade, bullish should be positive boosts
        assert "beat" in FINANCIAL_BOOST_LEXICON
        assert FINANCIAL_BOOST_LEXICON["beat"] > 0

    def test_negative_lexicon_entries_exist(self):
        # Miss, bankruptcy, fraud should be negative boosts
        assert "miss" in FINANCIAL_BOOST_LEXICON
        assert FINANCIAL_BOOST_LEXICON["miss"] < 0
        assert "bankruptcy" in FINANCIAL_BOOST_LEXICON
        assert FINANCIAL_BOOST_LEXICON["bankruptcy"] < 0

    def test_lexicon_non_empty(self):
        assert len(FINANCIAL_BOOST_LEXICON) >= 10

    def test_phrase_lexicon_non_empty(self):
        assert len(FINANCIAL_BOOST_PHRASES) >= 5

    def test_phrase_beat_estimates_positive(self):
        assert "beat estimates" in FINANCIAL_BOOST_PHRASES
        assert FINANCIAL_BOOST_PHRASES["beat estimates"] > 0

    def test_phrase_chapter_11_negative(self):
        assert "chapter 11" in FINANCIAL_BOOST_PHRASES
        assert FINANCIAL_BOOST_PHRASES["chapter 11"] < 0

    def test_all_boost_values_in_range(self):
        for word, val in FINANCIAL_BOOST_LEXICON.items():
            assert -1.0 <= val <= 1.0, f"Boost for '{word}' out of range: {val}"

    def test_all_phrase_values_in_range(self):
        for phrase, val in FINANCIAL_BOOST_PHRASES.items():
            assert -1.0 <= val <= 1.0, f"Boost for '{phrase}' out of range: {val}"


# ── Tests: score_articles ───────────────────────────────────────────────


class TestScoreArticles:
    """Tests for VADER + financial domain batch article scoring."""

    def test_score_multiple_articles(self, sample_parsed_articles):
        result = score_articles(sample_parsed_articles)
        assert len(result) == 2
        # Each article should now have sentiment fields
        for article in result:
            assert "sentiment_score" in article
            assert "sentiment_label" in article
            assert "vader_raw" in article
            assert "boost_delta" in article

    def test_score_empty_list(self):
        result = score_articles([])
        assert result == []

    def test_score_articles_preserves_original_fields(self, sample_parsed_articles):
        result = score_articles(sample_parsed_articles)
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["publisher"] == "Reuters"

    def test_score_positive_headline(self):
        articles = [{"title": "Apple beats earnings with record revenue growth"}]
        result = score_articles(articles)
        # "beats" is in FINANCIAL_BOOST_LEXICON with positive value
        assert result[0]["boost_delta"] > 0 or result[0]["vader_raw"] > 0

    def test_score_negative_headline(self):
        articles = [{"title": "Company files for bankruptcy amid fraud allegations"}]
        result = score_articles(articles)
        # Should be negative overall
        score = result[0]["sentiment_score"]
        assert score <= 0.05, f"Expected negative/neutral score, got {score}"

    def test_score_neutral_headline(self):
        articles = [{"title": "Company announces quarterly results on Tuesday"}]
        result = score_articles(articles)
        score = result[0]["sentiment_score"]
        assert -1.0 <= score <= 1.0

    def test_score_range_valid(self):
        articles = [
            {"title": "surge rally growth beat upgrade"},
            {"title": "crash loss decline fall plunge"},
        ]
        result = score_articles(articles)
        for a in result:
            assert -1.0 <= a["sentiment_score"] <= 1.0

    def test_score_empty_title(self):
        articles = [{"title": ""}]
        result = score_articles(articles)
        # Should not crash; returns neutral (0.0) when VADER unavailable or empty
        assert "sentiment_score" in result[0]

    def test_score_none_title(self):
        articles = [{"title": None}]
        result = score_articles(articles)
        assert "sentiment_score" in result[0]

    def test_score_label_positive(self):
        articles = [{"title": "Stock upgraded analyst raises price target record"}]
        result = score_articles(articles)
        if result[0]["sentiment_score"] >= 0.05:
            assert result[0]["sentiment_label"] == "positive"

    def test_score_label_negative(self):
        articles = [{"title": "Bankruptcy fraud criminal charges class action lawsuit"}]
        result = score_articles(articles)
        if result[0]["sentiment_score"] <= -0.05:
            assert result[0]["sentiment_label"] == "negative"


# ── Tests: deduplicate_articles ─────────────────────────────────────────


class TestDeduplicateArticles:
    """Tests for headline deduplication before scoring."""

    def test_remove_exact_duplicates(self):
        articles = [
            {"title": "Apple beats earnings"},
            {"title": "Apple beats earnings"},
            {"title": "Different headline"},
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 2

    def test_case_insensitive_dedup(self):
        articles = [
            {"title": "Apple Beats Earnings"},
            {"title": "apple beats earnings"},
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 1

    def test_preserves_unique(self):
        articles = [
            {"title": "Article one"},
            {"title": "Article two"},
            {"title": "Article three"},
        ]
        result = deduplicate_articles(articles)
        assert len(result) == 3

    def test_empty_list(self):
        result = deduplicate_articles([])
        assert result == []

    def test_articles_without_title(self):
        articles = [{"publisher": "Reuters"}, {"publisher": "Bloomberg"}]
        result = deduplicate_articles(articles)
        # Articles without title should be preserved
        assert len(result) == 2


# ── Tests: aggregate_sentiment ──────────────────────────────────────────


class TestAggregateSentiment:
    """Tests for per-ticker sentiment aggregation with composite 0-100 score."""

    def test_aggregate_basic(self):
        scored = [
            {"title": "Positive news", "sentiment_score": 0.5, "sentiment_label": "positive"},
            {"title": "Negative news", "sentiment_score": -0.5, "sentiment_label": "negative"},
            {"title": "Neutral news", "sentiment_score": 0.0, "sentiment_label": "neutral"},
        ]
        result = aggregate_sentiment(scored, "AAPL")

        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["article_count"] == 3
        assert result["positive_count"] == 1
        assert result["negative_count"] == 1
        assert result["neutral_count"] == 1
        assert result["avg_sentiment"] == 0.0
        assert result["max_sentiment"] == 0.5
        assert result["min_sentiment"] == -0.5

    def test_aggregate_all_positive(self):
        scored = [
            {"sentiment_score": 0.8, "sentiment_label": "positive"},
            {"sentiment_score": 0.6, "sentiment_label": "positive"},
        ]
        result = aggregate_sentiment(scored, "MSFT")
        assert result["avg_sentiment"] == 0.7
        assert result["positive_count"] == 2
        assert result["negative_count"] == 0

    def test_aggregate_empty_list(self):
        result = aggregate_sentiment([], "AAPL")
        assert result is None

    def test_aggregate_none_list(self):
        result = aggregate_sentiment(None, "AAPL")
        assert result is None

    def test_aggregate_single_article(self):
        scored = [
            {"sentiment_score": -0.3, "sentiment_label": "negative"},
        ]
        result = aggregate_sentiment(scored, "TSLA")
        assert result["article_count"] == 1
        assert result["avg_sentiment"] == -0.3
        assert result["max_sentiment"] == -0.3
        assert result["min_sentiment"] == -0.3

    def test_aggregate_includes_composite_score(self):
        scored = [
            {"sentiment_score": 0.5, "sentiment_label": "positive"},
            {"sentiment_score": 0.3, "sentiment_label": "positive"},
        ]
        result = aggregate_sentiment(scored, "AAPL")
        assert "sentiment_score" in result
        assert "score_dispersion" in result
        assert "positive_ratio" in result
        # Composite score is 0-100
        assert 0.0 <= result["sentiment_score"] <= 100.0

    def test_aggregate_dispersion_zero_single(self):
        scored = [{"sentiment_score": 0.5, "sentiment_label": "positive"}]
        result = aggregate_sentiment(scored, "AAPL")
        assert result["score_dispersion"] == 0.0

    def test_aggregate_dispersion_nonzero_multiple(self):
        scored = [
            {"sentiment_score": 0.8, "sentiment_label": "positive"},
            {"sentiment_score": -0.8, "sentiment_label": "negative"},
        ]
        result = aggregate_sentiment(scored, "AAPL")
        assert result["score_dispersion"] > 0.0


# ── Tests: args_parser ──────────────────────────────────────────────────


class TestSentimentArgParser:
    """Tests for sentiment in CLI argument parser."""

    def test_sentiment_in_sources_choices(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev", "--sources", "sentiment"])
        assert "sentiment" in args.sources

    def test_sentiment_in_default_sources(self):
        from modules.utils.args_parser import arg_parse_cmd

        parser = arg_parse_cmd()
        args = parser.parse_args(["--env_type", "dev"])
        assert "sentiment" in args.sources


# ── Tests: Kafka topic ──────────────────────────────────────────────────


class TestSentimentKafkaTopic:
    """Tests for sentiment topic in Kafka config."""

    def test_sentiment_topic_exists(self):
        from modules.db_ops.kafka_ops import TOPICS

        assert "sentiment" in TOPICS
        assert TOPICS["sentiment"] == "market.sentiment"


# ── Tests: NewsSentiment table model ────────────────────────────────────


class TestNewsSentimentModel:
    """Tests for the NewsSentiment SQLAlchemy model."""

    def test_table_name(self):
        from modules.data_models.table_models import NewsSentiment

        assert NewsSentiment.__table__.name == "news_sentiment"

    def test_table_schema(self):
        from modules.data_models.table_models import NewsSentiment

        assert NewsSentiment.__table__.schema == "systematic_equity"

    def test_primary_key_columns(self):
        from modules.data_models.table_models import NewsSentiment

        pk_cols = [c.name for c in NewsSentiment.__table__.primary_key]
        assert "symbol" in pk_cols
        assert "cob_date" in pk_cols

    def test_all_columns_present(self):
        from modules.data_models.table_models import NewsSentiment

        col_names = [c.name for c in NewsSentiment.__table__.columns]
        expected = [
            "symbol",
            "cob_date",
            "article_count",
            "avg_sentiment",
            "positive_count",
            "negative_count",
            "neutral_count",
            "max_sentiment",
            "min_sentiment",
            "ingestion_timestamp",
        ]
        for col in expected:
            assert col in col_names, f"Missing column: {col}"

    def test_new_sentiment_columns_present(self):
        """Verify the three new VADER scoring columns are in the schema."""
        from modules.data_models.table_models import NewsSentiment

        col_names = [c.name for c in NewsSentiment.__table__.columns]
        # Added in v2.0.0: VADER composite score + breakdown columns
        assert "positive_ratio" in col_names
        assert "sentiment_score" in col_names
        assert "score_dispersion" in col_names


# ── parse_news_articles coverage for legacy + edge cases ──────────────


class TestParseNewsArticlesLegacy:

    def test_legacy_flat_format(self):
        """Legacy format without content key is parsed correctly."""
        from modules.input.news_downloader import parse_news_articles

        articles = [
            {
                "title": "AAPL hits record",
                "publisher": "Reuters",
                "providerPublishTime": 1700000000,
                "link": "https://example.com/1",
                "type": "STORY",
            }
        ]
        result = parse_news_articles(articles, "AAPL")
        assert len(result) == 1
        assert result[0]["title"] == "AAPL hits record"
        assert result[0]["publisher"] == "Reuters"

    def test_legacy_no_timestamp(self):
        """Legacy format without providerPublishTime uses current time."""
        from modules.input.news_downloader import parse_news_articles

        articles = [{"title": "News", "publisher": "AP"}]
        result = parse_news_articles(articles, "AAPL")
        assert len(result) == 1
        assert result[0]["published_at"] is not None

    def test_empty_title_skipped(self):
        """Articles with empty title are skipped."""
        from modules.input.news_downloader import parse_news_articles

        articles = [{"title": "", "publisher": "AP"}]
        result = parse_news_articles(articles, "AAPL")
        assert len(result) == 0

    def test_nested_invalid_pubdate_fallback(self):
        """Invalid pubDate in nested format falls back to now."""
        from modules.input.news_downloader import parse_news_articles

        articles = [
            {
                "content": {
                    "title": "Test",
                    "pubDate": "not-a-date",
                    "provider": {"displayName": "AP"},
                }
            }
        ]
        result = parse_news_articles(articles, "AAPL")
        assert len(result) == 1

    def test_nested_empty_pubdate(self):
        """Empty pubDate string falls back to now."""
        from modules.input.news_downloader import parse_news_articles

        articles = [
            {
                "content": {
                    "title": "Test",
                    "pubDate": "",
                    "provider": {"displayName": "AP"},
                }
            }
        ]
        result = parse_news_articles(articles, "AAPL")
        assert len(result) == 1

    def test_circuit_breaker_open_returns_none(self):
        """NewsDownloader returns None when circuit breaker is open."""
        from modules.input.news_downloader import NewsDownloader

        dl = NewsDownloader(max_retries=1)
        # Open the breaker by recording enough failures
        for _ in range(dl.circuit_breaker.failure_threshold + 5):
            dl.circuit_breaker.record_failure()
        result = dl.download("AAPL")
        assert result is None
