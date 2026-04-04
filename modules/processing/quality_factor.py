from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class QualityFactorConfig:
    """Configuration for quality factor construction."""

    winsor_lower: float = 0.025
    winsor_upper: float = 0.975
    min_sector_size: int = 5
    neutral_score: float = 0.0
    eps_stability_quarters: int = 12


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


def prepare_eps_history(
    fundamentals_df: pd.DataFrame,
    rebalance_date: str | pd.Timestamp,
) -> pd.DataFrame:
    """Prepare trailing EPS history per symbol up to rebalance date.

    Uses diluted_eps first, then basic_eps as fallback at each report_date.
    Returns columns:
        symbol, earnings_stability_raw
    """
    required = {"symbol", "report_date", "field_name", "field_value"}
    missing = required - set(fundamentals_df.columns)
    if missing:
        raise ValueError(f"fundamentals_df is missing columns: {sorted(missing)}")

    df = fundamentals_df.copy()
    df["report_date"] = _to_datetime(df["report_date"])
    df = df[df["report_date"].notna() & (df["report_date"] <= pd.Timestamp(rebalance_date))].copy()
    if df.empty:
        return pd.DataFrame(columns=["symbol", "earnings_stability_raw"])

    eps_df = df[df["field_name"].isin(["diluted_eps", "basic_eps"])].copy()
    if eps_df.empty:
        return pd.DataFrame(columns=["symbol", "earnings_stability_raw"])

    eps_df["field_value"] = _to_numeric(eps_df["field_value"])

    eps_wide = (
        eps_df.pivot_table(
            index=["symbol", "report_date"],
            columns="field_name",
            values="field_value",
            aggfunc="last",
        )
        .reset_index()
    )
    eps_wide.columns.name = None

    if "diluted_eps" not in eps_wide.columns:
        eps_wide["diluted_eps"] = np.nan
    if "basic_eps" not in eps_wide.columns:
        eps_wide["basic_eps"] = np.nan

    eps_wide["eps_used"] = eps_wide["diluted_eps"].fillna(eps_wide["basic_eps"])
    eps_wide = eps_wide.sort_values(["symbol", "report_date"]).copy()

    out_rows = []

    for symbol, group in eps_wide.groupby("symbol", dropna=False):
        g = group.copy()
        g = g[g["eps_used"].notna()].copy()
        if g.empty:
            out_rows.append({"symbol": symbol, "earnings_stability_raw": np.nan})
            continue

        g = g.tail(12).copy()
        if len(g) < 4:
            out_rows.append({"symbol": symbol, "earnings_stability_raw": np.nan})
            continue

        # EPS growth based on pct_change; remove impossible infinite values
        g["eps_growth"] = g["eps_used"].pct_change()
        g["eps_growth"] = g["eps_growth"].replace([np.inf, -np.inf], np.nan)

        valid_growth = g["eps_growth"].dropna()
        if len(valid_growth) < 3:
            out_rows.append({"symbol": symbol, "earnings_stability_raw": np.nan})
            continue

        growth_std = valid_growth.std(ddof=0)
        if pd.isna(growth_std) or growth_std <= 0:
            out_rows.append({"symbol": symbol, "earnings_stability_raw": np.nan})
            continue

        out_rows.append(
            {
                "symbol": symbol,
                "earnings_stability_raw": 1.0 / growth_std,
            }
        )

    return pd.DataFrame(out_rows)


# ---------------------------------------------------------------------
# Core factor logic
# ---------------------------------------------------------------------


def compute_quality_subsignals(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Compute raw quality signals from merged fundamentals.

    Signals:
    - ROE = net_income / equity
    - inverse D/E = equity / total_debt
    - earnings_stability_raw is expected to be precomputed and merged in

    Fallback:
    - equity fallback: stockholders_equity -> total_assets - total_liabilities
    """
    df = raw_df.copy()

    for col in [
        "net_income",
        "stockholders_equity",
        "total_assets",
        "total_liabilities",
        "total_debt",
        "earnings_stability_raw",
    ]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = _to_numeric(df[col])

    # Equity fallback
    df["equity_used"] = df["stockholders_equity"]
    missing_equity = df["equity_used"].isna()
    df.loc[missing_equity, "equity_used"] = (
        df.loc[missing_equity, "total_assets"] - df.loc[missing_equity, "total_liabilities"]
    )

    # Clean denominators
    df.loc[df["equity_used"] <= 0, "equity_used"] = np.nan
    df.loc[df["total_debt"] < 0, "total_debt"] = np.nan

    # Raw sub-signals
    df["roe_raw"] = df["net_income"] / df["equity_used"]

    # Inverse D/E
    # If debt is zero, this becomes an extreme "good" value; winsorisation later controls this.
    df["inverse_de_raw"] = df["equity_used"] / df["total_debt"]
    df.loc[df["total_debt"] == 0, "inverse_de_raw"] = np.inf

    for col in ["roe_raw", "inverse_de_raw", "earnings_stability_raw"]:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan if col != "inverse_de_raw" else np.inf)

    return df


def build_quality_factor(
    fundamentals_df: pd.DataFrame,
    sectors_df: pd.DataFrame,
    rebalance_date: str | pd.Timestamp,
    config: Optional[QualityFactorConfig] = None,
) -> pd.DataFrame:
    """Build the full quality factor table for one rebalance date."""
    if config is None:
        config = QualityFactorConfig()

    required_sector_cols = {"symbol", "gics_sector"}
    missing_sector_cols = required_sector_cols - set(sectors_df.columns)
    if missing_sector_cols:
        raise ValueError(f"sectors_df is missing columns: {sorted(missing_sector_cols)}")

    fund_wide = prepare_latest_fundamentals(fundamentals_df, rebalance_date)
    eps_stability = prepare_eps_history(fundamentals_df, rebalance_date)

    merged = sectors_df[["symbol", "gics_sector"]].copy()
    merged = merged.merge(fund_wide, on="symbol", how="left")
    merged = merged.merge(eps_stability, on="symbol", how="left")

    merged = compute_quality_subsignals(merged)

    # Winsorise within sector
    merged["roe_w"] = _apply_group_winsorisation(
        merged,
        value_col="roe_raw",
        group_col="gics_sector",
        lower=config.winsor_lower,
        upper=config.winsor_upper,
    )
    merged["earnings_stability_w"] = _apply_group_winsorisation(
        merged,
        value_col="earnings_stability_raw",
        group_col="gics_sector",
        lower=config.winsor_lower,
        upper=config.winsor_upper,
    )
    merged["inverse_de_w"] = _apply_group_winsorisation(
        merged,
        value_col="inverse_de_raw",
        group_col="gics_sector",
        lower=config.winsor_lower,
        upper=config.winsor_upper,
    )

    # Sector-neutral z-scores
    merged["roe_z"] = _zscore_within_sector(
        merged,
        value_col="roe_w",
        sector_col="gics_sector",
        min_sector_size=config.min_sector_size,
        neutral_score=config.neutral_score,
    )
    merged["earnings_stability_z"] = _zscore_within_sector(
        merged,
        value_col="earnings_stability_w",
        sector_col="gics_sector",
        min_sector_size=config.min_sector_size,
        neutral_score=config.neutral_score,
    )
    merged["inverse_de_z"] = _zscore_within_sector(
        merged,
        value_col="inverse_de_w",
        sector_col="gics_sector",
        min_sector_size=config.min_sector_size,
        neutral_score=config.neutral_score,
    )

    merged["quality_score"] = merged[
        ["roe_z", "earnings_stability_z", "inverse_de_z"]
    ].mean(axis=1, skipna=True)

    merged["rebalance_date"] = pd.Timestamp(rebalance_date).date()

    return merged[
        [
            "symbol",
            "gics_sector",
            "rebalance_date",
            "roe_raw",
            "earnings_stability_raw",
            "inverse_de_raw",
            "roe_w",
            "earnings_stability_w",
            "inverse_de_w",
            "roe_z",
            "earnings_stability_z",
            "inverse_de_z",
            "quality_score",
        ]
    ].copy()