"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : NewsAPI article downloader for sentiment analysis
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads recent news articles from the NewsAPI (newsapi.org)
``/v2/everything`` endpoint.  This serves as a secondary gap-fill
source when yfinance ``Ticker.news`` returns zero articles for a
given ticker.

The cascade order is:
  1. yfinance Ticker.news  (primary — no API key needed)
  2. NewsAPI /v2/everything (secondary — requires NEWSAPI_KEY)
  3. GDELT DOC API          (tertiary — free, no key)

Free-tier rate limit: 100 requests per day, 250 requests/15 min.
The downloader uses ``api_delay=1.0`` to stay comfortably within
limits during parallel gap-fill bursts.

Data flow:
  NewsAPI /v2/everything → parse articles → sentiment scoring
                         → MongoDB (raw documents)
                         → PostgreSQL (aggregated scores)
                         → Kafka (event stream)

"""

import os
from datetime import datetime
from typing import Optional

import requests

from modules.input.base_downloader import BaseDownloader
from modules.utils.info_logger import pipeline_logger

NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"


class NewsApiDownloader(BaseDownloader):
    """Downloads news articles from NewsAPI /v2/everything.

    Queries NewsAPI for recent English-language articles mentioning
    a company ticker or name.  Results include title, URL, publication
    date, and source name.

    :param api_key: NewsAPI API key (loaded from NEWSAPI_KEY env var)
    :type api_key: str
    :param api_delay: Delay between API calls in seconds
    :type api_delay: float
    :param max_retries: Maximum retry attempts per request
    :type max_retries: int
    :param backoff_base: Exponential backoff multiplier
    :type backoff_base: float
    :param max_articles: Maximum articles to retrieve per query
    :type max_articles: int
    :param timeout: HTTP request timeout in seconds
    :type timeout: int
    """

    def __init__(
        self,
        api_key: str = "",
        api_delay: float = 1.0,
        max_retries: int = 2,
        backoff_base: float = 2.0,
        max_articles: int = 10,
        timeout: int = 15,
    ):
        super().__init__(
            source_name="newsapi",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            cb_failure_threshold=10,
            cb_recovery_timeout=120.0,
        )
        self.api_key = api_key or os.environ.get("NEWSAPI_KEY", "")
        self.max_articles = max_articles
        self.timeout = timeout

    def _execute_download(self, symbol: str, **kwargs) -> Optional[list]:
        """Execute the NewsAPI query for a single ticker.

        :param symbol: Company ticker symbol (e.g. 'AAPL')
        :return: List of raw article dicts or None on failure
        """
        if not self.api_key:
            return None

        params = {
            "q": symbol,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": self.max_articles,
            "apiKey": self.api_key,
        }

        try:
            resp = requests.get(
                NEWSAPI_ENDPOINT,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "KolmogorovTeam/1.0"},
            )
            if resp.status_code == 429:
                pipeline_logger.debug("NewsAPI rate limit hit — backing off")
                return None
            if resp.status_code != 200:
                pipeline_logger.debug(f"NewsAPI HTTP {resp.status_code} for {symbol}")
                return None

            data = resp.json()
            articles = data.get("articles", [])
            return articles if articles else None

        except requests.RequestException as e:
            pipeline_logger.debug(f"NewsAPI request failed for {symbol}: {e}")
            return None

    def download(self, symbol: str) -> Optional[list]:
        """Download news articles for a ticker from NewsAPI.

        :param symbol: Company ticker symbol
        :return: List of article dicts or empty list
        """
        if not self.api_key:
            return []

        self._download_count += 1

        if not self._check_circuit():
            self._failure_count += 1
            return []

        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.acquire()
                articles = self._execute_download(symbol)
                if articles:
                    self.circuit_breaker.record_success()
                    self._success_count += 1
                    return articles
                # Empty result is not a failure — just no coverage
                self._success_count += 1
                return []
            except Exception as e:
                self.circuit_breaker.record_failure()
                self._failure_count += 1
                if attempt < self.max_retries - 1:
                    import time

                    time.sleep(self.backoff_base**attempt)
                pipeline_logger.debug(
                    f"NewsAPI attempt {attempt + 1}/{self.max_retries} " f"for {symbol}: {e}"
                )

        return []


def parse_newsapi_articles(articles: list, db_symbol: str) -> list[dict]:
    """Parse NewsAPI articles into standardised sentiment records.

    Normalises the NewsAPI response format to match the structure
    expected by ``score_articles()`` and ``aggregate_sentiment()``.

    :param articles: Raw article list from NewsAPI
    :param db_symbol: Database symbol for tagging
    :return: List of normalised article dicts
    """
    parsed = []
    if not articles:
        return parsed

    for art in articles:
        title = (art.get("title") or "").strip()
        if not title or title == "[Removed]":
            continue

        pub = art.get("publishedAt", "")
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            pub_str = pub_dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            pub_str = pub

        source = art.get("source", {})
        parsed.append(
            {
                "title": title,
                "publisher": source.get("name", "NewsAPI"),
                "published_at": pub_str,
                "url": art.get("url", ""),
                "symbol": db_symbol,
                "source_api": "newsapi",
            }
        )

    return parsed
