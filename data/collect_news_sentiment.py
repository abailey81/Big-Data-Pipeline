#!/usr/bin/env python3
"""
collect_news_sentiment.py — News sentiment collector for cw2_strategy.

Produces news_sentiment.parquet aligned 1:1 with cw1 schema:
  (symbol, cob_date, article_count, avg_sentiment, positive_count,
   negative_count, neutral_count, max_sentiment, min_sentiment,
   positive_ratio, sentiment_score, score_dispersion)

Sources:
  - Google News RSS (primary) — free, no key, supports historical date ranges
  - yfinance Ticker.news (supplementary recent)

Scoring (identical to cw1 sentiment_scorer.py):
  VADER compound + financial domain boost → composite 0-100 score

Usage:
  python3 collect_news_sentiment.py                          # recent 30d
  python3 collect_news_sentiment.py --backfill               # + semi-annual from 2020
  python3 collect_news_sentiment.py --tickers AAPL MSFT      # specific tickers
  python3 collect_news_sentiment.py --backfill --workers 4
"""

import argparse
import math
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

DATA_DIR = Path(__file__).parent

# Google News RSS rate limiter: 1 request per 1.5 seconds
_gnews_lock = threading.Lock()
_gnews_last_call = [0.0]

def _gnews_rate_limit():
    with _gnews_lock:
        now = time.time()
        elapsed = now - _gnews_last_call[0]
        if elapsed < 1.5:
            time.sleep(1.5 - elapsed)
        _gnews_last_call[0] = time.time()


# ═══════════════════════════════════════════════════════════════════════════
# VADER + Financial Domain Boost (identical to cw1 sentiment_scorer.py)
# ═══════════════════════════════════════════════════════════════════════════

_ANALYSER = SentimentIntensityAnalyzer()

FINANCIAL_BOOST_LEXICON = {
    "beat": +0.22, "beats": +0.22, "exceeded": +0.18, "exceeds": +0.18,
    "outperformed": +0.16, "outperform": +0.14, "upgraded": +0.22,
    "upgrade": +0.18, "buyback": +0.14, "dividend": +0.10,
    "acquisition": +0.08, "merger": +0.08, "record": +0.10,
    "breakout": +0.12, "expansion": +0.10,
    "miss": -0.22, "misses": -0.22, "missed": -0.22,
    "downgraded": -0.22, "downgrade": -0.18, "underperform": -0.14,
    "underperformed": -0.16, "layoffs": -0.16, "layoff": -0.16,
    "bankruptcy": -0.32, "bankrupt": -0.28, "default": -0.22,
    "fraud": -0.26, "investigation": -0.14, "probe": -0.12,
    "recall": -0.14, "restatement": -0.18, "restate": -0.16,
    "restated": -0.16, "suspended": -0.14, "delisted": -0.20,
    "delist": -0.20, "writedown": -0.16, "impairment": -0.14,
    "settlement": -0.10, "regulatory": -0.08, "subpoena": -0.20,
    "sec": -0.08,
}

FINANCIAL_BOOST_PHRASES = {
    "guidance raise": +0.22, "raised guidance": +0.22,
    "raised its guidance": +0.22, "raised outlook": +0.18,
    "raised forecast": +0.18, "beat estimates": +0.24,
    "beat expectations": +0.24, "top estimates": +0.18,
    "record quarter": +0.20, "record revenue": +0.18,
    "share buyback": +0.16, "stock buyback": +0.16, "debt free": +0.12,
    "guidance cut": -0.24, "cut guidance": -0.24,
    "cut its guidance": -0.24, "cut outlook": -0.20,
    "missed estimates": -0.26, "missed expectations": -0.26,
    "below estimates": -0.22, "profit warning": -0.24,
    "earnings miss": -0.26, "revenue miss": -0.24,
    "chapter 11": -0.35, "class action": -0.22,
    "criminal charges": -0.28, "going concern": -0.24,
}


def _compute_financial_boost(text: str) -> float:
    if not text:
        return 0.0
    text_lower = text.lower()
    words = text_lower.split()
    boost = 0.0
    for word in words:
        clean = word.strip(".,!?:;()\"'—–")
        b = FINANCIAL_BOOST_LEXICON.get(clean)
        if b is not None:
            boost += b
    for phrase, val in FINANCIAL_BOOST_PHRASES.items():
        if phrase in text_lower:
            boost += val
    return max(-0.50, min(0.50, boost))


def score_article(text: str) -> dict:
    if not text or not text.strip():
        return {"enhanced": 0.0, "vader_raw": 0.0, "boost": 0.0}
    vader_scores = _ANALYSER.polarity_scores(text)
    vader_raw = vader_scores["compound"]
    boost = _compute_financial_boost(text)
    enhanced = max(-1.0, min(1.0, vader_raw + 0.35 * boost))
    return {"enhanced": round(enhanced, 4), "vader_raw": round(vader_raw, 4), "boost": round(boost, 4)}


def aggregate_sentiment(scored_articles: list, symbol: str, cob_date: str) -> dict:
    """Aggregate article scores into cw1-compatible news_sentiment record."""
    if not scored_articles:
        return {
            "symbol": symbol, "cob_date": cob_date,
            "article_count": 0, "avg_sentiment": 0.0,
            "positive_count": 0, "negative_count": 0, "neutral_count": 0,
            "max_sentiment": 0.0, "min_sentiment": 0.0,
            "positive_ratio": 0.0, "sentiment_score": 50.0,
            "score_dispersion": 0.0,
        }

    scores = [a["enhanced"] for a in scored_articles]
    n = len(scores)
    avg_enhanced = sum(scores) / n

    labels = []
    for s in scores:
        if s >= 0.05:
            labels.append("positive")
        elif s <= -0.05:
            labels.append("negative")
        else:
            labels.append("neutral")

    pos = labels.count("positive")
    neg = labels.count("negative")
    neu = labels.count("neutral")
    pos_ratio = pos / n

    volume_factor = min(n / 20.0, 1.0)
    if n > 1:
        variance = sum((s - avg_enhanced) ** 2 for s in scores) / n
        dispersion = math.sqrt(variance)
    else:
        dispersion = 0.0

    agreement_bonus = max(0.0, 1.0 - dispersion * 2.0)

    vader_component = (avg_enhanced + 1.0) / 2.0 * 100.0
    sentiment_score = (
        vader_component * 0.45
        + pos_ratio * 100.0 * 0.25
        + volume_factor * 100.0 * 0.20
        + agreement_bonus * 100.0 * 0.10
    )

    return {
        "symbol": symbol, "cob_date": cob_date,
        "article_count": n,
        "avg_sentiment": round(avg_enhanced, 4),
        "positive_count": pos, "negative_count": neg, "neutral_count": neu,
        "max_sentiment": round(max(scores), 4),
        "min_sentiment": round(min(scores), 4),
        "positive_ratio": round(pos_ratio, 4),
        "sentiment_score": round(sentiment_score, 4),
        "score_dispersion": round(dispersion, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Google News RSS downloader
# ═══════════════════════════════════════════════════════════════════════════

def _clean_company_name(name: str) -> str:
    """Clean company name for search query."""
    for suffix in [",", " Inc", " Corp", " Ltd", " plc", " PLC",
                   " SE", " SA", " AG", " NV", " Co.", " &"]:
        name = name.split(suffix)[0]
    return name.strip()


def download_gnews_rss(query: str, max_articles: int = 100,
                       after: str = None, before: str = None) -> list:
    """Download articles from Google News RSS.

    Args:
        query: search query (company name or ticker)
        max_articles: max results (Google caps at 100)
        after: date string "YYYY-MM-DD" for historical start
        before: date string "YYYY-MM-DD" for historical end

    Returns:
        list of {"title": str, "published_at": datetime}
    """
    _gnews_rate_limit()

    q = query
    if after and before:
        q += f" after:{after} before:{before}"
    elif after:
        q += f" after:{after}"

    url = f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=en-US&gl=US&ceid=US:en"

    try:
        resp = requests.get(url, timeout=20,
                            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        items = root.findall(".//item")

        parsed = []
        for item in items[:max_articles]:
            title_el = item.find("title")
            pub_el = item.find("pubDate")
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()

            if pub_el is not None and pub_el.text:
                try:
                    # Format: "Fri, 03 Apr 2026 07:00:00 GMT"
                    pub_date = datetime.strptime(pub_el.text.strip(),
                                                 "%a, %d %b %Y %H:%M:%S %Z")
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pub_date = datetime.now(timezone.utc)
            else:
                pub_date = datetime.now(timezone.utc)

            parsed.append({"title": title, "published_at": pub_date})
        return parsed
    except Exception:
        return []


def download_news_for_symbol(symbol: str, company_name: str = "",
                             after: str = None, before: str = None) -> list:
    """Download news for a symbol using multi-query cascade (like cw1 GDELT).

    Cascade:
      1. "Company Name" stock (quoted exact match)
      2. Ticker symbol stock
    """
    articles = []

    # Query 1: Company name (better precision)
    if company_name:
        clean = _clean_company_name(company_name)
        if clean and len(clean) > 2:
            articles = download_gnews_rss(f'"{clean}" stock', after=after, before=before)

    # Query 2: Ticker symbol (fallback)
    if not articles:
        base = symbol.split(".")[0]
        articles = download_gnews_rss(f"{base} stock", after=after, before=before)

    return articles


# ═══════════════════════════════════════════════════════════════════════════
# Scoring + aggregation helpers
# ═══════════════════════════════════════════════════════════════════════════

def deduplicate(articles: list) -> list:
    seen = set()
    out = []
    for a in articles:
        key = a["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(a)
    return out


def score_and_aggregate(articles: list, symbol: str, cob_date: str) -> dict:
    deduped = deduplicate(articles)
    scored = [score_article(a["title"]) for a in deduped]
    return aggregate_sentiment(scored, symbol, cob_date)


def _incremental_save(new_records: list, out_path: Path):
    """Merge new records into existing parquet (upsert by symbol+cob_date)."""
    df_new = pd.DataFrame(new_records)
    df_new["cob_date"] = pd.to_datetime(df_new["cob_date"])
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing["cob_date"] = pd.to_datetime(existing["cob_date"])
        combined = pd.concat([existing, df_new], ignore_index=True)
        combined.sort_values(["symbol", "cob_date"], inplace=True)
        combined.drop_duplicates(subset=["symbol", "cob_date"], keep="last", inplace=True)
        combined.reset_index(drop=True, inplace=True)
        combined.to_parquet(out_path, index=False)
    else:
        df_new.sort_values(["symbol", "cob_date"], inplace=True)
        df_new.reset_index(drop=True, inplace=True)
        df_new.to_parquet(out_path, index=False)


# ═══════════════════════════════════════════════════════════════════════════
# Collection stages
# ═══════════════════════════════════════════════════════════════════════════

def collect_recent(symbols: list, company_names: dict, workers: int = 4) -> list:
    """Collect recent sentiment via Google News RSS (last 30 days)."""
    results = []
    today = date.today().isoformat()
    total = len(symbols)
    out_path = DATA_DIR / "news_sentiment.parquet"

    for i, sym in enumerate(symbols):
        name = company_names.get(sym, "")
        articles = download_news_for_symbol(sym, name)
        agg = score_and_aggregate(articles, sym, today)
        results.append(agg)

        if (i + 1) % 50 == 0 or (i + 1) == total:
            _incremental_save(results, out_path)
            print(f"  Recent: {i+1}/{total} done (saved)", flush=True)

    return results


def collect_backfill(symbols: list, company_names: dict, start_year: int = 2020) -> list:
    """Backfill semi-annual sentiment from Google News RSS.

    633 symbols x 13 windows x 1.5s rate limit ≈ 2-3 hours.
    Saves incrementally every 10 tickers.
    """
    # Generate semi-annual date ranges
    windows = []
    w_start = date(start_year, 1, 1)
    now = date.today()
    while w_start < now:
        w_end_month = w_start.month + 6
        w_end_year = w_start.year
        if w_end_month > 12:
            w_end_month -= 12
            w_end_year += 1
        w_end = date(w_end_year, w_end_month, 1) - timedelta(days=1)
        if w_end > now:
            w_end = now
        windows.append((w_start, w_end))
        w_start = date(w_end_year, w_end_month, 1)

    print(f"  Backfill windows: {len(windows)} semi-annual periods", flush=True)
    print(f"  Estimated: {len(symbols)} x {len(windows)} = {len(symbols)*len(windows)} requests "
          f"@ 1.5s ≈ {len(symbols)*len(windows)*1.5/3600:.1f} hours", flush=True)

    out_path = DATA_DIR / "news_sentiment.parquet"
    results = []

    # Check which symbols already have backfill data
    existing_backfill_syms = set()
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing["cob_date"] = pd.to_datetime(existing["cob_date"])
        sym_dates = existing.groupby("symbol")["cob_date"].nunique()
        existing_backfill_syms = set(sym_dates[sym_dates > 1].index)
        if existing_backfill_syms:
            print(f"  Skipping {len(existing_backfill_syms)} already-backfilled symbols", flush=True)

    remaining = [s for s in symbols if s not in existing_backfill_syms]
    total = len(remaining)
    t0 = time.time()

    for i, sym in enumerate(remaining):
        name = company_names.get(sym, "")

        for w_start, w_end in windows:
            mid = w_start + (w_end - w_start) / 2
            cob = mid.isoformat()

            articles = download_news_for_symbol(
                sym, name,
                after=w_start.isoformat(),
                before=w_end.isoformat(),
            )
            agg = score_and_aggregate(articles, sym, cob)
            results.append(agg)

        # Incremental save every 10 tickers
        if (i + 1) % 10 == 0 or (i + 1) == total:
            _incremental_save(results, out_path)
            results = []
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
            remaining_min = (total - i - 1) / rate if rate > 0 else 0
            print(f"  Backfill: {i+1}/{total} tickers done "
                  f"({rate:.1f} tickers/min, ~{remaining_min:.0f}min left)", flush=True)

    if results:
        _incremental_save(results, out_path)

    return []


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Collect news sentiment for cw2_strategy")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill semi-annual from 2020 via Google News RSS")
    parser.add_argument("--workers", type=int, default=1,
                        help="Workers (rate-limited to 1.5s/req regardless)")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Specific tickers (default: all with price data)")
    parser.add_argument("--start-year", type=int, default=2020,
                        help="Backfill start year (default: 2020)")
    args = parser.parse_args()

    # Load symbol list + company names
    cs = pd.read_parquet(DATA_DIR / "company_static.parquet")
    company_names = dict(zip(cs["symbol"], cs["security"]))

    if args.tickers:
        symbols = args.tickers
    else:
        prices = pd.read_parquet(DATA_DIR / "daily_prices.parquet")
        symbols = sorted(prices["symbol"].unique().tolist())

    # Resume: skip symbols already done today
    out_path = DATA_DIR / "news_sentiment.parquet"
    existing_symbols = set()
    today_str = date.today().isoformat()
    if out_path.exists():
        existing_df = pd.read_parquet(out_path)
        existing_df["cob_date"] = pd.to_datetime(existing_df["cob_date"])
        today_done = existing_df[
            (existing_df["cob_date"] == today_str) & (existing_df["article_count"] > 0)
        ]
        existing_symbols = set(today_done["symbol"].unique())
        if existing_symbols:
            print(f"Resuming: {len(existing_symbols)} symbols already have today's data", flush=True)

    remaining = [s for s in symbols if s not in existing_symbols]

    print(f"Collecting news sentiment for {len(remaining)}/{len(symbols)} symbols", flush=True)
    print(f"Source: Google News RSS + VADER scoring (cw1-aligned)", flush=True)
    print(f"Backfill: {args.backfill} (from {args.start_year})", flush=True)

    # Step 1: Recent
    if remaining:
        print("\n--- Collecting recent sentiment ---", flush=True)
        recent = collect_recent(remaining, company_names)
        print(f"  Recent: {len(recent)} records", flush=True)

    # Step 2: Backfill
    if args.backfill:
        print("\n--- Backfilling historical sentiment ---", flush=True)
        collect_backfill(symbols, company_names, start_year=args.start_year)

    # Final stats
    if out_path.exists():
        df = pd.read_parquet(out_path)
        df["cob_date"] = pd.to_datetime(df["cob_date"])
        print(f"\n{'='*60}")
        print(f"DONE — news_sentiment.parquet")
        print(f"  Total records: {len(df)}")
        print(f"  Symbols: {df['symbol'].nunique()}")
        print(f"  Date range: {df['cob_date'].min()} → {df['cob_date'].max()}")
        print(f"  With articles (count>0): {(df['article_count'] > 0).sum()}")
        print(f"  Neutral (no articles): {(df['article_count'] == 0).sum()}")
        if args.backfill:
            yearly = df.groupby(df["cob_date"].dt.year)["symbol"].nunique()
            print(f"\n  Symbols per year:")
            for yr, cnt in yearly.items():
                print(f"    {yr}: {cnt}")


if __name__ == "__main__":
    main()
