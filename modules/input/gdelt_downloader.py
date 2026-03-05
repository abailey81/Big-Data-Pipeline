"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : GDELT news article downloader for sentiment analysis
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Downloads recent news articles from the GDELT Project (Global Database
of Events, Language, and Tone) DOC 2.0 API.  GDELT monitors broadcast,
print, and web news worldwide and provides free, unrestricted access to
its article database.

This supplements the yfinance news source with broader coverage from
GDELT's 100+ language, 200+ country news monitoring network.  Article
headlines are scored using the same keyword-based sentiment pipeline
(``sentiment_scorer.py``) used for yfinance articles.

Data flow:
  GDELT DOC API → parse articles → sentiment scoring
                → MongoDB (raw documents)
                → PostgreSQL (aggregated scores)
                → Kafka (event stream)

Reference:
  Leetaru, K. and Schrodt, P. (2013) 'GDELT: Global Data on Events,
  Location and Tone, 1979-2012', ISA Annual Convention.

"""

from datetime import datetime, timezone
from typing import Optional

import requests

from modules.input.base_downloader import BaseDownloader
from modules.utils.info_logger import pipeline_logger

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "KolmogorovTeam/1.0 (Systematic Equity Pipeline)"


class GdeltDownloader(BaseDownloader):
    """Downloads news articles from the GDELT DOC 2.0 API.

    Queries GDELT for recent articles mentioning a company name or
    ticker symbol.  Results include title, URL, publication date,
    source domain, and source country.

    :param api_delay: Delay between API calls in seconds
    :type api_delay: float
    :param max_retries: Maximum retry attempts
    :type max_retries: int
    :param backoff_base: Exponential backoff base multiplier
    :type backoff_base: float
    :param max_articles: Maximum articles per ticker
    :type max_articles: int
    :param timeout: HTTP request timeout in seconds
    :type timeout: int
    """

    def __init__(
        self,
        api_delay: float = 1.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        max_articles: int = 15,
        timeout: int = 20,
        **kwargs,
    ):
        super().__init__(
            source_name="gdelt_news",
            api_delay=api_delay,
            max_retries=max_retries,
            backoff_base=backoff_base,
            **kwargs,
        )
        self.max_articles = max_articles
        self.timeout = timeout

    def _execute_download(self, symbol: str, company_name: str = None, **kwargs) -> Optional[list]:
        """Download news articles from GDELT for a single ticker.

        Constructs a search query using the ticker symbol and, when
        available, the company name to improve article relevance.

        :param symbol: Ticker symbol
        :type symbol: str
        :param company_name: Company name for improved query matching
        :type company_name: str or None
        :return: List of article dicts or None
        :rtype: list[dict] or None
        """
        # Build query: prefer company name, fall back to ticker
        query_parts = []
        if company_name:
            # Use quoted company name for exact match
            clean_name = company_name.split(",")[0].split(" Inc")[0]
            clean_name = clean_name.split(" Corp")[0].split(" Ltd")[0]
            clean_name = clean_name.strip()
            if clean_name:
                query_parts.append(f'"{clean_name}"')
        # Always include ticker (without exchange suffix)
        base_symbol = symbol.split(".")[0]
        query_parts.append(base_symbol)
        query = " ".join(query_parts)

        resp = requests.get(
            GDELT_DOC_API,
            params={
                "query": query,
                "mode": "ArtList",
                "maxrecords": self.max_articles,
                "format": "json",
                "sourcelang": "english",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
        return articles if articles else None

    def download(self, symbol: str, company_name: str = None) -> Optional[list]:
        """Download GDELT articles with retry and circuit breaker.

        :param symbol: Ticker symbol
        :type symbol: str
        :param company_name: Company name for query
        :type company_name: str or None
        :return: List of article dicts or None
        :rtype: list[dict] or None
        """
        self._download_count += 1

        if not self._check_circuit():
            pipeline_logger.debug(f"GDELT: circuit breaker OPEN — skipping {symbol}")
            self._failure_count += 1
            return None

        self.rate_limiter.acquire()

        for attempt in range(self.max_retries):
            try:
                result = self._execute_download(
                    symbol=symbol,
                    company_name=company_name,
                )
                self.circuit_breaker.record_success()
                self._success_count += 1
                return result
            except Exception as e:
                self.circuit_breaker.record_failure()
                if attempt < self.max_retries - 1:
                    pipeline_logger.debug(
                        f"GDELT retry {attempt + 1}/{self.max_retries} " f"for {symbol}: {e}"
                    )
                    self._jitter_wait(attempt)

        self._failure_count += 1
        return None


def parse_gdelt_articles(articles: list, symbol: str) -> list[dict]:
    """Parse raw GDELT articles into the standard news record format.

    Transforms GDELT article responses into the same schema used by
    the yfinance news parser (``parse_news_articles``), enabling
    consistent downstream sentiment scoring.

    :param articles: Raw article list from GDELT API
    :type articles: list[dict]
    :param symbol: Ticker symbol
    :type symbol: str
    :return: Parsed news records
    :rtype: list[dict]
    """
    if not articles:
        return []

    parsed = []
    for article in articles:
        try:
            # Skip non-English articles that bypass the API filter
            lang = article.get("language", "").lower()
            if lang and lang != "english":
                continue

            title = article.get("title", "")
            if not title:
                continue

            # GDELT seendate format: YYYYMMDDTHHmmSSZ
            seen = article.get("seendate", "")
            try:
                pub_date = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pub_date = datetime.now(timezone.utc)

            record = {
                "symbol": symbol,
                "title": title,
                "publisher": article.get("domain", ""),
                "published_at": pub_date,
                "link": article.get("url", ""),
                "article_type": "GDELT",
                "related_tickers": [symbol],
                "source_country": article.get("sourcecountry", ""),
                "language": article.get("language", "English"),
            }
            parsed.append(record)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            pipeline_logger.debug(f"Skipping unparseable GDELT article for {symbol}: {e}")
            continue

    return parsed
