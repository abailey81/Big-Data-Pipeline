"""Value factor module for Coursework 2.

This module is written to fit your current repo style, but it is also simple
and self-contained so it can be tested without connecting to PostgreSQL.

What it does
------------
1. Select the latest fundamentals available as of a rebalance date.
2. Select the latest price available as of the same rebalance date.
3. Compute three value sub-signals:
   - Book-to-Price (B/P)
   - Earnings-to-Price (E/P)
   - Cashflow-to-Price (CF/P)
4. Winsorise each sub-signal within GICS sectors.
5. Compute sector-neutral z-scores.
6. Combine them into a final value score.

Expected inputs
---------------
fundamentals_df columns:
    symbol, report_date, field_name, field_value
    Optional: period_type

prices_df columns:
    symbol, cob_date, adj_close_price, close_price

sectors_df columns:
    symbol, gics_sector

Notes
-----
- This follows your CW1 / CW2 logic closely:
  * use report_date <= rebalance_date
  * use latest available information only
  * winsorise at 2.5% / 97.5%
  * z-score within GICS sectors
  * sectors with fewer than 5 names get neutral z-score = 0
- CF/P is implemented as cash flow divided by market cap, consistent with your
  repo's existing ratio logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ValueFactorConfig:
    """Configuration for value factor construction."""

    winsor_lower: float = 0.025
    winsor_upper: float = 0.975
    min_sector_size: int = 5
    neutral_score: float = 0.0


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------


def _to_datetime(series: pd.Series) -> pd.Series:
    """Convert a pandas Series to datetime safely."""
    return pd.to_datetime(series, errors="coerce")


def _to_numeric(series: pd.Series) -> pd.Series:
    """Convert a pandas Series to numeric safely."""
    return pd.to_numeric(series, errors="coerce")


def _latest_rows_asof(
    df: pd.DataFrame,
    date_col: str,
    group_cols: list[str],
    asof_date: str | pd.Timestamp,
) -> pd.DataFrame:
    """Keep the latest row per group with date <= asof_date."""
    out = df.copy()
    out[date_col] = _to_datetime(out[date_col])
    asof_ts = pd.Timestamp(asof_date)
    out = out[out[date_col].notna() & (out[date_col] <= asof_ts)].copy()
    if out.empty:
        return out
    out = out.sort_values(group_cols + [date_col])
    out = out.drop_duplicates(subset=group_cols, keep="last")
    return out


def _winsorise_within_group(
    series: pd.Series,
    lower: float,
    upper: float,
) -> pd.Series:
    """Winsorise a numeric Series using its own quantiles."""
    clean = series.dropna()
    if clean.empty:
        return series
    lo = clean.quantile(lower)
    hi = clean.quantile(upper)
    return series.clip(lower=lo, upper=hi)


def _apply_group_winsorisation(
    df: pd.DataFrame,
    value_col: str,
    group_col: str,
    lower: float,
    upper: float,
) -> pd.Series:
    """Winsorise one column within groups."""
    return df.groupby(group_col, dropna=False)[value_col].transform(
        lambda s: _winsorise_within_group(s, lower=lower, upper=upper)
    )


def _zscore_within_sector(
    df: pd.DataFrame,
    value_col: str,
    sector_col: str,
    min_sector_size: int,
    neutral_score: float,
) -> pd.Series:
    """Compute z-score within sector; small sectors get neutral score."""

    def _sector_zscore(series: pd.Series) -> pd.Series:
        valid = series.dropna()
        if len(valid) < min_sector_size:
            return pd.Series(neutral_score, index=series.index, dtype="float64")

        std = valid.std(ddof=0)
        if pd.isna(std) or std == 0:
            return pd.Series(neutral_score, index=series.index, dtype="float64")

        mean = valid.mean()
        z = (series - mean) / std
        return z.fillna(neutral_score)

    return df.groupby(sector_col, dropna=False)[value_col].transform(_sector_zscore)


# ---------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------


def prepare_latest_fundamentals(
    fundamentals_df: pd.DataFrame,
    rebalance_date: str | pd.Timestamp,
) -> pd.DataFrame:
    """Prepare one latest fundamental value per symbol / field as of date.

    Required columns:
        symbol, report_date, field_name, field_value
    """
    required = {"symbol", "report_date", "field_name", "field_value"}
    missing = required - set(fundamentals_df.columns)
    if missing:
        raise ValueError(f"fundamentals_df is missing columns: {sorted(missing)}")

    latest = _latest_rows_asof(
        fundamentals_df.copy(),
        date_col="report_date",
        group_cols=["symbol", "field_name"],
        asof_date=rebalance_date,
    )
    if latest.empty:
        return latest

    latest["field_value"] = _to_numeric(latest["field_value"])
    wide = latest.pivot(index="symbol", columns="field_name", values="field_value").reset_index()
    wide.columns.name = None
    return wide


def prepare_latest_prices(
    prices_df: pd.DataFrame,
    rebalance_date: str | pd.Timestamp,
) -> pd.DataFrame:
    """Prepare one latest price per symbol as of date.

    Required columns:
        symbol, cob_date
    Optional price columns:
        adj_close_price, close_price
    """
    required = {"symbol", "cob_date"}
    missing = required - set(prices_df.columns)
    if missing:
        raise ValueError(f"prices_df is missing columns: {sorted(missing)}")

    latest = _latest_rows_asof(
        prices_df.copy(),
        date_col="cob_date",
        group_cols=["symbol"],
        asof_date=rebalance_date,
    )
    if latest.empty:
        return latest

    if "adj_close_price" in latest.columns:
        latest["adj_close_price"] = _to_numeric(latest["adj_close_price"])
    if "close_price" in latest.columns:
        latest["close_price"] = _to_numeric(latest["close_price"])

    latest["price"] = latest.get("adj_close_price")
    if "price" not in latest.columns or latest["price"].isna().all():
        latest["price"] = latest.get("close_price")
    else:
        latest["price"] = latest["price"].fillna(latest.get("close_price"))

    return latest[["symbol", "cob_date", "price"]].copy()


# ---------------------------------------------------------------------
# Core factor logic
# ---------------------------------------------------------------------


def compute_value_subsignals(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Compute raw B/P, E/P, and CF/P from merged fundamentals + prices.

    Fallback rules follow your repo / report logic:
    - equity fallback: total_assets - total_liabilities
    - EPS fallback: diluted_eps -> basic_eps
    - FCF fallback: free_cash_flow -> operating_cash_flow - abs(capex)
    - shares fallback: stockholders_equity / book_value_per_share
    """
    df = raw_df.copy()

    for col in [
        "price",
        "book_value_per_share",
        "stockholders_equity",
        "total_assets",
        "total_liabilities",
        "diluted_eps",
        "basic_eps",
        "free_cash_flow",
        "operating_cash_flow",
        "capital_expenditure",
        "shares_outstanding",
    ]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = _to_numeric(df[col])

    # Price must be positive
    df.loc[df["price"] <= 0, "price"] = np.nan

    # Equity fallback
    df["equity_fallback"] = df["stockholders_equity"]
    missing_equity = df["equity_fallback"].isna()
    df.loc[missing_equity, "equity_fallback"] = (
        df.loc[missing_equity, "total_assets"] - df.loc[missing_equity, "total_liabilities"]
    )

    # EPS fallback
    df["eps_used"] = df["diluted_eps"].fillna(df["basic_eps"])

    # FCF fallback
    df["fcf_used"] = df["free_cash_flow"]
    missing_fcf = df["fcf_used"].isna()
    can_build_fcf = missing_fcf & df["operating_cash_flow"].notna() & df["capital_expenditure"].notna()
    df.loc[can_build_fcf, "fcf_used"] = (
        df.loc[can_build_fcf, "operating_cash_flow"] - df.loc[can_build_fcf, "capital_expenditure"].abs()
    )

    # Shares fallback: if missing, try equity / book_value_per_share
    missing_shares = df["shares_outstanding"].isna() | (df["shares_outstanding"] <= 0)
    can_build_shares = (
        missing_shares
        & df["equity_fallback"].notna()
        & df["book_value_per_share"].notna()
        & (df["book_value_per_share"] != 0)
    )
    df.loc[can_build_shares, "shares_outstanding"] = (
        df.loc[can_build_shares, "equity_fallback"] / df.loc[can_build_shares, "book_value_per_share"]
    )

    # Market cap for CF/P
    df["market_cap_used"] = df["price"] * df["shares_outstanding"]
    df.loc[df["market_cap_used"] <= 0, "market_cap_used"] = np.nan

    # Raw sub-signals
    df["book_to_price_raw"] = df["book_value_per_share"] / df["price"]
    df["earnings_to_price_raw"] = df["eps_used"] / df["price"]
    df["cashflow_to_price_raw"] = df["fcf_used"] / df["market_cap_used"]

    # Clean impossible inf values
    for col in ["book_to_price_raw", "earnings_to_price_raw", "cashflow_to_price_raw"]:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    return df


def build_value_factor(
    fundamentals_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    sectors_df: pd.DataFrame,
    rebalance_date: str | pd.Timestamp,
    config: Optional[ValueFactorConfig] = None,
) -> pd.DataFrame:
    """Build the full value factor table for one rebalance date."""
    if config is None:
        config = ValueFactorConfig()

    required_sector_cols = {"symbol", "gics_sector"}
    missing_sector_cols = required_sector_cols - set(sectors_df.columns)
    if missing_sector_cols:
        raise ValueError(f"sectors_df is missing columns: {sorted(missing_sector_cols)}")

    fund_wide = prepare_latest_fundamentals(fundamentals_df, rebalance_date)
    price_latest = prepare_latest_prices(prices_df, rebalance_date)

    merged = sectors_df[["symbol", "gics_sector"]].copy()
    merged = merged.merge(fund_wide, on="symbol", how="left")
    merged = merged.merge(price_latest[["symbol", "price"]], on="symbol", how="left")

    merged = compute_value_subsignals(merged)

    # Winsorise within sector
    merged["book_to_price_w"] = _apply_group_winsorisation(
        merged,
        value_col="book_to_price_raw",
        group_col="gics_sector",
        lower=config.winsor_lower,
        upper=config.winsor_upper,
    )
    merged["earnings_to_price_w"] = _apply_group_winsorisation(
        merged,
        value_col="earnings_to_price_raw",
        group_col="gics_sector",
        lower=config.winsor_lower,
        upper=config.winsor_upper,
    )
    merged["cashflow_to_price_w"] = _apply_group_winsorisation(
        merged,
        value_col="cashflow_to_price_raw",
        group_col="gics_sector",
        lower=config.winsor_lower,
        upper=config.winsor_upper,
    )

    # Sector-neutral z-scores
    merged["book_to_price_z"] = _zscore_within_sector(
        merged,
        value_col="book_to_price_w",
        sector_col="gics_sector",
        min_sector_size=config.min_sector_size,
        neutral_score=config.neutral_score,
    )
    merged["earnings_to_price_z"] = _zscore_within_sector(
        merged,
        value_col="earnings_to_price_w",
        sector_col="gics_sector",
        min_sector_size=config.min_sector_size,
        neutral_score=config.neutral_score,
    )
    merged["cashflow_to_price_z"] = _zscore_within_sector(
        merged,
        value_col="cashflow_to_price_w",
        sector_col="gics_sector",
        min_sector_size=config.min_sector_size,
        neutral_score=config.neutral_score,
    )

    merged["value_score"] = merged[
        ["book_to_price_z", "earnings_to_price_z", "cashflow_to_price_z"]
    ].mean(axis=1, skipna=True)
    merged["rebalance_date"] = pd.Timestamp(rebalance_date).date()

    return merged[
        [
            "symbol",
            "gics_sector",
            "rebalance_date",
            "price",
            "book_to_price_raw",
            "earnings_to_price_raw",
            "cashflow_to_price_raw",
            "book_to_price_w",
            "earnings_to_price_w",
            "cashflow_to_price_w",
            "book_to_price_z",
            "earnings_to_price_z",
            "cashflow_to_price_z",
            "value_score",
        ]
    ].copy()


# ---------------------------------------------------------------------
# Optional helper: SQL query text you can use later
# ---------------------------------------------------------------------

FUNDAMENTALS_SQL = """
SELECT symbol, report_date, field_name, field_value, period_type
FROM systematic_equity.fundamentals
WHERE report_date <= :rebalance_date
""".strip()


PRICES_SQL = """
SELECT symbol, cob_date, adj_close_price, close_price
FROM systematic_equity.daily_prices
WHERE cob_date <= :rebalance_date
""".strip()


SECTORS_SQL = """
SELECT symbol, gics_sector
FROM systematic_equity.company_static
""".strip()