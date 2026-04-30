"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Advanced financial news sentiment scoring engine
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Implements a dual-layer sentiment analysis pipeline for financial news:

Layer 1 — VADER (Valence Aware Dictionary and sEntiment Reasoner):
  Industry-standard rule-based NLP model designed for short-form text.
  Handles negation, punctuation, capitalization, and degree modifiers.
  Outputs compound score in [-1.0, +1.0].

Layer 2 — Financial Domain Boost:
  A domain-specific lexicon of high-signal financial terms that VADER
  under-weights (e.g. 'beat', 'miss', 'downgraded', 'buyback').
  Applied as a signed delta on top of the raw VADER compound score.
  This captures earnings surprise vocabulary that is often misclassified
  as neutral by general-purpose sentiment models.

Composite Sentiment Score (0-100 investable factor):

  sentiment_score = (vader_component * 0.45)         # Primary NLP signal
                  + (positive_ratio * 0.25)           # Directional consensus
                  + (volume_component * 0.20)         # Coverage intensity
                  + (agreement_bonus * 0.10)          # Inter-article consensus

  Where:
    vader_component    = (avg_enhanced + 1) / 2 * 100   # 0-100 scale
    positive_ratio     = positive_count / total * 100
    volume_component   = min(total / 20, 1.0) * 100
    agreement_bonus    = max(0, 1 - dispersion * 2) * 100

  score_dispersion (separate factor) = std dev of per-article scores.
  High dispersion = market disagreement — a standalone Phase 2 factor.

Article deduplication:
  Headlines are de-duplicated before scoring to prevent the same
  story (syndicated across multiple outlets) from biasing the aggregate.

Academic references:
  - Hutto, C.J. & Gilbert, E. (2014), "VADER: A Parsimonious Rule-based
    Model for Sentiment Analysis of Social Media Text", AAAI ICWSM.
  - Tetlock, P.C. (2007), "Giving Content to Investor Sentiment", JF.
  - Baker, M. & Wurgler, J. (2006), "Investor Sentiment and the
    Cross-Section of Stock Returns", JF.
  - Jegadeesh, N. & Wu, D. (2013), "Word Power: A New Approach for
    Content Analysis", JFE.

"""

import math
from typing import Optional

from modules.utils.info_logger import pipeline_logger

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer as _VADER

    _ANALYSER = _VADER()
    VADER_AVAILABLE = True
except ImportError:
    _ANALYSER = None
    VADER_AVAILABLE = False
    pipeline_logger.warning(
        "vaderSentiment not installed — sentiment scoring disabled. "
        "Install with: pip install vaderSentiment"
    )


# ---------------------------------------------------------------------------
# Financial domain boost lexicon
# ---------------------------------------------------------------------------
# High-signal financial vocabulary that VADER underweights.  Values are
# signed delta adjustments applied to the raw VADER compound per article.
# Clipped at ±0.50 after summing.
# ---------------------------------------------------------------------------
FINANCIAL_BOOST_LEXICON: dict[str, float] = {
    # ── Strong positive (earnings / guidance) ──────────────────────────────
    "beat": +0.22,  # "beat estimates" — strong positive signal
    "beats": +0.22,
    "exceeded": +0.18,
    "exceeds": +0.18,
    "outperformed": +0.16,
    "outperform": +0.14,
    "upgraded": +0.22,
    "upgrade": +0.18,
    "buyback": +0.14,
    "dividend": +0.10,
    "acquisition": +0.08,
    "merger": +0.08,
    "record": +0.10,  # "record revenue / record quarter"
    "breakout": +0.12,
    "expansion": +0.10,
    # ── Strong negative (earnings / guidance) ──────────────────────────────
    "miss": -0.22,  # "miss on earnings" — strong negative signal
    "misses": -0.22,
    "missed": -0.22,
    "downgraded": -0.22,
    "downgrade": -0.18,
    "underperform": -0.14,
    "underperformed": -0.16,
    "layoffs": -0.16,
    "layoff": -0.16,
    "bankruptcy": -0.32,
    "bankrupt": -0.28,
    "default": -0.22,
    "fraud": -0.26,
    "investigation": -0.14,
    "probe": -0.12,
    "recall": -0.14,
    "restatement": -0.18,
    "restate": -0.16,
    "restated": -0.16,
    "suspended": -0.14,
    "delisted": -0.20,
    "delist": -0.20,
    "writedown": -0.16,
    "impairment": -0.14,
    "settlement": -0.10,
    "regulatory": -0.08,
    "subpoena": -0.20,
    "sec": -0.08,  # SEC investigation context
}

# Multi-word phrases (checked after single words)
FINANCIAL_BOOST_PHRASES: dict[str, float] = {
    "guidance raise": +0.22,
    "raised guidance": +0.22,
    "raised its guidance": +0.22,
    "raised outlook": +0.18,
    "raised forecast": +0.18,
    "beat estimates": +0.24,
    "beat expectations": +0.24,
    "top estimates": +0.18,
    "record quarter": +0.20,
    "record revenue": +0.18,
    "share buyback": +0.16,
    "stock buyback": +0.16,
    "debt free": +0.12,
    "guidance cut": -0.24,
    "cut guidance": -0.24,
    "cut its guidance": -0.24,
    "cut outlook": -0.20,
    "missed estimates": -0.26,
    "missed expectations": -0.26,
    "below estimates": -0.22,
    "profit warning": -0.24,
    "earnings miss": -0.26,
    "revenue miss": -0.24,
    "chapter 11": -0.35,
    "class action": -0.22,
    "criminal charges": -0.28,
    "going concern": -0.24,
}


def _compute_financial_boost(text: str) -> float:
    """Compute signed financial domain boost for a single text.

    :param text: Article title (or combined title + description)
    :type text: str
    :return: Boost delta in approximately [-0.50, +0.50]
    :rtype: float
    """
    if not text:
        return 0.0
    text_lower = text.lower()
    words = text_lower.split()
    boost = 0.0

    # Single-word boosts
    for word in words:
        clean = word.strip(".,!?:;()\"'—–")
        b = FINANCIAL_BOOST_LEXICON.get(clean)
        if b is not None:
            boost += b

    # Multi-word phrase boosts
    for phrase, val in FINANCIAL_BOOST_PHRASES.items():
        if phrase in text_lower:
            boost += val

    return max(-0.50, min(0.50, boost))


def _score_article_text(text: str) -> dict:
    """Score a single text with VADER + financial boost.

    :param text: Article headline (VADER + boost applied)
    :type text: str
    :return: Dict with 'enhanced', 'vader_raw', 'boost'
    :rtype: dict
    """
    if not VADER_AVAILABLE or not text or not text.strip():
        return {"enhanced": 0.0, "vader_raw": 0.0, "boost": 0.0}

    vader_scores = _ANALYSER.polarity_scores(text)
    vader_raw = vader_scores["compound"]
    boost = _compute_financial_boost(text)

    # Combine: VADER is the primary signal; boost is an additive correction
    enhanced = max(-1.0, min(1.0, vader_raw + 0.35 * boost))

    return {
        "enhanced": round(enhanced, 4),
        "vader_raw": round(vader_raw, 4),
        "boost": round(boost, 4),
    }


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles based on headline text.

    Addresses the data quality requirement: same story syndicated across
    multiple outlets appears multiple times → deduplicate before scoring
    to prevent single-story bias.

    :param articles: List of article dicts (must have 'title')
    :type articles: list[dict]
    :return: Deduplicated list (preserves first occurrence)
    :rtype: list[dict]
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for article in articles:
        key = (article.get("title", "") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(article)
        elif not key:
            unique.append(article)
    removed = len(articles) - len(unique)
    if removed > 0:
        pipeline_logger.debug(f"Sentiment deduplication: removed {removed} duplicate headlines")
    return unique


def score_articles(articles: list[dict]) -> list[dict]:
    """Score each article with VADER + financial domain boost.

    Adds 'sentiment_score' (enhanced), 'sentiment_label', 'vader_raw',
    and 'boost_delta' fields to each article dict in-place.

    :param articles: List of parsed article dicts (must have 'title')
    :type articles: list[dict]
    :return: Same list with sentiment fields added to each element
    :rtype: list[dict]
    """
    for article in articles:
        title = article.get("title", "") or ""
        result = _score_article_text(title)
        article["sentiment_score"] = result["enhanced"]
        article["vader_raw"] = result["vader_raw"]
        article["boost_delta"] = result["boost"]
        # Label using VADER standard thresholds
        enhanced = result["enhanced"]
        if enhanced >= 0.05:
            article["sentiment_label"] = "positive"
        elif enhanced <= -0.05:
            article["sentiment_label"] = "negative"
        else:
            article["sentiment_label"] = "neutral"
    return articles


def aggregate_sentiment(scored_articles: list[dict], symbol: str) -> Optional[dict]:
    """Aggregate article-level scores into a per-ticker summary.

    Computes the composite Sentiment Score (0-100 investable factor)
    alongside supporting metrics for storage in PostgreSQL.

    Composite formula (see module docstring for full derivation):
      sentiment_score = vader_component*0.45 + positive_ratio*0.25
                      + volume_component*0.20 + agreement_bonus*0.10

    :param scored_articles: List of scored article dicts
    :type scored_articles: list[dict]
    :param symbol: Ticker symbol
    :type symbol: str
    :return: Aggregated sentiment record or None if no articles
    :rtype: dict or None
    """
    if not scored_articles:
        return None

    scores = [a.get("sentiment_score", 0.0) for a in scored_articles]
    labels = [a.get("sentiment_label", "neutral") for a in scored_articles]
    n = len(scores)

    avg_enhanced = sum(scores) / n

    positive_count = labels.count("positive")
    negative_count = labels.count("negative")
    neutral_count = labels.count("neutral")
    positive_ratio = positive_count / n

    # Volume factor: saturates at 20 articles (sufficient news coverage)
    volume_factor = min(n / 20.0, 1.0)

    # Score dispersion — std dev of per-article scores
    # High dispersion = market disagreement about this stock (standalone Phase 2 factor)
    if n > 1:
        variance = sum((s - avg_enhanced) ** 2 for s in scores) / n
        dispersion = math.sqrt(variance)
    else:
        dispersion = 0.0

    # Agreement bonus: reward low inter-article dispersion
    # dispersion in [0, 1]; clamp at 0.5 for the bonus
    agreement_bonus = max(0.0, 1.0 - dispersion * 2.0)

    # ── Composite Sentiment Score [0, 100] ──────────────────────────────
    vader_component = (avg_enhanced + 1.0) / 2.0 * 100.0
    positive_ratio_pct = positive_ratio * 100.0
    volume_component = volume_factor * 100.0
    agreement_pct = agreement_bonus * 100.0

    sentiment_score = (
        vader_component * 0.45 + positive_ratio_pct * 0.25 + volume_component * 0.20 + agreement_pct * 0.10
    )

    return {
        "symbol": symbol,
        "article_count": n,
        "avg_sentiment": round(avg_enhanced, 4),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "max_sentiment": round(max(scores), 4),
        "min_sentiment": round(min(scores), 4),
        "positive_ratio": round(positive_ratio, 4),
        "sentiment_score": round(sentiment_score, 4),
        "score_dispersion": round(dispersion, 4),
    }
