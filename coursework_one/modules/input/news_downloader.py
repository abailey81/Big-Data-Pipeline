"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : News article downloader for sentiment analysis
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads recent news articles per ticker using the yfinance
``Ticker.news`` property.  News sentiment is a complementary signal
for the multi-factor model — negative headline flow often precedes
price momentum reversals, while sustained positive coverage reinforces
quality and growth factor loadings.

News data is semi-structured (variable-length titles, different
providers, optional thumbnails) and therefore stored in MongoDB as
the primary persistence layer, with aggregated sentiment scores
written to PostgreSQL for efficient factor construction in Phase 2.

Data flow:
  yfinance Ticker.news → MongoDB (raw documents)
                        → sentiment scoring → PostgreSQL (aggregated)
                        → Kafka (event stream)
                        → MinIO (raw JSON backup)

"""

import time  # noqa: F401 — needed for test mocking (patch news_downloader.time)
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

from modules.input.base_downloader import BaseDownloader
from modules.utils.info_logger import pipeline_logger


class NewsDownloader(BaseDownloader):
    """Downloads news articles from Yahoo Finance for sentiment analysis.

    Uses ``yfinance.Ticker(symbol).news`` which returns a list of
    recent news articles with title, publisher, link, and publish time.

    :param api_delay: Delay between API calls in seconds
    :type api_delay: float
    :param max_retries: Maximum retry attempts
    :type max_retries: int
    :param backoff_base: Exponential backoff base multiplier
    :type backoff_base: float
    :param max_articles: Maximum articles to keep per ticker
    :type max_articles: int
    """

    def __init__(
        self,
        api_delay: float = 0.5,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        max_articles: int = 20,
        **kwargs,
    ):
        super().__init__(
            source_name="news_sentiment",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            **kwargs,
        )
        self.max_articles = max_articles

    def _execute_download(self, symbol: str, **kwargs) -> Optional[list]:
        """Download news articles for a single ticker.

        :param symbol: Yahoo Finance ticker symbol
        :type symbol: str
        :return: List of news article dictionaries or None
        :rtype: list[dict] or None
        """
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if news and len(news) > 0:
            return news[: self.max_articles]
        return None

    def download(self, symbol: str) -> Optional[list]:
        """Download news with retry and circuit breaker protection.

        :param symbol: Yahoo Finance ticker symbol
        :type symbol: str
        :return: List of news article dicts or None
        :rtype: list[dict] or None
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.debug(f"News: circuit breaker OPEN — skipping {symbol}")
            self._failure_count += 1
            return None

        self.rate_limiter.acquire()

        for attempt in range(self.max_retries):
            try:
                result = self._execute_download(symbol=symbol)
                self.circuit_breaker.record_success()
                self._success_count += 1
                return result
            except Exception as e:
                self.circuit_breaker.record_failure()
                if attempt < self.max_retries - 1:
                    pipeline_logger.debug(
                        f"News retry {attempt + 1}/{self.max_retries} " f"for {symbol}: {e}"
                    )
                    self._jitter_wait(attempt)

        self._failure_count += 1
        return None


def parse_news_articles(articles: list, symbol: str) -> list[dict]:
    """Parse raw yfinance news articles into standardised records.

    Handles both the current yfinance nested format (``content`` key)
    and the legacy flat format for backwards compatibility.

    Current format (yfinance >= 0.2.36)::

        {"id": "...", "content": {"title": "...", "pubDate": "...",
         "provider": {"displayName": "..."}, "canonicalUrl": {"url": "..."},
         "contentType": "STORY"}}

    Legacy format::

        {"title": "...", "publisher": "...", "providerPublishTime": 1234567890,
         "link": "...", "type": "STORY", "relatedTickers": [...]}

    :param articles: Raw news article list from yfinance
    :type articles: list[dict]
    :param symbol: Ticker symbol these articles relate to
    :type symbol: str
    :return: List of parsed news records
    :rtype: list[dict]
    """
    if not articles:
        return []

    parsed = []
    for article in articles:
        try:
            content = article.get("content")
            if content and isinstance(content, dict):
                # Current nested format
                title = content.get("title", "")
                provider = content.get("provider") or {}
                publisher = provider.get("displayName", "")
                pub_str = content.get("pubDate", "")
                if pub_str:
                    try:
                        pub_date = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pub_date = datetime.now(timezone.utc)
                else:
                    pub_date = datetime.now(timezone.utc)
                canon = content.get("canonicalUrl") or {}
                click = content.get("clickThroughUrl") or {}
                link = canon.get("url", "") or click.get("url", "")
                article_type = content.get("contentType", "STORY")
            else:
                # Legacy flat format
                title = article.get("title", "")
                publisher = article.get("publisher", "")
                pub_ts = article.get("providerPublishTime")
                if pub_ts:
                    pub_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                else:
                    pub_date = datetime.now(timezone.utc)
                link = article.get("link", "")
                article_type = article.get("type", "STORY")

            if not title:
                continue

            record = {
                "symbol": symbol,
                "title": title,
                "publisher": publisher,
                "published_at": pub_date,
                "link": link,
                "article_type": article_type,
                "related_tickers": article.get("relatedTickers", []),
            }
            parsed.append(record)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            pipeline_logger.debug(f"Skipping unparseable news article for {symbol}: {e}")
            continue

    return parsed
