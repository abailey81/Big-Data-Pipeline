"""
factors/sentiment_modifier.py -- Delta-based conviction modifier (15%).

Sentiment delta = current score - previous score (6 months ago).
Only for stocks with >= min_articles.

Returns per-stock multiplier:
  - Improving sentiment:     long_mult=1.2, short_mult=0.7
  - Deteriorating sentiment: long_mult=0.7, short_mult=1.2
  - Neutral / no data:       mult=1.0

FIX (Codex audit #6): cob_date = window END (data already fixed in parquet).
"""

import pandas as pd
import numpy as np


def compute_sentiment_conviction(
    news_sentiment_df: pd.DataFrame,
    as_of: pd.Timestamp,
    config: dict,
) -> pd.DataFrame:
    """Compute per-stock sentiment delta and conviction multipliers.

    Parameters
    ----------
    news_sentiment_df : raw news_sentiment DataFrame
    as_of : rebalance date
    config : factors.sentiment config block

    Returns
    -------
    DataFrame with columns: symbol, delta_sentiment, long_mult, short_mult
    """
    lookback_days = config.get("lookback_days", 180)
    min_articles = config.get("confidence_min_articles", 3)
    improving_mult = config.get("improving_mult", 1.2)
    deteriorating_mult = config.get("deteriorating_mult", 0.7)

    empty = pd.DataFrame(
        columns=["symbol", "delta_sentiment", "long_mult", "short_mult"]
    )

    if news_sentiment_df is None or news_sentiment_df.empty:
        return empty

    # cob_date = window END, so we filter directly
    cutoff = as_of - pd.Timedelta(days=lookback_days)

    # Current sentiment: most recent cob_date <= as_of
    recent = news_sentiment_df[news_sentiment_df["cob_date"] <= as_of]
    if recent.empty:
        return empty

    # Filter by minimum article count
    recent = recent[recent["article_count"] >= min_articles]

    # Get latest record per symbol
    current = recent.sort_values("cob_date").groupby("symbol").last()

    # Previous sentiment: most recent cob_date <= as_of - 180 days
    past_cutoff = as_of - pd.Timedelta(days=lookback_days)
    past = news_sentiment_df[news_sentiment_df["cob_date"] <= past_cutoff]
    past = past[past["article_count"] >= min_articles]

    if past.empty:
        return empty

    previous = past.sort_values("cob_date").groupby("symbol").last()

    # Compute delta
    common = current.index.intersection(previous.index)
    if len(common) < 5:
        return empty

    delta = (
        current.loc[common, "sentiment_score"]
        - previous.loc[common, "sentiment_score"]
    )

    # Rank delta cross-sectionally
    delta_rank = delta.rank(pct=True)

    # Build result
    rows = []
    for sym in common:
        dr = delta_rank[sym]
        ds = delta[sym]

        if dr > 0.7:
            # Improving sentiment
            lm = improving_mult
            sm = deteriorating_mult
        elif dr < 0.3:
            # Deteriorating sentiment
            lm = deteriorating_mult
            sm = improving_mult
        else:
            lm = 1.0
            sm = 1.0

        rows.append({
            "symbol": sym,
            "delta_sentiment": ds,
            "long_mult": lm,
            "short_mult": sm,
        })

    return pd.DataFrame(rows)
